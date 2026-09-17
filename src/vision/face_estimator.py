"""face_estimator.py — Wrapper de MediaPipe Face Mesh en modo streaming."""

import logging
from dataclasses import dataclass

import mediapipe as mp
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FaceResult:
    """Resultado de una estimación facial."""
    landmarks: list          # 468 (+10 con refine) NormalizedLandmark
    detected: bool = False
    raw: object = None       # landmark_list original de MediaPipe (para dibujar)


class FaceEstimator:
    """
    Wrapper de MediaPipe Face Mesh con los índices de landmarks necesarios
    para calcular EAR y apertura bucal (Capítulo III).

    Índices de Face Mesh para EAR, en el orden de Soukupová & Čech
    [P1, P2, P3, P4, P5, P6]:

      Ojo izquierdo:  [362, 385, 387, 263, 373, 380]
      Ojo derecho:    [33,  160, 158, 133, 153, 144]

    Boca (apertura bucal): labio superior interno 13, inferior interno 14,
    comisuras 61 y 291.

    Igual que PoseEstimator, la inferencia (`process`) y el dibujo (`draw`)
    están separados para poder ejecutar ambos modelos en paralelo sobre el
    mismo buffer RGB.
    """

    # Índices de los 6 puntos por ojo para EAR (Soukupová & Čech)
    LEFT_EYE_EAR_INDICES = [362, 385, 387, 263, 373, 380]
    RIGHT_EYE_EAR_INDICES = [33, 160, 158, 133, 153, 144]

    # Índices para apertura bucal
    MOUTH_TOP = 13
    MOUTH_BOTTOM = 14
    MOUTH_LEFT = 61
    MOUTH_RIGHT = 291

    # Para dibujo completo del iris (requiere refine_landmarks=True)
    LEFT_IRIS = [474, 475, 476, 477]
    RIGHT_IRIS = [469, 470, 471, 472]

    #: Modos de dibujo, de más barato a más caro.
    DRAW_MODES = ("metrics", "contours", "tesselation")

    def __init__(self,
                 max_num_faces: int = 1,
                 refine_landmarks: bool = True,
                 min_detection_confidence: float = 0.5,
                 min_tracking_confidence: float = 0.5,
                 draw_landmarks: bool = False,
                 draw_mode: str = "metrics"):
        """
        Parameters
        ----------
        max_num_faces:
            Número máximo de caras a detectar (1 para este sistema).
        refine_landmarks:
            Si True, añade los landmarks del iris (478 puntos en total) a
            costa de una pasada extra de la red.
        draw_landmarks:
            Activa el overlay facial.
        draw_mode:
            Cuánto dibujar. El coste es muy distinto entre modos y se paga en
            cada frame:

            - ``"metrics"`` (por defecto): solo los 12 puntos de los ojos y
              los 4 de la boca que realmente entran en el EAR y el MAR.
              ~16 primitivas por frame. Además de ser el más rápido es el
              más informativo para la validación con el experto: se ve
              exactamente sobre qué puntos se calcula cada métrica.
            - ``"contours"``: contornos faciales de MediaPipe (~130 líneas).
            - ``"tesselation"``: la malla completa, ~2600 segmentos por
              frame dibujados por CPU con OpenCV. Es vistoso pero cuesta
              más que la propia inferencia de Face Mesh, y ninguna de las
              métricas de la tesis lo necesita. Úsalo solo para capturas de
              pantalla del documento.
        """
        self.draw_landmarks = draw_landmarks
        if draw_mode not in self.DRAW_MODES:
            raise ValueError(
                f"draw_mode debe ser uno de {self.DRAW_MODES}, se recibió {draw_mode!r}"
            )
        self.draw_mode = draw_mode

        self._mp_face_mesh = mp.solutions.face_mesh
        self._mp_drawing = mp.solutions.drawing_utils
        self._mp_drawing_styles = mp.solutions.drawing_styles

        self._face_mesh = self._mp_face_mesh.FaceMesh(
            max_num_faces=max_num_faces,
            refine_landmarks=refine_landmarks,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        logger.info("FaceEstimator inicializado (refine=%s, draw_mode=%s)",
                    refine_landmarks, draw_mode)

    # ------------------------------------------------------------------

    def process(self, frame, is_rgb: bool = False) -> FaceResult:
        """
        Infiere la malla facial. No modifica ni copia la imagen.

        Parameters
        ----------
        frame:
            Frame HxWx3 uint8. BGR por defecto (formato de OpenCV).
        is_rgb:
            True si `frame` ya viene en RGB.
        """
        if is_rgb:
            frame_rgb = frame
        else:
            import cv2
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        results = self._face_mesh.process(frame_rgb)

        if not results.multi_face_landmarks:
            return FaceResult(landmarks=[], detected=False)

        first = results.multi_face_landmarks[0]
        return FaceResult(
            landmarks=list(first.landmark),
            detected=True,
            raw=first,
        )

    def draw(self, image_bgr: np.ndarray, result: FaceResult) -> np.ndarray:
        """Dibuja el overlay facial sobre `image_bgr` **in situ**."""
        if not self.draw_landmarks or result.raw is None:
            return image_bgr

        if self.draw_mode == "metrics":
            return self._draw_metric_points(image_bgr, result)

        if self.draw_mode == "contours":
            self._mp_drawing.draw_landmarks(
                image=image_bgr,
                landmark_list=result.raw,
                connections=self._mp_face_mesh.FACEMESH_CONTOURS,
                landmark_drawing_spec=None,
                connection_drawing_spec=self._mp_drawing_styles
                    .get_default_face_mesh_contours_style(),
            )
            return image_bgr

        # tesselation
        self._mp_drawing.draw_landmarks(
            image=image_bgr,
            landmark_list=result.raw,
            connections=self._mp_face_mesh.FACEMESH_TESSELATION,
            landmark_drawing_spec=None,
            connection_drawing_spec=self._mp_drawing_styles
                .get_default_face_mesh_tesselation_style(),
        )
        return image_bgr

    def _draw_metric_points(self, image_bgr: np.ndarray,
                            result: FaceResult) -> np.ndarray:
        """
        Marca únicamente los puntos que alimentan el EAR y el MAR.

        Hace visible *qué* está midiendo el sistema, que es lo que el experto
        necesita ver para validar los landmarks (Bloque E1 del protocolo de
        entrevista).
        """
        import cv2
        h, w = image_bgr.shape[:2]
        lms = result.landmarks

        def _px(idx):
            lm = lms[idx]
            return int(lm.x * w), int(lm.y * h)

        try:
            for idx in (self.LEFT_EYE_EAR_INDICES + self.RIGHT_EYE_EAR_INDICES):
                cv2.circle(image_bgr, _px(idx), 1, (80, 220, 255), -1, cv2.LINE_AA)
            # Contorno cerrado de cada ojo para que se lea la apertura
            for indices in (self.LEFT_EYE_EAR_INDICES, self.RIGHT_EYE_EAR_INDICES):
                pts = np.array([_px(i) for i in indices], dtype=np.int32)
                cv2.polylines(image_bgr, [pts], True, (80, 220, 255), 1, cv2.LINE_AA)
            # Eje vertical y horizontal de la boca (numerador y denominador del MAR)
            cv2.line(image_bgr, _px(self.MOUTH_TOP), _px(self.MOUTH_BOTTOM),
                     (255, 160, 60), 1, cv2.LINE_AA)
            cv2.line(image_bgr, _px(self.MOUTH_LEFT), _px(self.MOUTH_RIGHT),
                     (255, 160, 60), 1, cv2.LINE_AA)
        except IndexError:
            logger.debug("Landmarks faciales insuficientes para el overlay.")

        return image_bgr

    # ------------------------------------------------------------------

    def get_landmark_xy(self, landmarks: list, index: int,
                        img_w: int = 1, img_h: int = 1) -> np.ndarray:
        """
        (x, y) de un landmark desnormalizado. Con img_w=img_h=1 devuelve las
        coordenadas normalizadas tal cual.
        """
        lm = landmarks[index]
        return np.array([lm.x * img_w, lm.y * img_h], dtype=np.float64)

    def close(self) -> None:
        """Libera recursos de MediaPipe."""
        self._face_mesh.close()
        logger.info("FaceEstimator cerrado.")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
