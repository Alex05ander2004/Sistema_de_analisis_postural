"""
test_background_pipeline.py
---------------------------
Pruebas de integración del monitoreo en segundo plano.

El requisito que verifican: el sistema debe seguir detectando mientras el
usuario trabaja en otra aplicación. Lo que se apaga al ocultar la ventana es
el **render**, nunca la medición.

Reutiliza los dobles de `test_integration_pipeline` (estimadores falsos de
MediaPipe y fuente de video sintética), así que ninguna prueba abre la cámara
ni ejecuta MediaPipe. Se ejercita el `InferenceThread` real, con geometría,
suavizado, FSM y SQLite reales.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import sys
import time
from pathlib import Path
from queue import Queue

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_integration_pipeline import (
    _FakePoseEstimator,
    _FakeFaceEstimator,
    _FakeVideoThread,
    _thresholds,
    NEUTRAL,
    FORWARD_HEAD,
)


# ---------------------------------------------------------------------------
# Arranque del pipeline con el hilo expuesto
# ---------------------------------------------------------------------------

def _start_pipeline(tmp_path, scenario, **over):
    """
    Construye el InferenceThread real con dobles, **sin arrancarlo**, para
    poder cambiar su estado (visibilidad, pausa) a mitad de ejecución.

    Devuelve (thread, state_queue, alerts, restaurar). `restaurar()` deshace
    los parches de MediaPipe y debe llamarse siempre en un `finally`.
    """
    import src.ui.app_ui as app_ui

    db_path = tmp_path / "bg_history.db"
    thresholds = _thresholds(db_path, **over)

    class _FakeVideoFeed:
        def encode(self, frame, fps=0.0):
            return "fake-b64"

    alerts = []
    state_queue: Queue = Queue(maxsize=1)

    orig_pose, orig_face = app_ui.PoseEstimator, app_ui.FaceEstimator
    app_ui.PoseEstimator = lambda **kw: _FakePoseEstimator(scenario)
    app_ui.FaceEstimator = lambda **kw: _FakeFaceEstimator(scenario)
    app_ui.PoseEstimator.REQUIRED_LANDMARKS = orig_pose.REQUIRED_LANDMARKS
    app_ui.FaceEstimator.LEFT_EYE_EAR_INDICES = orig_face.LEFT_EYE_EAR_INDICES
    app_ui.FaceEstimator.RIGHT_EYE_EAR_INDICES = orig_face.RIGHT_EYE_EAR_INDICES

    def restaurar():
        app_ui.PoseEstimator, app_ui.FaceEstimator = orig_pose, orig_face

    thread = app_ui.InferenceThread(
        video_thread=_FakeVideoThread(),
        thresholds=thresholds,
        state_queue=state_queue,
        on_alert=alerts.append,
        video_feed=_FakeVideoFeed(),
        log_metrics_every_n=5,
    )
    return thread, state_queue, alerts, restaurar


def _drain(state_queue, seconds: float):
    """Consume estados durante `seconds` y devuelve los recogidos."""
    estados = []
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        if not state_queue.empty():
            estados.append(state_queue.get())
        time.sleep(0.005)
    return estados


# ---------------------------------------------------------------------------
# Segundo plano
# ---------------------------------------------------------------------------

class TestBackgroundMonitoring:

    def test_measurement_continues_while_window_hidden(self, tmp_path):
        thread, q, _, restaurar = _start_pipeline(tmp_path, FORWARD_HEAD)
        try:
            thread.start()
            _drain(q, 0.3)

            thread.set_ui_visible(False)
            _drain(q, 0.2)                     # vaciar lo que quedara en vuelo
            estados = _drain(q, 0.6)

            assert estados, "el pipeline dejó de publicar estado al ocultarse"
            # Sigue midiendo...
            assert all(s.metrics is not None for s in estados)
            # ...pero ya no codifica ningún frame para la UI.
            assert all(s.frame_b64 is None for s in estados), (
                "con la ventana oculta no debe codificarse JPEG/Base64")
        finally:
            thread.stop()
            thread.join(timeout=5.0)
            restaurar()

    def test_render_resumes_when_window_shown_again(self, tmp_path):
        thread, q, _, restaurar = _start_pipeline(tmp_path, NEUTRAL)
        try:
            thread.start()
            thread.set_ui_visible(False)
            _drain(q, 0.3)

            thread.set_ui_visible(True)
            estados = _drain(q, 0.6)

            assert any(s.frame_b64 is not None for s in estados), (
                "al volver a mostrar la ventana debe reanudarse el render")
        finally:
            thread.stop()
            thread.join(timeout=5.0)
            restaurar()

    def test_alerts_still_fire_with_window_hidden(self, tmp_path):
        """
        El caso de uso completo: usuario trabajando en otra aplicación, con
        mala postura sostenida. La alerta debe dispararse igual.
        """
        thread, q, alerts, restaurar = _start_pipeline(tmp_path, FORWARD_HEAD)
        try:
            thread.set_ui_visible(False)
            thread.start()
            _drain(q, 1.2)

            assert alerts, "no se generó ninguna alerta en segundo plano"
            assert any(a.alert_type.value == "cervical_angle" for a in alerts)
        finally:
            thread.stop()
            thread.join(timeout=5.0)
            restaurar()

    def test_skip_render_can_be_disabled(self, tmp_path):
        """
        Con `skip_render_when_hidden=false` se sigue codificando siempre. Es el
        modo que permite medir el consumo diferencial para el Capítulo IV.
        """
        thread, q, _, restaurar = _start_pipeline(
            tmp_path, NEUTRAL, ui={"skip_render_when_hidden": False})
        try:
            thread.start()
            thread.set_ui_visible(False)
            estados = _drain(q, 0.6)

            assert any(s.frame_b64 is not None for s in estados)
        finally:
            thread.stop()
            thread.join(timeout=5.0)
            restaurar()


# ---------------------------------------------------------------------------
# Pausa desde la bandeja
# ---------------------------------------------------------------------------

class TestPauseMonitoring:
    """
    La pausa debe detener la medición de verdad: es lo que usa quien entra en
    una videollamada o cede el puesto a otra persona.
    """

    def test_pause_stops_publishing_state(self, tmp_path):
        thread, q, _, restaurar = _start_pipeline(tmp_path, FORWARD_HEAD)
        try:
            thread.start()
            _drain(q, 0.3)

            thread.set_paused(True)
            assert thread.is_paused is True
            _drain(q, 0.3)                     # vaciar lo que quedara en vuelo
            estados = _drain(q, 0.5)

            assert estados == [], "pausado no debe seguir publicando métricas"
        finally:
            thread._paused.clear()
            thread.stop()
            thread.join(timeout=5.0)
            restaurar()

    def test_pause_releases_the_camera(self, tmp_path):
        """
        Pausar libera `cv2.VideoCapture` para que se apague el piloto de la
        webcam. Sin eso la pausa no es creíble para el usuario.
        """
        thread, q, _, restaurar = _start_pipeline(tmp_path, NEUTRAL)
        detenciones = []
        thread._vt.stop = lambda: detenciones.append("stop")
        try:
            thread.start()
            _drain(q, 0.2)
            thread.set_paused(True)

            assert detenciones == ["stop"], (
                "pausar debe detener la captura, no solo el procesamiento")
        finally:
            thread._paused.clear()
            thread.stop()
            thread.join(timeout=5.0)
            restaurar()

    def test_resume_clears_stale_streak(self, tmp_path):
        """
        Al reanudar, los temporizadores arrancan de cero: la racha previa
        describe una postura que ya no está vigente, y arrastrarla dispararía
        una alerta por un intervalo que nadie midió.
        """
        thread, q, _, restaurar = _start_pipeline(tmp_path, FORWARD_HEAD)
        try:
            thread.start()
            _drain(q, 0.25)                    # acumula racha de riesgo

            thread.set_paused(True)
            thread.set_paused(False)

            assert thread.is_paused is False
            assert thread._fsm.get_timers_status()["cervical_elapsed_sec"] == 0.0
        finally:
            thread.stop()
            thread.join(timeout=5.0)
            restaurar()

    def test_toggling_same_state_is_a_noop(self, tmp_path):
        thread, q, _, restaurar = _start_pipeline(tmp_path, NEUTRAL)
        detenciones = []
        thread._vt.stop = lambda: detenciones.append("stop")
        try:
            thread.start()
            thread.set_paused(True)
            thread.set_paused(True)            # no debe repetir el stop
            assert detenciones == ["stop"]
        finally:
            thread._paused.clear()
            thread.stop()
            thread.join(timeout=5.0)
            restaurar()
