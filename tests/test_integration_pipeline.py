"""
Test de integración del pipeline completo (sin cámara y sin MediaPipe real).

Por qué hace falta
------------------
Los demás tests cubren `geometry.py` y `fusion_fsm.py` por separado, y ambos
pasaban al 100% mientras la aplicación tenía dos fallos de cableado que solo
se ven al ejecutar el pipeline entero:

  - `HistoryLogger.log_event()` no se llamaba desde ningún sitio. La tabla
    `events` quedaba vacía en todas las sesiones (verificado: 3 sesiones,
    107 filas de métricas, **0 eventos**) — y esa bitácora de eventos es un
    entregable del Capítulo IV.
  - `metrics_log.timestamp` guardaba `perf_counter()` en lugar de hora de
    pared, en una base de tiempo distinta a la de `events.timestamp`.

Aquí se sustituyen la cámara y los dos modelos de MediaPipe por dobles de
prueba, pero `InferenceThread`, `geometry`, `smoothing`, `FusionFSM` y
`HistoryLogger` son los reales.
"""

import math
import sqlite3
import sys
import time
from pathlib import Path
from queue import Queue

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fusion.fusion_fsm import AlertType

W, H = 320, 240


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------

class _LM:
    def __init__(self, x, y, z=0.0, visibility=1.0):
        self.x, self.y, self.z, self.visibility = x, y, z, visibility


def _pose_landmarks(forward_cm=0.0, roll_deg=0.0, px_per_cm=3.0):
    """Landmarks de pose para un sujeto a ~0.6 m en un frame 320x240."""
    cx, cy = W / 2.0, H * 0.75
    half = 19.0 * px_per_cm
    r = math.radians(roll_deg)
    ear_y = cy - 22.0 * px_per_cm
    ear_z = -forward_cm * px_per_cm / W

    lms = [_LM(0.5, 0.5) for _ in range(33)]
    lms[7]  = _LM((cx - 7.0 * px_per_cm) / W, ear_y / H, ear_z)
    lms[8]  = _LM((cx + 7.0 * px_per_cm) / W, ear_y / H, ear_z)
    lms[11] = _LM((cx - half * math.cos(r)) / W, (cy - half * math.sin(r)) / H)
    lms[12] = _LM((cx + half * math.cos(r)) / W, (cy + half * math.sin(r)) / H)
    return lms


def _face_landmarks(ear=0.32, mar=0.05):
    """Landmarks faciales con EAR y MAR conocidos (en píxeles)."""
    lms = [_LM(0.5, 0.5) for _ in range(478)]

    def _eye(indices, cx):
        w_px, h_px = 24.0, ear * 24.0
        pts = [(cx - w_px / 2, 100.0), (cx - w_px / 4, 100.0 - h_px / 2),
               (cx + w_px / 4, 100.0 - h_px / 2), (cx + w_px / 2, 100.0),
               (cx + w_px / 4, 100.0 + h_px / 2), (cx - w_px / 4, 100.0 + h_px / 2)]
        for idx, (x, y) in zip(indices, pts):
            lms[idx] = _LM(x / W, y / H)

    _eye([362, 385, 387, 263, 373, 380], 190.0)
    _eye([33, 160, 158, 133, 153, 144], 130.0)

    mouth_w = 40.0
    lms[13]  = _LM(160.0 / W, (150.0 - mar * mouth_w / 2) / H)
    lms[14]  = _LM(160.0 / W, (150.0 + mar * mouth_w / 2) / H)
    lms[61]  = _LM((160.0 - mouth_w / 2) / W, 150.0 / H)
    lms[291] = _LM((160.0 + mouth_w / 2) / W, 150.0 / H)
    return lms


class _FakePoseEstimator:
    REQUIRED_LANDMARKS = (7, 8, 11, 12)

    def __init__(self, scenario):
        self._scenario = scenario
        self.draw_landmarks = False

    def process(self, frame, is_rgb=False):
        from src.vision.pose_estimator import PoseResult
        return PoseResult(landmarks=_pose_landmarks(**self._scenario["pose"]),
                          world_landmarks=[], detected=True)

    def draw(self, image, result):
        return image

    def close(self):
        pass


class _FakeFaceEstimator:
    LEFT_EYE_EAR_INDICES = [362, 385, 387, 263, 373, 380]
    RIGHT_EYE_EAR_INDICES = [33, 160, 158, 133, 153, 144]

    def __init__(self, scenario):
        self._scenario = scenario
        self.draw_landmarks = False

    def process(self, frame, is_rgb=False):
        from src.vision.face_estimator import FaceResult
        return FaceResult(landmarks=_face_landmarks(**self._scenario["face"]),
                          detected=True)

    def draw(self, image, result):
        return image

    def close(self):
        pass


class _FakeVideoThread:
    def __init__(self):
        self._frame = np.zeros((H, W, 3), dtype=np.uint8)

    def start(self):
        return self

    def stop(self):
        pass

    def get_frame(self, timeout=0.05):
        return self._frame.copy()


# ---------------------------------------------------------------------------
# Arranque del pipeline con dobles
# ---------------------------------------------------------------------------

def _thresholds(tmp_db, **over):
    cfg = {
        "postural": {
            "cervical_angle_max_deg": 30.0,
            "cervical_alert_window_sec": 0.3,
            "shoulder_asymmetry_max_deg": 10.0,
            "shoulder_alert_window_sec": 0.3,
        },
        "fatigue": {
            "ear_threshold": 0.21,
            "ear_alert_window_sec": 0.3,
            "blink_min_ms": 100,
            "blink_max_ms": 400,
            "mouth_opening_threshold": 0.45,
            "yawn_alert_window_sec": 0.3,
            "perclos_enabled": False,
        },
        "smoothing": {"median_window": 3, "ema_alpha": 0.9},
        "camera": {
            "resolution_width": W,
            "resolution_height": H,
            "horizontal_fov_deg": 60.0,
            "min_distance_m": 0.30,
            "max_distance_m": 1.50,
            "distance_tolerance_m": 0.50,
        },
        "mediapipe": {"pose_min_landmark_visibility": 0.5},
        "ui": {"alert_cooldown_sec": 0.0},
        "storage": {"db_path": str(tmp_db)},
    }
    for section, values in over.items():
        cfg[section] = {**cfg[section], **values}
    return cfg


def _run_pipeline(tmp_path, scenario, seconds=1.2, **over):
    """
    Ejecuta el InferenceThread real durante `seconds` con estimadores falsos.

    Devuelve (alertas_recibidas, ruta_db, ultimo_estado).
    """
    import src.ui.app_ui as app_ui

    db_path = tmp_path / "test_history.db"
    thresholds = _thresholds(db_path, **over)

    class _FakeVideoFeed:
        def encode(self, frame, fps=0.0):
            return "fake-b64"

    alerts = []
    state_queue: Queue = Queue(maxsize=1)

    # Sustituir solo los modelos de MediaPipe: todo lo demás es real.
    orig_pose, orig_face = app_ui.PoseEstimator, app_ui.FaceEstimator
    app_ui.PoseEstimator = lambda **kw: _FakePoseEstimator(scenario)
    app_ui.FaceEstimator = lambda **kw: _FakeFaceEstimator(scenario)
    app_ui.PoseEstimator.REQUIRED_LANDMARKS = orig_pose.REQUIRED_LANDMARKS
    app_ui.FaceEstimator.LEFT_EYE_EAR_INDICES = orig_face.LEFT_EYE_EAR_INDICES
    app_ui.FaceEstimator.RIGHT_EYE_EAR_INDICES = orig_face.RIGHT_EYE_EAR_INDICES
    try:
        thread = app_ui.InferenceThread(
            video_thread=_FakeVideoThread(),
            thresholds=thresholds,
            state_queue=state_queue,
            on_alert=alerts.append,
            video_feed=_FakeVideoFeed(),
            log_metrics_every_n=5,
        )
        thread.start()
        deadline = time.perf_counter() + seconds
        last_state = None
        while time.perf_counter() < deadline:
            if not state_queue.empty():
                last_state = state_queue.get()
            time.sleep(0.01)
        thread.stop()
        thread.join(timeout=5.0)
        assert not thread.is_alive(), "El InferenceThread no terminó"
    finally:
        app_ui.PoseEstimator, app_ui.FaceEstimator = orig_pose, orig_face

    return alerts, db_path, last_state


NEUTRAL = {"pose": {"forward_cm": 0.0}, "face": {"ear": 0.32, "mar": 0.05}}
FORWARD_HEAD = {"pose": {"forward_cm": 15.0}, "face": {"ear": 0.32, "mar": 0.05}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPipelineNeutralPosture:

    def test_neutral_posture_produces_no_alerts(self, tmp_path):
        """
        Con la geometría corregida, una postura neutra no debe generar
        ninguna alerta. Antes de la corrección θc medía ~41° en postura
        normal (mediana real de la sesión del 2026-07-12), por encima del
        umbral de 30°, de modo que el sistema alertaba de forma permanente.
        """
        alerts, _, state = _run_pipeline(tmp_path, NEUTRAL)
        assert alerts == [], [a.message for a in alerts]
        assert state is not None
        assert state.metrics.cervical_angle < 5.0, state.metrics.cervical_angle

    def test_metrics_are_persisted_with_wall_clock(self, tmp_path):
        _, db_path, _ = _run_pipeline(tmp_path, NEUTRAL)
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT timestamp, cervical_angle, distance_m, pose_valid "
            "FROM metrics_log").fetchall()
        con.close()
        assert rows, "No se registró ninguna métrica"
        for ts, theta, dist, valid in rows:
            assert abs(ts - time.time()) < 60, (
                f"timestamp={ts} no es hora de pared (antes se guardaba "
                f"perf_counter, p. ej. 214173.39)"
            )
            assert dist is not None, "La distancia estimada debe persistirse"
            assert valid == 1

    def test_session_is_closed_on_shutdown(self, tmp_path):
        """`sessions.ended_at` debe rellenarse: sin él no hay duración."""
        _, db_path, _ = _run_pipeline(tmp_path, NEUTRAL)
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT started_at, ended_at FROM sessions").fetchall()
        con.close()
        assert rows
        for started, ended in rows:
            assert ended is not None, "La sesión quedó sin cerrar"
            assert ended >= started


class TestPipelineRiskPosture:

    def test_forward_head_triggers_cervical_alert(self, tmp_path):
        alerts, _, _ = _run_pipeline(tmp_path, FORWARD_HEAD)
        assert any(a.alert_type == AlertType.CERVICAL_ANGLE for a in alerts), (
            f"Esperada alerta cervical, se recibieron {[a.alert_type for a in alerts]}"
        )

    def test_alerts_are_written_to_the_event_log(self, tmp_path):
        """
        REGRESIÓN — la tabla `events` quedaba siempre vacía.

        `HistoryLogger.log_event()` existía y estaba probado, pero ningún
        punto del código lo llamaba: el callback del FSM iba solo a la UI. La
        bitácora de eventos de una sesión real es un entregable explícito del
        Capítulo IV, así que la sesión de validación se habría hecho sin poder
        recuperar un solo evento.
        """
        alerts, db_path, _ = _run_pipeline(tmp_path, FORWARD_HEAD)
        assert alerts, "El escenario debía generar alertas"

        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT alert_type, metric_value, threshold, timestamp "
            "FROM events ORDER BY timestamp").fetchall()
        con.close()

        assert len(rows) == len(alerts), (
            f"{len(alerts)} alertas emitidas pero {len(rows)} registradas"
        )
        assert rows[0][0] == "cervical_angle"
        assert rows[0][2] == 30.0
        assert abs(rows[0][3] - time.time()) < 60

    def test_shoulder_asymmetry_triggers_alert(self, tmp_path):
        scenario = {"pose": {"roll_deg": 20.0}, "face": {"ear": 0.32, "mar": 0.05}}
        alerts, _, _ = _run_pipeline(tmp_path, scenario)
        assert any(a.alert_type == AlertType.SHOULDER_ASYMM for a in alerts)

    def test_closed_eyes_trigger_fatigue_alert(self, tmp_path):
        scenario = {"pose": {"forward_cm": 0.0}, "face": {"ear": 0.10, "mar": 0.05}}
        alerts, _, _ = _run_pipeline(tmp_path, scenario)
        assert any(a.alert_type == AlertType.EYE_FATIGUE for a in alerts)


class TestPipelineFraming:

    def test_out_of_range_distance_blocks_postural_alerts(self, tmp_path):
        """
        Con el sujeto fuera del rango de distancia validado, el sistema no
        debe acumular tiempo de riesgo postural: a esa distancia la medición
        no es interpretable.
        """
        alerts, _, state = _run_pipeline(
            tmp_path, FORWARD_HEAD,
            camera={"min_distance_m": 0.50, "max_distance_m": 0.70,
                    "distance_tolerance_m": 0.02},
        )
        assert not any(a.alert_type == AlertType.CERVICAL_ANGLE for a in alerts)
        assert state is not None and state.metrics.pose_valid is False
        assert state.setup_hint, "Debe mostrarse un aviso de encuadre"

    def test_setup_hint_is_empty_when_framing_is_correct(self, tmp_path):
        _, _, state = _run_pipeline(tmp_path, NEUTRAL)
        assert state is not None
        assert state.setup_hint == "", state.setup_hint


class TestSessionSummary:

    def test_summary_has_the_fields_needed_for_chapter_iv(self, tmp_path):
        from src.storage.history_logger import HistoryLogger

        _, db_path, _ = _run_pipeline(tmp_path, FORWARD_HEAD)
        db = HistoryLogger(db_path=str(db_path))
        con = sqlite3.connect(db_path)
        sid = con.execute("SELECT id FROM sessions LIMIT 1").fetchone()[0]
        con.close()

        summary = db.get_session_summary(sid)
        db.close()

        for key in ("duration_min", "total_events", "events_by_type",
                    "avg_theta_c_deg", "max_theta_c_deg",
                    "avg_theta_sagital_deg", "avg_delta_e_deg",
                    "avg_ear", "avg_distance_m", "avg_fps", "total_frames"):
            assert key in summary, f"Falta '{key}' en el resumen"

        assert summary["total_events"] > 0
        assert summary["duration_min"] is not None
        assert summary["total_frames"] > 0
