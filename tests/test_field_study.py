"""
test_field_study.py
-------------------
Estudio de campo: cada participante usa la app en su equipo ~15 días, con
una línea base (la app mide pero no avisa) y una intervención (avisa), y
envía sus datos con «Exportar mis datos».

Se prueba:
  - perfil local del equipo (código, modo, FOV) y su guardado atómico;
  - decisión de la fase por días VÁLIDOS anteriores a hoy;
  - bitácora: columnas nuevas, minutos reales por día, autorreporte, copia
    consistente, alertas silenciadas;
  - hilo de inferencia en modo campo (fase y alertas silenciadas);
  - el paquete .zip que envía el participante;
  - los indicadores para el especialista y la exportación completa, incluida
    la deduplicación de envíos repetidos.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import csv
import json
import sqlite3
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from queue import Queue

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.fusion.fusion_fsm import AlertEvent, AlertType, SensorMetrics
from src.storage.history_logger import HistoryLogger
from src.study import field_study as fs
from src.study.data_bundle import create_bundle
from src.tools import export_study as ex
from src.tools import field_analysis as fa
from src.version import config_hash


# ---------------------------------------------------------------------------
# Datos sintéticos de campo
# ---------------------------------------------------------------------------

def _ts(day: str, hhmm: str = "09:00") -> float:
    return datetime.fromisoformat(f"{day} {hhmm}").timestamp()


def make_field_db(path, code, days, step=1.0):
    """
    `days` = lista de dicts con: fecha, fase, minutos, dev (desviación
    constante o función del segundo), y opcionales ausencia=(inicio_s, dur_s),
    eventos=[(segundo, tipo, notificada)], perclos.
    Una sesión por día. Inserta en bloque (HistoryLogger confirma fila a fila,
    demasiado lento para días enteros).
    """
    db = HistoryLogger(db_path=str(path))
    db.close()
    con = sqlite3.connect(str(path))
    for i, d in enumerate(days):
        sid = f"{code}-{d['fecha']}-{i}"
        t0 = _ts(d["fecha"], d.get("hora", "09:00"))
        n = int(d["minutos"] * 60 / step)
        con.execute(
            "INSERT INTO sessions (id, started_at, ended_at, notes, "
            "participant_code, study_phase, app_version, config_hash) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, t0, t0 + n * step, "", code, d["fase"], "1.1.0",
             d.get("hash", "abc123")))
        absent = d.get("ausencia")
        rows = []
        for k in range(n):
            sec = k * step
            gone = absent and absent[0] <= sec < absent[0] + absent[1]
            dev = d["dev"](sec) if callable(d["dev"]) else d["dev"]
            rows.append((sid, t0 + sec, 40 + dev, 0 if gone else 1,
                         0 if gone else 1, 0 if gone else 1,
                         None if gone else dev, 40.0, 0.6, 2.0, 1.0,
                         d.get("perclos", 0.05), 0.25))
        con.executemany(
            "INSERT INTO metrics_log (session_id, timestamp, cervical_angle, "
            "pose_detected, face_detected, pose_valid, cervical_deviation, "
            "cervical_baseline, distance_m, shoulder_asymmetry, "
            "cervical_lateral, perclos, ear_avg) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        for sec, tipo, notified in d.get("eventos", []):
            con.execute(
                "INSERT INTO events (session_id, timestamp, alert_type, "
                "metric_value, threshold, duration_sec, message, notified, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, t0 + sec, tipo, 20.0, 15.0, 5.0, "m", notified, t0))
    con.commit()
    con.close()


def _days(start: date, n: int):
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


FIELD_CONFIG = {
    "postural": {"cervical_deviation_max_deg": 15.0,
                 "cervical_alert_window_sec": 5.0},
    "study": {"enabled": True, "conditions": [], "field": {
        "baseline_days": 2, "valid_day_min_minutes": 60,
        "valid_day_min_block_minutes": 30, "planned_days": 4,
        "analysis_min_valid_days_baseline": 2,
        "analysis_min_valid_days_intervention": 2}},
}


@pytest.fixture
def field_config(tmp_path):
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps(FIELD_CONFIG), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Perfil local
# ---------------------------------------------------------------------------

class TestLocalProfile:

    def test_missing_file_gives_lab_defaults(self, tmp_path):
        p = fs.LocalProfile.load(tmp_path / "no.json")
        assert p.modo == fs.MODE_LAB and p.participante == ""

    def test_roundtrip_and_normalised_code(self, tmp_path):
        path = tmp_path / "d" / "estudio_local.json"
        fs.LocalProfile(participante="p 07", modo=fs.MODE_FIELD,
                        inscrito="2026-11-03", camera_hfov_deg=63.2).save(path)
        p = fs.LocalProfile.load(path)
        assert (p.participante, p.modo, p.camera_hfov_deg) == \
            ("P07", fs.MODE_FIELD, 63.2)

    def test_invalid_values_are_neutralised(self, tmp_path):
        path = tmp_path / "e.json"
        path.write_text(json.dumps({"modo": "otro", "camera_hfov_deg": 300}),
                        encoding="utf-8")
        p = fs.LocalProfile.load(path)
        assert p.modo == fs.MODE_LAB and p.camera_hfov_deg is None

    def test_corrupt_file_does_not_crash_the_app(self, tmp_path):
        path = tmp_path / "e.json"
        path.write_text("{no es json", encoding="utf-8")
        assert fs.LocalProfile.load(path).participante == ""

    def test_save_leaves_no_temporary_files(self, tmp_path):
        fs.LocalProfile(participante="P01").save(tmp_path / "x.json")
        assert [f.name for f in tmp_path.iterdir()] == ["x.json"]


# ---------------------------------------------------------------------------
# Fase del estudio
# ---------------------------------------------------------------------------

class TestPhase:

    def test_first_day_is_baseline(self):
        assert fs.decide_phase({}, "2026-11-03", 5, 60) == \
            (fs.PHASE_BASELINE, 1, 0)

    def test_switches_after_enough_valid_previous_days(self):
        prev = {f"2026-11-0{i}": 90.0 for i in range(1, 6)}
        phase, day, done = fs.decide_phase(prev, "2026-11-08", 5, 60)
        assert (phase, day, done) == (fs.PHASE_INTERVENTION, 6, 5)

    def test_short_days_do_not_count_for_the_baseline(self):
        """Un día con 10 minutos de uso no describe la conducta habitual."""
        prev = {"2026-11-01": 90.0, "2026-11-02": 10.0, "2026-11-03": 90.0}
        phase, day, done = fs.decide_phase(prev, "2026-11-04", 3, 60)
        assert (phase, day, done) == (fs.PHASE_BASELINE, 4, 2)

    def test_today_never_changes_phase_mid_day(self):
        """Los minutos de hoy no cuentan: todo el día queda en una fase."""
        prev = {"2026-11-01": 90.0, "2026-11-02": 600.0}
        assert fs.decide_phase(prev, "2026-11-02", 2, 60)[0] == fs.PHASE_BASELINE

    def test_zero_baseline_days_disables_it(self):
        assert fs.decide_phase({}, "2026-11-01", 0, 60)[0] == \
            fs.PHASE_INTERVENTION

    def test_pending_report_is_the_last_previous_use_day(self):
        assert fs.pending_report_date(
            ["2026-11-01", "2026-11-02"], set(), "2026-11-03") == "2026-11-02"
        assert fs.pending_report_date(
            ["2026-11-01", "2026-11-02"], {"2026-11-02"}, "2026-11-03") is None
        assert fs.pending_report_date(["2026-11-03"], set(), "2026-11-03") is None


# ---------------------------------------------------------------------------
# Bitácora
# ---------------------------------------------------------------------------

class TestHistoryLoggerField:

    def test_old_database_gets_the_new_columns(self, tmp_path):
        path = tmp_path / "old.db"
        con = sqlite3.connect(path)
        con.executescript(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT "
            "NULL, ended_at REAL, notes TEXT);"
            "CREATE TABLE events (id INTEGER PRIMARY KEY, session_id TEXT, "
            "timestamp REAL, alert_type TEXT, metric_value REAL, threshold "
            "REAL, duration_sec REAL, message TEXT, created_at REAL);"
            "INSERT INTO sessions VALUES ('s1', 1.0, 2.0, 'antigua');")
        con.commit()
        con.close()
        HistoryLogger(db_path=str(path)).close()
        con = sqlite3.connect(path)
        ses = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        ev = {r[1] for r in con.execute("PRAGMA table_info(events)")}
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        n = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        con.close()
        assert {"study_phase", "app_version", "config_hash"} <= ses
        assert "notified" in ev and "self_reports" in tables and n == 1

    def test_session_metadata_is_stored(self, tmp_path):
        db = HistoryLogger(db_path=str(tmp_path / "h.db"))
        db.start_session(participant_code="P01", study_phase="linea_base",
                         app_version="9.9", config_hash="h1")
        db.close()
        con = sqlite3.connect(tmp_path / "h.db")
        row = con.execute("SELECT participant_code, study_phase, app_version,"
                          " config_hash FROM sessions").fetchone()
        con.close()
        assert row == ("P01", "linea_base", "9.9", "h1")

    def test_muted_alert_is_logged_as_not_notified(self, tmp_path):
        db = HistoryLogger(db_path=str(tmp_path / "h.db"))
        db.start_session()
        ev = AlertEvent(alert_type=AlertType.CERVICAL_ANGLE, metric_value=20,
                        threshold=15, duration_sec=5)
        db.log_event(ev, notified=False)
        db.log_event(ev)
        db.close()
        con = sqlite3.connect(tmp_path / "h.db")
        vals = [r[0] for r in con.execute("SELECT notified FROM events "
                                          "ORDER BY id")]
        con.close()
        assert vals == [0, 1]

    def test_minutes_by_date_exclude_pauses(self, tmp_path):
        """Un hueco > 5 s es la cámara apagada: no cuenta como uso."""
        path = tmp_path / "h.db"
        make_field_db(path, "P01", [
            {"fecha": "2026-11-02", "fase": "linea_base", "minutos": 30,
             "dev": 0}])
        con = sqlite3.connect(path)
        # Hueco de 10 min en mitad de la sesión
        con.execute("UPDATE metrics_log SET timestamp = timestamp + 600 "
                    "WHERE timestamp >= ?", (_ts("2026-11-02") + 900,))
        con.commit()
        con.close()
        db = HistoryLogger(db_path=str(path))
        mins = db.field_minutes_by_date("P01")
        db.close()
        assert mins["2026-11-02"] == pytest.approx(30, abs=0.1)

    def test_minutes_by_date_filters_participant_and_phase(self, tmp_path):
        path = tmp_path / "h.db"
        make_field_db(path, "P01", [
            {"fecha": "2026-11-02", "fase": "validacion", "minutos": 20,
             "dev": 0},
            {"fecha": "2026-11-03", "fase": "intervencion", "minutos": 20,
             "dev": 0}])
        db = HistoryLogger(db_path=str(path))
        assert set(db.field_minutes_by_date("P01")) == {"2026-11-03"}
        assert db.field_minutes_by_date("P99") == {}
        db.close()

    def test_self_reports(self, tmp_path):
        db = HistoryLogger(db_path=str(tmp_path / "h.db"))
        db.log_self_report("P01", "2026-11-03", 4, 6, 2)
        db.log_self_report("P01", "2026-11-04", None, None, None,
                           skipped=True)
        assert db.self_report_dates("P01") == {"2026-11-03", "2026-11-04"}
        assert db.self_report_dates("P02") == set()
        db.close()

    def test_backup_includes_uncommitted_wal_pages(self, tmp_path):
        """La copia para exportar incluye lo escrito con la app abierta."""
        db = HistoryLogger(db_path=str(tmp_path / "h.db"))
        db.start_session(participant_code="P01")
        for i in range(50):
            db.log_metrics(SensorMetrics(timestamp=float(i)), fps=20)
        copy = db.backup_to(tmp_path / "copia" / "h.db")
        db.close()
        con = sqlite3.connect(copy)
        assert con.execute("SELECT COUNT(*) FROM metrics_log").fetchone()[0] == 50
        con.close()


def test_config_hash_ignores_line_endings(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_bytes(b'{"x": 1}\n')
    b.write_bytes(b'{"x": 1}\r\n')
    assert config_hash(a) == config_hash(b) != ""
    assert config_hash(tmp_path / "nada.json") == ""


# ---------------------------------------------------------------------------
# Paquete que envía el participante
# ---------------------------------------------------------------------------

def test_bundle_contains_database_profile_config_and_readme(tmp_path):
    db = HistoryLogger(db_path=str(tmp_path / "h.db"))
    db.start_session(participant_code="P07", study_phase="linea_base")
    db.log_metrics(SensorMetrics(timestamp=time.time()), fps=20)
    prof = tmp_path / "estudio_local.json"
    fs.LocalProfile(participante="P07", modo=fs.MODE_FIELD).save(prof)
    cfg = tmp_path / "thresholds.json"
    cfg.write_text("{}", encoding="utf-8")

    z = create_bundle(db, prof, cfg, "P07", "1.1.0", dest_dir=tmp_path / "out")
    db.close()
    assert z.name.startswith("Estudio_P07_") and z.suffix == ".zip"
    with zipfile.ZipFile(z) as zf:
        assert set(zf.namelist()) == {"history.db", "estudio_local.json",
                                      "thresholds.json", "LEEME.txt"}
        readme = zf.read("LEEME.txt").decode("utf-8")
    assert "P07" in readme and "imagen" in readme
    assert not list((tmp_path / "out").glob("*.part"))


# ---------------------------------------------------------------------------
# Hilo de inferencia en modo campo
# ---------------------------------------------------------------------------

def _field_thread(tmp_path, profile, baseline_days=5):
    import src.ui.app_ui as app_ui
    from tests.test_export_study import _thread  # noqa: F401 (fixtures)
    from tests.test_integration_pipeline import (
        _FakeFaceEstimator, _FakePoseEstimator, _FakeVideoThread, _thresholds,
        NEUTRAL)
    th = _thresholds(tmp_path / "t.db",
                     postural={"cervical_mode": "reference",
                               "posture_calibration_sec": 0.3},
                     fatigue={"ear_threshold_mode": "adaptive",
                              "ear_calibration_sec": 0.3})
    th["calibration"] = {"countdown_sec": 0.1, "framing_grace_sec": 0.5,
                         "timeout_sec": 10.0}
    th["study"] = {"enabled": True, "field": {
        "baseline_days": baseline_days, "baseline_min_minutes": 60}}

    class _Feed:
        def encode(self, frame, fps=0.0):
            return "b64"

    orig = app_ui.PoseEstimator, app_ui.FaceEstimator
    app_ui.PoseEstimator = lambda **kw: _FakePoseEstimator(NEUTRAL)
    app_ui.FaceEstimator = lambda **kw: _FakeFaceEstimator(NEUTRAL)
    app_ui.PoseEstimator.REQUIRED_LANDMARKS = orig[0].REQUIRED_LANDMARKS
    app_ui.FaceEstimator.LEFT_EYE_EAR_INDICES = orig[1].LEFT_EYE_EAR_INDICES
    app_ui.FaceEstimator.RIGHT_EYE_EAR_INDICES = orig[1].RIGHT_EYE_EAR_INDICES
    alerts = []
    t = app_ui.InferenceThread(
        video_thread=_FakeVideoThread(), thresholds=th, state_queue=Queue(1),
        on_alert=alerts.append, video_feed=_Feed(), start_mode="preview",
        profile=profile, log_metrics_interval_sec=0.2)

    def restore():
        app_ui.PoseEstimator, app_ui.FaceEstimator = orig
    return t, restore, tmp_path / "t.db", alerts


def _wait(pred, timeout=5.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _run_session(t):
    t.start()
    t.request_calibration()
    assert _wait(lambda: t.mode == "ready")
    t.request_start()
    assert _wait(lambda: t.mode == "monitoring")


class TestFieldThread:

    def test_first_day_is_baseline_with_muted_alerts(self, tmp_path):
        prof = fs.LocalProfile(participante="P03", modo=fs.MODE_FIELD)
        t, restore, db_path, alerts = _field_thread(tmp_path, prof)
        try:
            _run_session(t)
            assert t.phase == fs.PHASE_BASELINE and t.alerts_muted
            assert t.day_number == 1
            assert t.condition == "", "en campo no hay condición"
            ev = AlertEvent(alert_type=AlertType.CERVICAL_ANGLE, metric_value=20,
                            threshold=15, duration_sec=5)
            t._handle_alert(ev)
            time.sleep(0.5)
        finally:
            t.stop(); t.join(timeout=5.0); restore()
        assert alerts == [], "en línea base la alerta no se muestra"
        con = sqlite3.connect(db_path)
        assert con.execute("SELECT study_phase, participant_code FROM "
                           "sessions").fetchone() == ("linea_base", "P03")
        assert con.execute("SELECT notified FROM events").fetchone() == (0,)
        con.close()

    def test_intervention_after_enough_previous_days(self, tmp_path):
        prev = _days(date.today() - timedelta(days=3), 2)
        make_field_db(tmp_path / "t.db", "P03", [
            {"fecha": d, "fase": "linea_base", "minutos": 61, "dev": 0}
            for d in prev])
        prof = fs.LocalProfile(participante="P03", modo=fs.MODE_FIELD)
        t, restore, _, alerts = _field_thread(tmp_path, prof,
                                              baseline_days=2)
        try:
            _run_session(t)
            assert t.phase == fs.PHASE_INTERVENTION and not t.alerts_muted
            assert t.day_number == 3
            t._handle_alert(AlertEvent(alert_type=AlertType.CERVICAL_ANGLE,
                                       metric_value=20, threshold=15,
                                       duration_sec=5))
        finally:
            t.stop(); t.join(timeout=5.0); restore()
        assert len(alerts) == 1

    def test_day_change_splits_the_session(self, tmp_path, monkeypatch):
        """Sin pulsar Salir, al cambiar de fecha empieza otra sesión."""
        import src.ui.app_ui as app_ui
        prof = fs.LocalProfile(participante="P06", modo=fs.MODE_FIELD)
        t, restore, db_path, _ = _field_thread(tmp_path, prof)
        try:
            _run_session(t)
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            monkeypatch.setattr(app_ui, "today_iso", lambda: tomorrow)
            t._last_day_check = -1e9
            assert _wait(lambda: t._session_date == tomorrow)
        finally:
            t.stop(); t.join(timeout=5.0); restore()
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT ended_at IS NOT NULL, study_phase FROM "
                           "sessions ORDER BY started_at").fetchall()
        con.close()
        assert len(rows) == 2 and rows[0][0] == 1
        assert {r[1] for r in rows} == {"linea_base"}

    def test_lab_mode_is_validation_with_free_condition(self, tmp_path):
        prof = fs.LocalProfile(participante="P04", modo=fs.MODE_LAB)
        t, restore, db_path, _ = _field_thread(tmp_path, prof)
        try:
            _run_session(t)
            assert t.phase == fs.PHASE_VALIDATION and t.condition == "libre"
        finally:
            t.stop(); t.join(timeout=5.0); restore()

    def test_metrics_are_logged_by_time_not_by_frames(self, tmp_path):
        """Con 0.2 s de cadencia, ~5 filas por segundo sea cual sea el FPS."""
        prof = fs.LocalProfile(participante="P05", modo=fs.MODE_FIELD)
        t, restore, db_path, _ = _field_thread(tmp_path, prof)
        try:
            _run_session(t)
            time.sleep(1.2)
        finally:
            t.stop(); t.join(timeout=5.0); restore()
        con = sqlite3.connect(db_path)
        ts = [r[0] for r in con.execute(
            "SELECT timestamp FROM metrics_log ORDER BY timestamp")]
        con.close()
        diffs = [b - a for a, b in zip(ts, ts[1:])]
        assert len(ts) >= 3
        assert min(diffs) >= 0.19

    def test_profile_fov_overrides_the_generic_one(self, tmp_path):
        prof = fs.LocalProfile(participante="P05", modo=fs.MODE_FIELD,
                               camera_hfov_deg=72.0)
        t, restore, _, _ = _field_thread(tmp_path, prof)
        try:
            assert t.hfov_deg == 72.0
            t.set_hfov(65.0)
            assert t.hfov_deg == 65.0
        finally:
            restore()


# ---------------------------------------------------------------------------
# Indicadores
# ---------------------------------------------------------------------------

def _rows(devs, t0=0.0, step=1.0, present=None):
    out = []
    for i, d in enumerate(devs):
        here = True if present is None else present[i]
        out.append({"timestamp": t0 + i * step, "cervical_deviation": d,
                    "pose_valid": 1 if here else 0,
                    "pose_detected": 1 if here else 0,
                    "face_detected": 1 if here else 0,
                    "shoulder_asymmetry": 2.0, "cervical_lateral": 1.0,
                    "distance_m": 0.6, "perclos": 0.05})
    return out


class TestIndicators:
    cfg = fa.FieldConfig()

    def test_durations_cap_gaps(self):
        rows = _rows([0, 0, 0])
        rows[2]["timestamp"] = 100.0
        assert fa.row_durations(rows, max_gap=5) == [1.0, 0.0, 1.0]

    def test_bands_and_percent_over_threshold(self):
        rows = _rows([5] * 60 + [25] * 30 + [35] * 10)
        ex_ = fa.exposure(rows, fa.row_durations(rows), self.cfg)
        assert ex_["pct_banda_menos_10"] == pytest.approx(60)
        assert ex_["pct_banda_20_30"] == pytest.approx(30)
        assert ex_["pct_banda_30_o_mas"] == pytest.approx(10)
        assert ex_["pct_desviado"] == pytest.approx(40)

    def test_episodes_and_sustained_count(self):
        devs = [0] * 10 + [20] * 3 + [0] * 10 + [20] * 12 + [0] * 5
        rows = _rows(devs)
        ex_ = fa.exposure(rows, fa.row_durations(rows), self.cfg)
        assert ex_["episodio_max_s"] == pytest.approx(12)
        assert ex_["episodios_sostenidos"] == 1, "el de 3 s no es sostenido"

    def test_gap_splits_an_episode(self):
        rows = _rows([20] * 10)
        for r in rows[5:]:
            r["timestamp"] += 60
        ex_ = fa.exposure(rows, fa.row_durations(rows), self.cfg)
        assert ex_["episodio_max_s"] == pytest.approx(5)

    def test_breaks_and_longest_sitting_stretch(self):
        present = [True] * 600 + [False] * 120 + [True] * 300
        rows = _rows([0] * len(present), present=present)
        br = fa.breaks_and_blocks(rows, fa.row_durations(rows), self.cfg)
        assert br["pausas"] == 1
        assert br["min_en_pausa"] == pytest.approx(2)
        assert br["tramo_max_sin_pausa_min"] == pytest.approx(10)
        assert br["bloque_continuo_max_min"] == pytest.approx(17)

    def test_short_absence_is_not_a_break(self):
        present = [True] * 100 + [False] * 30 + [True] * 100
        rows = _rows([0] * len(present), present=present)
        br = fa.breaks_and_blocks(rows, fa.row_durations(rows), self.cfg)
        assert br["pausas"] == 0
        assert br["tramo_max_sin_pausa_min"] == pytest.approx(200 / 60)

    def test_first_vs_last_hour(self):
        rows = _rows([0] * 3600 + [5] * 1800 + [20] * 3600)
        fl = fa.first_vs_last_hour(rows, fa.row_durations(rows), self.cfg)
        assert fl["pct_desviado_1a_hora"] == pytest.approx(0)
        assert fl["pct_desviado_ult_hora"] == pytest.approx(100, abs=0.1)

    def test_first_vs_last_hour_needs_two_hours(self):
        rows = _rows([0] * 3000)
        fl = fa.first_vs_last_hour(rows, fa.row_durations(rows), self.cfg)
        assert fl["pct_desviado_1a_hora"] is None

    def test_correction_stops_at_a_gap(self):
        rows = _rows([20, 20, 20, 0])
        rows[3]["timestamp"] = 3600.0
        ts = [r["timestamp"] for r in rows]
        assert fa.correction_after(0.5, 15, ts, rows, max_gap=5) is None
        assert fa.correction_after(0.5, 15, ts, rows) == pytest.approx(3599.5)


# ---------------------------------------------------------------------------
# Exportación completa del estudio de campo
# ---------------------------------------------------------------------------

def _two_phase_db(path, code="P01", worse_in_baseline=True):
    d = _days(date(2026, 11, 2), 5)
    bad, good = (30, 0) if worse_in_baseline else (0, 30)

    def dev(pct):
        return lambda s: 20.0 if (s % 100) < pct else 0.0
    make_field_db(path, code, [
        {"fecha": d[0], "fase": "linea_base", "minutos": 70, "dev": dev(bad),
         "eventos": [(600, "cervical_angle", 0)]},
        {"fecha": d[1], "fase": "linea_base", "minutos": 70, "dev": dev(bad)},
        {"fecha": d[2], "fase": "intervencion", "minutos": 70,
         "dev": dev(good), "eventos": [(600, "cervical_angle", 1)]},
        {"fecha": d[3], "fase": "intervencion", "minutos": 70,
         "dev": dev(good)},
        {"fecha": d[4], "fase": "intervencion", "minutos": 20, "dev": 0},
    ])
    db = HistoryLogger(db_path=str(path))
    db.log_self_report(code, d[0], 6, 5, 3)
    db.log_self_report(code, d[3], 2, 2, 1)
    db.close()
    return d


class TestFieldExport:

    def test_daily_and_participant_summaries(self, tmp_path, field_config):
        d = _two_phase_db(tmp_path / "a.db")
        res = ex.export([tmp_path / "a.db"], tmp_path / "out", field_config)
        days = {x["fecha"]: x for x in res["field"]["days"]}
        assert days[d[0]]["fase"] == "linea_base"
        assert days[d[0]]["dia_valido"] == "si"
        assert days[d[4]]["dia_valido"] == "no", "20 min < 60 min"
        assert days[d[0]]["pct_desviado"] == pytest.approx(30, abs=1)
        assert days[d[0]]["alertas_mostradas"] == 0
        assert days[d[2]]["alertas_mostradas"] == 1
        assert days[d[0]]["dolor_cuello"] == 6

        s = res["field"]["participants"][0]
        assert s["dias_validos_linea_base"] == 2
        assert s["dias_validos_intervencion"] == 2
        assert s["valido_para_analisis"] == "si"
        assert s["pct_desviado_cambio"] == pytest.approx(-30, abs=1)
        assert s["dolor_cuello_cambio"] == pytest.approx(-4)

        out = tmp_path / "out"
        for f in ("resumen_diario.csv", "resumen_participantes.csv",
                  "perfil_horario.csv", "detalle/metricas_P01.csv",
                  "reportes/reporte_P01.html"):
            assert (out / f).exists(), f
        html = (out / "reportes" / "reporte_P01.html").read_text("utf-8")
        assert "P01" in html and "no es un diagnóstico" in html
        # Las filas de campo no inflan metricas_detalle.csv
        with open(out / "metricas_detalle.csv", encoding="utf-8-sig") as f:
            assert len(list(csv.DictReader(f))) == 0

    def test_repeated_submissions_are_counted_once(self, tmp_path,
                                                   field_config):
        """El zip del día 7 y el final repiten sesiones: no deben duplicarse."""
        _two_phase_db(tmp_path / "a.db")
        db = HistoryLogger(db_path=str(tmp_path / "a.db"))
        z1 = create_bundle(db, tmp_path / "x.json", field_config, "P01",
                           "1.1.0", dest_dir=tmp_path / "recibidos")
        z2 = create_bundle(db, tmp_path / "x.json", field_config, "P01",
                           "1.1.0", dest_dir=tmp_path / "tmpz")
        db.close()
        z2 = z2.rename(tmp_path / "recibidos" / "Estudio_P01_final.zip")

        res = ex.export([tmp_path / "recibidos"], tmp_path / "out",
                        field_config, reports=False)
        assert res["duplicated_sessions"] == 5
        assert len(res["field"]["days"]) == 5
        assert res["field"]["participants"][0]["dias_con_autorreporte"] == 2

    def test_mixed_configurations_are_flagged(self, tmp_path, field_config,
                                              capsys):
        make_field_db(tmp_path / "a.db", "P01", [
            {"fecha": "2026-11-02", "fase": "linea_base", "minutos": 5,
             "dev": 0, "hash": "aaa"},
            {"fecha": "2026-11-03", "fase": "intervencion", "minutos": 5,
             "dev": 0, "hash": "bbb"}])
        res = ex.export([tmp_path / "a.db"], tmp_path / "o", field_config,
                        reports=False)
        assert res["field"]["participants"][0]["configuracion_unica"] == "NO"
        ex.print_summary(res)
        assert "configuraciones distintas" in capsys.readouterr().out

    def test_lab_only_database_produces_no_field_files(self, tmp_path,
                                                       field_config):
        make_field_db(tmp_path / "a.db", "P01", [
            {"fecha": "2026-11-02", "fase": "validacion", "minutos": 5,
             "dev": 0}])
        res = ex.export([tmp_path / "a.db"], tmp_path / "o", field_config)
        assert res["field"]["participants"] == []
        assert not (tmp_path / "o" / "resumen_diario.csv").exists()


def test_low_fps_rows_are_not_counted_as_absence(tmp_path, field_config):
    """Filas a ~1 FPS (cámara compartida con Meet) no son pausas activas."""
    make_field_db(tmp_path / "a.db", "P01", [
        {"fecha": "2026-11-02", "fase": "linea_base", "minutos": 30, "dev": 0,
         "ausencia": (600, 300)}])
    con = sqlite3.connect(tmp_path / "a.db")
    t0 = _ts("2026-11-02")
    con.execute("UPDATE metrics_log SET fps = 25.0")
    con.execute("UPDATE metrics_log SET fps = 1.0 WHERE timestamp BETWEEN ? AND ?",
                (t0 + 600, t0 + 900))
    con.commit()
    con.close()
    res = ex.export([tmp_path / "a.db"], tmp_path / "o", field_config,
                    reports=False)
    day = res["field"]["days"][0]
    assert day["pausas"] == 0
    assert day["minutos"] == pytest.approx(25, abs=0.2)

