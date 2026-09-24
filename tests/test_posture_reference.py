"""
test_posture_reference.py
-------------------------
θc relativo a la postura neutra del usuario, indicador 2D sin profundidad y
emisión de notificaciones de Windows.

Contexto (sesión del 2026-09-24, 4.2 min, usuario sentado recto):
  - θc absoluto: mediana 47.9° → 8 alertas cervicales falsas.
  - Diagnóstico en vivo: θc 3D 52.9°, de ellos 52.5° de componente sagital
    (profundidad z de MediaPipe); solo con x-y, 11.0°. Caderas fuera de
    cuadro (visibilidad 0.000): el origen de z se extrapola.
  - Notificaciones: Windows las aceptaba pero no las mostraba. El AUMID
    "Monitor Postural y Fatiga" tenía espacios y no estaba registrado.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import math
import random
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.fusion.fusion_fsm import (
    AlertType, FusionFSM, PostureBaselineCalibrator, SensorMetrics,
)
from src.vision.geometry import calculate_neck_ratio

FPS = 22.0
SESSION_THETA_MEDIAN = 47.9      # θc absoluto medido con el usuario recto


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_fsm(mode="reference", **post_over):
    post = {
        "cervical_mode": mode,
        "cervical_angle_max_deg": 30.0,
        "cervical_alert_window_sec": 5.0,
        "cervical_deviation_max_deg": 15.0,
        "shoulder_asymmetry_max_deg": 10.0,
        "shoulder_alert_window_sec": 8.0,
        "posture_calibration_sec": 20.0,
        **post_over,
    }
    return FusionFSM({"postural": post, "fatigue": {}},
                     cooldown_sec=30.0, clock=_Clock())


def feed(fsm, thetas, shoulder=0.0, valid=True):
    """Alimenta θc a FPS de la sesión. Devuelve las alertas."""
    alerts = []
    for th in thetas:
        fsm._clock.t += 1.0 / FPS
        alerts += fsm.update(SensorMetrics(
            cervical_angle=th, shoulder_asymmetry=shoulder,
            monotonic=fsm._clock.t, pose_valid=valid, face_detected=False))
    return alerts


def upright_stream(seconds, seed=0):
    """θc de un usuario sentado recto, con la estadística de la sesión."""
    rng = random.Random(seed)
    return [rng.gauss(SESSION_THETA_MEDIAN, 2.5) for _ in range(int(seconds * FPS))]


def cervical(alerts):
    return [a for a in alerts if a.alert_type == AlertType.CERVICAL_ANGLE]


# ---------------------------------------------------------------------------
# El modo absoluto reproduce el fallo; el de referencia lo corrige
# ---------------------------------------------------------------------------

class TestReferenceMode:

    def test_absolute_mode_reproduces_the_false_alerts(self):
        """Documenta el fallo: recto, pero θc absoluto ~48° > 30°."""
        fsm = make_fsm(mode="absolute")
        alerts = feed(fsm, upright_stream(120))
        assert len(cervical(alerts)) >= 3, (
            "si esto deja de fallar, el caso ya no reproduce la sesión real")

    def test_upright_user_gets_no_cervical_alerts(self):
        fsm = make_fsm(mode="reference")
        alerts = feed(fsm, upright_stream(120))
        assert cervical(alerts) == []
        assert fsm.cervical_baseline == pytest.approx(SESSION_THETA_MEDIAN, abs=1.5)

    def test_real_forward_head_still_triggers_the_alert(self):
        """La referencia no debe dejar ciego al sistema."""
        fsm = make_fsm(mode="reference")
        feed(fsm, upright_stream(25))
        base = fsm.cervical_baseline
        alerts = feed(fsm, [base + 20.0] * int(6 * FPS))    # +20° durante 6 s
        assert len(cervical(alerts)) == 1
        assert cervical(alerts)[0].metric_value == pytest.approx(20.0, abs=0.5)
        assert "postura neutra" in cervical(alerts)[0].message

    def test_deviation_below_threshold_does_not_alert(self):
        fsm = make_fsm(mode="reference")
        feed(fsm, upright_stream(25))
        alerts = feed(fsm, [fsm.cervical_baseline + 10.0] * int(10 * FPS))
        assert cervical(alerts) == []

    def test_more_upright_than_reference_never_alerts(self):
        """Una desviación negativa (más erguido que al calibrar) no es riesgo."""
        fsm = make_fsm(mode="reference")
        feed(fsm, upright_stream(25))
        alerts = feed(fsm, [fsm.cervical_baseline - 25.0] * int(10 * FPS))
        assert cervical(alerts) == []

    def test_no_cervical_alert_during_calibration(self):
        """Sin referencia todavía, un θc alto no significa nada."""
        fsm = make_fsm(mode="reference")
        alerts = feed(fsm, [70.0] * int(15 * FPS))
        assert fsm.posture_calibrating
        assert cervical(alerts) == []

    def test_shoulder_alerts_are_not_blocked_by_posture_calibration(self):
        """ΔE es 2D (no usa z): no depende de la referencia postural."""
        fsm = make_fsm(mode="reference")
        alerts = feed(fsm, [48.0] * int(10 * FPS), shoulder=15.0)
        assert any(a.alert_type == AlertType.SHOULDER_ASYMM for a in alerts)

    def test_absolute_is_the_code_default(self):
        fsm = FusionFSM({"postural": {}, "fatigue": {}}, clock=_Clock())
        assert not fsm.posture_calibrating
        assert fsm.cervical_baseline is None

    def test_invalid_mode_falls_back_to_absolute(self):
        fsm = make_fsm(mode="inventado")
        assert not fsm.posture_calibrating


class TestPostureRecalibration:

    def test_recalibrate_all_restarts_posture_and_ear(self):
        fsm = FusionFSM({"postural": {"cervical_mode": "reference"},
                         "fatigue": {"ear_threshold_mode": "adaptive"}},
                        clock=_Clock())
        feed(fsm, upright_stream(25))
        assert not fsm.posture_calibrating
        fsm.recalibrate_all()
        assert fsm.posture_calibrating
        assert fsm.ear_calibrating

    def test_recalibrates_after_long_absence(self):
        fsm = make_fsm(mode="reference",
                       posture_recalibrate_after_absence_sec=30.0)
        feed(fsm, upright_stream(25))
        fsm._clock.t += 40.0                       # 40 s sin pose válida
        feed(fsm, [48.0])
        assert fsm.posture_calibrating

    def test_short_absence_keeps_reference(self):
        fsm = make_fsm(mode="reference")
        feed(fsm, upright_stream(25))
        base = fsm.cervical_baseline
        fsm._clock.t += 5.0
        feed(fsm, [48.0])
        assert fsm.cervical_baseline == base

    def test_resume_after_pause_recalibrates(self):
        fsm = make_fsm(mode="reference")
        feed(fsm, upright_stream(25))
        fsm.reset_all()
        assert fsm.posture_calibrating

    def test_status_exposes_calibration_for_the_panel(self):
        fsm = make_fsm(mode="reference")
        feed(fsm, upright_stream(5))
        st = fsm.get_timers_status()
        assert st["cervical_mode"] == "reference"
        assert st["posture_calibrating"] is True
        assert st["cervical_baseline"] is None
        assert 0.0 < st["posture_calibration_progress"] < 1.0
        assert st["cervical_deviation_max"] == 15.0


class TestPostureBaselineCalibrator:

    def test_baseline_is_the_median(self):
        cal = PostureBaselineCalibrator(calibration_sec=1.0, min_samples=5)
        for i, th in enumerate([47, 48, 49, 48, 90, 48, 47, 49, 48, 48]):
            cal.update(th, now=i * 0.2)
        assert cal.baseline == pytest.approx(48.0)

    def test_unstable_posture_is_rejected_and_retried(self):
        """Calibrar mientras el usuario se mueve no fija ninguna postura."""
        cal = PostureBaselineCalibrator(calibration_sec=1.0, min_samples=8,
                                        max_iqr_deg=15.0)
        for i in range(20):
            cal.update(20.0 if i % 2 else 60.0, now=i * 0.1)
        assert not cal.is_calibrated

        for i in range(20):
            cal.update(48.0, now=3.0 + i * 0.1)
        assert cal.is_calibrated

    def test_needs_both_time_and_samples(self):
        cal = PostureBaselineCalibrator(calibration_sec=20.0, min_samples=60)
        for i in range(100):
            cal.update(48.0, now=i * 0.05)          # 100 muestras en 5 s
        assert not cal.is_calibrated

    def test_deviation(self):
        cal = PostureBaselineCalibrator(calibration_sec=0.0, min_samples=1)
        assert cal.deviation(50.0) is None
        cal.update(48.0, now=0.0)
        assert cal.deviation(60.0) == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# Indicador 2D sin profundidad
# ---------------------------------------------------------------------------

class _LM:
    def __init__(self, x, y, z=0.0):
        self.x, self.y, self.z, self.visibility = x, y, z, 1.0


def _pose(neck_cm=22.0, px_per_cm=3.0, ear_z=0.0, W=640, H=480):
    """Orejas `neck_cm` por encima de los hombros; hombros de 38 cm."""
    cx, cy = W / 2.0, H * 0.75
    lms = [_LM(0.5, 0.5) for _ in range(33)]
    ear_y = cy - neck_cm * px_per_cm
    lms[7] = _LM((cx - 7 * px_per_cm) / W, ear_y / H, ear_z)
    lms[8] = _LM((cx + 7 * px_per_cm) / W, ear_y / H, ear_z)
    lms[11] = _LM((cx - 19 * px_per_cm) / W, cy / H)
    lms[12] = _LM((cx + 19 * px_per_cm) / W, cy / H)
    return lms


class TestNeckRatio:

    def test_value_is_neck_height_over_shoulder_width(self):
        assert calculate_neck_ratio(_pose(neck_cm=22.0)) == pytest.approx(22 / 38, abs=1e-6)

    @pytest.mark.parametrize("px_per_cm", [2.0, 3.0, 5.0])
    def test_invariant_to_distance(self, px_per_cm):
        """Es una razón de dos longitudes de la imagen: no depende de la distancia."""
        assert calculate_neck_ratio(_pose(px_per_cm=px_per_cm)) == pytest.approx(22 / 38, abs=1e-6)

    def test_drops_when_the_head_drops(self):
        assert calculate_neck_ratio(_pose(neck_cm=16.0)) < calculate_neck_ratio(_pose(neck_cm=22.0))

    def test_ignores_depth(self):
        """
        Justo lo que θc no hace: el valor de z (el que MediaPipe extrapola con
        las caderas fuera de cuadro) no altera este indicador.
        """
        assert (calculate_neck_ratio(_pose(ear_z=-0.9))
                == pytest.approx(calculate_neck_ratio(_pose(ear_z=0.0))))

    def test_none_without_shoulder_width(self):
        lms = _pose()
        lms[12] = lms[11]
        assert calculate_neck_ratio(lms) is None


# ---------------------------------------------------------------------------
# Notificaciones de Windows: identidad de la aplicación
# ---------------------------------------------------------------------------

class _FakeKey:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_WRITE = 1
    REG_SZ = 1

    def __init__(self, fail=False):
        self.values = {}
        self.fail = fail

    def CreateKeyEx(self, root, path, reserved, access):
        if self.fail:
            raise PermissionError("denegado")
        return _FakeKey(self, f"{root}\\{path}")

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[(key.path, name)] = value


class TestToastIdentity:

    def test_app_id_is_a_valid_aumid(self):
        """Un AppUserModelID no puede contener espacios (reglas de Windows)."""
        from src.ui.toast_notifier import _APP_ID, aumid_is_valid
        assert aumid_is_valid(_APP_ID)

    def test_previous_app_id_was_invalid(self):
        """El identificador que Windows aceptaba pero no mostraba."""
        from src.ui.toast_notifier import aumid_is_valid
        assert not aumid_is_valid("Monitor Postural y Fatiga")
        assert not aumid_is_valid("")
        assert not aumid_is_valid("x" * 129)

    def test_registration_writes_display_name_and_icon(self):
        from src.ui.toast_notifier import (
            _APP_ID, _APP_DISPLAY_NAME, ensure_aumid_registered)
        reg = _FakeWinreg()
        assert ensure_aumid_registered(winreg_module=reg) is True

        key = f"HKCU\\Software\\Classes\\AppUserModelId\\{_APP_ID}"
        assert reg.values[(key, "DisplayName")] == _APP_DISPLAY_NAME
        assert reg.values[(key, "IconUri")].endswith("icon.png")

    def test_registration_failure_does_not_raise(self):
        """Sin permisos, el monitoreo sigue: solo se pierde el aviso emergente."""
        from src.ui.toast_notifier import ensure_aumid_registered
        assert ensure_aumid_registered(winreg_module=_FakeWinreg(fail=True)) is False

    def test_toast_icon_is_png(self):
        """Los toasts de Windows no admiten .ico como imagen."""
        from src.ui.toast_notifier import _ICON_PATH
        assert _ICON_PATH.suffix == ".png"


# ---------------------------------------------------------------------------
# Recalibración manual desde la bandeja
# ---------------------------------------------------------------------------

class TestManualRecalibration:

    def test_tray_option_calls_back(self):
        from src.ui.tray_icon import TrayIcon
        llamadas = []
        tray = TrayIcon(on_recalibrate=lambda: llamadas.append(1))
        tray._handle_recalibrate()
        assert llamadas == [1]

    def test_recalibration_runs_on_the_inference_thread(self, tmp_path):
        """
        La bandeja llama desde su propio hilo, y el FSM no tiene cerrojos: la
        recalibración debe ejecutarse en el hilo de inferencia, no en el de
        la bandeja.
        """
        import threading
        import time
        from tests.test_background_pipeline import _start_pipeline
        from tests.test_integration_pipeline import NEUTRAL

        thread, q, _, restaurar = _start_pipeline(tmp_path, NEUTRAL)
        hilos = []
        thread._fsm.recalibrate_all = lambda: hilos.append(
            threading.current_thread().name)
        try:
            thread.start()
            thread.recalibrate()
            deadline = time.perf_counter() + 2.0
            while not hilos and time.perf_counter() < deadline:
                time.sleep(0.01)
            assert hilos == ["InferenceThread"]
        finally:
            thread.stop()
            thread.join(timeout=5.0)
            restaurar()

    def test_calibration_hint_guides_the_user(self, tmp_path):
        from tests.test_background_pipeline import _start_pipeline
        from tests.test_integration_pipeline import NEUTRAL

        thread, _, _, restaurar = _start_pipeline(
            tmp_path, NEUTRAL, postural={"cervical_mode": "reference"})
        try:
            hint = ""
            for i in range(40):
                m = SensorMetrics(distance_m=0.60, monotonic=i * 0.05,
                                  pose_detected=True, pose_valid=True)
                hint = thread._setup_hint(m)
            assert "siéntate recto" in hint
        finally:
            restaurar()
