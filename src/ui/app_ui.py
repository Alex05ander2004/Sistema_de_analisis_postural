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
from src.capture.camera_source import backend_api, resolve_camera_source
from src.capture.camera_usage import others_using_camera, own_markers
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
from src.study.field_study import (
    GROUP_CONTROL, GROUP_INTERVENTION, MAX_BASELINE_DAYS, MIN_BASELINE_DAYS,
    MODE_FIELD, MODE_LAB, PHASE_BASELINE, PHASE_CONTROL, PHASE_VALIDATION,
    LocalProfile, field_phase, normalize_code, pending_report_date, today_iso,
)
from src.study.data_bundle import create_bundle
from src.study.personal_history import daily_history, week_comparison
from src.tools.calibrar_fov import corrected_hfov
from src.version import APP_VERSION, config_hash
from src.paths import data_dir, data_file, resource_root, setup_file_logging
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

_THRESHOLDS_PATH = resource_root() / "config" / "thresholds.json"
# Perfil propio de ESTE equipo (código, modo del estudio, FOV de su cámara).
# Va aparte para que thresholds.json sea idéntico en todos los equipos.
_PROFILE_PATH = data_dir() / "estudio_local.json"


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
                 start_mode: str = "monitoring",
                 profile: LocalProfile | None = None,
                 log_metrics_interval_sec: float | None = None):
        """
        start_mode:
            "monitoring" empieza a vigilar de inmediato (comportamiento
            anterior; lo usan las pruebas y las herramientas). La app arranca
            en "preview" y el usuario calibra e inicia desde la interfaz.
        profile:
            Perfil local del equipo (data/estudio_local.json): código del
            participante, modo del estudio (laboratorio / campo) y FOV de su
            cámara. None = modo laboratorio sin código.
        log_metrics_interval_sec:
            Cadencia de metrics_log en SEGUNDOS. None = la de
            storage.metrics_interval_sec; si tampoco está, se registra cada
            `log_metrics_every_n` frames (comportamiento anterior).
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
        # Cesión temporal de la cámara a una videollamada (ver yield_camera)
        self._yield_until = 0.0
        self._yield_timer: threading.Timer | None = None
        if start_mode not in ("monitoring", "preview"):
            raise ValueError("start_mode debe ser 'monitoring' o 'preview'")
        self._mode = start_mode
        self._commands: Queue = Queue()

        # Modo estudio: código de participante y condición del protocolo. Son
        # cadenas que la interfaz asigna y este hilo solo lee: una asignación
        # de str es atómica en CPython, no hacen falta cerrojos.
        study_cfg = thresholds.get("study", {})
        self._study_enabled = bool(study_cfg.get("enabled", False))
        self._profile = profile or LocalProfile()
        self._study_mode = self._profile.modo if self._study_enabled else ""
        self._participant = self._profile.participante
        self._condition = ""
        # Estudio de campo: fase de la sesión en curso y, en la línea base,
        # alertas silenciadas (se registran pero no se muestran).
        self._field_cfg = study_cfg.get("field", {})
        self._phase = ""
        self._day_number = 0
        self._alerts_muted = False
        self._config_hash = config_hash(_THRESHOLDS_PATH)

        # Cadencia de la bitácora de métricas. Por TIEMPO y no por frames:
        # con "cada 30 frames" un equipo a 10 FPS registraba una fila cada
        # 3 s y otro a 30 FPS una por segundo, y los indicadores por tiempo
        # (minutos de uso, % del tiempo desviado) no eran comparables entre
        # los equipos de los participantes.
        if log_metrics_interval_sec is None:
            log_metrics_interval_sec = thresholds.get("storage", {}).get(
                "metrics_interval_sec")
        self._log_interval_sec = (float(log_metrics_interval_sec)
                                  if log_metrics_interval_sec else None)
        self._last_log_ts = 0.0
        # Fecha local en que empezó la sesión de campo en curso; ver
        # _check_day_rollover().
        self._session_date = ""
        self._last_day_check = 0.0

        ui_cfg_early = thresholds.get("ui", {})
        self._skip_render_when_hidden = bool(
            ui_cfg_early.get("skip_render_when_hidden", True))
        self._pause_on_hidden = bool(
            thresholds.get("alerts", {}).get("pause_on_window_hidden", False))

        cam_cfg = thresholds.get("camera", {})
        self._frame_size = (cam_cfg.get("resolution_width", 640),
                            cam_cfg.get("resolution_height", 480))
        # El FOV calibrado para la cámara de ESTE equipo (perfil local)
        # prevalece sobre el genérico de thresholds.json.
        self._hfov_deg = (self._profile.camera_hfov_deg
                          or cam_cfg.get("horizontal_fov_deg", 60.0))
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
        # Tope de FPS de inferencia (0 = sin tope). Las ventanas de alerta son
        # de 3–8 s: inferir a 22–30 FPS durante una jornada de 8 h solo gasta
        # CPU y batería del equipo del participante (medido 2026-10-29:
        # ~1.6 núcleos sin tope). Ver mediapipe.max_inference_fps.
        max_fps = float(mp_cfg.get("max_inference_fps", 0) or 0)
        self._min_frame_interval = 1.0 / max_fps if max_fps > 0 else 0.0

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
        self._fsm.on_suppressed = self._handle_suppressed
        self._alert_policy = thresholds.get("alert_policy") or None
        self._apply_alert_policy()

        storage_cfg = thresholds.get("storage", {})
        self._db = HistoryLogger(
            db_path=str(data_file(storage_cfg.get("db_path", "data/history.db")))
        )
        if start_mode == "monitoring":
            self._db.start_session(notes="Sesión automática",
                                   participant_code=self._participant,
                                   app_version=APP_VERSION,
                                   config_hash=self._config_hash)

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

    @property
    def ui_visible(self) -> bool:
        """True si la ventana está a la vista (no minimizada ni oculta)."""
        return self._ui_visible.is_set()

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
    # Ceder la cámara a una videollamada
    # ------------------------------------------------------------------

    @property
    def yielding(self) -> bool:
        return self._yield_until > 0.0

    @property
    def yield_remaining(self) -> float:
        return max(0.0, self._yield_until - time.monotonic())

    def yield_camera(self, seconds: float = 20.0) -> None:
        """
        Suelta la cámara `seconds` segundos y la vuelve a abrir sola.

        Prueba del 2026-10-29 con Google Meet: si la app abre la cámara
        PRIMERO, Meet no recibe imagen ("otra aplicación la está usando"),
        tanto con MSMF como con DirectShow. Cediéndola, Meet la toma. Al
        reabrir, la app solo recibe ~1 frame/s inútil mientras dure la
        llamada: VideoThread lo detecta (STATUS_SHARED), no registra nada en
        ese tiempo y reintenta cada 15 s hasta que Meet la suelta.

        Windows no avisa cuándo otra app quiere la cámara (el registro de uso
        solo apunta a las apps que LOGRAN abrirla), así que lo pide el
        usuario: botón «Ceder cámara» o menú de la bandeja.
        """
        self.cancel_yield()
        self._yield_until = time.monotonic() + seconds
        self.set_paused(True)
        self._yield_timer = threading.Timer(seconds, self._end_yield)
        self._yield_timer.daemon = True
        self._yield_timer.start()
        logger.info("Cámara cedida %.0f s a otra aplicación.", seconds)

    def _end_yield(self) -> None:
        if not self.yielding:
            return
        self._yield_until = 0.0
        self._yield_timer = None
        logger.info("Fin de la cesión: se vuelve a abrir la cámara.")
        self.set_paused(False)

    def cancel_yield(self) -> None:
        """Anula una cesión en curso (el usuario pausó o reanudó a mano)."""
        if self._yield_timer is not None:
            self._yield_timer.cancel()
        self._yield_timer = None
        self._yield_until = 0.0

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
    def study_mode(self) -> str:
        """laboratorio / campo ("" sin estudio)."""
        return self._study_mode

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def day_number(self) -> int:
        return self._day_number

    @property
    def alerts_muted(self) -> bool:
        return self._alerts_muted

    @property
    def history(self) -> HistoryLogger:
        """Bitácora (thread-safe): autorreporte y exportación desde la UI."""
        return self._db

    @property
    def hfov_deg(self) -> float:
        return self._hfov_deg

    def set_hfov(self, hfov_deg: float) -> None:
        """FOV de la cámara; asignar un float es atómico en CPython."""
        self._hfov_deg = float(hfov_deg)

    def set_study_mode(self, mode: str) -> None:
        """Cambia entre laboratorio y campo (surte efecto al pulsar Iniciar)."""
        if self._study_enabled and mode in (MODE_LAB, MODE_FIELD):
            self._study_mode = mode

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
        self._participant = normalize_code(code)

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
                self._open_session()
                # Laboratorio: uso libre hasta que el evaluador marque la
                # primera condición del protocolo. Campo: siempre uso libre.
                self._condition = ("libre" if self._study_mode == MODE_LAB
                                   else "")
                self._fsm.reset_timers()
                self._smoothers.reset()
                self._mode = "monitoring"
                logger.info("Modo: monitoreando.")

    def _apply_alert_policy(self) -> None:
        """
        Laboratorio (validación): política de detección, para medir tramo a
        tramo. Campo y uso normal: política de aviso, que no satura.
        """
        self._fsm.set_alert_policy(
            None if self._study_mode == MODE_LAB else self._alert_policy)

    def _open_session(self, extra_note: str = "") -> None:
        """Decide la fase y abre la sesión en la bitácora."""
        self._decide_phase()
        self._apply_alert_policy()
        notes = self._session_notes() + extra_note
        self._db.start_session(notes=notes,
                               participant_code=self._participant,
                               study_phase=self._phase,
                               app_version=APP_VERSION,
                               config_hash=self._config_hash)
        self._session_date = today_iso()

    def _check_day_rollover(self, now: float) -> None:
        """
        En campo, una sesión no puede abarcar dos días.

        Si el participante nunca pulsa Salir (suspende el portátil por la noche
        y sigue a la mañana siguiente), la sesión seguiría abierta con la fase
        del primer día: la línea base no terminaría nunca, o un día de
        intervención quedaría registrado como línea base. Al cambiar la fecha
        se cierra la sesión y se abre otra con la fase recalculada. La
        calibración se conserva: recalibrar es siempre una acción explícita.
        """
        if (self._mode != "monitoring" or self._study_mode != MODE_FIELD
                or now - self._last_day_check < 30.0):
            return
        self._last_day_check = now
        if self._session_date and today_iso() != self._session_date:
            logger.info("Cambio de día durante el seguimiento: nueva sesión.")
            self._db.end_session()
            self._open_session(" (continuación tras cambio de día)")
            self._fsm.reset_timers()
            self._smoothers.reset()

    def _decide_phase(self) -> None:
        """
        Fase de la sesión que empieza. En campo, línea base (alertas
        silenciadas) hasta completar `baseline_days` días válidos ANTERIORES
        a hoy; después, intervención. Ver src/study/field_study.py.
        """
        self._alerts_muted = False
        self._day_number = 0
        if self._study_mode == MODE_LAB:
            self._phase = PHASE_VALIDATION
        elif self._study_mode == MODE_FIELD:
            try:
                minutes = self._db.field_minutes_by_date(self._participant)
            except Exception:
                logger.exception("No se pudieron leer los días de uso; "
                                 "se asume intervención.")
                minutes = {}
            self._phase, self._day_number, done = field_phase(
                self._profile, minutes, today_iso(),
                default_baseline_days=int(self._field_cfg.get(
                    "baseline_days", 5)),
                min_minutes=float(self._field_cfg.get(
                    "baseline_min_minutes", 60)))
            # Línea base y control: se mide y se registra, pero no se avisa.
            self._alerts_muted = self._phase in (PHASE_BASELINE,
                                                 PHASE_CONTROL)
            logger.info("Estudio de campo: día de uso %d, fase %s (%d días "
                        "de línea base completados).", self._day_number,
                        self._phase, done)
        else:
            self._phase = ""

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

    def _handle_suppressed(self, alert_event) -> None:
        """Detección durante el enfriamiento: solo a la bitácora."""
        try:
            self._db.log_event(alert_event, condition=self._condition,
                               notified=False, suppressed=True)
        except Exception:
            logger.exception("No se pudo registrar la detección suprimida")

    def _handle_alert(self, alert_event) -> None:
        """
        Callback del FSM. Persiste el evento **y** lo muestra en la UI.

        La bitácora de eventos es un entregable del Capítulo IV, así que la
        escritura en SQLite va primero y protegida: si la UI fallara, el
        evento ya está registrado.
        """
        muted = self._alerts_muted
        try:
            self._db.log_event(alert_event, condition=self._condition,
                               notified=not muted)
        except Exception:
            logger.exception("No se pudo registrar el evento en la bitácora")
        # Línea base del estudio de campo: la alerta queda registrada (es la
        # medida de comparación) pero el participante no la ve.
        if muted:
            return
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
                self._check_day_rollover(time.perf_counter())

                # Pausado: la cámara está liberada, así que no hay nada que
                # leer. Se espera sin quemar CPU y sin tocar el FSM.
                if self._paused.is_set():
                    self._stop_event.wait(0.2)
                    continue


                frame = self._vt.get_frame(timeout=0.05)
                if frame is None:
                    continue

                loop_start = time.perf_counter()
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
                if self._mode == "monitoring" and self._should_log(metrics):
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

                self._throttle(loop_start)

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

    def _throttle(self, loop_start: float) -> None:
        """Espera lo que falte para no superar max_inference_fps."""
        if self._min_frame_interval <= 0:
            return
        remaining = self._min_frame_interval - (time.perf_counter() - loop_start)
        if remaining > 0:
            self._stop_event.wait(remaining)

    def _should_log(self, metrics: SensorMetrics) -> bool:
        if self._log_interval_sec is None:
            return self._frame_n % self._log_metrics_every_n == 0
        if metrics.monotonic - self._last_log_ts >= self._log_interval_sec:
            self._last_log_ts = metrics.monotonic
            return True
        return False

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
        self.cancel_yield()
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Aplicación Flet principal
# ---------------------------------------------------------------------------

def main(page: ft.Page):
    thresholds = _load_thresholds()
    ui_cfg = thresholds.get("ui", {})
    cam_cfg = thresholds.get("camera", {})

    profile = LocalProfile.load(_PROFILE_PATH)
    page.title = "Monitor Postural y Fatiga — Edge AI"
    _icon_path = resource_root() / "assets" / "icon.ico"
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
        # `inference_thread` se crea más abajo; la lambda lo resuelve al
        # llegar la primera alerta, cuando ya existe.
        is_ui_visible=lambda: inference_thread.ui_visible,
    )

    def on_alert_fired(alert_event):
        """Llamado desde el InferenceThread. No debe bloquear."""
        dispatcher.dispatch(alert_event)

    state_queue: Queue = Queue(maxsize=1)

    source_index = cam_cfg.get("source_index", -1)
    cam_api = backend_api(cam_cfg.get("backend", "auto"))
    camera_source = resolve_camera_source(source_index, api=cam_api)

    vt = VideoThread(
        source=camera_source,
        width=video_width,
        height=video_height,
        target_fps=cam_cfg.get("target_fps", 30),
        # Con autodetección, al perder la cámara se vuelve a buscar cuál da
        # imagen real (ver VideoThread.source_resolver).
        source_resolver=((lambda: resolve_camera_source(-1, verbose=False,
                                                        api=cam_api))
                         if source_index == -1 else None),
        api_preference=cam_api,
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
        profile=profile,
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
            (header_actions := ft.Row([], spacing=4)),
            ft.Text("100% Local • Sin nube", size=11,
                    color=ft.colors.WHITE38, italic=True),
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
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

    def _on_yield_click(_):
        inference_thread.yield_camera(yield_seconds)
        _refresh_controls(force=True)

    def _on_pause_click(_):
        paused = not inference_thread.is_paused
        inference_thread.cancel_yield()
        _yield["auto"] = False          # el usuario manda sobre la cesión
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
    yield_seconds = float(cam_cfg.get("yield_seconds", 20))
    btn_yield = ft.OutlinedButton(
        "Ceder cámara", icon=ft.icons.VIDEO_CALL, on_click=_on_yield_click,
        visible=False,
        tooltip=("Si Meet, Teams o Zoom dicen que la cámara está en uso: la "
                 "app la suelta y vuelve a medir sola al terminar la llamada"))
    control_bar = ft.Container(
        content=ft.Row([
            status_dot, status_text, ft.Container(expand=True),
            btn_calibrate, btn_start, btn_yield, btn_pause,
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
    study_title = ft.Text("Modo estudio", size=13,
                          weight=ft.FontWeight.W_600, color=ft.colors.WHITE)
    phase_text = ft.Text("", size=12, color=ft.colors.WHITE70)
    study_bar = ft.Container(
        content=ft.Row([
            ft.Icon(ft.icons.SCIENCE_OUTLINED, color="#00BCD4", size=18),
            study_title, phase_text,
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
        smode = inference_thread.study_mode
        signature = (mode, paused, checks_ok, phase, can_start, code, smode,
                     inference_thread.phase)
        if not force and signature == _ui["signature"]:
            return False
        _ui["signature"] = signature

        if mode == "monitoring" and paused and inference_thread.yielding:
            status_dot.bgcolor = "#FFAA00"
            status_text.value = "Cámara cedida a la videollamada"
        elif mode == "monitoring" and paused:
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
        if study_on and not code and mode != "monitoring":
            btn_start.tooltip = (
                "Introduce el código del participante para poder iniciar"
                if smode == MODE_LAB else
                "Falta el código: el investigador lo configura en «Estudio»")
        else:
            btn_start.tooltip = "Empieza a vigilar tu postura y fatiga"
        # En campo el código viene del perfil del equipo y no se edita aquí:
        # el participante no debe poder cambiarlo por error.
        participant_field.visible = (study_on and smode == MODE_LAB
                                     and mode != "monitoring")
        study_bar.visible = study_on and mode == "monitoring"
        condition_dropdown.visible = smode == MODE_LAB
        if smode == MODE_FIELD:
            study_title.value = f"Estudio · {code or 'sin código'}"
            if inference_thread.alerts_muted:
                phase_text.value = (f"Día {inference_thread.day_number} · "
                                    "Línea base: la app mide pero no avisa")
            else:
                phase_text.value = (f"Día {inference_thread.day_number} · "
                                    "Avisos activos")
        else:
            study_title.value = "Modo estudio"
            phase_text.value = ""
        btn_study.disabled = mode in ("monitoring", "calibrating")
        # Flet mantiene un bgcolor explícito aunque el botón esté
        # deshabilitado: sin esto, "Iniciar" parecía activo sin calibrar.
        btn_start.bgcolor = "#00BCD4" if can_start else None
        btn_start.color = "#0A0A16" if can_start else None

        if mode == "monitoring" and paused:
            video_feed.show_paused()

        btn_pause.visible = mode == "monitoring"
        btn_yield.visible = mode == "monitoring" and not paused
        btn_pause.text = "Reanudar" if paused else "Pausar"
        btn_pause.icon = ft.icons.PLAY_ARROW if paused else ft.icons.PAUSE

        if tray is not None:
            if tray.is_paused != paused:
                tray.set_paused(paused)
            tray.refresh_menu()
        return True

    # ----------------------------------------------------------------
    # Estudio de campo: configuración del equipo, exportación y encuesta
    # ----------------------------------------------------------------
    field_cfg = study_cfg.get("field", {})

    def _snack(msg: str, color: str = "#2E7D32", ms: int = 8000):
        page.open(ft.SnackBar(content=ft.Text(msg, color=ft.colors.WHITE),
                              bgcolor=color, duration=ms))
        page.update()

    # --- Configuración del estudio (la usa el investigador) -------------
    cfg_code = ft.TextField(label="Código de participante (P01, P02…)",
                            text_size=14, width=300)
    cfg_mode = ft.Dropdown(
        label="Modo", width=300, text_size=14,
        options=[ft.dropdown.Option(MODE_LAB,
                                    "Laboratorio (protocolo guiado)"),
                 ft.dropdown.Option(MODE_FIELD,
                                    "Campo (uso diario en casa)")])
    cfg_group = ft.Dropdown(
        label="Grupo", width=300, text_size=14,
        options=[ft.dropdown.Option(GROUP_INTERVENTION,
                                    "Intervención (recibe avisos)"),
                 ft.dropdown.Option(GROUP_CONTROL,
                                    "Control (solo medición, nunca avisa)")])
    cfg_days = ft.Dropdown(
        label="Días de línea base (sorteados)", width=300, text_size=14,
        options=[ft.dropdown.Option(str(n)) for n in
                 range(MIN_BASELINE_DAYS, MAX_BASELINE_DAYS + 1)])
    cfg_real = ft.TextField(label="Distancia real medida (m)", width=260,
                            text_size=14, hint_text="p. ej. 0.60")
    cfg_fov_info = ft.Text("", size=12, color=ft.colors.WHITE70)
    cfg_error = ft.Text("", size=12, color="#FF6B6B")
    _pending_fov = {"value": None}

    def _fov_info():
        d = _ui.get("distance")
        est = f"{d:.2f} m" if d else "sin lectura (colócate frente a la cámara)"
        nuevo = _pending_fov["value"]
        extra = f"  →  nuevo FOV: {nuevo:.1f}°" if nuevo else ""
        cfg_fov_info.value = (f"FOV actual: {inference_thread.hfov_deg:.1f}° · "
                              f"distancia que estima la app ahora: {est}{extra}")

    def _on_calc_fov(_):
        cfg_error.value = ""
        d = _ui.get("distance")
        try:
            real = float((cfg_real.value or "").replace(",", "."))
        except ValueError:
            real = 0.0
        if not d:
            cfg_error.value = "La app no está midiendo distancia ahora mismo."
        elif not 0.3 <= real <= 1.5:
            cfg_error.value = "Escribe la distancia medida en metros (0.30–1.50)."
        else:
            nuevo = corrected_hfov(inference_thread.hfov_deg, d, real,
                                   video_width)
            if not 50.0 <= nuevo <= 85.0:
                cfg_error.value = (f"Sale {nuevo:.1f}°, fuera de lo normal "
                                   "(55–78°). Repite la medición de frente, "
                                   "midiendo del lente a los hombros.")
            else:
                _pending_fov["value"] = nuevo
        _fov_info()
        page.update()

    def _on_save_study(_):
        code = normalize_code(cfg_code.value)
        if study_on and not code:
            cfg_error.value = "Falta el código de participante."
            page.update()
            return
        profile.participante = code
        profile.modo = cfg_mode.value or MODE_LAB
        profile.grupo = cfg_group.value or GROUP_INTERVENTION
        profile.dias_linea_base = (int(cfg_days.value) if cfg_days.value
                                   else None)
        if profile.modo == MODE_FIELD and not profile.inscrito:
            profile.inscrito = today_iso()
        if _pending_fov["value"]:
            profile.camera_hfov_deg = round(_pending_fov["value"], 1)
            inference_thread.set_hfov(profile.camera_hfov_deg)
        try:
            profile.save(_PROFILE_PATH)
        except OSError:
            logger.exception("No se pudo guardar el perfil local.")
            cfg_error.value = "No se pudo guardar data/estudio_local.json."
            page.update()
            return
        inference_thread.set_participant(code)
        inference_thread.set_study_mode(profile.modo)
        participant_field.value = code
        page.close(study_dialog)
        _refresh_controls(force=True)
        detalle = (" · grupo control" if profile.grupo == GROUP_CONTROL
                   else f" · línea base de "
                        f"{profile.baseline_days(field_cfg.get('baseline_days', 5))}"
                        f" días")
        _snack(f"Guardado: {code or 'sin código'} · modo {profile.modo}"
               + (detalle if profile.modo == MODE_FIELD else ""))

    study_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Configuración del estudio"),
        content=ft.Column([
            ft.Text("Para el investigador, al instalar la app en el equipo "
                    "del participante.", size=12, color=ft.colors.WHITE54),
            cfg_code, cfg_mode, cfg_group, cfg_days,
            ft.Text("Grupo y días de línea base solo cuentan en modo campo. "
                    "Sortea los días de línea base entre los participantes "
                    "de intervención (p. ej. 5, 7 y 9).",
                    size=11, color=ft.colors.WHITE54),
            ft.Divider(),
            ft.Text("Cámara de este equipo (FOV)", weight=ft.FontWeight.W_600),
            ft.Text("Siéntate a una distancia medida con cinta métrica, del "
                    "lente a los hombros, escríbela y pulsa Calcular.",
                    size=12, color=ft.colors.WHITE54),
            ft.Row([cfg_real, ft.OutlinedButton("Calcular",
                                                on_click=_on_calc_fov)]),
            cfg_fov_info, cfg_error,
            ft.Text(f"Versión {APP_VERSION}", size=11,
                    color=ft.colors.WHITE38),
        ], tight=True, spacing=10, width=440),
        actions=[
            ft.TextButton("Cancelar",
                          on_click=lambda _: page.close(study_dialog)),
            ft.ElevatedButton("Guardar", on_click=_on_save_study),
        ],
    )

    def _on_study_click(_):
        cfg_code.value = inference_thread.participant
        cfg_mode.value = inference_thread.study_mode or MODE_LAB
        cfg_group.value = profile.grupo
        cfg_days.value = str(profile.baseline_days(
            field_cfg.get("baseline_days", 5)))
        cfg_real.value = ""
        cfg_error.value = ""
        _pending_fov["value"] = None
        _fov_info()
        page.open(study_dialog)

    # --- Exportar mis datos ----------------------------------------------
    def _reveal(path: Path):
        if sys.platform == "win32":
            import subprocess
            try:
                subprocess.Popen(["explorer", "/select,", str(path)])
            except OSError:
                logger.debug("No se pudo abrir el Explorador.")

    def _on_export_click(_):
        btn_export.disabled = True
        page.update()

        def _work():
            try:
                path = create_bundle(
                    inference_thread.history, _PROFILE_PATH,
                    _THRESHOLDS_PATH, inference_thread.participant,
                    APP_VERSION, log_dir=data_dir() / "logs")
                _snack(f"Datos guardados en el Escritorio: {path.name}. "
                       "Envía ese archivo al investigador.", ms=12000)
                _reveal(path)
            except Exception:
                logger.exception("No se pudieron exportar los datos.")
                _snack("No se pudieron exportar los datos. Avisa al "
                       "investigador.", color="#C62828")
            finally:
                btn_export.disabled = False
                page.update()

        threading.Thread(target=_work, name="export", daemon=True).start()

    btn_export = ft.TextButton(
        "Exportar mis datos", icon=ft.icons.DOWNLOAD,
        on_click=_on_export_click, visible=study_on,
        tooltip="Guarda un .zip en el Escritorio para enviarlo al investigador")
    btn_study = ft.IconButton(
        icon=ft.icons.SETTINGS_OUTLINED, icon_size=18,
        on_click=_on_study_click, visible=study_on,
        tooltip="Configuración del estudio (investigador)")

    # --- Mi historial (OE 1.3.2.5) -----------------------------------------
    def _history_locked() -> str:
        """
        En la línea base del estudio de campo el historial no se muestra:
        ver su % de buena postura sería ya una retroalimentación, y la línea
        base debe medir la conducta SIN el sistema. Devuelve el motivo, o ""
        si se puede mostrar.
        """
        if not (study_on and inference_thread.study_mode == MODE_FIELD):
            return ""
        if profile.grupo == GROUP_CONTROL:
            return ("Durante el estudio la app solo mide. Tu historial estará "
                    "disponible al terminar el estudio.")
        default_days = int(field_cfg.get("baseline_days", 5))
        try:
            minutes = inference_thread.history.field_minutes_by_date(
                inference_thread.participant)
            phase, _, done = field_phase(
                profile, minutes, today_iso(),
                default_baseline_days=default_days,
                min_minutes=float(field_cfg.get("baseline_min_minutes", 60)))
        except Exception:
            logger.exception("No se pudo decidir la fase para el historial.")
            return ""
        if phase != PHASE_BASELINE:
            return ""
        faltan = max(1, profile.baseline_days(default_days) - done)
        return ("Durante los primeros días del estudio la app solo mide. Tu "
                "historial y los avisos se activarán cuando completes "
                f"{faltan} día(s) más de uso.")

    def _fmt(v, nd=0, suf=""):
        return "—" if v is None else f"{v:.{nd}f}{suf}"

    def _history_content(rows) -> ft.Control:
        if not rows:
            return ft.Text("Aún no hay días registrados. El historial se llena "
                           "a medida que usas el seguimiento.",
                           size=13, color=ft.colors.WHITE70)
        hoy = rows[0]

        def _card(titulo, valor, nota=""):
            return ft.Container(
                content=ft.Column([
                    ft.Text(titulo, size=10, color=ft.colors.WHITE54),
                    ft.Text(valor, size=20, weight=ft.FontWeight.BOLD,
                            color=ft.colors.WHITE),
                    ft.Text(nota, size=10, color=ft.colors.WHITE38),
                ], spacing=2),
                bgcolor="#16162A", border_radius=8, expand=True,
                padding=ft.padding.symmetric(vertical=8, horizontal=12))

        es_hoy = hoy["fecha"] == today_iso()
        cards = ft.Row([
            _card("Buena postura" + (" hoy" if es_hoy else f" ({hoy['fecha']})"),
                  _fmt(hoy["pct_buena_postura"], 0, " %"),
                  "del tiempo medido"),
            _card("Tiempo medido", _fmt(hoy["minutos"] / 60.0, 1, " h")),
            _card("Pausas activas", _fmt(hoy["pausas_por_hora"], 1, "/h"),
                  "tramo máx. " + _fmt(hoy["tramo_max_sin_pausa_min"]) + " min"),
            _card("Avisos", str(hoy["avisos"]),
                  str(hoy["avisos_inactividad"]) + " de pausa"),
        ], spacing=8)

        # Barras de % de buena postura por día (más antiguo a la izquierda).
        bars = []
        for r in reversed(rows):
            p = r["pct_buena_postura"]
            h = 0 if p is None else max(2, int(p * 1.2))
            color = ("#00C853" if p is not None and p >= 80 else
                     "#FFAA00" if p is not None and p >= 60 else "#FF4444")
            bars.append(ft.Column([
                ft.Text(_fmt(p), size=9, color=ft.colors.WHITE54),
                ft.Container(width=22, height=h, bgcolor=color,
                             border_radius=3,
                             tooltip=(r["fecha"] + ": " + _fmt(p)
                                      + " % buena postura, "
                                      + _fmt(r["minutos"]) + " min")),
                ft.Text(r["fecha"][8:10] + "/" + r["fecha"][5:7], size=9,
                        color=ft.colors.WHITE38),
            ], spacing=2, alignment=ft.MainAxisAlignment.END,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER))
        chart = ft.Container(
            content=ft.Row(bars, spacing=6,
                           vertical_alignment=ft.CrossAxisAlignment.END,
                           scroll=ft.ScrollMode.AUTO),
            height=170, padding=ft.padding.only(top=8))

        cmp_ = week_comparison(rows)
        if cmp_ is None:
            cmp_txt = "Con más días de uso verás cómo evolucionas."
        else:
            signo = "mejor" if cmp_["cambio"] >= 0 else "peor"
            cmp_txt = (f"Últimos 7 días con uso: {cmp_['reciente']:.0f} % de "
                       f"buena postura, {abs(cmp_['cambio']):.0f} puntos "
                       f"{signo} que los días anteriores.")

        table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text(t, size=11)) for t in (
                "Día", "Horas", "Buena postura", "Pausas/h",
                "Máx. sin levantarte", "Avisos")],
            rows=[ft.DataRow(cells=[ft.DataCell(ft.Text(v, size=11)) for v in (
                r["fecha"], _fmt(r["minutos"] / 60.0, 1),
                _fmt(r["pct_buena_postura"], 0, " %"),
                _fmt(r["pausas_por_hora"], 1),
                _fmt(r["tramo_max_sin_pausa_min"], 0, " min"),
                str(r["avisos"]))]) for r in rows],
            heading_row_height=32, data_row_min_height=28,
            data_row_max_height=28, column_spacing=18)

        limite = post_cfg.get("cervical_deviation_max_deg", 15)
        return ft.Column([
            cards,
            ft.Text("% del tiempo en buena postura por día", size=11,
                    color=ft.colors.WHITE54),
            chart,
            ft.Text(cmp_txt, size=12, color=ft.colors.WHITE70),
            table,
            ft.Text(f"«Buena postura» = la cabeza a menos de {limite:.0f}° de "
                    "tu postura neutra calibrada. Es una guía para ti, no un "
                    "diagnóstico médico.", size=10, color=ft.colors.WHITE38,
                    italic=True),
        ], spacing=10, scroll=ft.ScrollMode.AUTO)

    history_body = ft.Container(width=720, height=520)
    history_dialog = ft.AlertDialog(
        modal=False,
        title=ft.Row([ft.Icon(ft.icons.INSIGHTS, color="#00BCD4"),
                      ft.Text("Mi historial · últimos 14 días")], spacing=8),
        content=history_body,
        actions=[ft.TextButton("Cerrar",
                               on_click=lambda _: page.close(history_dialog))],
    )

    def _on_history_click(_):
        motivo = _history_locked()
        if motivo:
            history_body.content = ft.Text(motivo, size=13,
                                           color=ft.colors.WHITE70)
            history_body.height = 80
            page.open(history_dialog)
            return
        history_body.height = 520
        history_body.content = ft.Row([ft.ProgressRing(), ft.Text(
            "Calculando…", size=13)], spacing=10)
        page.open(history_dialog)

        def _work():
            try:
                code = (inference_thread.participant
                        if study_on and inference_thread.study_mode == MODE_FIELD
                        else None)
                rows = daily_history(inference_thread.history, thresholds,
                                     days=14, participant_code=code or None)
                history_body.content = _history_content(rows)
            except Exception:
                logger.exception("No se pudo calcular el historial.")
                history_body.content = ft.Text(
                    "No se pudo cargar el historial.", color="#FF6B6B")
            page.update()

        threading.Thread(target=_work, name="historial", daemon=True).start()

    btn_history = ft.TextButton(
        "Mi historial", icon=ft.icons.INSIGHTS, on_click=_on_history_click,
        tooltip="Tu postura y tus pausas de los últimos 14 días")
    header_actions.controls = [btn_history, btn_export, btn_study]

    # --- Autorreporte diario ----------------------------------------------
    _SURVEY_ITEMS = (
        ("neck_pain", "Dolor o molestia en cuello u hombros"),
        ("eye_strain", "Cansancio visual (ardor, sequedad, visión borrosa)"),
        ("sleepiness", "Somnolencia mientras trabajabas"),
    )
    survey_enabled = bool(field_cfg.get("self_report", True))

    def _survey_due_today() -> bool:
        """Hoy hubo uso suficiente en campo y aún no hay autorreporte."""
        if not (study_on and survey_enabled
                and inference_thread.study_mode == MODE_FIELD
                and inference_thread.participant):
            return False
        try:
            hist = inference_thread.history
            code = inference_thread.participant
            minutes = hist.field_minutes_by_date(code).get(today_iso(), 0.0)
            done = today_iso() in hist.self_report_dates(code)
        except Exception:
            logger.exception("No se pudo consultar el autorreporte.")
            return False
        return (not done and minutes >= float(
            field_cfg.get("self_report_min_minutes", 30)))

    def _open_survey(report_date: str, retrospective: bool, on_done=None):
        selectors = {
            key: ft.Dropdown(
                label=label, width=440, text_size=14,
                options=[ft.dropdown.Option(str(i)) for i in range(11)])
            for key, label in _SURVEY_ITEMS
        }
        err = ft.Text("", size=12, color="#FF6B6B")
        cuando = ("hoy" if not retrospective
                  else f"el día {report_date} (tu último día de uso)")

        def _finish(values, skipped):
            try:
                inference_thread.history.log_self_report(
                    inference_thread.participant, report_date,
                    values.get("neck_pain"), values.get("eye_strain"),
                    values.get("sleepiness"),
                    retrospective=retrospective, skipped=skipped)
            except Exception:
                logger.exception("No se pudo guardar el autorreporte.")
            page.close(dlg)
            page.update()
            if on_done:
                on_done()

        def _save(_):
            if any(sel.value is None for sel in selectors.values()):
                err.value = "Responde las tres preguntas (o pulsa Omitir)."
                page.update()
                return
            _finish({k: int(sel.value) for k, sel in selectors.items()}, False)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("¿Cómo terminaste la jornada?"),
            content=ft.Column([
                ft.Text(f"Responde pensando en {cuando}. "
                        "0 = nada · 10 = lo peor que puedas imaginar.",
                        size=12, color=ft.colors.WHITE70),
                *selectors.values(), err,
            ], tight=True, spacing=12, width=460),
            actions=[ft.TextButton("Omitir",
                                   on_click=lambda _: _finish({}, True)),
                     ft.ElevatedButton("Guardar", on_click=_save)],
        )
        page.open(dlg)
        page.update()

    def _ask_pending_report():
        """Al abrir: encuesta del último día de uso si quedó sin responder
        (p. ej. se apagó el equipo sin pasar por «Salir»)."""
        if not (study_on and survey_enabled
                and inference_thread.study_mode == MODE_FIELD
                and inference_thread.participant):
            return
        try:
            hist = inference_thread.history
            code = inference_thread.participant
            minutes = hist.field_minutes_by_date(code)
            min_m = float(field_cfg.get("self_report_min_minutes", 30))
            used = [d for d, m in minutes.items() if m >= min_m]
            pending = pending_report_date(used, hist.self_report_dates(code),
                                          today_iso())
        except Exception:
            logger.exception("No se pudo consultar el autorreporte pendiente.")
            return
        if pending:
            _open_survey(pending, retrospective=True)

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

    _cam = {"lost_since": None, "notified": False}
    _yield = {"auto": False, "apps": [], "stop": threading.Event()}
    yield_enabled = bool(cam_cfg.get("yield_to_other_apps", True))

    def _apply_banner() -> bool:
        """
        Banda de aviso: un problema de cámara (ocupada por otra app, perdida
        tras suspender) tiene prioridad sobre el aviso de encuadre, porque
        sin cámara no se mide nada. Devuelve True si cambió algo.

        Si la cámara lleva 5 min sin funcionar durante el seguimiento, se
        avisa una vez desde la bandeja: con la ventana oculta, el participante
        no sabría que ha dejado de medirse.
        """
        if inference_thread.is_paused and inference_thread.yielding:
            cam_msg = (f"Cámara cedida: tienes "
                       f"{inference_thread.yield_remaining:.0f} s para encenderla "
                       "en tu videollamada. La app volverá a medir sola cuando "
                       "termine la llamada.")
        elif inference_thread.is_paused and _yield["auto"]:
            cam_msg = (f"En pausa: {', '.join(_yield['apps'])} está usando la "
                       "cámara. El seguimiento se reanudará solo al terminar.")
        else:
            cam_msg = "" if inference_thread.is_paused else vt.status_message
        msg = cam_msg or _ui.get("setup_hint", "")
        now = time.monotonic()
        if (cam_msg and inference_thread.mode == "monitoring"
                and not inference_thread.is_paused
                and vt.status != "shared"):
            if _cam["lost_since"] is None:
                _cam["lost_since"] = now
            elif (not _cam["notified"] and tray is not None
                  and now - _cam["lost_since"] >= 300):
                _cam["notified"] = True
                tray.notify("La cámara no está disponible",
                            "Hace 5 minutos que no se puede medir. Si terminó "
                            "tu videollamada y sigue igual, cierra y vuelve "
                            "a abrir la app.")
        else:
            _cam["lost_since"] = None
            _cam["notified"] = False
        if (setup_banner.visible, setup_banner_text.value) == (bool(msg), msg):
            return False
        setup_banner.visible = bool(msg)
        setup_banner_text.value = msg
        return True

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
                        _ui["distance"] = state.metrics.distance_m
                    _ui["setup_hint"] = state.setup_hint
                    _apply_banner()
                    _ui["calibration"] = state.calibration or {}
                    calibration_view.update(_ui["calibration"], state.mode)
                    _refresh_controls()
                    page.update()
                except Empty:
                    # Sin estado nuevo (p. ej. en pausa o sin cámara): los
                    # botones y el aviso de cámara deben reflejarse igual.
                    changed = _apply_banner()
                    if _refresh_controls() or changed:
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
        _yield["stop"].set()
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

    def _on_tray_yield():
        inference_thread.yield_camera(yield_seconds)

    def _on_tray_pause(paused: bool):
        inference_thread.cancel_yield()
        _yield["auto"] = False
        inference_thread.set_paused(paused)

    # ----------------------------------------------------------------
    # Ceder la cámara a otras aplicaciones (videollamadas)
    #
    # La cámara de prueba no se comparte: con la app midiendo, otro programa
    # recibía 0 imágenes. Sin esto la app dejaría negra la cámara del
    # participante en Teams/Zoom. Cada 3 s se mira qué otras apps usan la
    # cámara (src/capture/camera_usage.py); si hay alguna, se pausa sola
    # (libera la cámara) y se reanuda al terminar. Una pausa del usuario
    # nunca se reanuda automáticamente.
    # ----------------------------------------------------------------
    def _yield_loop():
        markers = own_markers()
        notified = False
        while not _yield["stop"].wait(3.0):
            if inference_thread.mode != "monitoring":
                continue
            others = others_using_camera(markers)
            if others and not inference_thread.is_paused:
                _yield["auto"], _yield["apps"] = True, others
                logger.info("Cámara cedida a: %s", ", ".join(others))
                inference_thread.set_paused(True)
                if tray is not None and not notified:
                    notified = True
                    tray.notify("Seguimiento en pausa",
                                f"{', '.join(others)} está usando la cámara. "
                                "Se reanudará solo cuando termine.")
            elif not others and _yield["auto"] and inference_thread.is_paused:
                _yield["auto"] = False
                logger.info("La cámara quedó libre: se reanuda el seguimiento.")
                inference_thread.set_paused(False)

    if yield_enabled and sys.platform == "win32":
        threading.Thread(target=_yield_loop, name="CameraYield",
                         daemon=True).start()

    def _do_quit():
        _shutdown()
        try:
            page.window.destroy()
        except Exception:
            logger.debug("La ventana ya no estaba disponible al salir.")

    def _request_quit():
        """
        Salir. En el estudio de campo, si hoy hubo uso y falta el
        autorreporte, se pregunta antes de cerrar. Se llama desde el hilo de
        la bandeja o desde el de la UI: todo se reenvía a la UI.
        """
        if _survey_due_today():
            _show_window()
            page.run_thread(lambda: _open_survey(today_iso(), False,
                                                 on_done=_do_quit))
        else:
            page.run_thread(_do_quit)

    def _on_tray_quit():
        try:
            _request_quit()
        except Exception:
            logger.exception("Fallo al salir; se cierra igualmente.")
            _shutdown()

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
        on_yield_camera=_on_tray_yield,
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
                _request_quit()

    page.window.prevent_close = True
    page.window.on_event = _on_window_event

    import atexit
    atexit.register(_shutdown)

    vt.start()
    inference_thread.start()
    page.run_task(_ui_update_loop)
    _refresh_controls(force=True)
    _ask_pending_report()

    if tray_active:
        logger.info("Monitoreo en segundo plano habilitado (icono de bandeja).")
    elif background_enabled:
        logger.warning(
            "background_monitoring está activo pero no hay bandeja disponible: "
            "cerrar la ventana terminará la aplicación.")


def run() -> None:
    """Punto de entrada (también el del .exe)."""
    log_path = setup_file_logging()
    logger.info("Monitor Postural %s · datos en %s · log en %s",
                APP_VERSION, data_dir(), log_path)
    ft.app(target=main)


if __name__ == "__main__":
    run()
