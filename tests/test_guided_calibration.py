"""
test_guided_calibration.py
--------------------------
Calibración guiada y modos de la aplicación.

La app ya no empieza a vigilar al abrirse: arranca en vista previa, el
usuario calibra con el encuadre comprobado y pulsa Iniciar. Estas pruebas
cubren:

  - la lógica de la calibración guiada (fases, encuadre, reinicios, fallo);
  - que el FSM calibre sin evaluar alertas, y que reanudar conserve la
    calibración;
  - los modos del hilo de inferencia real (con MediaPipe sustituido): en
    vista previa no hay alertas ni bitácora, y una sesión = una calibración
    + un seguimiento;
  - la bandeja y la tarjeta de calibración.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import sqlite3
import sys
import time
from pathlib import Path
from queue import Queue

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.fusion.fusion_fsm import AlertType, FusionFSM, SensorMetrics
from src.fusion.guided_calibration import (
    COLLECTING, COUNTDOWN, DONE, FAILED, IDLE, GuidedCalibration,
)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_fsm(posture="reference", ear="adaptive", **post_over):
    post = {"cervical_mode": posture, "posture_calibration_sec": 2.0,
            "cervical_deviation_max_deg": 15.0,
            "cervical_alert_window_sec": 1.0, **post_over}
    fat = {"ear_threshold_mode": ear, "ear_calibration_sec": 2.0}
    return FusionFSM({"postural": post, "fatigue": fat},
                     cooldown_sec=0.0, clock=_Clock())


def good(t, theta=48.0, ear=0.25, distance=0.60, **over):
    """Métricas de un usuario bien encuadrado."""
    kw = dict(cervical_angle=theta, ear_avg=ear, distance_m=distance,
              monotonic=t, pose_detected=True, pose_valid=True,
              pose_visible=True, face_detected=True)
    kw.update(over)
    return SensorMetrics(**kw)


def run(calib, fsm, seconds, fps=30.0, **over):
    """Avanza la calibración `seconds` a `fps`. Devuelve el último estado."""
    st = None
    for _ in range(int(seconds * fps)):
        fsm._clock.t += 1.0 / fps
        st = calib.step(good(fsm._clock.t, **over), fsm._clock.t)
    return st


def make_calib(fsm, **kw):
    params = dict(min_distance_m=0.50, max_distance_m=0.70,
                  countdown_sec=1.0, framing_grace_sec=0.5, timeout_sec=20.0)
    params.update(kw)
    return GuidedCalibration(fsm, **params)


# ---------------------------------------------------------------------------
# Lógica de la calibración guiada
# ---------------------------------------------------------------------------

class TestGuidedCalibrationFlow:

    def test_full_flow_ends_with_a_result(self):
        fsm = make_fsm()
        calib = make_calib(fsm)
        assert calib.phase == IDLE

        calib.start(fsm._clock.t)
        assert calib.phase == COUNTDOWN
        assert run(calib, fsm, 0.5).phase == COUNTDOWN
        assert run(calib, fsm, 0.6).phase == COLLECTING

        st = run(calib, fsm, 3.0)
        assert st.phase == DONE
        assert st.progress == 1.0
        assert st.result["cervical_baseline"] == pytest.approx(48.0)
        assert st.result["ear_threshold"] == pytest.approx(0.65 * 0.25, abs=1e-3)
        assert st.result["distance_m"] == pytest.approx(0.60)
        assert not fsm.needs_calibration

    def test_countdown_does_not_feed_the_calibrators(self):
        """El usuario aún se está colocando: esas muestras no cuentan."""
        fsm = make_fsm()
        calib = make_calib(fsm, countdown_sec=5.0)
        calib.start(fsm._clock.t)
        run(calib, fsm, 4.0)
        assert fsm.calibration_progress()["posture"] == 0.0

    def test_losing_framing_restarts_the_measurement(self):
        """Una referencia que mezcla dos posturas no representa ninguna."""
        fsm = make_fsm()
        calib = make_calib(fsm)
        calib.start(fsm._clock.t)
        run(calib, fsm, 2.0)                       # ya midiendo
        assert calib.phase == COLLECTING

        st = run(calib, fsm, 1.0, distance=0.90)    # se aleja 1 s
        assert st.phase == COUNTDOWN
        assert "lejos" in st.message
        assert fsm.needs_calibration, "debe descartar lo medido"

    def test_brief_glitch_does_not_restart(self):
        """Un frame suelto sin detección no debe tirar la calibración."""
        fsm = make_fsm()
        calib = make_calib(fsm, framing_grace_sec=0.5)
        calib.start(fsm._clock.t)
        run(calib, fsm, 2.0)
        run(calib, fsm, 0.2, face_detected=False)   # 0.2 s < 0.5 s de gracia
        st = run(calib, fsm, 0.1)
        assert st.phase == COLLECTING

    def test_unstable_posture_is_reported(self):
        """Si el usuario se mueve, se le dice por qué la barra retrocede."""
        fsm = make_fsm()
        calib = make_calib(fsm)
        calib.start(fsm._clock.t)
        run(calib, fsm, 1.1)                        # fin de la cuenta atrás
        st = None
        for i in range(int(2.5 * 30)):              # 2.5 s oscilando ±25°
            fsm._clock.t += 1 / 30
            st = calib.step(good(fsm._clock.t, theta=25.0 if i % 2 else 75.0),
                            fsm._clock.t)
        assert st.phase == COLLECTING
        assert "moviste" in st.message

    def test_times_out_with_a_reason(self):
        fsm = make_fsm()
        calib = make_calib(fsm, timeout_sec=3.0)
        calib.start(fsm._clock.t)
        run(calib, fsm, 1.1)
        st = None
        for i in range(int(4 * 30)):
            fsm._clock.t += 1 / 30
            st = calib.step(good(fsm._clock.t, theta=25.0 if i % 2 else 75.0),
                            fsm._clock.t)
        assert st.phase == FAILED
        assert st.message

    def test_nothing_to_calibrate_finishes_after_countdown(self):
        """Con θc absoluto y EAR fijo solo se comprueba el encuadre."""
        fsm = make_fsm(posture="absolute", ear="fixed")
        calib = make_calib(fsm)
        calib.start(fsm._clock.t)
        st = run(calib, fsm, 1.2)
        assert st.phase == DONE

    def test_cancel_returns_to_idle(self):
        fsm = make_fsm()
        calib = make_calib(fsm)
        calib.start(fsm._clock.t)
        calib.cancel()
        assert calib.phase == IDLE


class TestFramingCheck:

    @pytest.mark.parametrize("over, fallo", [
        ({"face_detected": False}, "face"),
        ({"pose_visible": False}, "body"),
        ({"pose_detected": False}, "body"),
        ({"distance_m": 0.40}, "distance_ok"),
        ({"distance_m": 0.85}, "distance_ok"),
        ({"distance_m": None}, "distance_ok"),
    ])
    def test_each_problem_is_reported_separately(self, over, fallo):
        """El usuario necesita saber QUÉ corregir, no solo que algo falla."""
        calib = make_calib(make_fsm())
        chk = calib.check_framing(good(0.0, **over))
        assert chk.ok is False
        assert getattr(chk, fallo) is False

    def test_correct_framing_passes(self):
        chk = make_calib(make_fsm()).check_framing(good(0.0))
        assert chk.ok

    def test_requires_the_recommended_range_not_the_tolerance(self):
        """La referencia se toma en las condiciones de uso previstas."""
        chk = make_calib(make_fsm()).check_framing(good(0.0, distance=0.75))
        assert not chk.distance_ok


# ---------------------------------------------------------------------------
# FSM: calibrar sin evaluar, reanudar sin descalibrar
# ---------------------------------------------------------------------------

class TestFsmCalibrationApi:

    def test_feed_calibration_never_raises_alerts(self):
        fsm = make_fsm()
        alertas = []
        fsm.on_alert = alertas.append
        for _ in range(200):
            fsm._clock.t += 0.05
            fsm.feed_calibration(good(fsm._clock.t, theta=85.0, ear=0.05))
        assert alertas == []

    def test_reset_timers_keeps_the_calibration(self):
        fsm = make_fsm()
        for _ in range(100):
            fsm._clock.t += 0.05
            fsm.feed_calibration(good(fsm._clock.t))
        base, thr = fsm.cervical_baseline, fsm.ear_threshold
        fsm.reset_timers()
        assert fsm.cervical_baseline == base
        assert fsm.ear_threshold == thr

    def test_absence_recalibration_can_be_disabled(self):
        fsm = FusionFSM({"postural": {"cervical_mode": "reference",
                                      "posture_calibration_sec": 1.0,
                                      "posture_recalibrate_after_absence_sec": 0},
                         "fatigue": {}}, clock=_Clock())
        for _ in range(100):
            fsm._clock.t += 0.05
            fsm.feed_calibration(good(fsm._clock.t))
        fsm.update(good(fsm._clock.t))
        fsm._clock.t += 600                        # 10 min sin nadie
        fsm.update(good(fsm._clock.t))
        assert not fsm.posture_calibrating


# ---------------------------------------------------------------------------
# Modos del hilo de inferencia (pipeline real, MediaPipe sustituido)
# ---------------------------------------------------------------------------

def _pipeline(tmp_path, scenario, start_mode="preview"):
    import src.ui.app_ui as app_ui
    from tests.test_integration_pipeline import (
        _FakeFaceEstimator, _FakePoseEstimator, _FakeVideoThread, _thresholds,
    )

    th = _thresholds(
        tmp_path / "modes.db",
        postural={"cervical_mode": "reference", "posture_calibration_sec": 0.3},
        fatigue={"ear_threshold_mode": "adaptive", "ear_calibration_sec": 0.3},
    )
    th["calibration"] = {"countdown_sec": 0.1, "framing_grace_sec": 0.5,
                         "timeout_sec": 10.0}

    class _Feed:
        def encode(self, frame, fps=0.0):
            return "b64"

    orig = app_ui.PoseEstimator, app_ui.FaceEstimator
    app_ui.PoseEstimator = lambda **kw: _FakePoseEstimator(scenario)
    app_ui.FaceEstimator = lambda **kw: _FakeFaceEstimator(scenario)
    app_ui.PoseEstimator.REQUIRED_LANDMARKS = orig[0].REQUIRED_LANDMARKS
    app_ui.FaceEstimator.LEFT_EYE_EAR_INDICES = orig[1].LEFT_EYE_EAR_INDICES
    app_ui.FaceEstimator.RIGHT_EYE_EAR_INDICES = orig[1].RIGHT_EYE_EAR_INDICES

    def restaurar():
        app_ui.PoseEstimator, app_ui.FaceEstimator = orig

    alerts, q = [], Queue(maxsize=1)
    thread = app_ui.InferenceThread(
        video_thread=_FakeVideoThread(), thresholds=th, state_queue=q,
        on_alert=alerts.append, video_feed=_Feed(), log_metrics_every_n=5,
        start_mode=start_mode)
    return thread, q, alerts, tmp_path / "modes.db", restaurar


def _wait(pred, timeout=5.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _sessions(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT id, ended_at, notes FROM sessions "
                           "ORDER BY started_at").fetchall()
    finally:
        con.close()


def _metrics_rows(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM metrics_log").fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def forward_head():
    from tests.test_integration_pipeline import FORWARD_HEAD
    return FORWARD_HEAD


@pytest.fixture
def neutral():
    from tests.test_integration_pipeline import NEUTRAL
    return NEUTRAL


class TestAppModes:

    def test_app_opens_without_monitoring(self, tmp_path, forward_head):
        """
        Al abrir, la cámara guía el encuadre pero NO vigila: con una postura
        que en seguimiento daría alerta, no debe haber alertas, ni sesión, ni
        métricas en la bitácora.
        """
        thread, q, alerts, db, restaurar = _pipeline(tmp_path, forward_head)
        try:
            thread.start()
            time.sleep(1.0)
            assert thread.mode == "preview"
            assert alerts == []
            assert _sessions(db) == []
            assert _metrics_rows(db) == 0
        finally:
            thread.stop(); thread.join(timeout=5.0); restaurar()

    def test_start_is_refused_before_calibrating(self, tmp_path, neutral):
        thread, q, alerts, db, restaurar = _pipeline(tmp_path, neutral)
        try:
            thread.start()
            assert not thread.can_start
            thread.request_start()
            time.sleep(0.3)
            assert thread.mode == "preview"
        finally:
            thread.stop(); thread.join(timeout=5.0); restaurar()

    def test_calibrate_then_start_monitoring(self, tmp_path, neutral):
        thread, q, alerts, db, restaurar = _pipeline(tmp_path, neutral)
        try:
            thread.start()
            thread.request_calibration()
            assert _wait(lambda: thread.mode == "ready"), thread.mode
            assert thread.can_start

            thread.request_start()
            assert _wait(lambda: thread.mode == "monitoring")
            assert _wait(lambda: _metrics_rows(db) > 0)

            ses = _sessions(db)
            assert len(ses) == 1
            assert "θc neutro" in ses[0][2], "la sesión debe guardar la calibración"
        finally:
            thread.stop(); thread.join(timeout=5.0); restaurar()

    def test_state_carries_mode_and_calibration(self, tmp_path, neutral):
        thread, q, alerts, db, restaurar = _pipeline(tmp_path, neutral)
        try:
            thread.start()
            thread.request_calibration()
            assert _wait(lambda: thread.mode == "ready")
            st = None
            deadline = time.perf_counter() + 2
            while st is None and time.perf_counter() < deadline:
                if not q.empty():
                    st = q.get()
            assert st.mode == "ready"
            assert st.calibration["phase"] == "done"
            assert st.calibration["checks"]["ok"] is True
        finally:
            thread.stop(); thread.join(timeout=5.0); restaurar()

    def test_recalibrating_closes_the_session(self, tmp_path, neutral):
        """Una sesión = una calibración + un seguimiento (un participante)."""
        thread, q, alerts, db, restaurar = _pipeline(tmp_path, neutral)
        try:
            thread.start()
            thread.request_calibration()
            assert _wait(lambda: thread.mode == "ready")
            thread.request_start()
            assert _wait(lambda: thread.mode == "monitoring")

            thread.request_calibration()
            assert _wait(lambda: thread.mode == "ready")
            ses = _sessions(db)
            assert len(ses) == 1 and ses[0][1] is not None, "debe cerrarse"

            thread.request_start()
            assert _wait(lambda: len(_sessions(db)) == 2)
        finally:
            thread.stop(); thread.join(timeout=5.0); restaurar()

    def test_pause_and_resume_keep_the_calibration(self, tmp_path, neutral):
        thread, q, alerts, db, restaurar = _pipeline(tmp_path, neutral)
        try:
            thread.start()
            thread.request_calibration()
            assert _wait(lambda: thread.mode == "ready")
            thread.request_start()
            assert _wait(lambda: thread.mode == "monitoring")
            base = thread._fsm.cervical_baseline

            thread.set_paused(True)
            thread.set_paused(False)
            time.sleep(0.2)
            assert thread._fsm.cervical_baseline == base
            assert not thread._fsm.needs_calibration
        finally:
            thread.stop(); thread.join(timeout=5.0); restaurar()

    def test_recalibrate_while_paused_resumes_the_camera(self, tmp_path, neutral):
        thread, q, alerts, db, restaurar = _pipeline(tmp_path, neutral)
        try:
            thread.start()
            thread.request_calibration()
            assert _wait(lambda: thread.mode == "ready")
            thread.request_start()
            assert _wait(lambda: thread.mode == "monitoring")

            thread.set_paused(True)
            thread.request_calibration()
            assert _wait(lambda: thread.mode == "ready")
            assert not thread.is_paused
        finally:
            thread.stop(); thread.join(timeout=5.0); restaurar()

    def test_monitoring_mode_keeps_working_as_before(self, tmp_path, forward_head):
        """start_mode='monitoring' (pruebas y herramientas) vigila de inmediato."""
        import src.ui.app_ui as app_ui
        from tests.test_integration_pipeline import (
            _FakeFaceEstimator, _FakePoseEstimator, _FakeVideoThread, _thresholds)
        orig = app_ui.PoseEstimator, app_ui.FaceEstimator
        app_ui.PoseEstimator = lambda **kw: _FakePoseEstimator(forward_head)
        app_ui.FaceEstimator = lambda **kw: _FakeFaceEstimator(forward_head)
        app_ui.PoseEstimator.REQUIRED_LANDMARKS = orig[0].REQUIRED_LANDMARKS
        app_ui.FaceEstimator.LEFT_EYE_EAR_INDICES = orig[1].LEFT_EYE_EAR_INDICES
        app_ui.FaceEstimator.RIGHT_EYE_EAR_INDICES = orig[1].RIGHT_EYE_EAR_INDICES

        class _Feed:
            def encode(self, frame, fps=0.0):
                return "b64"

        alerts = []
        thread = app_ui.InferenceThread(
            video_thread=_FakeVideoThread(),
            thresholds=_thresholds(tmp_path / "m.db"),
            state_queue=Queue(maxsize=1), on_alert=alerts.append,
            video_feed=_Feed())
        try:
            thread.start()
            assert _wait(lambda: any(a.alert_type == AlertType.CERVICAL_ANGLE
                                     for a in alerts))
            assert thread.mode == "monitoring"
        finally:
            thread.stop(); thread.join(timeout=5.0)
            app_ui.PoseEstimator, app_ui.FaceEstimator = orig

    def test_invalid_start_mode_is_rejected(self, tmp_path, neutral):
        with pytest.raises(ValueError):
            import src.ui.app_ui as app_ui
            from tests.test_integration_pipeline import _thresholds
            app_ui.InferenceThread(video_thread=None,
                                   thresholds=_thresholds(tmp_path / "x.db"),
                                   state_queue=Queue(), on_alert=None,
                                   video_feed=None, start_mode="inventado")


# ---------------------------------------------------------------------------
# Bandeja y tarjeta de calibración
# ---------------------------------------------------------------------------

class TestTrayPauseAvailability:

    def test_pause_is_ignored_when_not_monitoring(self):
        from src.ui.tray_icon import TrayIcon
        llamadas = []
        tray = TrayIcon(on_toggle_pause=llamadas.append,
                        can_pause=lambda: False)
        tray._handle_toggle_pause()
        assert llamadas == []
        assert tray.is_paused is False
        assert tray.pause_enabled is False

    def test_pause_works_while_monitoring(self):
        from src.ui.tray_icon import TrayIcon
        llamadas = []
        tray = TrayIcon(on_toggle_pause=llamadas.append,
                        can_pause=lambda: True)
        tray._handle_toggle_pause()
        assert llamadas == [True]

    def test_refresh_menu_without_icon_is_harmless(self):
        from src.ui.tray_icon import TrayIcon
        TrayIcon().refresh_menu()


class TestCalibrationView:

    def _view(self):
        from src.ui.calibration_view import CalibrationView
        v = CalibrationView(0.50, 0.70)
        v.build()
        return v

    def _calib(self, phase, **over):
        base = {"phase": phase, "progress": 0.4, "countdown_remaining": 2.0,
                "message": "msg", "result": None,
                "checks": {"face": True, "body": True, "distance_ok": True,
                           "distance_m": 0.6, "ok": True}}
        base.update(over)
        return base

    @pytest.mark.parametrize("phase", ["idle", "countdown", "collecting",
                                       "done", "failed"])
    def test_every_phase_renders(self, phase):
        self._view().update(self._calib(phase), mode="preview")

    def test_hidden_while_monitoring(self):
        v = self._view()
        v.update(self._calib("done"), mode="monitoring")
        assert v._card.visible is False

    def test_result_is_shown_when_done(self):
        v = self._view()
        v.update(self._calib("done", result={"cervical_baseline": 40.6,
                                             "ear_threshold": 0.143,
                                             "distance_m": 0.62}),
                 mode="ready")
        assert v._result.visible
        assert len(v._result.controls) == 3

    def test_failed_check_is_marked(self):
        from src.ui.calibration_view import _BAD
        v = self._view()
        v.update(self._calib("idle", checks={"face": False, "body": True,
                                             "distance_ok": True,
                                             "distance_m": 0.6, "ok": False}),
                 mode="preview")
        assert v._chk_face._icon.color == _BAD


# ---------------------------------------------------------------------------
# Mejoras detectadas al revisar la interfaz en vivo
# ---------------------------------------------------------------------------

class TestLiveUiFixes:

    def test_restart_reason_stays_visible_while_measuring_again(self):
        """
        El motivo del reinicio solo se veía durante los 3 s de cuenta atrás:
        en la prueba en vivo la barra retrocedió de 88% a 57% sin explicación.
        """
        fsm = make_fsm()
        calib = make_calib(fsm)
        calib.start(fsm._clock.t)
        run(calib, fsm, 2.0)
        run(calib, fsm, 1.0, distance=0.90)        # reinicio por distancia
        st = run(calib, fsm, 1.2)                  # vuelve a medir
        assert st.phase == COLLECTING
        assert "Se reinició" in st.message and "lejos" in st.message

    def test_restart_reason_is_cleared_by_a_new_calibration(self):
        fsm = make_fsm()
        calib = make_calib(fsm)
        calib.start(fsm._clock.t)
        run(calib, fsm, 2.0)
        run(calib, fsm, 1.0, distance=0.90)
        calib.start(fsm._clock.t)
        st = run(calib, fsm, 1.2)
        assert "Se reinició" not in st.message

    def test_video_shows_pause_instead_of_a_frozen_frame(self):
        from src.ui.video_feed import VideoFeed
        vf = VideoFeed(width=320, height=240)
        vf.build()
        vf.set_encoded("frame-1")
        assert vf._image.visible and not vf._placeholder.visible

        vf.show_paused()
        assert vf._placeholder.visible and not vf._image.visible
        assert "pausa" in vf._placeholder_text.value

        vf.set_encoded("frame-2")                  # al reanudar
        assert vf._image.visible and not vf._placeholder.visible

    def test_video_image_hidden_until_first_frame(self):
        """Un ft.Image sin src se pintaba como un recuadro rojo de error."""
        from src.ui.video_feed import VideoFeed
        vf = VideoFeed(width=320, height=240)
        vf.build()
        assert vf._image.visible is False
        assert vf._placeholder.visible is not False

    def test_ear_is_grey_before_calibration(self):
        """Antes de calibrar, el EAR salía en rojo contra el 0.21 fijo."""
        import flet as ft
        from src.ui.telemetry_panel import TelemetryPanel
        panel = TelemetryPanel({"fatigue": {"ear_threshold": 0.21}})
        panel.build()
        m = SensorMetrics(ear_avg=0.183)
        panel.update(m, fps=20, timer_status={"ear_calibrating": True,
                                              "app_mode": "preview"})
        assert panel._row_ear._value_text.color == ft.colors.WHITE54
        assert "sin calibrar" in panel._row_ear._label_text.value

    def test_panel_says_calibrating_only_while_calibrating(self):
        from src.ui.telemetry_panel import TelemetryPanel
        panel = TelemetryPanel({})
        panel.build()
        m = SensorMetrics()
        st = {"cervical_mode": "reference", "posture_calibrating": True,
              "posture_calibration_progress": 0.4}
        panel.update(m, timer_status={**st, "app_mode": "preview"})
        assert "sin calibrar" in panel._row_cervical._label_text.value
        panel.update(m, timer_status={**st, "app_mode": "calibrating"})
        assert "calibrando 40%" in panel._row_cervical._label_text.value
