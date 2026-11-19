"""
test_multiple_baseline.py
-------------------------
Diseño de línea base múltiple con control (elegido el 2026-11-12):

  - perfil: grupo (intervención/control) y días de línea base propios;
  - fase: cada participante empieza sus avisos cuando cumple SU línea base;
    el control queda en fase "control" y nunca avisa;
  - análisis: NAP, día de inicio de avisos, corte de referencia del control;
  - figura de línea base múltiple.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import json
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.fusion.fusion_fsm import AlertEvent, AlertType
from src.study import field_study as fs
from src.tools import export_study as ex
from src.tools import field_analysis as fa
from src.tools.field_report import multiple_baseline_html

from tests.test_field_study import (FIELD_CONFIG, _days, _field_thread, _wait,
                                    _run_session, make_field_db)


# ---------------------------------------------------------------- perfil

class TestProfile:

    def test_roundtrip_group_and_days(self, tmp_path):
        p = tmp_path / "perfil.json"
        fs.LocalProfile(participante="P02", modo=fs.MODE_FIELD,
                        grupo=fs.GROUP_CONTROL, dias_linea_base=7).save(p)
        prof = fs.LocalProfile.load(p)
        assert prof.grupo == fs.GROUP_CONTROL
        assert prof.dias_linea_base == 7
        assert prof.baseline_days(5) == 7

    def test_old_profile_defaults(self, tmp_path):
        p = tmp_path / "perfil.json"
        p.write_text(json.dumps({"participante": "P01", "modo": "campo"}),
                     encoding="utf-8")
        prof = fs.LocalProfile.load(p)
        assert prof.grupo == fs.GROUP_INTERVENTION
        assert prof.dias_linea_base is None
        assert prof.baseline_days(5) == 5

    @pytest.mark.parametrize("grupo,dias", [("raro", 7), ("control", 0),
                                            ("control", 99), ("control", "x")])
    def test_invalid_values_are_neutralised(self, tmp_path, grupo, dias):
        p = tmp_path / "perfil.json"
        p.write_text(json.dumps({"grupo": grupo, "dias_linea_base": dias}),
                     encoding="utf-8")
        prof = fs.LocalProfile.load(p)
        assert prof.grupo in fs.VALID_GROUPS
        assert prof.dias_linea_base in (None, 7)


# ------------------------------------------------------------------ fase

class TestFieldPhase:

    def _minutes(self, n):
        start = date(2026, 11, 1)
        return {(start + timedelta(days=i)).isoformat(): 90.0 for i in range(n)}

    def test_each_participant_has_its_own_start(self):
        mins = self._minutes(6)               # 6 días previos válidos
        today = "2026-11-07"
        p5 = fs.LocalProfile(dias_linea_base=5)
        p7 = fs.LocalProfile(dias_linea_base=7)
        assert fs.field_phase(p5, mins, today, 5, 60)[0] == fs.PHASE_INTERVENTION
        assert fs.field_phase(p7, mins, today, 5, 60)[0] == fs.PHASE_BASELINE

    def test_control_never_leaves_control(self):
        prof = fs.LocalProfile(grupo=fs.GROUP_CONTROL)
        phase, day, _ = fs.field_phase(prof, self._minutes(14), "2026-11-20",
                                       5, 60)
        assert phase == fs.PHASE_CONTROL
        assert day == 15


class TestControlThread:

    def test_control_is_muted_and_logged(self, tmp_path):
        prev = _days(date.today() - timedelta(days=8), 7)
        make_field_db(tmp_path / "t.db", "P09", [
            {"fecha": d, "fase": "control", "minutos": 61, "dev": 0}
            for d in prev])
        prof = fs.LocalProfile(participante="P09", modo=fs.MODE_FIELD,
                               grupo=fs.GROUP_CONTROL)
        t, restore, db_path, alerts = _field_thread(tmp_path, prof,
                                                    baseline_days=2)
        try:
            _run_session(t)
            assert t.phase == fs.PHASE_CONTROL and t.alerts_muted
            assert t.day_number == 8
            t._handle_alert(AlertEvent(alert_type=AlertType.CERVICAL_ANGLE,
                                       metric_value=20, threshold=15,
                                       duration_sec=30))
            time.sleep(0.3)
        finally:
            t.stop(); t.join(timeout=5.0); restore()
        assert alerts == [], "el control nunca recibe avisos"
        con = sqlite3.connect(db_path)
        fases = {r[0] for r in con.execute("SELECT study_phase FROM sessions")}
        assert "control" in fases
        con.close()


# -------------------------------------------------------------- análisis

class TestNap:

    def test_full_improvement(self):
        assert fa.nap([30, 28, 32], [10, 12], lower_is_better=True) == 1.0

    def test_no_effect_and_ties(self):
        assert fa.nap([10, 20], [10, 20]) == 0.5

    def test_higher_is_better(self):
        assert fa.nap([1, 1], [3, 4], lower_is_better=False) == 1.0

    def test_empty(self):
        assert fa.nap([], [1]) is None


def _staggered_db(path, code, start, n=10, control=False):
    """n días de 70 min: 30 % desviado antes de `start`, 5 % desde ahí."""
    d = _days(date(2026, 11, 2), n)
    rows = []
    for k, day in enumerate(d, start=1):
        if control:
            fase, pct = "control", 30
        else:
            fase = "linea_base" if k < start else "intervencion"
            pct = 30 if k < start else 5
        rows.append({"fecha": day, "fase": fase, "minutos": 70,
                     "dev": (lambda p: (lambda s: 20.0 if (s % 100) < p
                                        else 0.0))(pct)})
    make_field_db(path, code, rows)


class TestMultipleBaselineExport:

    def test_summary_has_group_start_and_nap(self, tmp_path):
        cfg = json.loads(json.dumps(FIELD_CONFIG))
        cfg["study"]["field"]["baseline_days"] = 4
        cfg_p = tmp_path / "thresholds.json"
        cfg_p.write_text(json.dumps(cfg), encoding="utf-8")
        _staggered_db(tmp_path / "a.db", "P01", start=5)
        _staggered_db(tmp_path / "b.db", "P02", start=7)
        _staggered_db(tmp_path / "c.db", "P03", start=1, control=True)
        res = ex.export([tmp_path / "a.db", tmp_path / "b.db",
                         tmp_path / "c.db"], tmp_path / "out", cfg_p)
        by = {s["participante"]: s for s in res["field"]["participants"]}
        assert by["P01"]["grupo"] == "intervencion"
        assert by["P01"]["dia_inicio_avisos"] == 5
        assert by["P02"]["dia_inicio_avisos"] == 7
        assert by["P01"]["nap_pct_desviado"] == pytest.approx(1.0)
        assert by["P03"]["grupo"] == "control"
        assert by["P03"]["dia_inicio_avisos"] is None
        # El control no cambia con el tiempo: NAP alrededor de 0.5
        assert by["P03"]["nap_pct_desviado"] == pytest.approx(0.5, abs=0.15)
        fig = (tmp_path / "out" / "reportes" / "linea_base_multiple.html")
        assert fig.exists()
        html = fig.read_text("utf-8")
        assert html.index("P01") < html.index("P02") < html.index("P03")
        assert "control (sin avisos)" in html

    def test_figure_without_data_does_not_crash(self):
        html = multiple_baseline_html([{"participante": "P01"}], {})
        assert "P01" in html


class TestStopSession:

    def test_stop_closes_session_and_keeps_calibration(self, tmp_path):
        prof = fs.LocalProfile(participante="U01", modo=fs.MODE_LAB)
        t, restore, db_path, _ = _field_thread(tmp_path, prof)
        try:
            _run_session(t)
            t.set_paused(True)
            t.request_stop()
            assert _wait(lambda: t.mode == "ready")
            assert not t.is_paused, "al terminar se vuelve a encender la cámara"
            assert t.can_start, "la calibración se conserva"
            t.request_start()
            assert _wait(lambda: t.mode == "monitoring")
        finally:
            t.stop(); t.join(timeout=5.0); restore()
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT ended_at FROM sessions ORDER BY started_at"
                           ).fetchall()
        con.close()
        assert len(rows) == 2 and rows[0][0] is not None
