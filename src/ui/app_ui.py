"""
app_ui.py
---------
Entry point principal del Sistema de Monitoreo Postural y Fatiga.

Orquesta:
  1. VideoThread     -> captura de frames (hilo dedicado)
  2. PoseEstimator   -> BlazePose      | en paralelo, sobre el mismo buffer RGB
  3. FaceEstimator   -> Face Mesh      |
  4. geometry.py     -> θc, ΔE, EAR, MAR, distancia
  5. MetricSmoother  -> filtrado temporal antes del FSM
  6. FusionFSM       -> temporizadores / PERCLOS / alertas
  7. HistoryLogger   -> bitácora SQLite (métricas + eventos)
  8. Flet UI         -> feed de video + telemetría + alertas

Arquitectura de hilos
---------------------
    Hilo de captura (VideoThread)
        cv2.VideoCapture -> Queue(1) [drop-oldest]

    Hilo de inferencia (daemon)
        1 conversión BGR->RGB compartida
        pose y face lanzados EN PARALELO a un ThreadPoolExecutor(2)
        geometry -> smoothing -> FSM -> logger -> state_queue(1)

    Hilo de UI (event loop de Flet)
        consume state_queue y repinta cada ~33 ms

Por qué pose y face van en paralelo
-----------------------------------
La versión anterior los ejecutaba en secuencia dentro del mismo hilo y, al
encadenarlos, la latencia total era la **suma** de ambos modelos (72.2 ms
medidos = 48.0 pose + 23.7 face, ver docs/validation_report.md §3.1b), lo que
dejaba el sistema en ~13.9 FPS frente a la meta de 30. Son dos grafos de
MediaPipe independientes que solo comparten la imagen de entrada en modo
lectura, así que pueden correr a la vez: la latencia pasa a ser
`max(pose, face)` en lugar de `pose + face`.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import asyncio
import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue

import cv2
import flet as ft

# Asegurar que el directorio raíz del proyecto esté en sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.capture.video_thread import VideoThread
from src.capture.camera_source import resolve_camera_source
from src.vision.pose_estimator import PoseEstimator
from src.vision.face_estimator import FaceEstimator
from src.vision.smoothing import MetricSmootherBank
from src.vision.geometry import (
    calculate_cervical_components,
    calculate_shoulder_asymmetry,
    calculate_avg_ear,
    calculate_mouth_opening,
    estimate_distance_m,
    landmarks_visible,
)
from src.fusion.fusion_fsm import FusionFSM, SensorMetrics
from src.storage.history_logger import HistoryLogger
from src.ui.video_feed import VideoFeed
from src.ui.telemetry_panel import TelemetryPanel
from src.ui.alert_notifier import AlertNotifier

# ---------------------------------------------------------------------------
# Configuración de logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("app_ui")

_THRESHOLDS_PATH = PROJECT_ROOT / "config" / "thresholds.json"


def _load_thresholds() -> dict:
    try:
        with open(_THRESHOLDS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("thresholds.json no encontrado, usando valores por defecto.")
        return {}


# La detección de cámara vive en src/capture/camera_source.py, compartida con
# las herramientas de medición: si la app y calibration_mode.py resolvieran la
# cámara de forma distinta, medirían con dispositivos distintos.


# ---------------------------------------------------------------------------
# Snapshot de estado compartido entre hilos
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    """Estado del sistema en un frame dado. Transferido por Queue(1)."""
    frame_b64: str | None = None
    metrics: SensorMetrics | None = None
    fps: float = 0.0
    timer_status: dict = field(default_factory=dict)
    setup_hint: str = ""


# ---------------------------------------------------------------------------
# Hilo de inferencia
# ---------------------------------------------------------------------------

class InferenceThread(threading.Thread):
    """
    Hilo que ejecuta el pipeline completo en cada frame:
    VideoThread -> MediaPipe (paralelo) -> geometry -> smoothing -> FSM -> UI.
    """

    def __init__(self, video_thread: VideoThread,
                 thresholds: dict,
                 state_queue: Queue,
                 on_alert,
                 video_feed: VideoFeed,
                 log_metrics_every_n: int = 30):
        super().__init__(name="InferenceThread", daemon=True)
        self._vt = video_thread
        self._thresholds = thresholds
        self._state_queue = state_queue
        self._on_alert_ui = on_alert
        self._video_feed = video_feed
        self._log_metrics_every_n = log_metrics_every_n
        self._stop_event = threading.Event()

        cam_cfg = thresholds.get("camera", {})
        self._frame_size = (cam_cfg.get("resolution_width", 640),
                            cam_cfg.get("resolution_height", 480))
        self._hfov_deg = cam_cfg.get("horizontal_fov_deg", 60.0)
        self._min_dist = cam_cfg.get("min_distance_m", 0.50)
        self._max_dist = cam_cfg.get("max_distance_m", 0.70)
        # Banda de tolerancia: fuera del rango recomendado la medida sigue
        # siendo válida (solo desaconsejada), pero fuera de la tolerancia el
        # encuadre ya no permite medir postura y el FSM debe congelarse.
        self._dist_tolerance = cam_cfg.get("distance_tolerance_m", 0.20)

        # MediaPipe
        mp_cfg = thresholds.get("mediapipe", {})
        self._pose_est = PoseEstimator(
            model_complexity=mp_cfg.get("pose_model_complexity", 1),
            min_detection_confidence=mp_cfg.get("pose_min_detection_confidence", 0.5),
            min_tracking_confidence=mp_cfg.get("pose_min_tracking_confidence", 0.5),
            draw_landmarks=True,
        )
        self._face_est = FaceEstimator(
            max_num_faces=mp_cfg.get("face_max_num_faces", 1),
            refine_landmarks=mp_cfg.get("face_refine_landmarks", False),
            min_detection_confidence=mp_cfg.get("face_min_detection_confidence", 0.5),
            min_tracking_confidence=mp_cfg.get("face_min_tracking_confidence", 0.5),
            draw_landmarks=True,
            draw_mode=mp_cfg.get("face_draw_mode", "metrics"),
        )
        self._min_visibility = mp_cfg.get("pose_min_landmark_visibility", 0.5)
        # BlazePose es el modelo caro y θc/ΔE se evalúan en ventanas de 5 s:
        # inferirlo 1 de cada N frames no cambia la detección y libera CPU.
        self._pose_every_n = max(1, int(mp_cfg.get("pose_every_n_frames", 1)))

        # Dos workers: uno por modelo. Ambos leen el mismo buffer RGB en modo
        # solo-lectura, así que no hay carrera de datos.
        self._executor = ThreadPoolExecutor(max_workers=2,
                                            thread_name_prefix="mediapipe")

        # Filtrado temporal previo al FSM
        smooth_cfg = thresholds.get("smoothing", {})
        self._smoothers = MetricSmootherBank(
            ["theta_c", "theta_sag", "theta_lat", "delta_e", "ear", "mar"],
            median_window=smooth_cfg.get("median_window", 5),
            alpha=smooth_cfg.get("ema_alpha", 0.35),
        )

        # FSM y logger
        ui_cfg = thresholds.get("ui", {})
        self._fsm = FusionFSM(
            thresholds=thresholds,
            on_alert=self._handle_alert,
            cooldown_sec=ui_cfg.get("alert_cooldown_sec", 30),
        )

        storage_cfg = thresholds.get("storage", {})
        self._db = HistoryLogger(
            db_path=str(PROJECT_ROOT / storage_cfg.get("db_path", "data/history.db"))
        )
        self._db.start_session(notes="Sesión automática")

        # FPS interno
        self._fps: float = 0.0
        self._fps_counter = 0
        self._fps_ts = time.perf_counter()

        self._left_eye_idx = FaceEstimator.LEFT_EYE_EAR_INDICES
        self._right_eye_idx = FaceEstimator.RIGHT_EYE_EAR_INDICES

        self._frame_n = 0
        self._last_pose_result = None

        # Cadencia de render, alineada con el refresco de la UI
        self._render_interval_sec = ui_cfg.get("video_update_interval_ms", 33) / 1000.0
        self._last_render_ts = 0.0

    # ------------------------------------------------------------------

    def _handle_alert(self, alert_event) -> None:
        """
        Callback del FSM. Persiste el evento **y** lo muestra en la UI.

        La bitácora de eventos es un entregable del Capítulo IV, así que la
        escritura en SQLite va primero y protegida: si la UI fallara, el
        evento ya está registrado.
        """
        try:
            self._db.log_event(alert_event)
        except Exception:
            logger.exception("No se pudo registrar el evento en la bitácora")
        if self._on_alert_ui:
            self._on_alert_ui(alert_event)

    # ------------------------------------------------------------------

    def run(self):
        logger.info("InferenceThread iniciado.")
        try:
            while not self._stop_event.is_set():
                frame = self._vt.get_frame(timeout=0.05)
                if frame is None:
                    continue

                self._frame_n += 1

                # --- Una sola conversión de color para ambos modelos -------
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame_rgb.flags.writeable = False

                # --- Inferencia en paralelo -------------------------------
                run_pose = (self._frame_n % self._pose_every_n) == 0 \
                    or self._last_pose_result is None
                fut_face = self._executor.submit(self._face_est.process,
                                                 frame_rgb, True)
                if run_pose:
                    fut_pose = self._executor.submit(self._pose_est.process,
                                                     frame_rgb, True)
                    pose_result = fut_pose.result()
                    self._last_pose_result = pose_result
                else:
                    pose_result = self._last_pose_result
                face_result = fut_face.result()

                # --- Métricas ---------------------------------------------
                metrics = self._build_metrics(pose_result, face_result)

                # --- FSM ---------------------------------------------------
                self._fsm.update(metrics)
                timer_status = self._fsm.get_timers_status()
                metrics.perclos = timer_status["perclos"]
                metrics.blink_rate_per_min = timer_status["blink_rate_per_min"]

                # --- Bitácora de métricas (cada N frames) ------------------
                if self._frame_n % self._log_metrics_every_n == 0:
                    try:
                        self._db.log_metrics(metrics, fps=self._fps)
                    except Exception:
                        logger.exception("No se pudo registrar métricas")

                # --- FPS ---------------------------------------------------
                self._fps_counter += 1
                elapsed = time.perf_counter() - self._fps_ts
                if elapsed >= 1.0:
                    self._fps = self._fps_counter / elapsed
                    self._fps_counter = 0
                    self._fps_ts = time.perf_counter()

                # --- Publicar estado --------------------------------------
                # Dibujar el overlay y codificar a JPEG/Base64 cuesta unos
                # milisegundos por frame, y solo sirve para que la UI lo pinte.
                # La UI repinta cada `video_update_interval_ms` (33 ms por
                # defecto), así que codificar más rápido que eso es trabajo que
                # se tira: se limita el render a esa cadencia y los frames
                # intermedios publican solo métricas, que sí se procesan a la
                # velocidad de inferencia.
                now = time.perf_counter()
                b64 = None
                if (now - self._last_render_ts) >= self._render_interval_sec:
                    self._last_render_ts = now
                    annotated = frame  # el frame BGR original, ya no lo lee nadie
                    self._pose_est.draw(annotated, pose_result)
                    self._face_est.draw(annotated, face_result)
                    b64 = self._video_feed.encode(annotated, fps=self._fps)

                # Drop-oldest: la UI siempre debe recibir el estado más
                # reciente, no el que estuviera esperando en la cola.
                if self._state_queue.full():
                    try:
                        self._state_queue.get_nowait()
                    except Empty:
                        pass

                try:
                    self._state_queue.put_nowait(AppState(
                        frame_b64=b64,
                        metrics=metrics,
                        fps=self._fps,
                        timer_status=timer_status,
                        setup_hint=self._setup_hint(metrics),
                    ))
                except Exception:
                    pass
        finally:
            self._executor.shutdown(wait=True)
            self._pose_est.close()
            self._face_est.close()
            self._db.close()
            logger.info("InferenceThread detenido.")

    # ------------------------------------------------------------------

    def _build_metrics(self, pose_result, face_result) -> SensorMetrics:
        """Convierte los landmarks en métricas filtradas listas para el FSM."""
        now_wall = time.time()
        now_mono = time.perf_counter()

        metrics = SensorMetrics(
            timestamp=now_wall,
            monotonic=now_mono,
            pose_detected=bool(pose_result and pose_result.detected),
            face_detected=face_result.detected,
        )

        # --- Postura ---------------------------------------------------
        if metrics.pose_detected and len(pose_result.landmarks) >= 13:
            visible = landmarks_visible(pose_result.landmarks,
                                        PoseEstimator.REQUIRED_LANDMARKS,
                                        self._min_visibility)
            try:
                theta_c, theta_sag, theta_lat = calculate_cervical_components(
                    pose_result.landmarks, self._frame_size)
                delta_e = calculate_shoulder_asymmetry(
                    pose_result.landmarks, self._frame_size)
                metrics.distance_m = estimate_distance_m(
                    pose_result.landmarks, self._frame_size, self._hfov_deg)

                metrics.cervical_angle    = self._smoothers.update("theta_c", theta_c)
                metrics.cervical_sagittal = self._smoothers.update("theta_sag", theta_sag)
                metrics.cervical_lateral  = self._smoothers.update("theta_lat", theta_lat)
                metrics.shoulder_asymmetry = self._smoothers.update("delta_e", delta_e)

                metrics.pose_valid = visible and self._distance_ok(metrics.distance_m)
            except Exception as e:
                logger.debug("geometry error (pose): %s", e)
                metrics.pose_valid = False
        else:
            # Sin pose el filtro debe olvidarse del estado anterior: al
            # recuperar la detección el sujeto puede estar en otra postura.
            self._smoothers.reset("theta_c")
            self._smoothers.reset("theta_sag")
            self._smoothers.reset("theta_lat")
            self._smoothers.reset("delta_e")
            metrics.pose_valid = False

        # --- Fatiga ----------------------------------------------------
        if face_result.detected and len(face_result.landmarks) >= 300:
            try:
                ear = calculate_avg_ear(face_result.landmarks,
                                        self._left_eye_idx, self._right_eye_idx,
                                        self._frame_size)
                mar = calculate_mouth_opening(face_result.landmarks,
                                              frame_size=self._frame_size)
                metrics.ear_avg = self._smoothers.update("ear", ear)
                metrics.mouth_opening = self._smoothers.update("mar", mar)
            except Exception as e:
                logger.debug("geometry error (face): %s", e)
        else:
            self._smoothers.reset("ear")
            self._smoothers.reset("mar")

        return metrics

    def _distance_ok(self, distance_m) -> bool:
        """True si la distancia estimada permite una medición interpretable."""
        if distance_m is None:
            return False
        return (self._min_dist - self._dist_tolerance
                <= distance_m
                <= self._max_dist + self._dist_tolerance)

    def _setup_hint(self, metrics: SensorMetrics) -> str:
        """Mensaje de encuadre para el usuario (cadena vacía = todo correcto)."""
        if not metrics.pose_detected:
            return "No se detecta al usuario — colócate frente a la cámara."
        d = metrics.distance_m
        if d is None:
            return "No se pueden ver ambos hombros — ajusta el encuadre."
        if d < self._min_dist:
            return f"Demasiado cerca ({d:.2f} m) — aléjate hasta {self._min_dist:.2f}–{self._max_dist:.2f} m."
        if d > self._max_dist:
            return f"Demasiado lejos ({d:.2f} m) — acércate hasta {self._min_dist:.2f}–{self._max_dist:.2f} m."
        if not metrics.pose_valid:
            return "Landmarks poco visibles — mira de frente a la cámara."
        return ""

    def stop(self):
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Aplicación Flet principal
# ---------------------------------------------------------------------------

def main(page: ft.Page):
    thresholds = _load_thresholds()
    ui_cfg = thresholds.get("ui", {})
    cam_cfg = thresholds.get("camera", {})

    page.title = "Monitor Postural y Fatiga — Edge AI"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#0A0A16"
    page.padding = 0
    page.fonts = {
        "Inter": "https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap"
    }

    video_width  = cam_cfg.get("resolution_width", 640)
    video_height = cam_cfg.get("resolution_height", 480)

    video_feed      = VideoFeed(width=video_width, height=video_height)
    telemetry_panel = TelemetryPanel(thresholds)
    alert_notifier  = AlertNotifier(page, on_dismissed=lambda _: None)

    def on_alert_fired(alert_event):
        """Llamado desde el InferenceThread. Delega al hilo de UI."""
        page.run_thread(lambda: alert_notifier.show(alert_event))

    state_queue: Queue = Queue(maxsize=1)

    camera_source = resolve_camera_source(cam_cfg.get("source_index", -1))

    vt = VideoThread(
        source=camera_source,
        width=video_width,
        height=video_height,
        target_fps=cam_cfg.get("target_fps", 30),
    )
    inference_thread = InferenceThread(
        video_thread=vt,
        thresholds=thresholds,
        state_queue=state_queue,
        on_alert=on_alert_fired,
        video_feed=video_feed,
    )

    _running = {"value": True}

    # ----------------------------------------------------------------
    # Layout
    # ----------------------------------------------------------------
    header = ft.Container(
        content=ft.Row([
            ft.Container(
                content=ft.Row([
                    ft.Icon(ft.icons.MONITOR_HEART, color="#00BCD4", size=22),
                    ft.Text("Monitor Postural & Fatiga", size=16,
                            weight=ft.FontWeight.BOLD, color=ft.colors.WHITE),
                    ft.Container(
                        content=ft.Text("EDGE AI", size=9, color="#0A0A16",
                                        weight=ft.FontWeight.BOLD),
                        bgcolor="#00BCD4", border_radius=4,
                        padding=ft.padding.symmetric(vertical=2, horizontal=6),
                    ),
                ], spacing=10),
            ),
            ft.Container(expand=True),
            ft.Text("100% Local • Sin nube", size=11,
                    color=ft.colors.WHITE38, italic=True),
        ]),
        bgcolor="#0D0D1A",
        padding=ft.padding.symmetric(vertical=12, horizontal=20),
        border=ft.border.only(bottom=ft.BorderSide(1, "#1E1E3A")),
    )

    # Banda de ayuda de encuadre: aparece solo cuando hay algo que corregir.
    setup_banner_text = ft.Text("", size=12, color="#0A0A16",
                                weight=ft.FontWeight.W_600)
    setup_banner = ft.Container(
        content=ft.Row([
            ft.Icon(ft.icons.STRAIGHTEN, color="#0A0A16", size=16),
            setup_banner_text,
        ], spacing=8),
        bgcolor="#FFAA00",
        padding=ft.padding.symmetric(vertical=8, horizontal=14),
        border_radius=8,
        visible=False,
    )

    video_container = ft.Container(
        content=ft.Column([
            setup_banner,
            ft.Container(
                content=video_feed.build(),
                border=ft.border.all(1, "#1E1E3A"),
                border_radius=12,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                shadow=ft.BoxShadow(
                    spread_radius=0, blur_radius=20,
                    color=ft.colors.with_opacity(0.3, "#00BCD4"),
                    offset=ft.Offset(0, 4),
                ),
            ),
            ft.Container(
                content=ft.Row([
                    ft.Icon(ft.icons.INFO_OUTLINE, color=ft.colors.WHITE24, size=13),
                    ft.Text(
                        f"Posiciona la cámara a {cam_cfg.get('min_distance_m', 0.5)}–"
                        f"{cam_cfg.get('max_distance_m', 0.7)} m de distancia, "
                        f"a la altura de tus ojos.",
                        size=11, color=ft.colors.WHITE38, italic=True,
                    ),
                ], spacing=6),
                padding=ft.padding.only(top=8, left=4),
            ),
        ], spacing=8),
        expand=3,
        padding=ft.padding.all(16),
    )

    sidebar = ft.Container(
        content=ft.Column([telemetry_panel.build()],
                          scroll=ft.ScrollMode.AUTO, spacing=0),
        expand=2,
        padding=ft.padding.only(top=16, right=16, bottom=16),
        bgcolor="#0A0A16",
        border=ft.border.only(left=ft.BorderSide(1, "#1E1E3A")),
    )

    page.add(ft.Column(
        [header, ft.Row([video_container, sidebar], expand=True, spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.START)],
        spacing=0, expand=True,
    ))

    # ----------------------------------------------------------------
    # Loop de actualización de UI
    # ----------------------------------------------------------------
    update_interval_ms = ui_cfg.get("video_update_interval_ms", 33)

    async def _ui_update_loop():
        while _running["value"]:
            try:
                state: AppState = state_queue.get_nowait()
                if state.frame_b64 is not None:
                    video_feed.set_encoded(state.frame_b64)
                if state.metrics is not None:
                    telemetry_panel.update(state.metrics, fps=state.fps,
                                           timer_status=state.timer_status)
                setup_banner.visible = bool(state.setup_hint)
                setup_banner_text.value = state.setup_hint
                page.update()
            except Empty:
                pass
            except Exception:
                logger.exception("UI loop error")

            await asyncio.sleep(update_interval_ms / 1000.0)

    # ----------------------------------------------------------------
    # Apagado limpio
    # ----------------------------------------------------------------
    _shutdown_done = threading.Event()

    def _shutdown():
        """
        Cierre ordenado. Idempotente porque puede llegar por dos caminos
        (evento de ventana y `atexit`) y `HistoryLogger.close()` no debe
        ejecutarse dos veces.

        Cerrar bien no es cosmético: `end_session()` es lo que rellena
        `sessions.ended_at`, y sin eso el resumen del Capítulo IV no puede
        calcular la duración de la sesión. En las tres sesiones registradas
        antes de este cambio `ended_at` quedó NULL.
        """
        if _shutdown_done.is_set():
            return
        _shutdown_done.set()
        logger.info("Cerrando aplicación…")
        _running["value"] = False
        inference_thread.stop()
        vt.stop()
        inference_thread.join(timeout=5.0)
        if inference_thread.is_alive():
            logger.warning("InferenceThread no terminó en 5 s.")
        logger.info("Aplicación cerrada limpiamente.")

    def _on_window_event(e):
        if e.data == "close" or getattr(e, "type", None) == ft.WindowEventType.CLOSE:
            _shutdown()
            page.window.destroy()

    page.window.prevent_close = True
    page.window.on_event = _on_window_event

    import atexit
    atexit.register(_shutdown)

    vt.start()
    inference_thread.start()
    page.run_task(_ui_update_loop)


if __name__ == "__main__":
    ft.app(target=main)
