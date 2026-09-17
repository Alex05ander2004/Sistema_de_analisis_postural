"""
video_feed.py
-------------
Componente de feed de video con landmarks superpuestos.

Convierte frames de OpenCV (numpy BGR) a Base64/JPEG y los renderiza
en un ft.Image de Flet de forma asíncrona, sin bloquear el hilo de UI.

Estrategia:
  - El hilo de inferencia llama a `encode(frame)` y obtiene el string Base64.
  - El hilo de UI solo llama a `set_encoded(b64)`, que es una asignación.

La codificacion JPEG + Base64 de un frame 640x480 cuesta ~2-4 ms. Hacerla en
el event loop de Flet (como en la version anterior, que llamaba a
`update_frame` desde el loop async) bloquea el bucle de UI en cada repintado
y hace que los botones y el modal de alerta respondan a tirones. Por eso el
trabajo pesado vive en el hilo de inferencia y la UI recibe el string ya listo.

Autor: Generado según PLAN.md — Tesis Huisa Perez, UNSA 2026
"""

import base64
import logging
from typing import Optional

import cv2
import flet as ft
import numpy as np

logger = logging.getLogger(__name__)


class VideoFeed:
    """
    Widget de video en tiempo real para Flet.

    Uso::

        feed = VideoFeed(width=640, height=480)
        page.add(feed.build())

        # Hilo de inferencia:
        b64 = feed.encode(annotated_frame, fps=29.8)

        # Hilo de UI:
        feed.set_encoded(b64)
        page.update()
    """

    def __init__(self, width: int = 640, height: int = 480,
                 jpeg_quality: int = 80,
                 overlay_info: bool = True):
        """
        Parameters
        ----------
        width, height:
            Dimensiones del widget de video en la UI.
        jpeg_quality:
            Calidad JPEG [1-100]. 80 es un buen balance velocidad/calidad.
        overlay_info:
            Si True, dibuja sobre el frame una banda con el FPS.
        """
        self.width = width
        self.height = height
        self.jpeg_quality = jpeg_quality
        self.overlay_info = overlay_info

        self._image = ft.Image(
            width=width,
            height=height,
            fit=ft.ImageFit.CONTAIN,
            border_radius=ft.border_radius.all(10),
        )

        # Placeholder mientras no hay frame
        self._placeholder = ft.Container(
            width=width,
            height=height,
            bgcolor="#0D0D1A",
            border_radius=10,
            content=ft.Column(
                [
                    ft.Icon(ft.icons.VIDEOCAM_OFF_OUTLINED,
                            color=ft.colors.WHITE24, size=48),
                    ft.Text("Iniciando cámara…",
                            color=ft.colors.WHITE38, size=14),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )

        self._stack = ft.Stack(
            [self._placeholder, self._image],
            width=width,
            height=height,
        )

        self._frame_count = 0
        self._last_b64: Optional[str] = None

    def build(self) -> ft.Stack:
        """Devuelve el widget Flet listo para agregar a la página."""
        return self._stack

    def encode(self, frame_bgr: np.ndarray, fps: float = 0.0) -> Optional[str]:
        """
        Codifica un frame BGR a JPEG/Base64. **Llamar desde el hilo de
        inferencia**, nunca desde el event loop de Flet.

        Returns
        -------
        str | None
            El string Base64, o None si el frame es invalido o la
            codificacion falla.
        """
        if frame_bgr is None:
            return None

        if self.overlay_info and fps > 0:
            self._draw_overlay(frame_bgr, fps)

        success, buffer = cv2.imencode(
            ".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
        )
        if not success:
            logger.warning("cv2.imencode fallo en frame %d", self._frame_count)
            return None

        return base64.b64encode(buffer).decode("ascii")

    def set_encoded(self, b64_str: Optional[str]) -> None:
        """
        Asigna al widget un frame ya codificado. **Llamar desde el hilo de UI**.
        Flet requiere un `page.update()` posterior.
        """
        if b64_str is None or b64_str == self._last_b64:
            return

        self._last_b64 = b64_str
        self._image.src_base64 = b64_str
        self._frame_count += 1

        # Ocultar placeholder al recibir el primer frame
        if self._frame_count == 1:
            self._placeholder.visible = False

    def update_frame(self, frame_bgr: np.ndarray, fps: float = 0.0) -> None:
        """
        Codifica y asigna en una sola llamada.

        Solo para herramientas de un solo hilo (`src/tools/`). La app usa
        `encode()` + `set_encoded()` para no codificar en el hilo de UI.
        """
        self.set_encoded(self.encode(frame_bgr, fps))

    def show_no_signal(self) -> None:
        """Muestra el placeholder de 'sin señal'."""
        self._placeholder.visible = True
        self._image.src_base64 = None

    @staticmethod
    def _draw_overlay(frame: np.ndarray, fps: float) -> np.ndarray:
        """
        Dibuja una banda semitransparente en la esquina con el FPS, **in situ**.

        La mezcla alfa se aplica solo sobre el rectangulo de 160x30 px, no
        sobre el frame entero: la version anterior copiaba los 640x480 y
        hacia un `addWeighted` completo para oscurecer el 2% de la imagen.
        """
        roi = frame[0:30, 0:160]
        roi[:] = (roi * 0.5).astype(roi.dtype)

        color = (0, 200, 80) if fps >= 28 else (0, 165, 255) if fps >= 20 else (0, 0, 220)
        cv2.putText(
            frame,
            f"FPS: {fps:.1f}",
            (8, 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            1,
            cv2.LINE_AA,
        )
        return frame

    @property
    def frame_count(self) -> int:
        """Número total de frames renderizados."""
        return self._frame_count
