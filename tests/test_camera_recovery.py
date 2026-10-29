"""
test_camera_recovery.py
-----------------------
Recuperación de la cámara en el equipo del participante: una videollamada
que la toma en exclusiva, la vuelta de una suspensión, una cámara que no
abre al arrancar o que entrega frames negros.

Antes, una sola de estas situaciones dejaba la captura muerta el resto de la
jornada. Se prueba con una cámara simulada (sin hardware).

Autor: Tesis Huisa Perez, UNSA 2026
"""

import sys
import threading
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.capture import video_thread as vtm
from src.capture.video_thread import VideoThread

REAL = np.random.default_rng(0).integers(0, 255, (48, 64, 3), dtype=np.uint8)
BLACK = np.zeros((48, 64, 3), dtype=np.uint8)


class FakeCamera:
    """
    Dispositivo compartido por todas las aperturas. `script` decide qué pasa:
      fail_open   n aperturas fallidas antes de abrir
      mode        "ok" | "fail" (read falla) | "black"
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.fail_open = 0
        self.mode = "ok"
        self.opens = 0
        self.releases = 0

    def factory(self, source):
        return _Handle(self)


class _Handle:
    def __init__(self, cam):
        self.cam = cam
        with cam.lock:
            cam.opens += 1
            self.ok = cam.fail_open <= 0
            cam.fail_open -= 1

    def isOpened(self):
        return self.ok

    def set(self, *a):
        return True

    def get(self, *a):
        return 0

    def read(self):
        mode = self.cam.mode
        time.sleep(0.5 if mode == "slow" else 0.005)
        if mode == "fail":
            return False, None
        return True, (BLACK if mode == "black" else REAL).copy()

    def release(self):
        self.cam.releases += 1


def _wait(pred, timeout=5.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _thread(cam, **kw):
    kw.setdefault("reopen_after_sec", 0.2)
    kw.setdefault("max_backoff_sec", 0.2)
    kw.setdefault("black_after_sec", 0.2)
    return VideoThread(source=0, capture_factory=cam.factory, **kw)


def _drain(vt, n=5):
    got = 0
    for _ in range(n * 10):
        if vt.get_frame(timeout=0.05) is not None:
            got += 1
            if got >= n:
                return True
    return False


class TestCameraUsage:
    """Qué otras apps usan la cámara (registro de Windows simulado)."""

    from src.capture import camera_usage as cu

    NOW = 1_793_280_000.0

    def _ft(self, unix):
        return int(unix * 1e7) + self.cu._FILETIME_EPOCH

    def _reader(self, entries):
        return lambda: entries

    def test_detects_an_active_videocall(self):
        e = [("NonPackaged\\C:#Users#x#AppData#Roaming#Zoom#bin#Zoom.exe",
              self._ft(self.NOW - 60), 0),
             ("MSTeams_8wekyb3d8bbwe", self._ft(self.NOW - 30), 0),
             ("NonPackaged\\C:#Chrome#chrome.exe", self._ft(self.NOW - 900),
              self._ft(self.NOW - 800))]
        assert self.cu.others_using_camera(
            [], self._reader(e), now=self.NOW) == ["MSTeams", "Zoom"]

    def test_ignores_this_app(self):
        e = [("NonPackaged\\C:#Programas#MonitorPostural#MonitorPostural.exe",
              self._ft(self.NOW - 10), 0)]
        assert self.cu.others_using_camera(
            ["MonitorPostural.exe"], self._reader(e), now=self.NOW) == []

    def test_ignores_stale_entries_of_crashed_apps(self):
        """Una app que se colgó puede dejar 'en uso' para siempre."""
        e = [("NonPackaged\\C:#x#zoom.exe", self._ft(self.NOW - 13 * 3600), 0)]
        assert self.cu.others_using_camera(
            [], self._reader(e), now=self.NOW) == []

    def test_reader_failure_means_nobody(self):
        def boom():
            raise OSError("sin registro")
        assert self.cu.others_using_camera([], boom, now=self.NOW) == []

    def test_real_registry_can_be_read(self):
        """En Windows la lectura no debe fallar (puede devolver vacío)."""
        assert isinstance(self.cu.read_registry_entries(), list)


class TestRecovery:

    def test_normal_capture(self):
        cam = FakeCamera()
        vt = _thread(cam).start()
        try:
            assert _drain(vt)
            assert vt.status == vtm.STATUS_OK and vt.status_message == ""
        finally:
            vt.stop()

    def test_reopens_after_reads_start_failing(self):
        """Videollamada que toma la cámara y luego la suelta."""
        cam = FakeCamera()
        vt = _thread(cam).start()
        try:
            assert _drain(vt)
            cam.mode = "fail"
            assert _wait(lambda: vt.status == vtm.STATUS_RECONNECTING)
            assert "otra aplicación" in vt.status_message
            cam.mode = "ok"
            assert _wait(lambda: vt.status == vtm.STATUS_OK)
            assert _drain(vt), "vuelve a entregar frames"
            assert vt.reconnects >= 1 and cam.opens >= 2
        finally:
            vt.stop()

    def test_opens_but_never_delivers_does_not_flap(self, caplog):
        """
        Con Teams usando la cámara, el dispositivo abre pero no entrega nada.
        El estado no debe alternar ok/reconectando ni inundar el log.
        """
        cam = FakeCamera()
        vt = _thread(cam, max_backoff_sec=0.4).start()
        try:
            assert _drain(vt)
            after = []
            with caplog.at_level("WARNING", logger="src.capture.video_thread"):
                cam.mode = "fail"
                assert _wait(lambda: vt.status == vtm.STATUS_RECONNECTING)
                end = time.perf_counter() + 2.0
                while time.perf_counter() < end:
                    after.append(vt.status)
                    time.sleep(0.005)
            assert set(after) == {vtm.STATUS_RECONNECTING}, "no vuelve a 'ok'"
            assert len(caplog.records) <= 2, [r.message for r in caplog.records]
            cam.mode = "ok"
            assert _wait(lambda: vt.status == vtm.STATUS_OK)
            assert vt.reconnects == 1
        finally:
            vt.stop()

    def test_keeps_retrying_when_the_camera_does_not_open(self):
        """Antes el hilo terminaba en silencio y la app no medía nunca."""
        cam = FakeCamera()
        cam.fail_open = 3
        vt = _thread(cam).start()
        try:
            assert _wait(lambda: vt.status == vtm.STATUS_UNAVAILABLE)
            assert _wait(lambda: vt.status == vtm.STATUS_OK)
            assert vt.is_running and _drain(vt)
        finally:
            vt.stop()

    def test_black_frames_are_not_delivered(self):
        """Un frame negro haría parecer que la persona se levantó."""
        cam = FakeCamera()
        cam.mode = "black"
        vt = _thread(cam, black_after_sec=0.1).start()
        try:
            assert _wait(lambda: vt.status == vtm.STATUS_NO_IMAGE)
            while vt.get_frame(timeout=0.05) is not None:
                pass                     # vaciar lo entregado antes del umbral
            time.sleep(0.2)
            assert vt.get_frame(timeout=0.2) is None
            cam.mode = "ok"
            assert _wait(lambda: vt.status == vtm.STATUS_OK)
            assert _drain(vt)
        finally:
            vt.stop()

    def test_failed_reads_do_not_flood_the_log(self, caplog):
        """Antes: una advertencia cada 10 ms mientras la cámara fallaba."""
        cam = FakeCamera()
        vt = _thread(cam, reopen_after_sec=5.0).start()
        try:
            assert _drain(vt)
            cam.mode = "fail"
            with caplog.at_level("WARNING", logger="src.capture.video_thread"):
                time.sleep(1.0)
        finally:
            vt.stop()
        assert len(caplog.records) <= 3

    def test_switches_to_the_real_camera_when_it_becomes_free(self):
        """
        Caso real (2026-10-29): la app arrancó con la cámara real ocupada,
        la autodetección cayó en la IR (índice 0, siempre negra) y se quedó
        ahí aunque la real se liberara. Ahora vuelve a detectar al perderla.
        """
        ir, real = FakeCamera(), FakeCamera()
        ir.mode = "black"
        cams = {0: ir, 1: real}
        free = {"real": False}

        def factory(source):
            return _Handle(cams[source])

        def resolver():
            return 1 if free["real"] else 0

        vt = VideoThread(source=0, capture_factory=factory,
                         source_resolver=resolver, black_after_sec=0.1,
                         reopen_after_sec=0.2, max_backoff_sec=0.2)
        vt.start()
        try:
            assert _wait(lambda: vt.status == vtm.STATUS_NO_IMAGE)
            free["real"] = True
            assert _wait(lambda: vt.source == 1, timeout=15.0)
            assert _wait(lambda: vt.status == vtm.STATUS_OK)
            assert _drain(vt)
        finally:
            vt.stop()

    def test_shared_camera_is_a_gap_not_an_absence(self):
        """
        Caso real (2026-10-29): con Meet usando la cámara, la app recibía
        ~1 frame/s sin nadie, y esas filas se registraban como persona
        AUSENTE. Ahora no se entregan (hueco) y se recupera al soltarla.
        """
        cam = FakeCamera()
        vt = _thread(cam, low_fps_after_sec=1.0, shared_reopen_sec=2.0).start()
        try:
            assert _drain(vt)
            cam.mode = "slow"                       # ~2 frames/s
            assert _wait(lambda: vt.status == vtm.STATUS_SHARED, timeout=8)
            assert "videollamada" in vt.status_message
            while vt.get_frame(timeout=0.05) is not None:
                pass
            # Ni siquiera tras reabrir (periodo de prueba) se entrega basura
            opens = cam.opens
            assert _wait(lambda: cam.opens > opens, timeout=8)
            got = sum(vt.get_frame(timeout=0.2) is not None for _ in range(10))
            assert got == 0, "durante la llamada no debe entregar frames"
            cam.mode = "ok"                         # termina la llamada
            assert _wait(lambda: vt.status == vtm.STATUS_OK, timeout=10)
            assert _drain(vt)
        finally:
            vt.stop()

    def test_stop_is_prompt_while_retrying(self):
        cam = FakeCamera()
        cam.fail_open = 10_000
        vt = _thread(cam, max_backoff_sec=30.0).start()
        assert _wait(lambda: vt.status == vtm.STATUS_UNAVAILABLE)
        t0 = time.perf_counter()
        vt.stop()
        assert time.perf_counter() - t0 < 2.0
        assert not vt.is_running

    def test_restart_after_pause_is_not_counted_as_reconnection(self):
        cam = FakeCamera()
        vt = _thread(cam).start()
        assert _drain(vt)
        vt.stop()
        vt.start()
        try:
            assert _drain(vt)
            assert vt.reconnects == 0
        finally:
            vt.stop()


class TestYieldCamera:
    """«Ceder cámara»: soltarla unos segundos para la videollamada."""

    def _thread(self, tmp_path):
        from queue import Queue
        import src.ui.app_ui as app_ui
        from tests.test_integration_pipeline import (
            _FakeFaceEstimator, _FakePoseEstimator, _FakeVideoThread,
            _thresholds, NEUTRAL)

        class VT(_FakeVideoThread):
            def __init__(self):
                super().__init__()
                self.starts = self.stops = 0

            def start(self):
                self.starts += 1
                return self

            def stop(self):
                self.stops += 1

        orig = app_ui.PoseEstimator, app_ui.FaceEstimator
        app_ui.PoseEstimator = lambda **kw: _FakePoseEstimator(NEUTRAL)
        app_ui.FaceEstimator = lambda **kw: _FakeFaceEstimator(NEUTRAL)
        app_ui.PoseEstimator.REQUIRED_LANDMARKS = orig[0].REQUIRED_LANDMARKS
        app_ui.FaceEstimator.LEFT_EYE_EAR_INDICES = orig[1].LEFT_EYE_EAR_INDICES
        app_ui.FaceEstimator.RIGHT_EYE_EAR_INDICES = orig[1].RIGHT_EYE_EAR_INDICES

        class _Feed:
            def encode(self, frame, fps=0.0):
                return "b64"
        vt = VT()
        t = app_ui.InferenceThread(
            video_thread=vt, thresholds=_thresholds(tmp_path / "t.db"),
            state_queue=Queue(1), on_alert=lambda e: None,
            video_feed=_Feed(), start_mode="monitoring")

        def restore():
            app_ui.PoseEstimator, app_ui.FaceEstimator = orig
        return t, vt, restore

    def test_releases_and_reopens_by_itself(self, tmp_path):
        t, vt, restore = self._thread(tmp_path)
        try:
            t.yield_camera(0.3)
            assert t.is_paused and t.yielding and vt.stops == 1
            assert _wait(lambda: not t.is_paused, timeout=3.0)
            assert not t.yielding and vt.starts == 1
        finally:
            restore()

    def test_manual_pause_cancels_the_automatic_reopen(self, tmp_path):
        """Si el usuario pausa a mano, la cámara NO se reabre sola."""
        t, vt, restore = self._thread(tmp_path)
        try:
            t.yield_camera(0.3)
            t.cancel_yield()                 # lo que hace el botón Pausar
            time.sleep(0.6)
            assert t.is_paused and not t.yielding and vt.starts == 0
        finally:
            restore()
