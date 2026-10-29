"""
video_thread.py
---------------
Hilo de captura de video con política de descarte de frames antiguos.

Arquitectura:
  - Thread dedicado → cv2.VideoCapture (bloqueante)
  - Queue(maxsize=1) → el consumidor siempre recibe el frame más reciente
  - Si la queue está llena, el frame anterior se descarta (drop-oldest)

Criterio de aceptación (Fase 1):
  - ≥ 30 FPS sostenido bajo carga normal
  - Sin memory leak en ejecución continua de 10 minutos

Recuperación de la cámara (estudio de campo)
--------------------------------------------
En el equipo de un participante la cámara se pierde a diario: una
videollamada (Teams, Zoom, Meet) la toma en exclusiva, o el portátil vuelve
de suspensión con el dispositivo reiniciado. Antes, `cap.read()` fallaba para
siempre: el hilo escribía una advertencia cada 10 ms (~100 líneas/s en el
log) y NUNCA reabría la cámara, así que se perdía el resto de la jornada sin
que nadie lo supiera. Ahora:

  - tras `reopen_after_sec` de lecturas fallidas, se libera y se reabre;
  - si no abre, se reintenta con espera creciente (1, 2, 4… hasta
    `max_backoff_sec`), sin terminar el hilo;
  - si entrega frames NEGROS sostenidos (cámara tapada u ocupada por otra
    app que deja un dispositivo "vacío"), esos frames no se entregan: un frame
    negro haría creer al análisis que la persona se levantó (contaría como
    pausa activa) cuando en realidad no se midió nada;
  - `status` / `status_message` permiten a la interfaz avisar al usuario.

Los huecos sin frames quedan como huecos en metrics_log, que el exportador
trata como "sin datos", no como ausencia de la persona.

Autor: Sistema generado según PLAN.md — Tesis Huisa Perez, UNSA 2026
"""

import threading
import time
import logging
from queue import Queue, Empty, Full

import cv2

# Estados de la cámara (VideoThread.status)
STATUS_STARTING = "starting"
STATUS_OK = "ok"
STATUS_RECONNECTING = "reconnecting"
STATUS_UNAVAILABLE = "unavailable"
STATUS_NO_IMAGE = "no_image"

STATUS_MESSAGES = {
    STATUS_RECONNECTING: ("La cámara dejó de responder (¿la está usando otra "
                          "aplicación?). Reintentando…"),
    STATUS_UNAVAILABLE: ("No se puede abrir la cámara (¿la está usando otra "
                         "aplicación?). Se reintentará sola."),
    STATUS_NO_IMAGE: ("La cámara no entrega imagen (¿está tapada o la usa otra "
                      "aplicación?). No se está midiendo."),
}

# Desviación típica por debajo de la cual un frame se considera negro (la
# misma que usa camera_source para descartar la cámara IR).
BLACK_STD_THRESHOLD = 3.0

logger = logging.getLogger(__name__)


class VideoThread:
    """
    Hilo de captura de video que desacopla la adquisición de frames
    del hilo de inferencia y el hilo de UI.

    Uso básico::

        vt = VideoThread(source=0)
        vt.start()
        frame = vt.get_frame(timeout=0.1)   # None si no hay frame disponible
        vt.stop()
    """

    def __init__(self, source: int | str = 0,
                 width: int = 640, height: int = 480,
                 target_fps: int = 30,
                 reopen_after_sec: float = 2.0,
                 max_backoff_sec: float = 15.0,
                 black_after_sec: float = 3.0,
                 capture_factory=None,
                 source_resolver=None):
        """
        Parameters
        ----------
        source:
            Índice de la cámara (entero) o ruta a un archivo de video (str).
        width, height:
            Resolución de captura solicitada al driver.
        target_fps:
            FPS objetivo. Se usa para monitoreo de rendimiento interno.
        reopen_after_sec:
            Segundos de lecturas fallidas antes de reabrir la cámara.
        max_backoff_sec:
            Espera máxima entre intentos de apertura.
        black_after_sec:
            Segundos de frames negros seguidos para dejar de entregarlos.
        capture_factory:
            Inyectable para pruebas; por defecto cv2.VideoCapture.
        source_resolver:
            Callable que vuelve a elegir el índice de cámara (autodetección).
            Se usa al reabrir tras perder la cámara: si la app arrancó con la
            cámara real ocupada (videollamada), la autodetección cae en la
            cámara IR, que siempre da negro, y sin volver a detectar se
            quedaría en ella aunque la real se libere.
        """
        self.source = source
        self.width = width
        self.height = height
        self.target_fps = target_fps

        # Queue de capacidad 1 → política drop-oldest garantizada
        self._queue: Queue = Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.reopen_after_sec = reopen_after_sec
        self.max_backoff_sec = max_backoff_sec
        self.black_after_sec = black_after_sec
        self._capture_factory = capture_factory or cv2.VideoCapture
        self._source_resolver = source_resolver
        self._needs_resolve = False

        # Métricas de rendimiento (acceso seguro desde otros hilos con GIL)
        self._fps_actual: float = 0.0
        self._frame_count: int = 0
        self._dropped_count: int = 0
        self._status: str = STATUS_STARTING
        self._reconnects: int = 0

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def start(self) -> "VideoThread":
        """Inicia el hilo de captura. Retorna self para encadenamiento."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("VideoThread ya está corriendo.")
            return self

        self._stop_event.clear()
        self._status = STATUS_STARTING
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="VideoCapture",
            daemon=True  # muere con el proceso principal
        )
        self._thread.start()
        logger.info("VideoThread iniciado (fuente=%s, %dx%d @ %d FPS)",
                    self.source, self.width, self.height, self.target_fps)
        return self

    def stop(self) -> None:
        """Detiene el hilo de captura limpiamente."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("VideoThread detenido. Frames capturados=%d, descartados=%d",
                    self._frame_count, self._dropped_count)

    def get_frame(self, timeout: float = 0.05):
        """
        Obtiene el frame más reciente de la queue.

        Returns
        -------
        numpy.ndarray | None
            El frame BGR más reciente, o None si no hay frame disponible
            dentro del timeout.
        """
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None

    @property
    def fps(self) -> float:
        """FPS actual medido en el hilo de captura."""
        return self._fps_actual

    @property
    def is_running(self) -> bool:
        """True si el hilo está activo."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def status(self) -> str:
        """starting / ok / reconnecting / unavailable / no_image."""
        return self._status

    @property
    def status_message(self) -> str:
        """Mensaje para el usuario ("" si la cámara funciona)."""
        return STATUS_MESSAGES.get(self._status, "")

    @property
    def reconnects(self) -> int:
        """Veces que se ha reabierto la cámara (diagnóstico)."""
        return self._reconnects

    def _set_status(self, status: str) -> None:
        if status != self._status:
            log = logger.info if status == STATUS_OK else logger.warning
            log("Cámara: %s → %s", self._status, status)
            self._status = status

    # ------------------------------------------------------------------
    # Implementación interna
    # ------------------------------------------------------------------

    def _open(self):
        """Abre y configura la cámara; None si no se puede."""
        try:
            cap = self._capture_factory(self.source)
        except Exception:
            logger.exception("Error al abrir la cámara %s", self.source)
            return None
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.target_fps)
        logger.debug("Captura iniciada con %dx%d",
                     int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                     int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        return cap

    def _maybe_resolve_source(self) -> None:
        if not (self._needs_resolve and self._source_resolver):
            return
        self._needs_resolve = False
        try:
            new = self._source_resolver()
        except Exception:
            logger.exception("Falló la autodetección de cámara.")
            return
        if new != self.source:
            logger.info("Cámara: se cambia del índice %s al %s.",
                        self.source, new)
            self.source = new

    @staticmethod
    def _is_black(frame) -> bool:
        # Submuestreo: la desviación típica de 1 de cada 8 píxeles basta y
        # cuesta ~60 veces menos que sobre el frame entero.
        return float(frame[::8, ::8].std()) < BLACK_STD_THRESHOLD

    def _capture_loop(self) -> None:
        """Loop principal del hilo de captura, con reconexión."""
        cap = None
        backoff = 1.0
        fail_since = None       # instante de la primera lectura fallida
        black_since = None      # instante del primer frame negro seguido
        fps_window_start = time.perf_counter()
        fps_window_frames = 0

        try:
            while not self._stop_event.is_set():
                if cap is None:
                    self._maybe_resolve_source()
                    cap = self._open()
                    if cap is None:
                        self._needs_resolve = True
                        self._set_status(STATUS_UNAVAILABLE)
                        self._stop_event.wait(backoff)
                        backoff = min(backoff * 2, self.max_backoff_sec)
                        continue
                    # Abrir no basta: con la cámara tomada por otra app el
                    # dispositivo suele abrir y luego no entregar nada. El
                    # estado vuelve a "ok" solo con el primer frame bueno.
                    fail_since = black_since = None

                ret, frame = cap.read()
                now = time.perf_counter()
                if not ret or frame is None:
                    if fail_since is None:
                        fail_since = now
                    elif now - fail_since >= self.reopen_after_sec:
                        logger.debug("cap.read() falla desde hace %.1f s: "
                                     "se reabre la cámara.", now - fail_since)
                        cap.release()
                        cap = None
                        self._needs_resolve = True
                        self._set_status(STATUS_RECONNECTING)
                        # Espera creciente: si sigue ocupada, no se reabre en
                        # bucle (cada apertura cuesta ~0.5-2 s en Windows).
                        self._stop_event.wait(backoff)
                        backoff = min(backoff * 2, self.max_backoff_sec)
                        continue
                    self._stop_event.wait(0.05)
                    continue
                fail_since = None

                # Frames negros sostenidos: no se entregan (ver docstring)
                # y, si persisten, se reabre por si el dispositivo se liberó.
                if self._is_black(frame):
                    if black_since is None:
                        black_since = now
                    elapsed_black = now - black_since
                    if elapsed_black >= self.black_after_sec:
                        self._set_status(STATUS_NO_IMAGE)
                        if elapsed_black >= max(10.0, 3 * self.black_after_sec):
                            cap.release()
                            cap = None
                            black_since = None
                            self._needs_resolve = True
                            self._stop_event.wait(1.0)
                        continue
                else:
                    black_since = None
                    if self._status != STATUS_OK:
                        if self._status != STATUS_STARTING:
                            self._reconnects += 1
                        self._set_status(STATUS_OK)
                        backoff = 1.0

                self._frame_count += 1
                fps_window_frames += 1

                # Actualizar FPS cada segundo
                elapsed = now - fps_window_start
                if elapsed >= 1.0:
                    self._fps_actual = fps_window_frames / elapsed
                    fps_window_frames = 0
                    fps_window_start = now

                # Política drop-oldest: si queue llena, sacar el frame viejo
                if self._queue.full():
                    try:
                        self._queue.get_nowait()
                        self._dropped_count += 1
                    except Empty:
                        pass

                try:
                    self._queue.put_nowait(frame)
                except Full:
                    self._dropped_count += 1

        finally:
            if cap is not None:
                cap.release()
            logger.debug("cv2.VideoCapture liberado.")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
