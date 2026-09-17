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

Autor: Sistema generado según PLAN.md — Tesis Huisa Perez, UNSA 2026
"""

import threading
import time
import logging
from queue import Queue, Empty, Full

import cv2

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
                 target_fps: int = 30):
        """
        Parameters
        ----------
        source:
            Índice de la cámara (entero) o ruta a un archivo de video (str).
        width, height:
            Resolución de captura solicitada al driver.
        target_fps:
            FPS objetivo. Se usa para monitoreo de rendimiento interno.
        """
        self.source = source
        self.width = width
        self.height = height
        self.target_fps = target_fps

        # Queue de capacidad 1 → política drop-oldest garantizada
        self._queue: Queue = Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Métricas de rendimiento (acceso seguro desde otros hilos con GIL)
        self._fps_actual: float = 0.0
        self._frame_count: int = 0
        self._dropped_count: int = 0

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def start(self) -> "VideoThread":
        """Inicia el hilo de captura. Retorna self para encadenamiento."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("VideoThread ya está corriendo.")
            return self

        self._stop_event.clear()
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

    # ------------------------------------------------------------------
    # Implementación interna
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        """Loop principal del hilo de captura."""
        cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            logger.error("No se pudo abrir la cámara/archivo: %s", self.source)
            return

        # Configurar resolución y FPS
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.target_fps)

        # Variables para cálculo de FPS real
        fps_window_start = time.perf_counter()
        fps_window_frames = 0

        logger.debug("Captura iniciada con %dx%d",
                     int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                     int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

        try:
            while not self._stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    logger.warning("cap.read() falló — reintentando...")
                    time.sleep(0.01)
                    continue

                self._frame_count += 1
                fps_window_frames += 1

                # Actualizar FPS cada segundo
                elapsed = time.perf_counter() - fps_window_start
                if elapsed >= 1.0:
                    self._fps_actual = fps_window_frames / elapsed
                    fps_window_frames = 0
                    fps_window_start = time.perf_counter()

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
            cap.release()
            logger.debug("cv2.VideoCapture liberado.")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
