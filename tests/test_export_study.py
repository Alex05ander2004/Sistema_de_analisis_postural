"""
test_export_study.py
--------------------
Modo estudio (código de participante y condición del protocolo) y exportación
de resultados.

Para analizar un estudio con 20–30 personas hacen falta tres cosas que la
bitácora no tenía: saber de QUIÉN es cada sesión, saber QUÉ condición estaba
haciendo el participante en cada instante (sin eso no hay verdad de terreno y
no se puede calcular la precisión), y poder sacar todo a CSV.

Las bases de estas pruebas se crean con el esquema real (HistoryLogger).

Autor: Tesis Huisa Perez, UNSA 2026
"""

import csv
import math
import sqlite3
import sys
import time
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.fusion.fusion_fsm import AlertEvent, AlertType, SensorMetrics
from src.storage.history_logger import HistoryLogger
from src.tools import export_study as ex


# ---------------------------------------------------------------------------
# Configuración y base sintética
# ---------------------------------------------------------------------------

CONFIG = {
    "postural": {"cervical_deviation_max_deg": 15.0},
    "study": {"enabled": True, "conditions": [
        {"id": "correcta", "label": "Correcta", "expected_alert": None},
        {"id": "cabeza_adelantada", "label": "Cabeza adelantada",
         "expected_alert": "cervical_angle"},
        {"id": "ojos_cerrados", "label": "Ojos cerrados",
         "expected_alert": ["eye_fatigue", "drowsiness"]},
        {"id": "libre", "label": "Libre", "expected_alert": None,
         "scored": False},
    ]},
}


@pytest.fixture
def config_path(tmp_path):
    import json
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps(CONFIG), encoding="utf-8")
    return p


def make_db(path, code, plan, start=1_000_000.0, step=1.3, events=()):
    """
    Crea una sesión. `plan` = lista de (condición, segundos, desviación).
    `events` = lista de (segundo_relativo, tipo, condición, umbral).
    Devuelve el instante de inicio.
    """
    db = HistoryLogger(db_path=str(path))
    db.start_session(notes="prueba", participant_code=code)
    t = start
    for cond, secs, dev in plan:
        for _ in range(int(secs / step)):
            m = SensorMetrics(
                timestamp=t, cervical_angle=40 + dev, cervical_deviation=dev,
                cervical_baseline=40.0, ear_avg=0.25, ear_threshold=0.16,
                distance_m=0.6, pose_valid=True, condition_label=cond)
            db.log_metrics(m, fps=24.0)
            t += step
    for rel, tipo, cond, thr in events:
        ev = AlertEvent(alert_type=AlertType(tipo), metric_value=20.0,
                        threshold=thr, duration_sec=5.0,
                        timestamp=start + rel, message="m")
        db.log_event(ev, condition=cond)
    db.end_session()
    db.close()
    # En uso real el inicio y el fin de la sesión comparten reloj con las
    # filas; aquí las filas son sintéticas, así que se alinean explícitamente.
    con = sqlite3.connect(path)
    con.execute("UPDATE sessions SET started_at = ?, ended_at = ?",
                (start, t))
    con.commit()
    con.close()
    return start


# ---------------------------------------------------------------------------
# Esquema y migración
# ---------------------------------------------------------------------------

class TestSchema:

    def test_participant_and_condition_are_stored(self, tmp_path):
        db_path = tmp_path / "a.db"
        make_db(db_path, "P01", [("correcta", 10, 0.0)],
                events=[(5, "cervical_angle", "cabeza_adelantada", 15.0)])
        con = sqlite3.connect(db_path)
        assert con.execute("SELECT participant_code FROM sessions").fetchone()[0] == "P01"
        assert con.execute(
            "SELECT DISTINCT condition_label FROM metrics_log").fetchone()[0] == "correcta"
        assert con.execute(
            "SELECT condition_label FROM events").fetchone()[0] == "cabeza_adelantada"
        con.close()

    def test_old_database_is_migrated_in_place(self, tmp_path):
        """Una base de antes del modo estudio se actualiza sin perder datos."""
        db_path = tmp_path / "vieja.db"
        con = sqlite3.connect(db_path)
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL,
                                   ended_at REAL, notes TEXT);
            CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, timestamp REAL NOT NULL,
                alert_type TEXT NOT NULL, metric_value REAL, threshold REAL,
                duration_sec REAL, message TEXT,
                created_at REAL NOT NULL DEFAULT 0);
            CREATE TABLE metrics_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, timestamp REAL NOT NULL,
                cervical_angle REAL, shoulder_asymmetry REAL, ear_avg REAL,
                mouth_opening REAL, fps REAL, face_detected INTEGER,
                pose_detected INTEGER);
            INSERT INTO sessions VALUES ('s1', 1.0, 2.0, 'antigua');
            INSERT INTO metrics_log (session_id, timestamp, cervical_angle)
                VALUES ('s1', 1.5, 30.0);
        """)
        con.commit()
        con.close()

        HistoryLogger(db_path=str(db_path)).close()

        con = sqlite3.connect(db_path)
        cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        assert "participant_code" in cols
        assert con.execute("SELECT COUNT(*) FROM metrics_log").fetchone()[0] == 1
        con.close()

    def test_legacy_database_can_be_exported(self, tmp_path, config_path):
        """El exportador lee también bases sin las columnas nuevas."""
        db_path = tmp_path / "vieja.db"
        con = sqlite3.connect(db_path)
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL,
                                   ended_at REAL, notes TEXT);
            CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, timestamp REAL NOT NULL,
                alert_type TEXT NOT NULL, metric_value REAL, threshold REAL,
                duration_sec REAL, message TEXT);
            CREATE TABLE metrics_log (id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, timestamp REAL NOT NULL,
                cervical_angle REAL, ear_avg REAL, fps REAL);
            INSERT INTO sessions VALUES ('s1', 1.0, 61.0, 'antigua');
            INSERT INTO metrics_log (session_id, timestamp, cervical_angle, fps)
                VALUES ('s1', 2.0, 30.0, 20.0);
        """)
        con.commit()
        con.close()

        res = ex.export([db_path], tmp_path / "out", config_path)
        assert res["sessions"][0]["participante"] == ex.NO_CODE


# ---------------------------------------------------------------------------
# Estadística y puntuación
# ---------------------------------------------------------------------------

class TestWilson:

    def test_known_value(self):
        lo, hi = ex.wilson_interval(9, 10)
        assert lo == pytest.approx(0.5958, abs=1e-3)
        assert hi == pytest.approx(0.9821, abs=1e-3)

    def test_empty_sample(self):
        assert ex.wilson_interval(0, 0) == (None, None)

    def test_all_correct_does_not_reach_a_degenerate_interval(self):
        lo, hi = ex.wilson_interval(30, 30)
        assert hi == pytest.approx(1.0, abs=1e-9) and 0.85 < lo < 1.0

    def test_narrows_with_more_samples(self):
        """Por esto importa el número de tramos y no solo de personas."""
        a = ex.wilson_interval(18, 20)
        b = ex.wilson_interval(180, 200)
        assert (b[1] - b[0]) < (a[1] - a[0])


class TestSegmentScoring:

    def _score(self, tmp_path, config_path, plan, events, **kw):
        db_path = tmp_path / "s.db"
        make_db(db_path, "P01", plan, events=events)
        res = ex.export([db_path], tmp_path / "out", config_path, **kw)
        return res["segments"]

    def test_true_positive_with_latency(self, tmp_path, config_path):
        segs = self._score(
            tmp_path, config_path,
            [("cabeza_adelantada", 30, 20.0)],
            [(8, "cervical_angle", "cabeza_adelantada", 15.0)])
        assert segs[0]["resultado"] == "VP"
        assert float(segs[0]["latencia_s"]) == pytest.approx(8.0)

    def test_false_negative_when_no_alert(self, tmp_path, config_path):
        segs = self._score(tmp_path, config_path,
                           [("cabeza_adelantada", 30, 20.0)], [])
        assert segs[0]["resultado"] == "FN"

    def test_true_negative_on_correct_posture(self, tmp_path, config_path):
        segs = self._score(tmp_path, config_path, [("correcta", 30, 0.0)], [])
        assert segs[0]["resultado"] == "VN"

    def test_false_positive_on_correct_posture(self, tmp_path, config_path):
        segs = self._score(
            tmp_path, config_path, [("correcta", 30, 0.0)],
            [(10, "shoulder_asymmetry", "correcta", 10.0)])
        assert segs[0]["resultado"] == "FP"

    def test_wrong_alert_type_is_a_miss(self, tmp_path, config_path):
        """Una alerta de otro tipo no cuenta como acierto."""
        segs = self._score(
            tmp_path, config_path, [("cabeza_adelantada", 30, 20.0)],
            [(8, "yawn", "cabeza_adelantada", 0.45)])
        assert segs[0]["resultado"] == "FN"

    def test_any_accepted_alert_type_counts(self, tmp_path, config_path):
        segs = self._score(
            tmp_path, config_path, [("ojos_cerrados", 30, 0.0)],
            [(10, "drowsiness", "ojos_cerrados", 0.15)])
        assert segs[0]["resultado"] == "VP"

    def test_short_segment_is_not_scored(self, tmp_path, config_path):
        """No hay tiempo físico de alertar en 8 s con una ventana de 5 s + filtro."""
        segs = self._score(tmp_path, config_path,
                           [("cabeza_adelantada", 8, 20.0)], [])
        assert segs[0]["resultado"] == ""
        assert "<" in segs[0]["motivo_no_puntuado"]

    def test_free_use_is_not_scored(self, tmp_path, config_path):
        segs = self._score(tmp_path, config_path, [("libre", 60, 0.0)],
                           [(20, "cervical_angle", "libre", 15.0)])
        assert segs[0]["resultado"] == ""

    def test_alert_just_after_the_segment_is_attributed_to_it(self, tmp_path,
                                                              config_path):
        """El filtro y el temporizador retrasan la alerta."""
        segs = self._score(
            tmp_path, config_path,
            [("cabeza_adelantada", 20, 20.0), ("correcta", 30, 0.0)],
            [(23, "cervical_angle", "correcta", 15.0)])
        assert segs[0]["resultado"] == "VP"

    def test_consecutive_segments_split_on_condition_change(self, tmp_path,
                                                            config_path):
        segs = self._score(tmp_path, config_path,
                           [("correcta", 20, 0.0), ("cabeza_adelantada", 20, 20.0),
                            ("correcta", 20, 0.0)], [])
        assert [s["condicion"] for s in segs] == [
            "correcta", "cabeza_adelantada", "correcta"]

    def test_unlabelled_rows_are_not_segments(self, tmp_path, config_path):
        segs = self._score(tmp_path, config_path, [("", 30, 0.0)], [])
        assert segs == []


class TestConfusionMatrix:

    def test_global_accuracy_and_per_condition(self, tmp_path, config_path):
        db_path = tmp_path / "m.db"
        # 4 tramos con alerta esperada: 3 aciertos y 1 fallo; 2 correctos: 1 FP
        plan = [("cabeza_adelantada", 30, 20.0), ("correcta", 30, 0.0)] * 2
        plan += [("cabeza_adelantada", 30, 20.0), ("correcta", 30, 0.0)]
        plan += [("cabeza_adelantada", 30, 20.0)]
        # tiempos (s) de cada tramo con 30 s: 0,30,60,90,120,150,180
        events = [(5, "cervical_angle", "", 15.0),     # tramo 0: VP
                  (65, "cervical_angle", "", 15.0),    # tramo 2: VP
                  (125, "cervical_angle", "", 15.0),   # tramo 4: VP
                  (100, "cervical_angle", "", 15.0)]   # tramo 3 (correcta): FP
        make_db(db_path, "P01", plan, events=events)
        res = ex.export([db_path], tmp_path / "out", config_path)
        by = {c["condicion"]: c for c in res["confusion"]}

        assert by["cabeza_adelantada"]["VP"] == 3
        assert by["cabeza_adelantada"]["FN"] == 1
        assert by["cabeza_adelantada"]["metrica"] == "sensibilidad"
        assert by["correcta"]["VN"] == 2 and by["correcta"]["FP"] == 1
        assert by["correcta"]["metrica"] == "especificidad"
        g = by["GLOBAL"]
        assert g["tramos"] == 7
        assert float(g["valor_pct"]) == pytest.approx(100 * 5 / 7, abs=0.1)
        assert float(g["ic95_inf_pct"]) < float(g["valor_pct"]) < float(g["ic95_sup_pct"])


# ---------------------------------------------------------------------------
# Resumen por participante
# ---------------------------------------------------------------------------

class TestSessionSummary:

    def test_per_hour_rate_and_halves(self, tmp_path, config_path):
        db_path = tmp_path / "r.db"
        make_db(db_path, "P07", [("libre", 600, 0.0)],
                events=[(60, "cervical_angle", "libre", 15.0),
                        (120, "yawn", "libre", 0.45),
                        (500, "cervical_angle", "libre", 15.0)])
        s = ex.export([db_path], tmp_path / "out", config_path)["sessions"][0]
        assert s["participante"] == "P07"
        assert s["alertas_total"] == 3
        assert s["alertas_cervical"] == 2 and s["alertas_bostezo"] == 1
        assert s["alertas_1a_mitad"] == 2 and s["alertas_2a_mitad"] == 1
        assert float(s["alertas_por_hora"]) == pytest.approx(3 / (600 / 3600), rel=0.05)

    def test_percentage_of_time_above_threshold(self, tmp_path, config_path):
        db_path = tmp_path / "p.db"
        make_db(db_path, "P02", [("libre", 130, 5.0), ("libre", 130, 20.0)])
        s = ex.export([db_path], tmp_path / "out", config_path)["sessions"][0]
        assert float(s["pct_tiempo_desviado"]) == pytest.approx(50.0, abs=2.0)
        assert float(s["delta_theta_media"]) == pytest.approx(12.5, abs=0.5)

    def test_calibration_values_are_reported(self, tmp_path, config_path):
        db_path = tmp_path / "c.db"
        make_db(db_path, "P03", [("libre", 30, 0.0)])
        s = ex.export([db_path], tmp_path / "out", config_path)["sessions"][0]
        assert float(s["theta_neutro_deg"]) == 40.0
        assert float(s["umbral_ear"]) == 0.16

    def test_session_without_code_is_flagged(self, tmp_path, config_path):
        db_path = tmp_path / "n.db"
        make_db(db_path, "", [("libre", 30, 0.0)])
        s = ex.export([db_path], tmp_path / "out", config_path)["sessions"][0]
        assert s["participante"] == ex.NO_CODE


class TestCorrectionTime:
    """Indicador de prevención del Capítulo I: ¿el usuario corrige tras el aviso?"""

    def test_seconds_until_deviation_drops_below_threshold(self):
        ev = {"alert_type": "cervical_angle", "timestamp": 100.0, "threshold": 15.0}
        rows = [{"timestamp": t, "cervical_deviation": d} for t, d in
                [(101, 22), (102.3, 19), (103.6, 14), (104.9, 3)]]
        assert ex.correction_seconds(ev, rows) == pytest.approx(3.6)

    def test_none_if_never_corrected(self):
        ev = {"alert_type": "cervical_angle", "timestamp": 100.0, "threshold": 15.0}
        rows = [{"timestamp": 101 + i, "cervical_deviation": 25} for i in range(5)]
        assert ex.correction_seconds(ev, rows) is None

    def test_only_for_cervical_alerts(self):
        ev = {"alert_type": "yawn", "timestamp": 100.0, "threshold": 0.45}
        assert ex.correction_seconds(
            ev, [{"timestamp": 101, "cervical_deviation": 0}]) is None

    def test_rows_before_the_alert_are_ignored(self):
        ev = {"alert_type": "cervical_angle", "timestamp": 100.0, "threshold": 15.0}
        rows = [{"timestamp": 99, "cervical_deviation": 0},
                {"timestamp": 103, "cervical_deviation": 2}]
        assert ex.correction_seconds(ev, rows) == pytest.approx(3.0)

    def test_median_reported_in_the_summary(self, tmp_path, config_path):
        db_path = tmp_path / "k.db"
        make_db(db_path, "P04",
                [("libre", 60, 20.0), ("libre", 60, 0.0)],
                events=[(10, "cervical_angle", "libre", 15.0)])
        s = ex.export([db_path], tmp_path / "out", config_path)["sessions"][0]
        assert s["alertas_cervicales_corregidas"] == 1
        assert 45 < float(s["correccion_mediana_s"]) < 60


# ---------------------------------------------------------------------------
# Archivos y varias bases
# ---------------------------------------------------------------------------

class TestFiles:

    def test_all_five_files_are_written_and_readable(self, tmp_path, config_path):
        db_path = tmp_path / "f.db"
        make_db(db_path, "P01",
                [("correcta", 30, 0.0), ("cabeza_adelantada", 30, 20.0)],
                events=[(40, "cervical_angle", "cabeza_adelantada", 15.0)])
        out = tmp_path / "out"
        ex.export([db_path], out, config_path)
        for name in ("resumen_sesiones", "segmentos", "matriz_confusion",
                     "eventos", "metricas_detalle"):
            f = out / f"{name}.csv"
            assert f.exists(), name
            with open(f, encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            assert rows, f"{name}.csv vacío"

    def test_excel_friendly_encoding(self, tmp_path, config_path):
        """BOM utf-8: Excel en español abre bien tildes y la θ."""
        db_path = tmp_path / "e.db"
        make_db(db_path, "P01", [("correcta", 30, 0.0)])
        out = tmp_path / "out"
        ex.export([db_path], out, config_path)
        assert (out / "resumen_sesiones.csv").read_bytes().startswith(b"\xef\xbb\xbf")

    def test_merges_several_databases(self, tmp_path, config_path):
        """Escenario de uso en el PC de cada participante: una base por equipo."""
        a, b = tmp_path / "pc1.db", tmp_path / "pc2.db"
        make_db(a, "P01", [("libre", 30, 0.0)])
        make_db(b, "P02", [("libre", 30, 0.0)])
        res = ex.export([a, b], tmp_path / "out", config_path)
        assert {s["participante"] for s in res["sessions"]} == {"P01", "P02"}
        assert {s["fuente"] for s in res["sessions"]} == {"pc1.db", "pc2.db"}

    def test_missing_database_is_reported(self, tmp_path, config_path):
        assert ex.main(["--db", str(tmp_path / "no_existe.db"),
                        "--output-dir", str(tmp_path / "o"),
                        "--config", str(config_path)]) == 1

    def test_command_line_end_to_end(self, tmp_path, config_path, capsys):
        db_path = tmp_path / "cli.db"
        make_db(db_path, "P01",
                [("cabeza_adelantada", 30, 20.0), ("correcta", 30, 0.0)],
                events=[(8, "cervical_angle", "cabeza_adelantada", 15.0)])
        code = ex.main(["--db", str(db_path), "--output-dir", str(tmp_path / "o"),
                        "--config", str(config_path)])
        out = capsys.readouterr().out
        assert code == 0
        assert "GLOBAL" in out and "P01" in out

    def test_open_session_is_flagged(self, tmp_path, config_path, capsys):
        db_path = tmp_path / "ab.db"
        db = HistoryLogger(db_path=str(db_path))
        db.start_session(notes="x", participant_code="P09")
        db.log_metrics(SensorMetrics(timestamp=10.0), fps=20)
        db.close()                      # close() cierra la sesión: la reabrimos
        con = sqlite3.connect(db_path)
        con.execute("UPDATE sessions SET ended_at = NULL")
        con.commit()
        con.close()
        res = ex.export([db_path], tmp_path / "o", config_path)
        ex.print_summary(res)
        assert "sin cerrar" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Hilo de inferencia: código obligatorio y etiquetas
# ---------------------------------------------------------------------------

def _thread(tmp_path, study_enabled=True):
    import src.ui.app_ui as app_ui
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
    th["study"] = {"enabled": study_enabled}

    class _Feed:
        def encode(self, frame, fps=0.0):
            return "b64"

    orig = app_ui.PoseEstimator, app_ui.FaceEstimator
    app_ui.PoseEstimator = lambda **kw: _FakePoseEstimator(NEUTRAL)
    app_ui.FaceEstimator = lambda **kw: _FakeFaceEstimator(NEUTRAL)
    app_ui.PoseEstimator.REQUIRED_LANDMARKS = orig[0].REQUIRED_LANDMARKS
    app_ui.FaceEstimator.LEFT_EYE_EAR_INDICES = orig[1].LEFT_EYE_EAR_INDICES
    app_ui.FaceEstimator.RIGHT_EYE_EAR_INDICES = orig[1].RIGHT_EYE_EAR_INDICES
    t = app_ui.InferenceThread(
        video_thread=_FakeVideoThread(), thresholds=th, state_queue=Queue(1),
        on_alert=lambda e: None, video_feed=_Feed(), log_metrics_every_n=3,
        start_mode="preview")

    def restore():
        app_ui.PoseEstimator, app_ui.FaceEstimator = orig
    return t, restore, tmp_path / "t.db"


def _wait(pred, timeout=5.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class TestStudyMode:

    def test_code_is_normalised(self, tmp_path):
        t, restore, _ = _thread(tmp_path)
        try:
            t.set_participant(" p 01 ")
            assert t.participant == "P01"
        finally:
            restore()

    def test_cannot_start_without_a_code(self, tmp_path):
        """Sin código no se sabría de quién es la sesión."""
        t, restore, _ = _thread(tmp_path)
        try:
            t.start()
            t.request_calibration()
            assert _wait(lambda: t.mode == "ready")
            assert not t.can_start
            t.request_start()
            time.sleep(0.3)
            assert t.mode == "ready"

            t.set_participant("P05")
            assert t.can_start
            t.request_start()
            assert _wait(lambda: t.mode == "monitoring")
        finally:
            t.stop(); t.join(timeout=5.0); restore()

    def test_study_disabled_needs_no_code(self, tmp_path):
        t, restore, _ = _thread(tmp_path, study_enabled=False)
        try:
            t.start()
            t.request_calibration()
            assert _wait(lambda: t.mode == "ready")
            assert t.can_start
        finally:
            t.stop(); t.join(timeout=5.0); restore()

    def test_session_and_rows_carry_participant_and_condition(self, tmp_path):
        t, restore, db_path = _thread(tmp_path)
        try:
            t.start()
            t.set_participant("P11")
            t.request_calibration()
            assert _wait(lambda: t.mode == "ready")
            t.request_start()
            assert _wait(lambda: t.mode == "monitoring")
            assert t.condition == "libre", "arranca en uso libre"

            t.set_condition("cabeza_adelantada")
            time.sleep(0.8)
        finally:
            t.stop(); t.join(timeout=5.0); restore()

        con = sqlite3.connect(db_path)
        assert con.execute(
            "SELECT participant_code FROM sessions").fetchone()[0] == "P11"
        labels = {r[0] for r in con.execute(
            "SELECT DISTINCT condition_label FROM metrics_log")}
        con.close()
        assert "cabeza_adelantada" in labels

    def test_each_participant_gets_their_own_session(self, tmp_path):
        """Recalibrar entre participantes separa las sesiones."""
        t, restore, db_path = _thread(tmp_path)
        try:
            t.start()
            for code in ("P01", "P02"):
                t.set_participant(code)
                t.request_calibration()
                assert _wait(lambda: t.mode == "ready")
                t.request_start()
                assert _wait(lambda: t.mode == "monitoring")
                time.sleep(0.3)
        finally:
            t.stop(); t.join(timeout=5.0); restore()

        con = sqlite3.connect(db_path)
        codes = [r[0] for r in con.execute(
            "SELECT participant_code FROM sessions ORDER BY started_at")]
        con.close()
        assert codes == ["P01", "P02"]


# ---------------------------------------------------------------------------
# Calibración del FOV (una vez por cámara)
# ---------------------------------------------------------------------------

class TestFovCalibration:

    def test_known_case(self):
        """60° con lectura 0.79 m cuando la realidad es 0.60 m → ~74.4°."""
        from src.tools.calibrar_fov import corrected_hfov
        assert corrected_hfov(60.0, 0.79, 0.60) == pytest.approx(74.4, abs=0.2)

    def test_no_change_when_reading_is_right(self):
        from src.tools.calibrar_fov import corrected_hfov
        assert corrected_hfov(60.0, 0.60, 0.60) == pytest.approx(60.0)

    def test_closes_the_loop_with_the_real_distance_model(self):
        """
        Aplicando el FOV corregido a estimate_distance_m, la distancia estimada
        coincide con la real: comprueba la fórmula contra el modelo de la app.
        """
        from src.tools.calibrar_fov import corrected_hfov
        from src.vision.geometry import estimate_distance_m

        class LM:
            def __init__(self, x, y):
                self.x, self.y, self.z, self.visibility = x, y, 0.0, 1.0

        lms = [LM(0.5, 0.5) for _ in range(33)]
        lms[11] = LM(0.30, 0.6)
        lms[12] = LM(0.70, 0.6)               # hombros a 256 px
        leida = estimate_distance_m(lms, (640, 480), hfov_deg=60.0)
        nuevo = corrected_hfov(60.0, leida, 0.60)
        assert estimate_distance_m(lms, (640, 480), hfov_deg=nuevo) == pytest.approx(0.60, rel=1e-3)

    @pytest.mark.parametrize("est, real", [(0, 0.6), (0.6, 0), (-1, 0.6)])
    def test_rejects_non_positive_distances(self, est, real):
        from src.tools.calibrar_fov import corrected_hfov
        with pytest.raises(ValueError):
            corrected_hfov(60.0, est, real)

    def test_command_line(self, capsys):
        from src.tools.calibrar_fov import main
        assert main(["--estimada", "0.79", "--real", "0.60"]) == 0
        assert "camera.horizontal_fov_deg" in capsys.readouterr().out

    def test_warns_when_result_is_implausible(self, capsys):
        from src.tools.calibrar_fov import main
        main(["--estimada", "2.0", "--real", "0.60"])
        assert "AVISO" in capsys.readouterr().out
