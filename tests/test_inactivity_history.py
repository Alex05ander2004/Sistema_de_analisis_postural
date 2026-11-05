"""
test_inactivity_history.py
--------------------------
OE 1.3.2.3 y OE 1.3.2.5:

  - alerta de inactividad física (demasiado tiempo sin levantarse), con los
    mismos criterios de pausa que el análisis de campo;
  - respuesta a esa alerta en el análisis (¿hubo pausa después?);
  - historial personal por día que muestra la ventana «Mi historial».

Autor: Tesis Huisa Perez, UNSA 2026
"""

import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.fusion.fusion_fsm import AlertType, FusionFSM, SensorMetrics
from src.study.personal_history import daily_history, week_comparison
from src.storage.history_logger import HistoryLogger
from src.tools import field_analysis as fa

from tests.test_field_study import _rows, _ts, make_field_db


def _fsm(**sed):
    cfg = {"enabled": True, "alert_after_min": 1.0, "repeat_every_min": 0.5,
           "break_min_sec": 10, "unknown_gap_sec": 20}
    cfg.update(sed)
    return FusionFSM({"sedentary": cfg}, cooldown_sec=0.0)


def _feed(fsm, t0, t1, present=True, step=1.0):
    """Alimenta el FSM de t0 a t1 (s) y devuelve las alertas de inactividad."""
    out, t = [], t0
    while t < t1:
        m = SensorMetrics(pose_detected=present, face_detected=present,
                          pose_valid=present, monotonic=t, timestamp=t)
        out += [a for a in fsm.update(m)
                if a.alert_type is AlertType.SEDENTARY]
        t += step
    return out


class TestSedentaryAlert:

    def test_alerts_after_the_limit_and_repeats(self):
        fsm = _fsm()
        assert _feed(fsm, 0, 59) == []
        first = _feed(fsm, 59, 61)
        assert len(first) == 1
        assert first[0].metric_value >= 1.0
        # repetición cada 30 s mientras siga sin levantarse
        assert len(_feed(fsm, 61, 92)) == 1

    def test_a_break_restarts_the_count(self):
        fsm = _fsm()
        _feed(fsm, 0, 50)
        _feed(fsm, 50, 62, present=False)          # 12 s fuera >= 10 s
        assert _feed(fsm, 62, 100) == []           # tramo nuevo desde 62
        assert len(_feed(fsm, 100, 125)) == 1

    def test_short_absence_is_not_a_break(self):
        fsm = _fsm()
        _feed(fsm, 0, 30)
        _feed(fsm, 30, 35, present=False)          # 5 s < 10 s
        assert len(_feed(fsm, 35, 62)) == 1

    def test_no_alert_while_absent(self):
        fsm = _fsm()
        _feed(fsm, 0, 55)
        assert _feed(fsm, 55, 200, present=False) == []

    def test_gap_without_data_restarts_the_count(self):
        fsm = _fsm()
        _feed(fsm, 0, 50)
        # 50 -> 80: sin mediciones (app en pausa, cámara cedida)
        assert _feed(fsm, 80, 125) == []
        assert len(_feed(fsm, 125, 145)) == 1

    def test_reset_timers_restarts_the_count(self):
        fsm = _fsm()
        _feed(fsm, 0, 50)
        fsm.reset_timers()
        assert _feed(fsm, 50, 105) == []

    def test_disabled_by_default(self):
        fsm = FusionFSM({}, cooldown_sec=0.0)
        assert _feed(fsm, 0, 4000, step=10.0) == []
        assert fsm.get_timers_status()["sedentary_enabled"] is False

    def test_status_reports_sitting_minutes(self):
        fsm = _fsm(alert_after_min=45)
        _feed(fsm, 0, 120)
        st = fsm.get_timers_status()
        assert st["sedentary_enabled"] is True
        assert st["sedentary_alert_min"] == 45.0
        assert st["sitting_min"] >= 0


class TestBreakAfter:

    def _data(self, present):
        rows = _rows([5.0] * len(present), present=present)
        for r, d in zip(rows, fa.row_durations(rows)):
            r["_dur"] = d
        return rows, [r["timestamp"] for r in rows]

    def test_break_within_window(self):
        present = [True] * 100 + [False] * 70 + [True] * 30
        rows, ts = self._data(present)
        s = fa.break_after(10.0, ts, rows, fa.FieldConfig(), within_sec=600)
        assert s == 90.0

    def test_no_break_in_time(self):
        present = [True] * 200
        rows, ts = self._data(present)
        assert fa.break_after(10.0, ts, rows, fa.FieldConfig(),
                              within_sec=60) is None

    def test_short_absence_does_not_count(self):
        present = [True] * 50 + [False] * 30 + [True] * 100
        rows, ts = self._data(present)
        assert fa.break_after(0.0, ts, rows, fa.FieldConfig()) is None

    def test_daily_summary_has_the_response_indicator(self, tmp_path):
        day = (date.today() - timedelta(days=3)).isoformat()
        db = tmp_path / "h.db"
        make_field_db(db, "P01", [{
            "fecha": day, "fase": "intervencion", "minutos": 60, "dev": 5.0,
            "ausencia": (1000, 120),
            "eventos": [(900, "sedentary", 1), (2000, "sedentary", 1)]}])
        from src.tools import export_study as ex
        sessions = ex.read_sessions(db)
        data = fa.collect_field_data(sessions, {}, fa.FieldConfig())
        row = fa.daily_summary(data["P01"], fa.FieldConfig())[0]
        assert row["alertas_inactividad"] == 2
        assert row["pct_pausa_tras_aviso_inactividad"] == 50.0
        assert row["pausa_tras_aviso_mediana_s"] == 100.0


class TestPersonalHistory:

    def test_daily_rows_from_a_real_database(self, tmp_path):
        d1 = (date.today() - timedelta(days=2)).isoformat()
        d2 = (date.today() - timedelta(days=1)).isoformat()
        db = tmp_path / "h.db"
        make_field_db(db, "P01", [
            {"fecha": d1, "fase": "intervencion", "minutos": 30,
             "dev": lambda s: 25.0 if s < 900 else 5.0,
             "eventos": [(10, "cervical_angle", 1), (20, "sedentary", 1),
                         (30, "cervical_angle", 0)]},
            {"fecha": d2, "fase": "intervencion", "minutos": 20, "dev": 5.0},
        ])
        hist = HistoryLogger(db_path=str(db))
        try:
            rows = daily_history(hist, {}, days=14)
        finally:
            hist.close()
        assert [r["fecha"] for r in rows] == [d2, d1]      # reciente primero
        old = rows[1]
        assert abs(old["minutos"] - 30) < 0.1
        assert abs(old["pct_buena_postura"] - 50.0) < 0.5
        assert old["avisos"] == 2          # la no notificada no cuenta
        assert old["avisos_inactividad"] == 1
        assert rows[0]["pct_buena_postura"] == 100.0

    def test_filters_by_participant_and_age(self, tmp_path):
        recent = (date.today() - timedelta(days=1)).isoformat()
        old = (date.today() - timedelta(days=30)).isoformat()
        db = tmp_path / "h.db"
        make_field_db(db, "P01", [
            {"fecha": recent, "fase": "intervencion", "minutos": 5, "dev": 5.0},
            {"fecha": old, "fase": "intervencion", "minutos": 5, "dev": 5.0}])
        hist = HistoryLogger(db_path=str(db))
        try:
            assert [r["fecha"] for r in daily_history(hist, {}, days=14)] == [recent]
            assert daily_history(hist, {}, participant_code="P99") == []
        finally:
            hist.close()

    def test_week_comparison(self):
        rows = [{"pct_buena_postura": v} for v in
                [90, 90, 90, 90, 90, 90, 90, 70, 70, 70]]
        cmp_ = week_comparison(rows)
        assert round(cmp_["cambio"]) == 20
        assert week_comparison(rows[:3]) is None
        assert week_comparison(rows[:7]) is None
