"""
test_multiple_baseline.py
-------------------------
Diseño experimental con preprueba, posprueba y grupo de control
(2026-11-19; antes, línea base múltiple con control):

  - perfil: grupo (intervención/control) y días de línea base;
  - fase: el grupo de intervención empieza sus avisos al cumplir su línea
    base; el control queda en fase "control" y nunca avisa;
  - análisis: el corte del control usa la misma regla que la app, NAP,
    Wilcoxon en cada grupo y Mann-Whitney del cambio entre grupos;
  - figuras: evolución por participante y comparación entre grupos.

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
from src.tools.field_report import evolution_html, group_comparison_html

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


class TestControlCut:

    def test_same_rule_as_the_app(self):
        cfg = fa.FieldConfig(baseline_days=3, baseline_min_minutes=120)
        mins = [150, 40, 130, 200, 150, 150]       # el día 2 no cuenta
        days = [{"dia_uso": k + 1, "minutos": m} for k, m in enumerate(mins)]
        assert fa.control_cut_day(days, cfg) == 5

    def test_never_completes_the_baseline(self):
        cfg = fa.FieldConfig(baseline_days=5)
        days = [{"dia_uso": k + 1, "minutos": 150} for k in range(4)]
        assert fa.control_cut_day(days, cfg) is None


class TestGroupDesignExport:

    def test_summary_tests_and_figures(self, tmp_path):
        cfg = json.loads(json.dumps(FIELD_CONFIG))
        cfg["study"]["field"]["baseline_days"] = 4
        cfg["study"]["field"]["baseline_min_minutes"] = 60
        cfg_p = tmp_path / "thresholds.json"
        cfg_p.write_text(json.dumps(cfg), encoding="utf-8")
        _staggered_db(tmp_path / "a.db", "P01", start=5)
        _staggered_db(tmp_path / "b.db", "P02", start=5)
        _staggered_db(tmp_path / "c.db", "P03", start=1, control=True)
        res = ex.export([tmp_path / "a.db", tmp_path / "b.db",
                         tmp_path / "c.db"], tmp_path / "out", cfg_p)
        by = {s["participante"]: s for s in res["field"]["participants"]}
        assert by["P01"]["grupo"] == "intervencion"
        assert by["P01"]["dia_inicio_avisos"] == 5
        assert by["P01"]["dia_inicio_despues"] == 5
        assert by["P01"]["nap_pct_desviado"] == pytest.approx(1.0)
        # El control: sin avisos, corte el mismo día que el otro grupo
        assert by["P03"]["grupo"] == "control"
        assert by["P03"]["dia_inicio_avisos"] is None
        assert by["P03"]["dia_inicio_despues"] == 5
        assert by["P03"]["nap_pct_desviado"] == pytest.approx(0.5, abs=0.15)
        tests = [t for t in res["field"]["tests"]
                 if t["indicador"] == "pct_desviado"]
        assert {t["prueba"] for t in tests} == {
            fa.TEST_WITHIN_INTERV, fa.TEST_WITHIN_CONTROL, fa.TEST_BETWEEN}
        between = next(t for t in tests if t["prueba"] == fa.TEST_BETWEEN)
        assert (between["n_intervencion"], between["n_control"]) == (2, 1)
        assert between["r_biserial"] == pytest.approx(-1.0)
        assert "muy pocas personas" in between["nota"]
        rep = tmp_path / "out" / "reportes"
        evo = (rep / "evolucion_participantes.html").read_text("utf-8")
        assert evo.index("P01") < evo.index("P02") < evo.index("P03")
        assert "control (sin avisos) · corte el día 5" in evo
        comp = (rep / "comparacion_grupos.html").read_text("utf-8")
        assert "Mann-Whitney" in comp and "Control (sin avisos)" in comp
        csv_text = (tmp_path / "out" / "prueba_grupo.csv").read_text("utf-8")
        assert "mann_whitney_cambio" in csv_text

    def test_figures_without_data_do_not_crash(self):
        assert "P01" in evolution_html([{"participante": "P01"}], {})
        html = group_comparison_html([{"participante": "P01"}], {}, [])
        assert "Comparación entre grupos" in html


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


# ------------------------------------------------- prueba de grupo

def _summary(code, base, interv, grupo="intervencion", ok="si", key=None):
    key = key or "pct_desviado"
    return {"participante": code, "grupo": grupo, "valido_para_analisis": ok,
            f"{key}_base": base, f"{key}_interv": interv}


def _test(summaries, prueba, key="pct_desviado"):
    return next(t for t in fa.group_tests(summaries)
                if t["indicador"] == key and t["prueba"] == prueba)


class TestGroupTests:

    def _study(self):
        interv = [_summary(f"P{i}", 30 + i, 20 + i * 0.5) for i in range(6)]
        # El control apenas cambia (sin empates: prueba exacta)
        control = [_summary(f"C{i}", 30 + i, 30 + i + (i - 2.4) / 10,
                            grupo="control") for i in range(6)]
        return interv + control

    def test_within_intervention(self):
        t = _test(self._study(), fa.TEST_WITHIN_INTERV)
        assert t["n_intervencion"] == 6
        assert t["r_biserial"] == -1.0
        assert t["p_una_cola"] == pytest.approx(1 / 64)
        assert t["p_dos_colas"] == pytest.approx(2 / 64)
        assert t["nota"] == ""

    def test_within_control_does_not_change(self):
        t = _test(self._study(), fa.TEST_WITHIN_CONTROL)
        assert t["n_control"] == 6
        assert t["p_dos_colas"] > 0.5

    def test_between_groups_is_the_main_test(self):
        t = _test(self._study(), fa.TEST_BETWEEN)
        assert (t["n_intervencion"], t["n_control"]) == (6, 6)
        assert t["r_biserial"] == -1.0           # todos bajaron más
        assert t["p_una_cola"] == pytest.approx(1 / 924)
        assert t["p_dos_colas"] == pytest.approx(2 / 924)
        assert t["mediana_cambio_intervencion"] < t["mediana_cambio_control"]
        assert t["nota"] == ""

    def test_direction_when_higher_is_better(self):
        rows = ([_summary(f"P{i}", 0.5, 1.2 + i / 10, key="pausas_por_hora")
                 for i in range(6)]
                + [_summary(f"C{i}", 0.5, 0.5 + (i - 3) / 100,
                            grupo="control", key="pausas_por_hora")
                   for i in range(6)])
        t = _test(rows, fa.TEST_BETWEEN, "pausas_por_hora")
        assert t["mejora_si"] == "sube"
        assert t["p_una_cola"] < 0.01

    def test_invalid_are_excluded_and_small_groups_flagged(self):
        rows = [_summary("P1", 30, 20), _summary("P2", 30, 25),
                _summary("C1", 30, 31, grupo="control"),
                _summary("P3", 30, 10, ok="no")]
        within = _test(rows, fa.TEST_WITHIN_INTERV)
        assert within["n_intervencion"] == 2
        assert "menos de 5 personas" in within["nota"]
        between = _test(rows, fa.TEST_BETWEEN)
        assert (between["n_intervencion"], between["n_control"]) == (2, 1)
        assert "muy pocas personas" in between["nota"]

    def test_no_data(self):
        for prueba in (fa.TEST_WITHIN_INTERV, fa.TEST_WITHIN_CONTROL,
                       fa.TEST_BETWEEN):
            t = _test([], prueba)
            assert t["p_una_cola"] is None and t["p_dos_colas"] is None
