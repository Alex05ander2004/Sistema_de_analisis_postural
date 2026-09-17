"""
Tests de la máquina de estados de fusión multimodal (fusion_fsm.py).

Reloj falso
-----------
Todos los tests de ventanas temporales usan `_FakeClock` en lugar de
`time.sleep`. Eso permite (a) probar las ventanas reales de 3 s y 5 s en
microsegundos, y (b) que los tests sean deterministas: la versión anterior
comprimía las ventanas a 0.05-0.1 s y dependía de que el planificador del SO
respetara un `sleep`, de modo que verificaba una configuración que nunca se
usa en producción.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fusion.fusion_fsm import (
    AlertEvent,
    AlertType,
    BlinkAnalyzer,
    ConditionTimer,
    FusionFSM,
    SensorMetrics,
)


_TEST_THRESHOLDS = {
    "postural": {
        "cervical_angle_max_deg": 30.0,
        "cervical_alert_window_sec": 5.0,
        "shoulder_asymmetry_max_deg": 10.0,
        "shoulder_alert_window_sec": 8.0,
    },
    "fatigue": {
        "ear_threshold": 0.21,
        "ear_alert_window_sec": 3.0,
        "blink_min_ms": 100,
        "blink_max_ms": 400,
        "mouth_opening_threshold": 0.45,
        "yawn_alert_window_sec": 3.0,
        "perclos_enabled": False,      # se activa solo en TestPerclos
        "perclos_threshold": 0.15,
        "perclos_window_sec": 60.0,
    },
}


class _FakeClock:
    """Reloj monótono controlado por el test."""

    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += seconds
        return self.t


# ---------------------------------------------------------------------------
# ConditionTimer
# ---------------------------------------------------------------------------

class TestConditionTimer:

    def test_not_confirmed_before_window(self):
        clock = _FakeClock()
        timer = ConditionTimer(window_sec=5.0, clock=clock)
        assert timer.update(True) is False
        clock.advance(4.9)
        assert timer.update(True) is False

    def test_confirmed_after_window(self):
        clock = _FakeClock()
        timer = ConditionTimer(window_sec=5.0, clock=clock)
        timer.update(True)
        clock.advance(5.0)
        assert timer.update(True) is True

    def test_interruption_resets(self):
        clock = _FakeClock()
        timer = ConditionTimer(window_sec=5.0, clock=clock)
        timer.update(True)
        clock.advance(4.0)
        timer.update(True)
        clock.advance(0.1)
        timer.update(False)          # sin periodo de gracia -> reinicia
        assert timer.elapsed_sec == 0.0
        clock.advance(1.0)
        timer.update(True)
        assert timer.elapsed_sec == 0.0

    def test_grace_period_survives_short_interruption(self):
        """
        REGRESIÓN — el filtro de parpadeo era código muerto.

        La implementación anterior exigía `blink_min_ms <= gap <= blink_max_ms`
        midiendo `gap` desde el primer frame inactivo. Ese `gap` vale 0 en ese
        primer frame y 0 < blink_min_ms (100 ms), así que la condición nunca
        se cumplía: el temporizador se reiniciaba en el primer frame inactivo
        y el filtro no protegía nada. Una microapertura del párpado de 50 ms
        durante un cierre somnoliento borraba el conteo y la alerta de 3 s no
        llegaba a confirmarse nunca.
        """
        clock = _FakeClock()
        timer = ConditionTimer(window_sec=3.0, grace_ms=400, clock=clock)

        timer.update(True)
        clock.advance(2.0)
        timer.update(True)

        clock.advance(0.05)
        timer.update(False)          # microapertura de 50 ms
        clock.advance(0.05)
        timer.update(False)
        clock.advance(0.05)
        timer.update(True)           # el párpado vuelve a caer

        assert timer.elapsed_sec > 2.0, (
            "Una interrupción de 150 ms (< 400 ms) no debe borrar la racha"
        )

        clock.advance(1.0)
        assert timer.update(True) is True

    def test_grace_period_does_not_chain_normal_blinks(self):
        """
        El periodo de gracia no puede convertir el parpadeo normal en fatiga.

        Una persona despierta parpadea cada 3-5 s: el hueco con los ojos
        abiertos es muy superior a los 400 ms de gracia, así que cada
        parpadeo reinicia la racha y nunca se acumulan 3 s de cierre.
        """
        clock = _FakeClock()
        timer = ConditionTimer(window_sec=3.0, grace_ms=400, clock=clock)

        for _ in range(20):
            timer.update(True)        # ojo cerrado
            clock.advance(0.2)        # parpadeo de 200 ms
            timer.update(True)
            clock.advance(0.01)
            timer.update(False)       # ojo abierto
            clock.advance(3.0)        # 3 s con los ojos abiertos
            assert timer.update(False) is False

        assert timer.elapsed_sec == 0.0

    def test_inactive_without_streak_is_noop(self):
        clock = _FakeClock()
        timer = ConditionTimer(window_sec=3.0, grace_ms=400, clock=clock)
        for _ in range(5):
            assert timer.update(False) is False
            clock.advance(1.0)
        assert timer.elapsed_sec == 0.0

    def test_elapsed_sec_zero_when_no_streak(self):
        assert ConditionTimer(window_sec=5.0).elapsed_sec == 0.0

    def test_reset_clears_state(self):
        clock = _FakeClock()
        timer = ConditionTimer(window_sec=1.0, clock=clock)
        timer.update(True)
        clock.advance(1.5)
        assert timer.update(True) is True
        timer.reset()
        assert timer.elapsed_sec == 0.0
        assert timer.update(True) is False


# ---------------------------------------------------------------------------
# BlinkAnalyzer
# ---------------------------------------------------------------------------

class TestBlinkAnalyzer:

    def _blink(self, an, clock, closed_ms, open_sec=3.0, ear_open=0.32,
               ear_closed=0.10):
        an.update(ear_closed, clock.t)
        clock.advance(closed_ms / 1000.0)
        an.update(ear_closed, clock.t)
        an.update(ear_open, clock.t)
        clock.advance(open_sec)
        an.update(ear_open, clock.t)

    def test_counts_physiological_blinks(self):
        clock = _FakeClock()
        an = BlinkAnalyzer(window_sec=600.0, clock=clock)
        for _ in range(10):
            self._blink(an, clock, closed_ms=200)
        assert an.blink_rate_per_min > 0
        # 10 parpadeos en ~32 s -> ~19/min
        assert 15 <= an.blink_rate_per_min <= 23, an.blink_rate_per_min

    def test_ignores_closures_longer_than_a_blink(self):
        """Un cierre de 2 s no es un parpadeo: no debe contar en la tasa."""
        clock = _FakeClock()
        an = BlinkAnalyzer(window_sec=600.0, clock=clock)
        for _ in range(5):
            self._blink(an, clock, closed_ms=2000)
        assert an.blink_rate_per_min == 0.0

    def test_ignores_closures_shorter_than_a_blink(self):
        clock = _FakeClock()
        an = BlinkAnalyzer(window_sec=600.0, clock=clock)
        for _ in range(5):
            self._blink(an, clock, closed_ms=30)
        assert an.blink_rate_per_min == 0.0

    def test_perclos_zero_with_eyes_open(self):
        clock = _FakeClock()
        an = BlinkAnalyzer(window_sec=60.0, clock=clock)
        for _ in range(100):
            an.update(0.32, clock.t)
            clock.advance(0.05)
        assert an.perclos == 0.0

    def test_perclos_reflects_closed_fraction(self):
        """PERCLOS ≈ fracción de muestras con el ojo cerrado."""
        clock = _FakeClock()
        an = BlinkAnalyzer(window_sec=60.0, clock=clock)
        for i in range(100):
            an.update(0.10 if i % 4 == 0 else 0.32, clock.t)
            clock.advance(0.05)
        assert abs(an.perclos - 0.25) < 0.02, an.perclos

    def test_window_slides(self):
        """Las muestras viejas salen de la ventana móvil."""
        clock = _FakeClock()
        an = BlinkAnalyzer(window_sec=10.0, clock=clock)
        for _ in range(50):
            an.update(0.10, clock.t)     # ojos cerrados
            clock.advance(0.1)
        assert an.perclos > 0.9
        for _ in range(150):
            an.update(0.32, clock.t)     # ojos abiertos 15 s
            clock.advance(0.1)
        assert an.perclos == 0.0

    def test_reset_clears(self):
        clock = _FakeClock()
        an = BlinkAnalyzer(window_sec=60.0, clock=clock)
        for _ in range(20):
            an.update(0.10, clock.t)
            clock.advance(0.05)
        an.reset()
        assert an.perclos == 0.0
        assert an.blink_rate_per_min == 0.0


# ---------------------------------------------------------------------------
# FusionFSM
# ---------------------------------------------------------------------------

class TestFusionFSM:

    def _fsm(self, on_alert=None, cooldown=0.0, thresholds=None, clock=None):
        clock = clock or _FakeClock()
        fsm = FusionFSM(
            thresholds=thresholds or _TEST_THRESHOLDS,
            on_alert=on_alert,
            cooldown_sec=cooldown,
            clock=clock,
        )
        return fsm, clock

    @staticmethod
    def _metrics(clock, **kw) -> SensorMetrics:
        base = dict(cervical_angle=5.0, shoulder_asymmetry=3.0,
                    ear_avg=0.32, mouth_opening=0.1,
                    face_detected=True, pose_detected=True, pose_valid=True)
        base.update(kw)
        return SensorMetrics(monotonic=clock.t, **base)

    # ------ flujo básico ------

    def test_no_alert_within_normal_range(self):
        fsm, clock = self._fsm()
        for _ in range(50):
            assert fsm.update(self._metrics(clock)) == []
            clock.advance(0.05)

    def test_no_alert_before_window(self):
        fsm, clock = self._fsm()
        for _ in range(90):                # 4.5 s < ventana de 5 s
            assert fsm.update(self._metrics(clock, cervical_angle=40.0)) == []
            clock.advance(0.05)

    def test_cervical_alert_after_full_window(self):
        cb = MagicMock()
        fsm, clock = self._fsm(on_alert=cb)
        alerts = []
        for _ in range(120):               # 6 s > ventana de 5 s
            alerts += fsm.update(self._metrics(clock, cervical_angle=40.0))
            clock.advance(0.05)
        assert len(alerts) >= 1
        assert alerts[0].alert_type == AlertType.CERVICAL_ANGLE
        assert alerts[0].metric_value == 40.0
        assert alerts[0].threshold == 30.0
        cb.assert_called()

    def test_shoulder_window_is_independent_of_cervical(self):
        """
        El experto pidió que la ventana de hombros fuera distinta de la
        cervical (Bloque B2). Con ΔE fuera de rango y θc dentro, a los 6 s
        debe haber disparado la cervical (5 s) pero no aún la de hombros (8 s).
        """
        fsm, clock = self._fsm()
        alerts = []
        for _ in range(120):               # 6 s
            alerts += fsm.update(self._metrics(clock, shoulder_asymmetry=20.0))
            clock.advance(0.05)
        assert alerts == [], "A los 6 s la ventana de hombros (8 s) no debe cerrar"

        for _ in range(60):                # hasta 9 s
            alerts += fsm.update(self._metrics(clock, shoulder_asymmetry=20.0))
            clock.advance(0.05)
        assert any(a.alert_type == AlertType.SHOULDER_ASYMM for a in alerts)

    def test_eye_fatigue_alert_after_window(self):
        fsm, clock = self._fsm()
        alerts = []
        for _ in range(80):                # 4 s > ventana de 3 s
            alerts += fsm.update(self._metrics(clock, ear_avg=0.15))
            clock.advance(0.05)
        assert any(a.alert_type == AlertType.EYE_FATIGUE for a in alerts)

    def test_yawn_alert_after_window(self):
        fsm, clock = self._fsm()
        alerts = []
        for _ in range(80):
            alerts += fsm.update(self._metrics(clock, mouth_opening=0.60))
            clock.advance(0.05)
        assert any(a.alert_type == AlertType.YAWN for a in alerts)

    def test_physiological_blink_never_alerts(self):
        """
        El test más crítico del sistema: 60 s de parpadeo normal (200 ms
        cerrado cada 3 s) no deben producir ninguna alerta de fatiga.
        """
        cb = MagicMock()
        fsm, clock = self._fsm(on_alert=cb)
        for _ in range(20):
            fsm.update(self._metrics(clock, ear_avg=0.10))   # cierre
            clock.advance(0.2)
            fsm.update(self._metrics(clock, ear_avg=0.10))
            clock.advance(0.01)
            for _ in range(60):                              # 3 s abierto
                fsm.update(self._metrics(clock, ear_avg=0.32))
                clock.advance(0.05)
        cb.assert_not_called()

    # ------ pérdida de detección ------

    def test_no_alert_when_nothing_detected(self):
        fsm, clock = self._fsm()
        for _ in range(200):
            assert fsm.update(self._metrics(
                clock, cervical_angle=45.0, ear_avg=0.10,
                face_detected=False, pose_detected=False)) == []
            clock.advance(0.05)

    def test_detection_loss_resets_timers(self):
        """
        REGRESIÓN — falso positivo tras una ausencia.

        Antes, al perder la detección los temporizadores no se actualizaban
        pero tampoco se reiniciaban: seguían corriendo. Si el usuario se
        levantaba 10 s con el temporizador a medias, al volver el `elapsed`
        ya superaba la ventana y saltaba una alerta por un intervalo que el
        sistema nunca llegó a medir.
        """
        fsm, clock = self._fsm()
        for _ in range(60):                     # 3 s en riesgo
            fsm.update(self._metrics(clock, cervical_angle=40.0))
            clock.advance(0.05)

        fsm.update(self._metrics(clock, cervical_angle=40.0, pose_detected=False))
        clock.advance(30.0)                     # 30 s ausente

        alerts = fsm.update(self._metrics(clock, cervical_angle=40.0))
        assert alerts == [], "Al recuperar la pose no debe alertar de inmediato"
        assert fsm.get_timers_status()["cervical_elapsed_sec"] < 0.1

    def test_invalid_pose_freezes_postural_timers(self):
        """
        `pose_valid=False` (landmarks poco visibles o distancia fuera de rango)
        no debe acumular tiempo de riesgo: el ángulo medido no es una medición.
        """
        fsm, clock = self._fsm()
        for _ in range(200):                    # 10 s, el doble de la ventana
            assert fsm.update(self._metrics(
                clock, cervical_angle=45.0, pose_valid=False)) == []
            clock.advance(0.05)

    def test_face_loss_resets_fatigue_timer(self):
        fsm, clock = self._fsm()
        for _ in range(40):                     # 2 s con ojos cerrados
            fsm.update(self._metrics(clock, ear_avg=0.10))
            clock.advance(0.05)
        fsm.update(self._metrics(clock, ear_avg=0.10, face_detected=False))
        clock.advance(20.0)
        assert fsm.update(self._metrics(clock, ear_avg=0.10)) == []

    # ------ cooldown ------

    def test_cooldown_prevents_repeated_alerts(self):
        cb = MagicMock()
        fsm, clock = self._fsm(on_alert=cb, cooldown=30.0)
        for _ in range(1200):                   # 60 s en postura de riesgo
            fsm.update(self._metrics(clock, cervical_angle=40.0))
            clock.advance(0.05)
        # 60 s / cooldown 30 s -> como mucho 2 alertas
        assert cb.call_count <= 2, cb.call_count
        assert cb.call_count >= 1

    def test_alert_requires_full_window_again_after_firing(self):
        """Tras disparar, el temporizador se reinicia: no re-dispara por frame."""
        cb = MagicMock()
        fsm, clock = self._fsm(on_alert=cb, cooldown=0.0)
        for _ in range(120):                    # 6 s
            fsm.update(self._metrics(clock, cervical_angle=40.0))
            clock.advance(0.05)
        assert cb.call_count == 1, (
            f"Con la ventana de 5 s y 6 s de riesgo debe haber 1 alerta, "
            f"hubo {cb.call_count}"
        )

    # ------ eventos ------

    def test_alert_event_serializes(self):
        fsm, clock = self._fsm()
        alerts = []
        for _ in range(120):
            alerts += fsm.update(self._metrics(clock, cervical_angle=40.0))
            clock.advance(0.05)
        event = alerts[0]
        assert isinstance(event, AlertEvent)
        import json
        payload = json.loads(event.to_json())
        assert payload["alert_type"] == "cervical_angle"
        assert payload["threshold"] == 30.0
        assert payload["timestamp"] > 1_600_000_000, (
            "El timestamp del evento debe ser hora de pared para poder "
            "correlacionarlo con la bitácora"
        )

    def test_callback_exception_does_not_break_fsm(self):
        fsm, clock = self._fsm(on_alert=MagicMock(side_effect=RuntimeError("boom")))
        alerts = []
        for _ in range(120):
            alerts += fsm.update(self._metrics(clock, cervical_angle=40.0))
            clock.advance(0.05)
        assert len(alerts) == 1, "El fallo del callback no debe perder el evento"

    def test_simultaneous_conditions_generate_both(self):
        fsm, clock = self._fsm()
        alerts = []
        for _ in range(140):                    # 7 s
            alerts += fsm.update(self._metrics(
                clock, cervical_angle=40.0, ear_avg=0.10))
            clock.advance(0.05)
        kinds = {a.alert_type for a in alerts}
        assert AlertType.CERVICAL_ANGLE in kinds
        assert AlertType.EYE_FATIGUE in kinds

    # ------ estado ------

    def test_get_timers_status_structure(self):
        fsm, _ = self._fsm()
        status = fsm.get_timers_status()
        expected = {
            "cervical_elapsed_sec", "shoulder_elapsed_sec", "ear_elapsed_sec",
            "yawn_elapsed_sec", "cervical_window_sec", "shoulder_window_sec",
            "ear_window_sec", "yawn_window_sec", "perclos", "blink_rate_per_min",
        }
        assert expected.issubset(status.keys()), expected - status.keys()

    def test_reset_all_clears_timers(self):
        fsm, clock = self._fsm()
        for _ in range(40):
            fsm.update(self._metrics(clock, cervical_angle=40.0, ear_avg=0.10))
            clock.advance(0.05)
        fsm.reset_all()
        status = fsm.get_timers_status()
        assert status["cervical_elapsed_sec"] == 0.0
        assert status["ear_elapsed_sec"] == 0.0
        assert status["perclos"] == 0.0


# ---------------------------------------------------------------------------
# PERCLOS dentro del FSM
# ---------------------------------------------------------------------------

class TestPerclosAlert:

    def _thresholds(self, **over):
        fat = {**_TEST_THRESHOLDS["fatigue"], "perclos_enabled": True,
               "perclos_window_sec": 60.0, "perclos_threshold": 0.15}
        fat.update(over)
        return {**_TEST_THRESHOLDS, "fatigue": fat}

    def test_no_alert_before_window_is_half_filled(self):
        """
        Con la ventana casi vacía, unos pocos frames con el ojo cerrado darían
        PERCLOS = 1.0. El FSM debe esperar a tener datos suficientes.
        """
        clock = _FakeClock()
        fsm = FusionFSM(self._thresholds(), cooldown_sec=0.0, clock=clock)
        alerts = []
        for _ in range(100):                    # 5 s << 30 s (media ventana)
            alerts += fsm.update(SensorMetrics(monotonic=clock.t, ear_avg=0.10))
            clock.advance(0.05)
        assert not any(a.alert_type == AlertType.DROWSINESS for a in alerts)

    def test_alert_when_perclos_exceeds_threshold(self):
        clock = _FakeClock()
        fsm = FusionFSM(self._thresholds(), cooldown_sec=0.0, clock=clock)
        alerts = []
        for i in range(1400):                   # 70 s
            # 25% del tiempo con el ojo cerrado -> PERCLOS 0.25 > 0.15
            ear = 0.10 if i % 4 == 0 else 0.32
            alerts += fsm.update(SensorMetrics(monotonic=clock.t, ear_avg=ear))
            clock.advance(0.05)
        assert any(a.alert_type == AlertType.DROWSINESS for a in alerts)

    def test_no_alert_with_normal_blinking(self):
        """Parpadeo normal → PERCLOS muy por debajo del umbral."""
        clock = _FakeClock()
        fsm = FusionFSM(self._thresholds(), cooldown_sec=0.0, clock=clock)
        alerts = []
        for _ in range(30):                     # ~96 s de parpadeo normal
            fsm.update(SensorMetrics(monotonic=clock.t, ear_avg=0.10))
            clock.advance(0.2)
            for _ in range(62):
                alerts += fsm.update(SensorMetrics(monotonic=clock.t, ear_avg=0.32))
                clock.advance(0.05)
        assert not any(a.alert_type == AlertType.DROWSINESS for a in alerts)

    def test_disabled_by_config(self):
        clock = _FakeClock()
        fsm = FusionFSM(self._thresholds(perclos_enabled=False),
                        cooldown_sec=0.0, clock=clock)
        alerts = []
        for i in range(1400):
            ear = 0.10 if i % 4 == 0 else 0.32
            alerts += fsm.update(SensorMetrics(monotonic=clock.t, ear_avg=ear))
            clock.advance(0.05)
        assert not any(a.alert_type == AlertType.DROWSINESS for a in alerts)


# ---------------------------------------------------------------------------
# Contrato de SensorMetrics
# ---------------------------------------------------------------------------

class TestSensorMetrics:

    def test_timestamp_is_wall_clock(self):
        """
        REGRESIÓN — `metrics_log.timestamp` era inutilizable.

        `timestamp` usaba `time.perf_counter()`, cuyo origen es arbitrario
        (valores como 214173.39 en la base de datos), mientras que
        `events.timestamp` usaba `time.time()`. Las dos tablas quedaban en
        bases de tiempo distintas y era imposible correlacionar una alerta
        con las métricas del momento en que se disparó.
        """
        m = SensorMetrics()
        assert abs(m.timestamp - time.time()) < 5.0
        assert m.timestamp > 1_600_000_000

    def test_monotonic_is_separate_from_timestamp(self):
        m = SensorMetrics()
        assert m.monotonic != m.timestamp
