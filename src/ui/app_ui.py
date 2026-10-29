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
from src.vision.smoothing import DebouncedState, MetricSmootherBank
from src.vision.geometry import (
    calculate_cervical_components,
    calculate_shoulder_asymmetry,
    calculate_avg_ear,
    calculate_mouth_opening,
    calculate_neck_ratio,
    estimate_distance_m,
    landmarks_visible,
)
from src.fusion.fusion_fsm import FusionFSM, SensorMetrics
from src.fusion.guided_calibration import (
    DONE as CALIB_DONE, FAILED as CALIB_FAILED, GuidedCalibration,
)
from src.storage.history_logger import HistoryLogger
from src.ui.video_feed import VideoFeed
from src.ui.telemetry_panel import TelemetryPanel
from src.ui.alert_notifier import AlertNotifier
from src.ui.calibration_view import CalibrationView
from src.ui.alert_dispatcher import AlertDispatcher
from src.ui.toast_notifier import ToastNotifier
from src.ui.tray_icon import TrayIcon

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
    mode: str = "monitoring"          # ver InferenceThread (modos de la app)
    calibration: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Hilo de inferencia
# ---------------------------------------------------------------------------

class InferenceThread(threading.Thread):
    """
    Hilo que ejecuta el pipeline completo en cada frame:
    VideoThread -> MediaPipe (paralelo) -> geometry -> smoothing -> FSM -> UI.

    Monitoreo en segundo plano
    --------------------------
    Este hilo no consulta en ningún momento el estado de la ventana para
    **medir**: sigue capturando e infiriendo aunque la interfaz esté oculta en
    la bandeja, que es justamente el caso de uso (el usuario trabaja en otra
    aplicación mientras el sistema vigila su postura).

    Lo que sí depende de la visibilidad es el **render**: dibujar el overlay de
    landmarks y codificar el frame a JPEG/Base64 cuesta varios milisegundos por
    frame y solo sirve para que la UI lo pinte. Con la ventana oculta eso es
    trabajo que se tira, así que se omite. La inferencia, la geometría, el FSM
    y la bitácora siguen a plena velocidad: el ahorro no afecta a ninguna
    medición del Capítulo IV.

    Modos de la aplicación
    ----------------------
        preview      cámara y métricas activas para guiar el encuadre, pero
                     sin alertas ni bitácora
        calibrating  calibración guiada (ver src/fusion/guided_calibration.py)
        ready        calibrado; esperando a que el usuario inicie
        monitoring   seguimiento: FSM, alertas y bitácora

    Las transiciones las pide la interfaz con request_calibration() /
    request_start() y se aplican al inicio de la siguiente iteración de run(),
    **en este hilo**: el FSM y los calibradores no tienen cerrojos, así que
    nunca deben tocarse desde el hilo de la interfaz ni desde el de la bandeja.

    Una sesión de la bitácora = una calibración + un seguimiento. Recalibrar
    durante el seguimiento cierra la sesión en curso; la siguiente empieza al
    pulsar Iniciar. En la validación con varios participantes, cada uno queda
    así en su propia sesión.

    Pausa
    -----
    `pause()` **detiene la captura de verdad**, no solo el procesamiento: libera
    `cv2.VideoCapture` y con ello se apaga el piloto de la webcam. Para alguien
    que pausa el monitoreo porque entra en una videollamada o porque hay otra
    persona delante, ver el LED apagado es la única confirmación creíble de que
    no se le está grabando.
    """

    def __init__(self, video_thread: VideoThread,
                 thresholds: dict,
                 state_queue: Queue,
                 on_alert,
                 video_feed: VideoFeed,
                 log_metrics_every_n: int = 30,
                 start_mode: str = "monitoring"):
        """
        start_mode:
            "monitoring" empieza a vigilar de inmediato (comportamiento
            anterior; lo usan las pruebas y las herramientas). La app arranca
            en "preview" y el usuario calibra e inicia desde la interfaz.
        """
        super().__init__(name="InferenceThread", daemon=True)
        self._vt = video_thread
        self._thresholds = thresholds
        self._state_queue = state_queue
        self._on_alert_ui = on_alert
        self._video_feed = video_feed
        self._log_metrics_every_n = log_metrics_every_n
        self._stop_event = threading.Event()

        # --- Estado de segundo plano -----------------------------------
        # `_ui_visible` arranca activo: la app se abre con la ventana a la
        # vista. `_paused` arranca inactivo: se monitoriza desde el inicio.
        self._ui_visible = threading.Event()
        self._ui_visible.set()
        self._paused = threading.Event()
        if start_mode not in ("monitoring", "preview"):
            raise ValueError("start_mode debe ser 'monitoring' o 'preview'")
        self._mode = start_mode
        self._commands: Queue = Queue()

        # Modo estudio: código de participante y condición del protocolo. Son
        # cadenas que la interfaz asigna y este hilo solo lee: una asignación
        # de str es atómica en CPython, no hacen falta cerrojos.
        study_cfg = thresholds.get("study", {})
        self._study_enabled = bool(study_cfg.get("enabled", False))
        self._participant = ""
        self._condition = ""

        ui_cfg_early = thresholds.get("ui", {})
        self._skip_render_when_hidden = bool(
            ui_cfg_early.get("skip_render_when_hidden", True))
        self._pause_on_hidden = bool(
            thresholds.get("alerts", {}).get("pause_on_window_hidden", False))

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
        # La distancia también se filtra: se estima del ancho de hombros en
        # píxeles, que tiene el mismo jitter que ΔE. Sin filtrar, en la sesión
        # del 2026-09-24 saltaba hasta 3.51 m en frames sueltos estando el
        # usuario quieto a ~0.64 m, y cada salto marcaba pose_valid=False y
        # REINICIABA los temporizadores de postura.
        self._smoothers = MetricSmootherBank(
            ["theta_c", "theta_sag", "theta_lat", "delta_e", "ear", "mar",
             "distance", "neck_ratio"],
            median_window=smooth_cfg.get("median_window", 5),
            alpha=smooth_cfg.get("ema_alpha", 0.35),
        )

        # Aviso de encuadre con retardo de confirmación e histéresis, para que
        # no parpadee cuando la distancia ronda un límite. Ver _setup_hint().
        self._hint_state = DebouncedState(
            neutral="",
            hold_sec=cam_cfg.get("setup_hint_hold_sec", 1.5),
            clear_sec=cam_cfg.get("setup_hint_clear_sec", 1.0),
        )
        self._dist_hysteresis = cam_cfg.get("distance_hysteresis_m", 0.03)

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
        if start_mode == "monitoring":
            self._db.start_session(notes="Sesión automática",
                                   participant_code=self._participant)

        calib_cfg = thresholds.get("calibration", {})
        self._calibration = GuidedCalibration(
            self._fsm,
            min_distance_m=self._min_dist,
            max_distance_m=self._max_dist,
            countdown_sec=calib_cfg.get("countdown_sec", 3.0),
            framing_grace_sec=calib_cfg.get("framing_grace_sec", 1.0),
            timeout_sec=calib_cfg.get("timeout_sec", 60.0),
        )

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

    # ------------------------------------------------------------------
    # Control de segundo plano
    # ------------------------------------------------------------------

    def set_ui_visible(self, visible: bool) -> None:
        """
        Informa al pipeline de si la ventana está a la vista.

        Solo gobierna el render (y la pausa, si `pause_on_window_hidden` está
        activo). La medición continúa en cualquier caso.
        """
        if visible:
            self._ui_visible.set()
        else:
            self._ui_visible.clear()

        if self._pause_on_hidden:
            self.set_paused(not visible)

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    def set_paused(self, paused: bool) -> None:
        """
        Pausa o reanuda el monitoreo, apagando/encendiendo la cámara.

        Al reanudar se reinician los temporizadores del FSM y los filtros: la
        racha anterior a la pausa describe una postura que ya no está vigente,
        y arrastrarla dispararía una alerta por un intervalo que nadie midió.

        La calibración se conserva: quien reanuda tras una llamada es la misma
        persona ante la misma cámara. Recalibrar es siempre una acción
        explícita (botón o bandeja), nunca algo que ocurra en silencio con el
        usuario quizá mal sentado.
        """
        if paused == self._paused.is_set():
            return

        if paused:
            self._paused.set()
            try:
                self._vt.stop()          # libera la cámara -> LED apagado
            except Exception:
                logger.exception("No se pudo detener la captura al pausar.")
            logger.info("Monitoreo PAUSADO (cámara liberada).")
        else:
            self._fsm.reset_timers()
            self._smoothers.reset()
            self._hint_state.reset()
            try:
                self._vt.start()
            except Exception:
                logger.exception("No se pudo reanudar la captura.")
            self._paused.clear()
            logger.info("Monitoreo reanudado.")

    # ------------------------------------------------------------------
    # Modos: calibración guiada e inicio del seguimiento
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def can_start(self) -> bool:
        """Se puede iniciar el seguimiento: calibrado, o nada que calibrar."""
        calibrated = (self._mode == "ready"
                      or (self._mode == "preview"
                          and not self._fsm.needs_calibration))
        return calibrated and self.study_ready

    @property
    def study_enabled(self) -> bool:
        return self._study_enabled

    @property
    def participant(self) -> str:
        return self._participant

    @property
    def condition(self) -> str:
        return self._condition

    def set_participant(self, code: str) -> None:
        """
        Código seudonimizado del participante. Se normaliza (mayúsculas, sin
        espacios) para que "p 01" y "P01" no acaben como dos participantes en
        el análisis.
        """
        self._participant = "".join(str(code).split()).upper()

    def set_condition(self, condition: str) -> None:
        """Condición del protocolo vigente; se estampa en filas y alertas."""
        self._condition = str(condition or "")

    @property
    def study_ready(self) -> bool:
        """En modo estudio hace falta un código antes de iniciar."""
        return (not self._study_enabled) or bool(self._participant)

    def request_calibration(self) -> None:
        """Pide empezar (o repetir) la calibración guiada."""
        self._commands.put("calibrate")

    def request_start(self) -> None:
        """Pide empezar el seguimiento; se ignora si falta calibrar."""
        self._commands.put("start")

    def cancel_calibration(self) -> None:
        self._commands.put("cancel")

    def _process_commands(self) -> None:
        """Aplica las órdenes de la interfaz, en este hilo."""
        while True:
            try:
                cmd = self._commands.get_nowait()
            except Empty:
                return
            now = time.perf_counter()

            if cmd == "calibrate":
                if self._mode == "monitoring":
                    # Una sesión = una calibración: la nueva referencia empieza
                    # una sesión nueva al pulsar Iniciar.
                    self._db.end_session()
                if self._paused.is_set():
                    self.set_paused(False)
                self._smoothers.reset()
                self._calibration.start(now)
                self._mode = "calibrating"
                logger.info("Modo: calibrando.")

            elif cmd == "cancel" and self._mode == "calibrating":
                self._calibration.cancel()
                self._mode = "preview"
                logger.info("Calibración cancelada; modo: vista previa.")

            elif cmd == "start":
                if not self.can_start:
                    logger.info("Inicio ignorado: falta calibrar.")
                    continue
                self._db.start_session(notes=self._session_notes(),
                                       participant_code=self._participant)
                # Al empezar, el participante está en uso libre hasta que el
                # evaluador marque la primera condición del protocolo.
                self._condition = "libre" if self._study_enabled else ""
                self._fsm.reset_timers()
                self._smoothers.reset()
                self._mode = "monitoring"
                logger.info("Modo: monitoreando.")

    def _session_notes(self) -> str:
        """Resumen de la calibración vigente, guardado con la sesión."""
        res = self._calibration.status.result or {}
        partes = ["Calibración guiada"]
        if res.get("cervical_baseline") is not None:
            partes.append(f"θc neutro {res['cervical_baseline']:.1f}°")
        if res.get("ear_threshold") is not None:
            partes.append(f"EAR base {res.get('ear_baseline') or 0:.3f} "
                          f"→ umbral {res['ear_threshold']:.3f}")
        if res.get("distance_m") is not None:
            partes.append(f"distancia {res['distance_m']:.2f} m")
        return "; ".join(partes) if len(partes) > 1 else "Sin calibración"

    # ------------------------------------------------------------------

    def _handle_alert(self, alert_event) -> None:
        """
        Callback del FSM. Persiste el evento **y** lo muestra en la UI.

        La bitácora de eventos es un entregable del Capítulo IV, así que la
        escritura en SQLite va primero y protegida: si la UI fallara, el
        evento ya está registrado.
        """
        try:
            self._db.log_event(alert_event, condition=self._condition)
        except Exception:
            logger.exception("No se pudo registrar el evento en la bitácora")
        if self._on_alert_ui:
            self._on_alert_ui(alert_event)

    # ------------------------------------------------------------------

    def run(self):
        logger.info("InferenceThread iniciado.")
        try:
            while not self._stop_event.is_set():
                # Órdenes de la interfaz primero: "Recalibrar" también tiene
                # que funcionar estando en pausa (reanuda la cámara).
                self._process_commands()

                # Pausado: la cámara está liberada, así que no hay nada que
                # leer. Se espera sin quemar CPU y sin tocar el FSM.
                if self._paused.is_set():
                    self._stop_event.wait(0.2)
                    continue


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
                try:
                    fut_face = self._executor.submit(self._face_est.process,
                                                     frame_rgb, True)
                    fut_pose = (self._executor.submit(self._pose_est.process,
                                                      frame_rgb, True)
                                if run_pose else None)
                except RuntimeError:
                    # El executor ya no acepta trabajo: el intérprete se está
                    # cerrando. Python apaga todos los ThreadPoolExecutor en
                    # threading._shutdown(), ANTES de los handlers de atexit,
                    # así que cuando _shutdown() pide parar a este hilo el
                    # executor ya está muerto. Sin esto, el hilo moría con
                    # "RuntimeError: cannot schedule new futures after
                    # shutdown" y una traza en cada cierre que no pasara por
                    # "Salir" (cliente de Flet cerrado, Ctrl+C, cierre forzado).
                    logger.info("Executor cerrado: el intérprete está "
                                "terminando; se detiene la inferencia.")
                    break

                if fut_pose is not None:
                    pose_result = fut_pose.result()
                    self._last_pose_result = pose_result
                else:
                    pose_result = self._last_pose_result
                face_result = fut_face.result()

                # --- Métricas ---------------------------------------------
                metrics = self._build_metrics(pose_result, face_result)

                # --- Según el modo -----------------------------------------
                # Solo el seguimiento evalúa alertas y escribe en la bitácora.
                # En vista previa y calibración las métricas sirven para guiar
                # al usuario, no describen un riesgo.
                if self._mode == "monitoring":
                    self._fsm.update(metrics)
                elif self._mode == "calibrating":
                    st = self._calibration.step(metrics, metrics.monotonic)
                    if st.phase == CALIB_DONE:
                        self._mode = "ready"
                        logger.info("Modo: listo para iniciar.")
                    elif st.phase == CALIB_FAILED:
                        self._mode = "preview"
                else:
                    # Mantener actualizadas las comprobaciones de encuadre que
                    # habilitan el botón Calibrar.
                    self._calibration.step(metrics, metrics.monotonic)
                timer_status = self._fsm.get_timers_status()
                timer_status["app_mode"] = self._mode
                metrics.perclos = timer_status["perclos"]
                metrics.blink_rate_per_min = timer_status["blink_rate_per_min"]
                metrics.ear_threshold = timer_status.get("ear_threshold")
                baseline = timer_status.get("cervical_baseline")
                metrics.cervical_baseline = baseline
                metrics.condition_label = self._condition
                if baseline is not None and metrics.pose_detected:
                    metrics.cervical_deviation = metrics.cervical_angle - baseline

                # --- Bitácora de métricas (cada N frames) ------------------
                if (self._mode == "monitoring"
                        and self._frame_n % self._log_metrics_every_n == 0):
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
                # Con la ventana oculta (bandeja o minimizada) nadie va a ver
                # este frame: dibujar el overlay y codificarlo a JPEG/Base64
                # sería trabajo tirado durante toda la jornada. Se omite el
                # render, pero todo lo anterior —inferencia, geometría, FSM y
                # bitácora— ya se ejecutó a plena velocidad.
                render_wanted = (self._ui_visible.is_set()
                                 or not self._skip_render_when_hidden)
                if (render_wanted
                        and (now - self._last_render_ts) >= self._render_interval_sec):
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
                        mode=self._mode,
                        calibration=self._calibration.status.as_dict(),
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
                raw_distance = estimate_distance_m(
                    pose_result.landmarks, self._frame_size, self._hfov_deg)
                if raw_distance is not None:
                    metrics.distance_m = self._smoothers.update(
                        "distance", raw_distance)
                else:
                    self._smoothers.reset("distance")

                metrics.cervical_angle    = self._smoothers.update("theta_c", theta_c)
                metrics.cervical_sagittal = self._smoothers.update("theta_sag", theta_sag)
                metrics.cervical_lateral  = self._smoothers.update("theta_lat", theta_lat)
                metrics.shoulder_asymmetry = self._smoothers.update("delta_e", delta_e)

                # Indicador 2D sin profundidad: solo se mide y registra, para
                # compararlo con θc en la validación (ver geometry.py).
                raw_neck = calculate_neck_ratio(pose_result.landmarks,
                                                self._frame_size)
                if raw_neck is not None:
                    metrics.neck_ratio = self._smoothers.update(
                        "neck_ratio", raw_neck)

                metrics.pose_visible = visible
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
            self._smoothers.reset("distance")
            self._smoothers.reset("neck_ratio")
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

    def _hint_category(self, metrics: SensorMetrics) -> str:
        """
        Problema de encuadre que indican las métricas de ESTE frame.

        Histéresis en los límites de distancia: un aviso de "demasiado lejos"
        se activa al superar max_distance_m, pero solo se retira al bajar de
        max_distance_m − histéresis (y simétrico para "demasiado cerca"). Sin
        esa banda, una distancia que ronda el límite alterna de categoría
        aunque esté filtrada.
        """
        if not metrics.pose_detected:
            return "no_user"
        d = metrics.distance_m
        if d is None:
            return "no_shoulders"

        shown = self._hint_state.value
        m = self._dist_hysteresis
        close_limit = self._min_dist + (m if shown == "too_close" else 0.0)
        far_limit = self._max_dist - (m if shown == "too_far" else 0.0)
        if d < close_limit:
            return "too_close"
        if d > far_limit:
            return "too_far"
        if not metrics.pose_valid:
            return "low_visibility"
        return ""

    def _setup_hint(self, metrics: SensorMetrics) -> str:
        """
        Mensaje de encuadre para el usuario (cadena vacía = todo correcto).

        La categoría del frame pasa por un retardo de confirmación
        (DebouncedState): un aviso solo aparece si el problema se mantiene
        `setup_hint_hold_sec` seguidos, y solo desaparece tras
        `setup_hint_clear_sec` sin él. Antes el aviso se decidía frame a frame
        y parpadeaba con el ruido de estimación.
        """
        category = self._hint_state.update(self._hint_category(metrics),
                                           metrics.monotonic)

        # Con el encuadre correcto, la banda guía la calibración inicial:
        # la postura de referencia y el EAR se miden en los primeros segundos
        # y solo valen si el usuario está sentado recto mirando la pantalla.
        if category == "" and self._mode == "monitoring":
            calib = self._calibration_hint()
            if calib:
                return calib
        d = metrics.distance_m
        rango = f"{self._min_dist:.2f}–{self._max_dist:.2f} m"

        if category == "no_user":
            return "No se detecta al usuario — colócate frente a la cámara."
        if category == "no_shoulders":
            return "No se pueden ver ambos hombros — ajusta el encuadre."
        if category == "too_close":
            actual = f" ({d:.2f} m)" if d is not None else ""
            return f"Demasiado cerca{actual} — aléjate hasta {rango}."
        if category == "too_far":
            actual = f" ({d:.2f} m)" if d is not None else ""
            return f"Demasiado lejos{actual} — acércate hasta {rango}."
        if category == "low_visibility":
            return "Landmarks poco visibles — mira de frente a la cámara."
        return ""

    def _calibration_hint(self) -> str:
        """Instrucción mientras se calibran la postura neutra o el EAR."""
        st = self._fsm.get_timers_status()
        pendientes = []
        if st.get("posture_calibrating"):
            pendientes.append(st.get("posture_calibration_progress", 0.0))
        if st.get("ear_calibrating"):
            pendientes.append(st.get("ear_calibration_progress", 0.0))
        if not pendientes:
            return ""
        progreso = min(pendientes)
        return (f"Calibrando ({progreso * 100:.0f}%): siéntate recto y mira "
                f"la pantalla con normalidad unos segundos.")

    def recalibrate(self) -> None:
        """
        Pide recalibrar la postura neutra y el EAR (menú de bandeja): para
        cuando se calibró encorvado o se sienta otra persona.

        Solo levanta una señal. Se llama desde el hilo de la bandeja, y el FSM
        lo usa el hilo de inferencia sin cerrojos: el cambio real se hace al
        inicio de la siguiente iteración de run(), en el propio hilo.
        """
        self.request_calibration()
        logger.info("Recalibración solicitada por el usuario.")

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
    _icon_path = PROJECT_ROOT / "assets" / "icon.ico"
    if _icon_path.exists():
        page.window.icon = str(_icon_path)
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

    # ----------------------------------------------------------------
    # Canales de alerta
    #
    # El modal bloqueante se reserva para lo agudo (somnolencia). Todo lo
    # demás sale como notificación del sistema, que se ve por encima de la
    # aplicación en la que esté trabajando el usuario y se cierra sola. Ver
    # src/ui/alert_dispatcher.py para el porqué de esta separación.
    # ----------------------------------------------------------------
    alerts_cfg = thresholds.get("alerts", {})

    def _show_modal_on_ui_thread(alert_event):
        """El FSM llama desde el hilo de inferencia; Flet exige su propio hilo."""
        page.run_thread(lambda: alert_notifier.show(alert_event))

    def _fallback_banner(alert_event):
        """
        Aviso dentro de la ventana para plataformas sin toast nativo.

        Un SnackBar no bloquea ni roba el foco, así que conserva el carácter
        no intrusivo del toast aunque no salga del marco de la aplicación.
        """
        def _show():
            page.open(ft.SnackBar(
                content=ft.Text(alert_event.message, color=ft.colors.WHITE),
                bgcolor="#FF8800",
                duration=6000,
            ))
        try:
            page.run_thread(_show)
        except Exception:
            logger.debug("No se pudo mostrar el aviso de respaldo.")

    toast_notifier = ToastNotifier(
        fallback=_fallback_banner,
        enabled=bool(alerts_cfg.get("toast_enabled", True)),
    )

    def _record_alert_in_panel(alert_event):
        """Deja rastro visible en el panel: es lo que el usuario mira al volver."""
        try:
            page.run_thread(lambda: telemetry_panel.note_alert(alert_event))
        except Exception:
            logger.debug("No se pudo anotar la alerta en el panel.")

    dispatcher = AlertDispatcher(
        thresholds=thresholds,
        toast=toast_notifier,
        show_modal=_show_modal_on_ui_thread,
        on_any_alert=_record_alert_in_panel,
    )

    def on_alert_fired(alert_event):
        """Llamado desde el InferenceThread. No debe bloquear."""
        dispatcher.dispatch(alert_event)

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
        # La app ya no empieza a vigilar al abrirse: el usuario ajusta el
        # encuadre, calibra y pulsa Iniciar. Ver InferenceThread (modos).
        start_mode="preview",
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

    # ----------------------------------------------------------------
    # Barra de control: estado + Calibrar / Iniciar / Pausar
    # ----------------------------------------------------------------
    post_cfg = thresholds.get("postural", {})
    calibration_view = CalibrationView(
        min_distance_m=cam_cfg.get("min_distance_m", 0.50),
        max_distance_m=cam_cfg.get("max_distance_m", 0.70),
        calibration_sec=post_cfg.get("posture_calibration_sec", 20.0),
    )

    status_dot = ft.Container(width=10, height=10, border_radius=5,
                              bgcolor="#555555")
    status_text = ft.Text("Sin calibrar", size=13, color=ft.colors.WHITE,
                          weight=ft.FontWeight.W_600)

    def _on_calibrate_click(_):
        if inference_thread.mode == "calibrating":
            inference_thread.cancel_calibration()
        else:
            inference_thread.request_calibration()

    def _on_start_click(_):
        inference_thread.request_start()

    def _on_pause_click(_):
        paused = not inference_thread.is_paused
        inference_thread.set_paused(paused)
        if tray is not None:
            tray.set_paused(paused)
        _refresh_controls(force=True)

    btn_calibrate = ft.ElevatedButton(
        "Calibrar", icon=ft.icons.TUNE, on_click=_on_calibrate_click,
        disabled=True,
        tooltip="Mide tu postura neutra y tu ojo abierto (≈20 s)",
    )
    btn_start = ft.ElevatedButton(
        "Iniciar seguimiento", icon=ft.icons.PLAY_ARROW,
        on_click=_on_start_click, disabled=True,
        tooltip="Empieza a vigilar tu postura y fatiga",
    )
    btn_pause = ft.OutlinedButton(
        "Pausar", icon=ft.icons.PAUSE, on_click=_on_pause_click,
        visible=False,
        tooltip="Detiene la cámara (el piloto se apaga) sin perder la calibración",
    )
    control_bar = ft.Container(
        content=ft.Row([
            status_dot, status_text, ft.Container(expand=True),
            btn_calibrate, btn_start, btn_pause,
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor="#16162A",
        border=ft.border.all(1, "#2A2A4A"),
        border_radius=10,
        padding=ft.padding.symmetric(vertical=8, horizontal=14),
    )

    _ui = {"calibration": {}, "signature": None}

    # ----------------------------------------------------------------
    # Modo estudio: código de participante y condición del protocolo
    # ----------------------------------------------------------------
    study_cfg = thresholds.get("study", {})
    study_on = bool(study_cfg.get("enabled", False))
    study_conditions = study_cfg.get("conditions", [])

    def _on_participant_change(e):
        inference_thread.set_participant(e.control.value or "")
        _refresh_controls(force=True)
        page.update()

    participant_field = ft.TextField(
        label="Código de participante (P01, P02…)",
        hint_text="Solo un código, nunca el nombre",
        on_change=_on_participant_change,
        text_size=14, height=48, border_color="#2A2A4A",
        visible=study_on,
    )

    def _on_condition_change(e):
        inference_thread.set_condition(e.control.value or "")

    condition_dropdown = ft.Dropdown(
        label="Condición del protocolo",
        value="libre",
        options=[ft.dropdown.Option(c["id"], c.get("label", c["id"]))
                 for c in study_conditions],
        on_change=_on_condition_change,
        text_size=14, width=360, border_color="#2A2A4A",
    )
    study_bar = ft.Container(
        content=ft.Row([
            ft.Icon(ft.icons.SCIENCE_OUTLINED, color="#00BCD4", size=18),
            ft.Text("Modo estudio", size=13, weight=ft.FontWeight.W_600,
                    color=ft.colors.WHITE),
            ft.Container(expand=True),
            condition_dropdown,
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor="#16162A", border=ft.border.all(1, "#2A2A4A"),
        border_radius=10,
        padding=ft.padding.symmetric(vertical=6, horizontal=14),
        visible=False,
    )

    def _refresh_controls(force: bool = False) -> bool:
        """
        Ajusta la barra de control al modo del hilo de inferencia.

        Se llama en cada vuelta del bucle de la UI, no solo cuando llega un
        estado nuevo: en pausa no se publican estados, y los botones deben
        reflejar igualmente el cambio. Devuelve True si algo cambió.
        """
        mode = inference_thread.mode
        paused = inference_thread.is_paused
        calib = _ui["calibration"]
        checks_ok = bool(calib.get("checks", {}).get("ok"))
        phase = calib.get("phase", "idle")
        can_start = inference_thread.can_start

        code = inference_thread.participant
        signature = (mode, paused, checks_ok, phase, can_start, code)
        if not force and signature == _ui["signature"]:
            return False
        _ui["signature"] = signature

        if mode == "monitoring" and paused:
            status_dot.bgcolor, status_text.value = "#FFAA00", "En pausa — cámara apagada"
        elif mode == "monitoring":
            status_dot.bgcolor = "#00C853"
            status_text.value = ("Monitoreando" if not code
                                 else f"Monitoreando · {code}")
        elif mode == "calibrating":
            status_dot.bgcolor, status_text.value = "#FFAA00", "Calibrando…"
        elif mode == "ready":
            status_dot.bgcolor, status_text.value = "#00BCD4", "Calibrado — listo para iniciar"
        else:
            status_dot.bgcolor = "#555555"
            status_text.value = ("Sin calibrar" if phase != "failed"
                                 else "Calibración fallida — reinténtalo")

        if mode == "calibrating":
            btn_calibrate.text, btn_calibrate.icon = "Cancelar", ft.icons.CLOSE
            btn_calibrate.disabled = False
        else:
            ya_calibrado = mode in ("ready", "monitoring") or phase == "done"
            btn_calibrate.text = "Recalibrar" if ya_calibrado else "Calibrar"
            btn_calibrate.icon = ft.icons.TUNE
            # Solo se puede calibrar con el encuadre correcto: una referencia
            # tomada mal encuadrado sesgaría toda la sesión. Durante el
            # seguimiento se permite siempre (la calibración comprueba el
            # encuadre por su cuenta y espera a que sea correcto).
            btn_calibrate.disabled = not (checks_ok or mode == "monitoring")
        btn_calibrate.tooltip = (
            "Termina la sesión actual y vuelve a calibrar"
            if mode == "monitoring" else
            "Mide tu postura neutra y tu ojo abierto (≈20 s)")

        btn_start.visible = mode != "monitoring"
        btn_start.disabled = not can_start
        btn_start.tooltip = (
            "Introduce el código del participante para poder iniciar"
            if (study_on and not code and mode != "monitoring")
            else "Empieza a vigilar tu postura y fatiga")
        participant_field.visible = study_on and mode != "monitoring"
        study_bar.visible = study_on and mode == "monitoring"
        # Flet mantiene un bgcolor explícito aunque el botón esté
        # deshabilitado: sin esto, "Iniciar" parecía activo sin calibrar.
        btn_start.bgcolor = "#00BCD4" if can_start else None
        btn_start.color = "#0A0A16" if can_start else None

        if mode == "monitoring" and paused:
            video_feed.show_paused()

        btn_pause.visible = mode == "monitoring"
        btn_pause.text = "Reanudar" if paused else "Pausar"
        btn_pause.icon = ft.icons.PLAY_ARROW if paused else ft.icons.PAUSE

        if tray is not None:
            if tray.is_paused != paused:
                tray.set_paused(paused)
            tray.refresh_menu()
        return True

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
            control_bar,
            study_bar,
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
        content=ft.Column([participant_field,
                           calibration_view.build(),
                           telemetry_panel.build()],
                          scroll=ft.ScrollMode.AUTO, spacing=10),
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
        try:
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
                    _ui["calibration"] = state.calibration or {}
                    calibration_view.update(_ui["calibration"], state.mode)
                    _refresh_controls()
                    page.update()
                except Empty:
                    # Sin estado nuevo (p. ej. en pausa): los botones deben
                    # reflejar igualmente un cambio de modo.
                    if _refresh_controls():
                        page.update()
                except Exception:
                    logger.exception("UI loop error")

                await asyncio.sleep(update_interval_ms / 1000.0)
        except asyncio.CancelledError:
            # Flet cancela esta tarea al cerrarse la sesión. Se termina en
            # silencio: si la cancelación se propagara, el envoltorio de
            # page.run_task() de Flet 0.24 llama a f.exception() sobre un
            # futuro cancelado, que LANZA CancelledError, y vuelca un
            # "ERROR ... Traceback" en el log en cada cierre de la app. No es
            # un fallo real, pero en una bitácora de validación lo parece.
            return

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
        toast_notifier.close()
        if tray is not None:
            tray.stop()
        logger.info("Aplicación cerrada limpiamente.")

    # ----------------------------------------------------------------
    # Bandeja del sistema y monitoreo en segundo plano
    #
    # Con `background_monitoring` activo, cerrar la ventana la OCULTA: la
    # captura y la inferencia siguen corriendo en sus hilos y el usuario
    # recupera el panel desde la bandeja. Salir de verdad es una acción
    # explícita, porque terminar el proceso cierra la sesión en SQLite y parte
    # en dos la bitácora de la jornada.
    # ----------------------------------------------------------------
    background_enabled = bool(alerts_cfg.get("background_monitoring", True))

    def _show_window():
        """Restaura la ventana desde la bandeja. Llamado desde el hilo de pystray."""
        def _restore():
            page.window.visible = True
            page.window.minimized = False
            page.window.to_front()
            page.update()
            inference_thread.set_ui_visible(True)
        try:
            page.run_thread(_restore)
        except Exception:
            logger.exception("No se pudo restaurar la ventana.")

    def _hide_window():
        """Oculta la ventana sin detener el monitoreo."""
        page.window.visible = False
        page.update()
        inference_thread.set_ui_visible(False)
        logger.info("Ventana oculta; el monitoreo continúa en segundo plano.")

    def _on_tray_pause(paused: bool):
        inference_thread.set_paused(paused)

    def _on_tray_quit():
        _shutdown()
        try:
            page.run_thread(page.window.destroy)
        except Exception:
            logger.debug("La ventana ya no estaba disponible al salir.")

    def _on_tray_recalibrate():
        # La calibración guiada necesita que el usuario vea las instrucciones
        # y el encuadre: se abre el panel antes de empezar.
        _show_window()
        inference_thread.request_calibration()

    tray = TrayIcon(
        on_show=_show_window,
        on_toggle_pause=_on_tray_pause,
        on_recalibrate=_on_tray_recalibrate,
        on_quit=_on_tray_quit,
        can_pause=lambda: inference_thread.mode == "monitoring",
    ) if background_enabled else None

    tray_active = tray.start() if tray is not None else False

    def _on_window_event(e):
        event = e.data or getattr(e, "type", None)

        # Minimizar/ocultar solo afecta al render, nunca a la medición.
        #
        # Deliberadamente NO se reacciona a "blur": una ventana puede perder el
        # foco y seguir visible en pantalla (segundo monitor, ventanas lado a
        # lado). Congelar ahí el render dejaría al usuario mirando un feed
        # detenido. Solo cuentan los eventos en los que la ventana deja de
        # verse de verdad.
        if event in ("minimize", "hide"):
            inference_thread.set_ui_visible(False)
            return
        if event in ("restore", "show", "maximize", "focus"):
            inference_thread.set_ui_visible(True)
            return

        if event == "close" or getattr(e, "type", None) == ft.WindowEventType.CLOSE:
            # Solo se oculta si la bandeja está realmente activa: sin icono
            # visible, ocultar la ventana dejaría un proceso fantasma que el
            # usuario no puede recuperar ni cerrar.
            #
            # Y solo si se está monitoreando: sin seguimiento activo no hay
            # nada que vigilar en segundo plano, y dejar la cámara encendida
            # tras cerrar la ventana no tendría ningún sentido para el usuario.
            if tray_active and inference_thread.mode == "monitoring":
                _hide_window()
                tray.notify(
                    "Monitoreo activo",
                    "El sistema sigue vigilando tu postura en segundo plano. "
                    "Usa 'Salir' en la bandeja para terminar.")
            else:
                _shutdown()
                page.window.destroy()

    page.window.prevent_close = True
    page.window.on_event = _on_window_event

    import atexit
    atexit.register(_shutdown)

    vt.start()
    inference_thread.start()
    page.run_task(_ui_update_loop)

    if tray_active:
        logger.info("Monitoreo en segundo plano habilitado (icono de bandeja).")
    elif background_enabled:
        logger.warning(
            "background_monitoring está activo pero no hay bandeja disponible: "
            "cerrar la ventana terminará la aplicación.")


if __name__ == "__main__":
    ft.app(target=main)
