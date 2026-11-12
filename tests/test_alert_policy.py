"""
test_alert_policy.py
--------------------
Política de AVISO del uso diario frente a la de DETECCIÓN (validación):

  - postura: solo se avisa si se mantiene la ventana larga (30 s), no 5 s;
  - enfriamiento por tipo (5 min de postura) en vez del global de 30 s;
  - bostezos: aviso con 3 en 15 min, no con uno;
  - sin política (laboratorio) todo vuelve a la detección inmediata;
  - textos del aviso de Windows comprensibles para el usuario.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.fusion.fusion_fsm import AlertEvent, AlertType, FusionFSM, SensorMetrics
from src.ui import toast_notifier as tn

POLICY = {
    "cervical_alert_window_sec": 30,
    "shoulder_alert_window_sec": 30,
    "yawn_alert_count": 3,
    "yawn_alert_period_sec": 900,
    "cooldown_sec": {"cervical_angle": 300, "yawn": 600},
}

BASE = {"postural": {"cervical_mode": "absolute", "cervical_angle_max_deg": 30,
                     "cervical_alert_window_sec": 5.0},
        "fatigue": {"mouth_opening_threshold": 0.45,
                    "yawn_alert_window_sec": 3.0}}


def _feed(fsm, t0, t1, step=0.5, **kw):
    out, t = [], t0
    while t < t1:
        m = SensorMetrics(monotonic=t, timestamp=t, **kw)
        out += fsm.update(m)
        t += step
    return out


def _types(alerts):
    return [a.alert_type for a in alerts]


class TestPostureWindow:

    def test_detection_policy_alerts_after_5_s(self):
        fsm = FusionFSM(BASE, cooldown_sec=30)
        got = _types(_feed(fsm, 0, 7, cervical_angle=40))
        assert got.count(AlertType.CERVICAL_ANGLE) == 1

    def test_daily_policy_ignores_a_short_gesture(self):
        fsm = FusionFSM(BASE, cooldown_sec=30)
        fsm.set_alert_policy(POLICY)
        assert _feed(fsm, 0, 20, cervical_angle=40) == []
        got = _types(_feed(fsm, 20, 32, cervical_angle=40))
        assert got == [AlertType.CERVICAL_ANGLE]

    def test_daily_policy_cooldown_is_per_type(self):
        fsm = FusionFSM(BASE, cooldown_sec=30)
        fsm.set_alert_policy(POLICY)
        # 4 minutos de mala postura sostenida: un solo aviso (5 min de
        # enfriamiento), no ocho como con 30 s.
        got = _types(_feed(fsm, 0, 240, cervical_angle=40))
        assert got.count(AlertType.CERVICAL_ANGLE) == 1
        got = _types(_feed(fsm, 240, 400, cervical_angle=40))
        assert got.count(AlertType.CERVICAL_ANGLE) == 1

    def test_removing_the_policy_restores_detection(self):
        fsm = FusionFSM(BASE, cooldown_sec=30)
        fsm.set_alert_policy(POLICY)
        fsm.set_alert_policy(None)
        got = _types(_feed(fsm, 0, 7, cervical_angle=40))
        assert got == [AlertType.CERVICAL_ANGLE]


class TestYawnCount:

    def _yawn(self, fsm, t):
        """Un bostezo de 4 s y 6 s de boca cerrada."""
        a = _feed(fsm, t, t + 4, mouth_opening=0.6, cervical_angle=0)
        b = _feed(fsm, t + 4, t + 10, mouth_opening=0.1, cervical_angle=0)
        return _types(a + b)

    def test_single_yawn_alerts_without_policy(self):
        fsm = FusionFSM(BASE, cooldown_sec=0)
        assert AlertType.YAWN in self._yawn(fsm, 0)

    def test_three_yawns_needed_with_policy(self):
        fsm = FusionFSM(BASE, cooldown_sec=0)
        fsm.set_alert_policy(POLICY)
        assert AlertType.YAWN not in self._yawn(fsm, 0)
        assert AlertType.YAWN not in self._yawn(fsm, 60)
        got = self._yawn(fsm, 120)
        assert got.count(AlertType.YAWN) == 1

    def test_old_yawns_expire(self):
        fsm = FusionFSM(BASE, cooldown_sec=0)
        fsm.set_alert_policy(POLICY)
        self._yawn(fsm, 0)
        self._yawn(fsm, 60)
        # El tercero llega pasados 15 min del primero: no se completa la cuenta.
        assert AlertType.YAWN not in self._yawn(fsm, 1000)


class TestConfigAndTexts:

    def test_config_has_a_valid_policy(self):
        cfg = json.loads((PROJECT_ROOT / "config" / "thresholds.json")
                         .read_text(encoding="utf-8"))
        pol = cfg["alert_policy"]
        assert pol["cervical_alert_window_sec"] >= 30
        for name in pol["cooldown_sec"]:
            AlertType(name)            # todo nombre es un tipo real

    def test_eye_text_no_longer_asks_to_stand_6_m_away(self):
        txt = tn._TOAST_ACTIONS["eye_fatigue"]
        assert "6 m" not in txt and "lejano" in txt

    def test_sedentary_detail_speaks_in_minutes(self):
        ev = AlertEvent(alert_type=AlertType.SEDENTARY, metric_value=45.2,
                        threshold=45, duration_sec=2712)
        assert tn._detail(ev) == "Llevas 45 min sin levantarte."

    def test_long_posture_detail_in_minutes(self):
        ev = AlertEvent(alert_type=AlertType.CERVICAL_ANGLE, metric_value=20,
                        threshold=15, duration_sec=180)
        assert tn._detail(ev) == "Mantenida durante 3 min."
