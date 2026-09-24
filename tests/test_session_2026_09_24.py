"""
test_session_2026_09_24.py
--------------------------
Regresiones de los tres fallos observados en la primera sesión de prueba con
cámara real (2026-09-24, 3.3 min, un usuario):

  1. EAR: con los ojos ABIERTOS la mediana del EAR fue 0.200, por debajo del
     umbral fijo 0.21. El sistema clasificó "ojo cerrado" el 64% de las
     muestras y disparó somnolencia cada 30 s con el usuario despierto.
  2. Distancia: cruzó 30 veces el límite de 0.70 m en 129 muestras sin que el
     usuario se moviera, y saltó a 3.51 m en frames sueltos. El aviso de
     encuadre parpadeaba y cada salto reiniciaba los temporizadores de postura.
  3. Modal: tras pulsar "Entendido" quedaba un recuadro vacío que no se podía
     cerrar (cubierto en tests/test_ui_components.py::TestAlertNotifier).

Las secuencias sintéticas de este archivo reproducen las estadísticas medidas
en esa sesión (mediana, dispersión, outliers), no valores inventados.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import random
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.fusion.fusion_fsm import (
    AlertType, EarBaselineCalibrator, FusionFSM, SensorMetrics,
)
from src.vision.smoothing import DebouncedState, MetricSmoother


# ---------------------------------------------------------------------------
# Datos de la sesión real
# ---------------------------------------------------------------------------

SESSION_EAR_MEDIAN = 0.200     # EAR de ojo abierto medido
SESSION_EAR_SPREAD = 0.025     # dispersión aproximada (p10 0.164, p90 0.237)
FPS = 22.0                     # mediana de FPS de la sesión


def open_eye_ear_stream(seconds: float, seed: int = 0, blinks_per_min=17):
    """
    EAR de un usuario DESPIERTO con la estadística de la sesión: ojo abierto
    alrededor de 0.200 y parpadeos normales (~150-300 ms) intercalados.
    """
    rng = random.Random(seed)
    n = int(seconds * FPS)
    blink_every = int(FPS * 60 / blinks_per_min)
    out = []
    for i in range(n):
        if blink_every and i % blink_every < 4:          # parpadeo ~180 ms
            out.append(rng.uniform(0.06, 0.10))
        else:
            out.append(rng.gauss(SESSION_EAR_MEDIAN, SESSION_EAR_SPREAD / 2))
    return out


FAT_BASE = {
    "ear_threshold": 0.21,
    "ear_alert_window_sec": 3.0,
    "blink_min_ms": 100,
    "blink_max_ms": 400,
    "mouth_opening_threshold": 0.45,
    "yawn_alert_window_sec": 3.0,
    "perclos_enabled": True,
    "perclos_threshold": 0.15,
    "perclos_window_sec": 60.0,
}


class _Clock:
    """
    Reloj del FSM sincronizado con el instante de cada frame, como en
    producción (allí ambos son time.perf_counter()).
    """

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_fsm(**fatigue_over):
    fat = {**FAT_BASE, **fatigue_over}
    fsm = FusionFSM(
        thresholds={"postural": {}, "fatigue": fat},
        cooldown_sec=30.0,
        clock=_Clock(),
    )
    return fsm


def feed(fsm, ears, t0=None):
    """Alimenta el FSM a FPS de la sesión. Devuelve (alertas, t_final)."""
    alerts = []
    t = fsm._clock.t if t0 is None else t0
    for ear in ears:
        fsm._clock.t = t
        m = SensorMetrics(ear_avg=ear, monotonic=t, pose_detected=False)
        alerts += fsm.update(m)
        t += 1.0 / FPS
    fsm._clock.t = t
    return alerts, t


# ---------------------------------------------------------------------------
# 1. EAR — el umbral fijo no sirve para este usuario; el adaptativo sí
# ---------------------------------------------------------------------------

class TestFixedThresholdReproducesTheBug:
    """
    Documenta el fallo: con el umbral fijo 0.21, un usuario despierto cuyo
    ojo abierto mide 0.200 recibe alertas de somnolencia.
    """

    def test_awake_user_gets_drowsiness_alerts_with_fixed_threshold(self):
        fsm = make_fsm(ear_threshold_mode="fixed")
        alerts, _ = feed(fsm, open_eye_ear_stream(120))

        assert any(a.alert_type == AlertType.DROWSINESS for a in alerts), (
            "si esto deja de fallar, el caso de regresión ya no reproduce la "
            "sesión real y hay que revisarlo")
        assert fsm.blink_analyzer.perclos > 0.5


class TestAdaptiveThreshold:

    def test_awake_user_gets_no_drowsiness_alerts(self):
        """La misma secuencia, con umbral adaptativo: ninguna alerta falsa."""
        fsm = make_fsm(ear_threshold_mode="adaptive")
        alerts, _ = feed(fsm, open_eye_ear_stream(120))

        drowsy = [a for a in alerts if a.alert_type == AlertType.DROWSINESS]
        fatigue = [a for a in alerts if a.alert_type == AlertType.EYE_FATIGUE]
        assert drowsy == [], f"somnolencia falsa: PERCLOS={drowsy[0].metric_value:.2f}"
        assert fatigue == []
        assert fsm.blink_analyzer.perclos < 0.15

    def test_calibrated_threshold_matches_the_session(self):
        """Mediana 0.200 × ratio 0.65 → umbral ≈ 0.130."""
        fsm = make_fsm(ear_threshold_mode="adaptive")
        feed(fsm, open_eye_ear_stream(30))

        assert not fsm.ear_calibrating
        assert fsm.ear_threshold == pytest.approx(0.130, abs=0.01)

    def test_relaxed_eyes_after_attentive_calibration_stay_open(self):
        """
        REGRESIÓN — sesión 2 del 2026-09-24. La calibración captó los ojos
        atentos (0.257) y al trabajar se relajaron un 9% (0.236). Con ratio
        0.70 el PERCLOS rozó el límite; con 0.65 debe quedar holgado.
        """
        fsm = make_fsm(ear_threshold_mode="adaptive")
        rng = random.Random(7)
        feed(fsm, [rng.gauss(0.257, 0.012) for _ in range(int(25 * FPS))])
        alerts, _ = feed(fsm, [rng.gauss(0.236, 0.02)
                               for _ in range(int(120 * FPS))])

        assert not any(a.alert_type == AlertType.DROWSINESS for a in alerts)
        assert fsm.blink_analyzer.perclos < 0.08

    def test_real_eye_closure_is_still_detected(self):
        """
        El umbral adaptativo no debe volver el sistema ciego: un cierre real
        y sostenido tiene que seguir disparando la alerta de fatiga ocular.
        """
        fsm = make_fsm(ear_threshold_mode="adaptive")
        _, t = feed(fsm, open_eye_ear_stream(30))
        cierre = [0.07] * int(4.0 * FPS)             # 4 s con el ojo cerrado
        alerts, _ = feed(fsm, cierre, t0=t)

        assert any(a.alert_type == AlertType.EYE_FATIGUE for a in alerts)

    def test_no_fatigue_evaluation_during_calibration(self):
        """
        Mientras calibra no hay umbral válido para el usuario: ni PERCLOS ni
        fatiga ocular deben evaluarse, aunque el EAR sea muy bajo.
        """
        fsm = make_fsm(ear_threshold_mode="adaptive", ear_calibration_sec=20.0)
        alerts, _ = feed(fsm, [0.05] * int(10 * FPS))   # 10 s, calibrando

        assert fsm.ear_calibrating
        assert fsm.ear_threshold is None
        assert not any(a.alert_type in (AlertType.EYE_FATIGUE, AlertType.DROWSINESS)
                       for a in alerts)

    def test_fixed_is_the_code_default(self):
        """
        Sin la clave en el JSON se conserva el comportamiento anterior: una
        configuración antigua no cambia de criterio en silencio.
        """
        fsm = make_fsm()
        assert fsm.ear_threshold == 0.21
        assert not fsm.ear_calibrating

    def test_recalibrates_after_long_absence(self):
        """Otra persona puede haberse sentado: su ojo necesita su umbral."""
        fsm = make_fsm(ear_threshold_mode="adaptive",
                       ear_recalibrate_after_absence_sec=30.0)
        _, t = feed(fsm, open_eye_ear_stream(30))
        assert not fsm.ear_calibrating

        # 40 s sin cara
        fsm._clock.t = t + 1
        fsm.update(SensorMetrics(face_detected=False, monotonic=t + 1,
                                 pose_detected=False))
        fsm._clock.t = t + 41
        fsm.update(SensorMetrics(ear_avg=0.30, monotonic=t + 41,
                                 pose_detected=False))

        assert fsm.ear_calibrating, "debe recalibrar tras 40 s de ausencia"

    def test_short_absence_keeps_calibration(self):
        fsm = make_fsm(ear_threshold_mode="adaptive")
        _, t = feed(fsm, open_eye_ear_stream(30))
        thr = fsm.ear_threshold

        fsm.update(SensorMetrics(ear_avg=0.20, monotonic=t + 5,
                                 pose_detected=False))
        assert fsm.ear_threshold == thr

    def test_resume_after_pause_recalibrates(self):
        fsm = make_fsm(ear_threshold_mode="adaptive")
        feed(fsm, open_eye_ear_stream(30))
        fsm.reset_all()
        assert fsm.ear_calibrating

    def test_status_exposes_calibration_for_the_panel(self):
        fsm = make_fsm(ear_threshold_mode="adaptive")
        feed(fsm, open_eye_ear_stream(5))
        st = fsm.get_timers_status()
        assert st["ear_calibrating"] is True
        assert st["ear_threshold"] is None
        assert 0.0 < st["ear_calibration_progress"] < 1.0

    def test_invalid_mode_falls_back_to_fixed(self):
        fsm = make_fsm(ear_threshold_mode="inventado")
        assert fsm.ear_threshold == 0.21


class TestEarBaselineCalibrator:

    def test_baseline_is_the_median_so_blinks_do_not_bias_it(self):
        cal = EarBaselineCalibrator(calibration_sec=1.0, min_samples=10)
        # 90% ojo abierto a 0.30, 10% parpadeos a 0.05
        samples = [0.30] * 45 + [0.05] * 5
        for i, e in enumerate(samples):
            cal.update(e, now=i * 0.05)
        assert cal.baseline == pytest.approx(0.30)
        assert cal.threshold == pytest.approx(0.195)     # 0.65 × 0.30

    def test_threshold_is_clamped(self):
        cal = EarBaselineCalibrator(calibration_sec=0.0, min_samples=1,
                                    ratio=0.70, bounds=(0.10, 0.25))
        cal.update(0.44, now=0.0)                  # 0.70 × 0.44 = 0.308
        assert cal.threshold == 0.25

    def test_implausible_baseline_is_rejected_and_retried(self):
        """
        Una línea base de 0.05 (ojos entornados durante la calibración) daría
        un umbral inútil. Se descarta y se sigue calibrando.
        """
        cal = EarBaselineCalibrator(calibration_sec=1.0, min_samples=5)
        for i in range(30):
            cal.update(0.05, now=i * 0.1)
        assert not cal.is_calibrated

        for i in range(30):
            cal.update(0.28, now=3.0 + i * 0.1)
        assert cal.is_calibrated

    def test_needs_both_time_and_samples(self):
        cal = EarBaselineCalibrator(calibration_sec=20.0, min_samples=60)
        for i in range(100):                       # 100 muestras en 5 s
            cal.update(0.25, now=i * 0.05)
        assert not cal.is_calibrated, "no debe calibrar antes de 20 s"

    def test_update_reports_completion_once(self):
        cal = EarBaselineCalibrator(calibration_sec=0.0, min_samples=1)
        assert cal.update(0.25, now=0.0) is True
        assert cal.update(0.25, now=1.0) is False

    @pytest.mark.parametrize("ratio", [0.0, 1.0, 1.5])
    def test_rejects_invalid_ratio(self, ratio):
        with pytest.raises(ValueError):
            EarBaselineCalibrator(ratio=ratio)


# ---------------------------------------------------------------------------
# 2. Distancia — filtro, retardo de confirmación e histéresis
# ---------------------------------------------------------------------------

class TestDistanceSmoothing:

    def test_single_frame_outlier_is_rejected(self):
        """
        La sesión registró saltos a 3.51 m en frames sueltos con el usuario
        quieto a ~0.64 m. La mediana del filtro debe descartarlos.
        """
        s = MetricSmoother(median_window=5, alpha=0.35)
        out = [s.update(d) for d in [0.64, 0.63, 0.65, 3.51, 0.64, 0.64, 0.63]]
        assert max(out) < 0.70, f"el outlier atravesó el filtro: {max(out):.2f}"


class TestDebouncedState:

    def test_problem_must_persist_before_showing(self):
        d = DebouncedState(hold_sec=1.5, clear_sec=1.0)
        assert d.update("too_far", 0.0) == ""
        assert d.update("too_far", 1.0) == ""
        assert d.update("too_far", 1.6) == "too_far"

    def test_flicker_never_shows_the_warning(self):
        """
        El patrón de la sesión: la distancia alterna a un lado y otro del
        límite. Si nunca se mantiene 1.5 s seguidos, el aviso no aparece.
        """
        d = DebouncedState(hold_sec=1.5, clear_sec=1.0)
        shown = [d.update("too_far" if i % 2 else "", i * 0.4)
                 for i in range(40)]
        assert set(shown) == {""}

    def test_warning_clears_after_clear_delay(self):
        d = DebouncedState(hold_sec=0.5, clear_sec=1.0)
        d.update("too_far", 0.0)
        d.update("too_far", 0.6)
        assert d.value == "too_far"
        assert d.update("", 1.0) == "too_far"
        assert d.update("", 2.1) == ""

    def test_interrupted_candidate_restarts_the_count(self):
        d = DebouncedState(hold_sec=1.0, clear_sec=1.0)
        d.update("too_far", 0.0)
        d.update("too_close", 0.8)          # otra categoría: reinicia
        assert d.update("too_close", 1.5) == ""
        assert d.update("too_close", 1.9) == "too_close"

    def test_reset_returns_to_neutral(self):
        d = DebouncedState(hold_sec=0.0)
        d.update("too_far", 0.0)
        d.reset()
        assert d.value == ""

    def test_rejects_negative_times(self):
        with pytest.raises(ValueError):
            DebouncedState(hold_sec=-1.0)


class TestSetupHintInPipeline:
    """_setup_hint() del InferenceThread real, con histéresis y retardo."""

    @pytest.fixture
    def thread(self, tmp_path):
        from tests.test_background_pipeline import _start_pipeline
        from tests.test_integration_pipeline import NEUTRAL
        t, _, _, restaurar = _start_pipeline(
            tmp_path, NEUTRAL,
            camera={"min_distance_m": 0.50, "max_distance_m": 0.70,
                    "distance_hysteresis_m": 0.03,
                    "setup_hint_hold_sec": 1.0, "setup_hint_clear_sec": 1.0})
        yield t
        restaurar()

    @staticmethod
    def _m(distance, t):
        return SensorMetrics(distance_m=distance, monotonic=t,
                             pose_detected=True, pose_valid=True)

    def test_hovering_around_the_limit_does_not_flicker(self, thread):
        """Reproduce los 30 cruces de 0.70 m de la sesión."""
        rng = random.Random(3)
        hints = [thread._setup_hint(self._m(rng.uniform(0.66, 0.74), i * 0.05))
                 for i in range(200)]
        cambios = sum(1 for a, b in zip(hints, hints[1:]) if a != b)
        assert cambios <= 2, f"el aviso cambió {cambios} veces en 10 s"

    def test_hysteresis_keeps_warning_until_clearly_back(self, thread):
        for i in range(30):                              # 1.5 s a 0.78 m
            thread._setup_hint(self._m(0.78, i * 0.05))
        assert "lejos" in thread._setup_hint(self._m(0.78, 1.5))

        # 0.69 m: dentro del rango, pero dentro de la banda de histéresis
        # (0.70 - 0.03 = 0.67): el aviso se mantiene
        for i in range(40):
            hint = thread._setup_hint(self._m(0.69, 2.0 + i * 0.05))
        assert "lejos" in hint

        # 0.64 m: claramente dentro → se retira tras el retardo
        for i in range(40):
            hint = thread._setup_hint(self._m(0.64, 5.0 + i * 0.05))
        assert hint == ""
