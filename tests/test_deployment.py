"""
test_deployment.py
------------------
Lo necesario para entregar la app a los participantes: rutas del ejecutable
(datos fuera de la carpeta del programa), log a archivo, logs dentro del
.zip y el veredicto de la prueba de jornada larga.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import logging
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src import paths
from src.storage.history_logger import HistoryLogger
from src.study.data_bundle import create_bundle
from src.tools import soak_monitor as sm


class TestPaths:

    def test_source_mode_uses_the_project_data_folder(self):
        assert paths.data_file("data/history.db") == \
            PROJECT_ROOT / "data" / "history.db"
        assert paths.resource_root() == PROJECT_ROOT

    def test_frozen_mode_writes_to_localappdata(self, tmp_path, monkeypatch):
        """El .exe no debe escribir en su propia carpeta (solo lectura)."""
        monkeypatch.setattr(paths, "FROZEN", True)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        d = paths.data_dir()
        assert d == tmp_path / paths.APP_DIR_NAME and d.is_dir()
        assert paths.data_file("data/history.db") == d / "history.db"

    def test_frozen_resources_come_from_the_bundle(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "FROZEN", True)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert paths.resource_root() == tmp_path

    def test_file_logging_is_installed_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
        root = logging.getLogger()
        before = list(root.handlers)
        try:
            p1 = paths.setup_file_logging()
            p2 = paths.setup_file_logging()
            assert p1 == p2 == tmp_path / "logs" / "app.log"
            added = [h for h in root.handlers if h not in before]
            assert len(added) == 1
            logging.getLogger("x").warning("hola registro")
            added[0].flush()
            assert "hola registro" in p1.read_text(encoding="utf-8")
        finally:
            for h in root.handlers[:]:
                if h not in before:
                    root.removeHandler(h)
                    h.close()


def test_bundle_includes_logs(tmp_path):
    db = HistoryLogger(db_path=str(tmp_path / "h.db"))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "app.log").write_text("Cámara: ok → reconnecting", "utf-8")
    (logs / "app.log.1").write_text("anterior", "utf-8")
    z = create_bundle(db, tmp_path / "no.json", tmp_path / "no.json", "P01",
                      "1.1.0", dest_dir=tmp_path / "out", log_dir=logs)
    db.close()
    with zipfile.ZipFile(z) as zf:
        assert {"logs/app.log", "logs/app.log.1"} <= set(zf.namelist())


class TestSoakVerdict:

    def _rows(self, rss, step_min=1.0, cpu=30.0):
        return [{"min": i * step_min, "rss_mb": r, "cpu_pct": cpu,
                 "threads": 20, "db_mb": 0.01 * i} for i, r in enumerate(rss)]

    def test_slope(self):
        assert sm.slope_per_hour([0, 60, 120], [100, 110, 120]) == \
            pytest.approx(10.0)
        assert sm.slope_per_hour([0, 1], [1, 2]) is None

    def test_stable_memory_is_ok(self):
        rows = self._rows([300 + (i % 3) for i in range(240)])
        text = "\n".join(sm.verdict(rows, None))
        assert "[OK]" in text and "FALLO" not in text

    def test_growing_memory_is_flagged(self):
        rows = self._rows([300 + i for i in range(240)])     # 60 MB/h
        assert "posible fuga" in "\n".join(sm.verdict(rows, None))

    def test_warmup_growth_is_ignored(self):
        """Cargar los modelos sube la memoria al principio: no es fuga."""
        rss = [100 + 10 * i for i in range(30)] + [400] * 210
        assert "[OK]" in "\n".join(sm.verdict(self._rows(rss), None))

    def test_crash_is_reported(self):
        text = "\n".join(sm.verdict(self._rows([300] * 5), "15:42:10"))
        assert "se cerró a las 15:42:10" in text


# ---------------------------------------------------------------------------
# Tope de FPS de inferencia
# ---------------------------------------------------------------------------

def _count_frames(tmp_path, max_fps, seconds=1.0):
    import time
    from queue import Queue
    import src.ui.app_ui as app_ui
    from tests.test_integration_pipeline import (
        _FakeFaceEstimator, _FakePoseEstimator, _FakeVideoThread, _thresholds,
        NEUTRAL)
    th = _thresholds(tmp_path / "t.db")
    th.setdefault("mediapipe", {})["max_inference_fps"] = max_fps

    class _Feed:
        def encode(self, frame, fps=0.0):
            return "b64"

    orig = app_ui.PoseEstimator, app_ui.FaceEstimator
    app_ui.PoseEstimator = lambda **kw: _FakePoseEstimator(NEUTRAL)
    app_ui.FaceEstimator = lambda **kw: _FakeFaceEstimator(NEUTRAL)
    app_ui.PoseEstimator.REQUIRED_LANDMARKS = orig[0].REQUIRED_LANDMARKS
    app_ui.FaceEstimator.LEFT_EYE_EAR_INDICES = orig[1].LEFT_EYE_EAR_INDICES
    app_ui.FaceEstimator.RIGHT_EYE_EAR_INDICES = orig[1].RIGHT_EYE_EAR_INDICES
    try:
        t = app_ui.InferenceThread(
            video_thread=_FakeVideoThread(), thresholds=th,
            state_queue=Queue(1), on_alert=lambda e: None,
            video_feed=_Feed(), start_mode="preview")
        t.start()
        time.sleep(0.3)
        n0 = t._frame_n
        time.sleep(seconds)
        n = t._frame_n - n0
        t.stop()
        t.join(timeout=5.0)
        return n
    finally:
        app_ui.PoseEstimator, app_ui.FaceEstimator = orig


def test_inference_fps_cap(tmp_path):
    """Con tope de 5 FPS, ~5 frames por segundo aunque haya más."""
    capped = _count_frames(tmp_path / "a", 5)
    free = _count_frames(tmp_path / "b", 0)
    assert 3 <= capped <= 7
    assert free > 3 * capped
