"""pose_estimator.py — Wrapper de MediaPipe BlazePose en modo streaming."""

import logging
from dataclasses import dataclass

import mediapipe as mp
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PoseResult:
    """Resultado de una estimación de pose."""
    landmarks: list          # 33 NormalizedLandmark (x, y, z, visibility)
    world_landmarks: list    # En metros, origen en el centro de las caderas
    detected: bool = False
    raw: object = None       # landmark_list original de MediaPipe (para dibujar)


class PoseEstimator:
    """
    Wrapper de MediaPipe BlazePose optimizado para uso en streaming.

    Expone los 33 landmarks 3D normalizados de BlazePose necesarios para
    calcular θc y ΔE (Capítulo III de la tesis).

    Landmarks clave usados en geometry.py:
      - P7  (LEFT_EAR)  / P8 (RIGHT_EAR) -> punto medio inter-auricular
      - P11 (LEFT_SHOULDER) / P12 (RIGHT_SHOULDER) -> punto medio escapular

    Inferencia y dibujo están separados
    -----------------------------------
    `process()` solo infiere; `draw()` solo pinta. Antes iban juntos, lo que
    obligaba a (a) copiar el frame dentro de `process()` y (b) encadenar
    pose -> face secuencialmente porque el segundo modelo recibía el frame ya
    anotado por el primero. Separándolos, los dos modelos pueden compartir el
    mismo buffer RGB y ejecutarse en paralelo; ver `InferenceThread` en
    `src/ui/app_ui.py`.
    """

    # Índices BlazePose relevantes para este sistema
    LEFT_EAR = 7
    RIGHT_EAR = 8
    LEFT_SHOULDER = 11
    RIGHT_SHOULDER = 12
    LEFT_HIP = 23
    RIGHT_HIP = 24

    #: Landmarks que deben ser visibles para que θc y ΔE sean interpretables.
    REQUIRED_LANDMARKS = (LEFT_EAR, RIGHT_EAR, LEFT_SHOULDER, RIGHT_SHOULDER)

    def __init__(self,
                 model_complexity: int = 1,
                 min_detection_confidence: float = 0.5,
                 min_tracking_confidence: float = 0.5,
                 smooth_landmarks: bool = True,
                 draw_landmarks: bool = True):
        """
        Parameters
        ----------
        model_complexity:
            0 = lite (más rápido), 1 = full, 2 = heavy.
        min_detection_confidence:
            Umbral de confianza para la detección inicial.
        min_tracking_confidence:
            Umbral de confianza para el seguimiento entre frames.
        smooth_landmarks:
            Filtro temporal interno de MediaPipe.
        draw_landmarks:
            Valor por defecto de `draw()`; permite apagar el overlay sin
            cambiar las llamadas.
        """
        self.draw_landmarks = draw_landmarks

        self._mp_pose = mp.solutions.pose
        self._mp_drawing = mp.solutions.drawing_utils
        self._mp_drawing_styles = mp.solutions.drawing_styles

        self._pose = self._mp_pose.Pose(
            model_complexity=model_complexity,
            smooth_landmarks=smooth_landmarks,
            enable_segmentation=False,
            smooth_segmentation=False,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        logger.info("PoseEstimator inicializado (complexity=%d)", model_complexity)

    # ------------------------------------------------------------------

    def process(self, frame, is_rgb: bool = False) -> PoseResult:
        """
        Infiere la pose sobre un frame. No modifica ni copia la imagen.

        Parameters
        ----------
        frame:
            Frame HxWx3 uint8. BGR por defecto (formato de OpenCV).
        is_rgb:
            True si `frame` ya viene en RGB, para evitar una conversión de
            color redundante cuando pose y face comparten el mismo buffer.
        """
        if is_rgb:
            frame_rgb = frame
        else:
            import cv2
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        results = self._pose.process(frame_rgb)

        if results.pose_landmarks is None:
            return PoseResult(landmarks=[], world_landmarks=[], detected=False)

        world_landmarks = (list(results.pose_world_landmarks.landmark)
                           if results.pose_world_landmarks else [])

        return PoseResult(
            landmarks=list(results.pose_landmarks.landmark),
            world_landmarks=world_landmarks,
            detected=True,
            raw=results.pose_landmarks,
        )

    def draw(self, image_bgr: np.ndarray, result: PoseResult) -> np.ndarray:
        """Dibuja el esqueleto sobre `image_bgr` **in situ**."""
        if not self.draw_landmarks or result.raw is None:
            return image_bgr
        self._mp_drawing.draw_landmarks(
            image_bgr,
            result.raw,
            self._mp_pose.POSE_CONNECTIONS,
            landmark_drawing_spec=self._mp_drawing_styles.get_default_pose_landmarks_style(),
        )
        return image_bgr

    # ------------------------------------------------------------------

    def get_landmark_array(self, landmarks: list, index: int) -> np.ndarray:
        """Coordenadas (x, y, z) de un landmark como array numpy."""
        lm = landmarks[index]
        return np.array([lm.x, lm.y, lm.z], dtype=np.float64)

    def close(self) -> None:
        """Libera recursos de MediaPipe."""
        self._pose.close()
        logger.info("PoseEstimator cerrado.")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
