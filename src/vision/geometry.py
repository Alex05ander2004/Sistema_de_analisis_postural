"""
geometry.py
-----------
Funciones matemáticas del Capítulo III.3.3.2.C de la tesis.

Implementa los modelos formalizados en la tesis:
  - θc  → ángulo cervical (cabeza adelantada)
  - ΔE  → asimetría escapular (inclinación de la línea de hombros)
  - EAR → Eye Aspect Ratio (fatiga ocular, Soukupová & Čech)
  - MAR → apertura bucal normalizada (bostezo)

ESPACIO DE COORDENADAS (importante)
-----------------------------------
MediaPipe entrega landmarks *normalizados*: `x` se divide entre el ancho del
frame y `y` entre el alto. En un frame 640x480 ese espacio es **anisótropo**:
un mismo desplazamiento físico vale 1/640 en x y 1/480 en y. Calcular ángulos
o razones (EAR, MAR, ΔE) directamente sobre esas coordenadas introduce un
factor de escala W/H = 1.333 — un ΔE real de 10° se mide como 13.2°, y un EAR
real de 0.21 se mide como 0.28.

Por eso **todas** las funciones de este módulo convierten primero a un espacio
isótropo antes de operar:

    x_px = x * W        y_px = y * H        z_px = z * W

(MediaPipe documenta que la magnitud de `z` usa aproximadamente la misma
escala que `x`.)

Si se pasan landmarks que ya están en un espacio isótropo — p. ej. los
`pose_world_landmarks` de BlazePose, expresados en metros — se debe usar
`frame_size=None` para desactivar la conversión.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import math
from typing import Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Constantes de índices BlazePose (33 landmarks)
# ---------------------------------------------------------------------------
LANDMARK_NOSE           = 0
LANDMARK_LEFT_EAR       = 7
LANDMARK_RIGHT_EAR      = 8
LANDMARK_LEFT_SHOULDER  = 11
LANDMARK_RIGHT_SHOULDER = 12
LANDMARK_LEFT_HIP       = 23
LANDMARK_RIGHT_HIP      = 24

# Resolución de captura por defecto (config/thresholds.json → camera.*)
DEFAULT_FRAME_SIZE: Tuple[int, int] = (640, 480)

# Antropometría de referencia para la estimación de distancia.
# Ancho biacromial medio adulto ~0.38 m (media poblacional; Pheasant &
# Haslegrave, "Bodyspace", 3a ed.). Es un valor de referencia, no una
# medición individual — ver docstring de estimate_distance_m().
DEFAULT_BIACROMIAL_WIDTH_M = 0.38


# ---------------------------------------------------------------------------
# Helpers de conversión de espacio
# ---------------------------------------------------------------------------

def _to_iso(landmark, frame_size: Optional[Tuple[int, int]]) -> np.ndarray:
    """
    Convierte un landmark de MediaPipe a un espacio isótropo 3D.

    Parameters
    ----------
    landmark:
        NormalizedLandmark (atributos x, y, z).
    frame_size:
        (ancho, alto) del frame para deshacer la normalización, o None si el
        landmark ya está en un espacio isótropo (pose_world_landmarks, en
        metros).
    """
    if frame_size is None:
        return np.array([landmark.x, landmark.y, landmark.z], dtype=np.float64)
    w, h = frame_size
    return np.array([landmark.x * w, landmark.y * h, landmark.z * w],
                    dtype=np.float64)


def _to_iso_2d(landmark, frame_size: Optional[Tuple[int, int]]) -> np.ndarray:
    """Igual que _to_iso pero descartando z (métricas puramente 2D)."""
    if frame_size is None:
        return np.array([landmark.x, landmark.y], dtype=np.float64)
    w, h = frame_size
    return np.array([landmark.x * w, landmark.y * h], dtype=np.float64)


def _euclidean(a: np.ndarray, b: np.ndarray) -> float:
    """Distancia euclidiana entre dos puntos."""
    return float(np.linalg.norm(a - b))


def _visibility(landmark) -> float:
    """Visibilidad del landmark; 1.0 si el objeto no la expone (tests)."""
    return float(getattr(landmark, "visibility", 1.0))


def landmarks_visible(pose_landmarks: list,
                      indices: Sequence[int],
                      min_visibility: float = 0.5) -> bool:
    """
    True si todos los landmarks indicados superan el umbral de visibilidad.

    BlazePose marca con visibilidad baja los puntos que infiere pero no
    observa (p. ej. una oreja cuando el usuario gira la cabeza). Medir un
    ángulo sobre esos puntos produce lecturas inventadas, así que el pipeline
    debe descartar el frame en vez de alimentar el FSM con ruido.
    """
    if not pose_landmarks:
        return False
    try:
        return all(_visibility(pose_landmarks[i]) >= min_visibility
                   for i in indices)
    except IndexError:
        return False


# ---------------------------------------------------------------------------
# 1. Ángulo Cervical (θc) — desplazamiento de cabeza adelantada
# ---------------------------------------------------------------------------

def calculate_cervical_angle(
        pose_landmarks: list,
        frame_size: Optional[Tuple[int, int]] = DEFAULT_FRAME_SIZE,
        use_ear_midpoint: bool = True) -> float:
    """
    Calcula el ángulo cervical θc: inclinación del segmento hombros→cabeza
    respecto al eje vertical.

    Fórmula (Capítulo III.3.3.2.C, con las dos correcciones descritas abajo):

        P_scapula  = midpoint(P11, P12)      # punto medio escapular
        P_head     = midpoint(P7, P8)        # punto medio inter-auricular
        V_cervical = P_head - P_scapula
        V_vertical = (0, -1, 0)
        θc = arccos( V_cervical . V_vertical / ||V_cervical|| ) * 180/π

    Corrección 1 — punto medio inter-auricular
        La formulación original usaba **solo P7** (oreja izquierda). Esa oreja
        está desplazada lateralmente ~7 cm respecto al plano medio sagital, de
        modo que incluso en postura perfecta el vector cervical nace inclinado
        y θc arranca en ~13°, no en 0°. El punto medio entre ambas orejas sí
        cae sobre el plano medio y elimina ese sesgo constante. (Verificado en
        tests/test_geometry.py::TestCervicalAngle::
        test_lateral_ear_offset_does_not_bias_angle.)

    Corrección 2 — espacio isótropo
        Ver la nota de coordenadas al inicio del módulo.

    Parameters
    ----------
    pose_landmarks:
        Lista de 33 landmarks de BlazePose.
    frame_size:
        (ancho, alto) del frame, o None si los landmarks ya son world
        landmarks en metros.
    use_ear_midpoint:
        False reproduce la formulación original (solo P7). Se conserva
        únicamente para poder cuantificar el sesgo en el Capítulo IV; no debe
        usarse en producción.

    Returns
    -------
    float
        θc en grados. 0° = cabeza alineada con el tronco.

    Raises
    ------
    ValueError
        Si la lista tiene menos de 13 landmarks.
    """
    if len(pose_landmarks) < 13:
        raise ValueError(
            f"Se necesitan al menos 13 landmarks de BlazePose, "
            f"se recibieron {len(pose_landmarks)}."
        )

    p11 = _to_iso(pose_landmarks[LANDMARK_LEFT_SHOULDER], frame_size)
    p12 = _to_iso(pose_landmarks[LANDMARK_RIGHT_SHOULDER], frame_size)
    p_scapula = (p11 + p12) / 2.0

    if use_ear_midpoint:
        p7 = _to_iso(pose_landmarks[LANDMARK_LEFT_EAR], frame_size)
        p8 = _to_iso(pose_landmarks[LANDMARK_RIGHT_EAR], frame_size)
        p_head = (p7 + p8) / 2.0
    else:
        p_head = _to_iso(pose_landmarks[LANDMARK_LEFT_EAR], frame_size)

    v_cervical = p_head - p_scapula
    v_vertical = np.array([0.0, -1.0, 0.0])

    norm_cervical = float(np.linalg.norm(v_cervical))
    if norm_cervical < 1e-9:
        return 0.0  # Landmarks superpuestos → ángulo indefinido

    cos_theta = float(np.dot(v_cervical, v_vertical) / norm_cervical)
    cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
    return math.degrees(math.acos(cos_theta))


def calculate_cervical_components(
        pose_landmarks: list,
        frame_size: Optional[Tuple[int, int]] = DEFAULT_FRAME_SIZE
) -> Tuple[float, float, float]:
    """
    Descompone θc en sus dos planos anatómicos.

    θc mide la inclinación total del segmento cervical, pero mezcla dos gestos
    clínicamente distintos. Separarlos permite reportarlos por separado en el
    Capítulo IV y explicar de dónde viene una alerta.

    Returns
    -------
    (theta_c, theta_sagital, theta_lateral) en grados
        theta_c
            Ángulo 3D total (idéntico a calculate_cervical_angle).
        theta_sagital
            Componente en el plano sagital = **cabeza adelantada**. Es el
            complemento del Ángulo Craneovertebral clínico:
            theta_sagital ~= 90° - CVA. Positivo = cabeza hacia adelante.
        theta_lateral
            Componente en el plano frontal = **inclinación lateral** de la
            cabeza. Positivo = cabeza inclinada hacia la derecha de la imagen.
    """
    if len(pose_landmarks) < 13:
        raise ValueError(
            f"Se necesitan al menos 13 landmarks, se recibieron {len(pose_landmarks)}."
        )

    p11 = _to_iso(pose_landmarks[LANDMARK_LEFT_SHOULDER], frame_size)
    p12 = _to_iso(pose_landmarks[LANDMARK_RIGHT_SHOULDER], frame_size)
    p7  = _to_iso(pose_landmarks[LANDMARK_LEFT_EAR], frame_size)
    p8  = _to_iso(pose_landmarks[LANDMARK_RIGHT_EAR], frame_size)

    v = (p7 + p8) / 2.0 - (p11 + p12) / 2.0
    if float(np.linalg.norm(v)) < 1e-9:
        return 0.0, 0.0, 0.0

    dx, dy, dz = float(v[0]), float(v[1]), float(v[2])
    up = -dy  # componente vertical positiva hacia arriba

    theta_c = calculate_cervical_angle(pose_landmarks, frame_size)
    # z de MediaPipe decrece hacia la cámara → -dz positivo = cabeza adelantada
    theta_sagital = math.degrees(math.atan2(-dz, up))
    theta_lateral = math.degrees(math.atan2(dx, up))
    return theta_c, theta_sagital, theta_lateral


# ---------------------------------------------------------------------------
# 2. Asimetría Escapular (ΔE)
# ---------------------------------------------------------------------------

def calculate_shoulder_asymmetry(
        pose_landmarks: list,
        frame_size: Optional[Tuple[int, int]] = DEFAULT_FRAME_SIZE) -> float:
    """
    Calcula la asimetría escapular ΔE: inclinación de la línea de hombros
    respecto a la horizontal de la cámara.

    Fórmula (Capítulo III.3.3.2.C):

        V_hombros = P11 - P12
        ΔE = atan2(V_hombros.y, |V_hombros.x|) * 180/π

    Se toma |x| para que el signo del resultado dependa solo de la inclinación
    y no de qué hombro cae a la izquierda de la imagen (lo que cambiaría con
    el espejado del frame).

    El cálculo se hace en píxeles isótropos: sobre coordenadas normalizadas de
    un frame 640x480 la tangente queda multiplicada por W/H = 1.333 y una
    inclinación real de 10° se reporta como 13.2°.

    Returns
    -------
    float
        ΔE en grados. Positivo = el hombro *izquierdo del sujeto* aparece más
        abajo en la imagen. ΔE ~= 0° = hombros nivelados.
    """
    if len(pose_landmarks) < 13:
        raise ValueError(
            f"Se necesitan al menos 13 landmarks, se recibieron {len(pose_landmarks)}."
        )

    p11 = _to_iso(pose_landmarks[LANDMARK_LEFT_SHOULDER], frame_size)
    p12 = _to_iso(pose_landmarks[LANDMARK_RIGHT_SHOULDER], frame_size)

    v_hombros = p11 - p12
    return math.degrees(math.atan2(v_hombros[1], abs(v_hombros[0])))


# ---------------------------------------------------------------------------
# 3. EAR — Eye Aspect Ratio (Soukupová & Čech, 2016)
# ---------------------------------------------------------------------------

def calculate_ear(face_landmarks: list,
                  eye_indices: Sequence[int],
                  frame_size: Optional[Tuple[int, int]] = DEFAULT_FRAME_SIZE
                  ) -> float:
    """
    Calcula el Eye Aspect Ratio (EAR) de un ojo:

        EAR = (||P2-P6|| + ||P3-P5||) / (2 * ||P1-P4||)

    Donde P1 = extremo externo, P2-P3 = párpado superior, P4 = extremo interno,
    P5-P6 = párpado inferior.

    El EAR es una razón entre una distancia *vertical* y una *horizontal*, así
    que solo es comparable con el umbral de la literatura (0.21) si ambas se
    miden en la misma escala. Sobre coordenadas normalizadas de un frame
    640x480 el resultado queda inflado por W/H = 1.333: un ojo con EAR real
    0.21 mide 0.28, y comparar ese 0.28 contra 0.21 equivale a exigir un EAR
    real de 0.157 — prácticamente el ojo cerrado del todo. Por eso se convierte
    a píxeles antes de operar.

    El cálculo es 2D: el párpado es una estructura plana en la imagen y la `z`
    de Face Mesh sobre los párpados es ruido que ensancha la varianza del EAR
    sin aportar señal.

    Returns
    -------
    float
        EAR (típico: ~0.30-0.35 ojo abierto, <0.15 ojo cerrado).
    """
    if len(eye_indices) != 6:
        raise ValueError(f"eye_indices debe tener 6 elementos, tiene {len(eye_indices)}.")

    p = [_to_iso_2d(face_landmarks[i], frame_size) for i in eye_indices]

    d26 = _euclidean(p[1], p[5])   # vertical párpado (P2-P6)
    d35 = _euclidean(p[2], p[4])   # vertical párpado (P3-P5)
    d14 = _euclidean(p[0], p[3])   # horizontal (ancho del ojo)

    if d14 < 1e-9:
        return 0.0
    return float((d26 + d35) / (2.0 * d14))


def calculate_avg_ear(face_landmarks: list,
                      left_indices: Sequence[int],
                      right_indices: Sequence[int],
                      frame_size: Optional[Tuple[int, int]] = DEFAULT_FRAME_SIZE
                      ) -> float:
    """EAR promedio de ambos ojos."""
    ear_left  = calculate_ear(face_landmarks, left_indices, frame_size)
    ear_right = calculate_ear(face_landmarks, right_indices, frame_size)
    return (ear_left + ear_right) / 2.0


# ---------------------------------------------------------------------------
# 4. Apertura Bucal (MAR — bostezo)
# ---------------------------------------------------------------------------

def calculate_mouth_opening(face_landmarks: list,
                            top_idx: int = 13,
                            bottom_idx: int = 14,
                            left_idx: int = 61,
                            right_idx: int = 291,
                            frame_size: Optional[Tuple[int, int]] = DEFAULT_FRAME_SIZE
                            ) -> float:
    """
    Apertura bucal normalizada (Mouth Aspect Ratio):

        MAR = ||P_labio_sup - P_labio_inf|| / ||P_comisura_izq - P_comisura_der||

    Igual que el EAR, es una razón vertical/horizontal y sufre el mismo factor
    W/H sobre coordenadas normalizadas; se calcula en píxeles y en 2D.

    Returns
    -------
    float
        MAR. 0.0 = boca cerrada; >= umbral (mouth_opening_threshold) = bostezo.
    """
    p_top    = _to_iso_2d(face_landmarks[top_idx], frame_size)
    p_bottom = _to_iso_2d(face_landmarks[bottom_idx], frame_size)
    p_left   = _to_iso_2d(face_landmarks[left_idx], frame_size)
    p_right  = _to_iso_2d(face_landmarks[right_idx], frame_size)

    vertical   = _euclidean(p_top, p_bottom)
    horizontal = _euclidean(p_left, p_right)

    if horizontal < 1e-9:
        return 0.0
    return float(vertical / horizontal)


# ---------------------------------------------------------------------------
# 5. Estimación de distancia cámara-usuario
# ---------------------------------------------------------------------------

def estimate_distance_m(pose_landmarks: list,
                        frame_size: Tuple[int, int] = DEFAULT_FRAME_SIZE,
                        hfov_deg: float = 60.0,
                        biacromial_width_m: float = DEFAULT_BIACROMIAL_WIDTH_M
                        ) -> Optional[float]:
    """
    Estima la distancia cámara-usuario a partir del ancho de hombros aparente.

    El experto fijó el rango de trabajo válido en 0.50-0.70 m y señaló que
    fuera de él la propia captura induce la mala postura que se quiere medir.
    Sin esta estimación el sistema no tiene forma de saber si el usuario está
    en ese rango, y todas las lecturas de θc quedan sin contexto. Con ella la
    app puede avisar antes de registrar nada.

    Modelo de cámara estenopeica:

        f_px = (W/2) / tan(HFOV/2)
        d    = biacromial_width_m * f_px / ancho_hombros_px

    Precisión y limitaciones
    ------------------------
    Es una estimación de **orden de magnitud**, no una medición:
      - biacromial_width_m es una media poblacional; la desviación individual
        (+-0.03 m) se traslada proporcionalmente a la distancia.
      - hfov_deg varía por modelo de webcam (típico 55-78°). Para reportar
        distancias en el Capítulo IV conviene calibrarlo una vez con una
        medición con cinta métrica y guardarlo en config/thresholds.json →
        camera.horizontal_fov_deg.
      - El ancho aparente se acorta si el usuario gira el torso.

    Returns
    -------
    float | None
        Distancia estimada en metros, o None si no se puede calcular.
    """
    if len(pose_landmarks) < 13:
        return None

    w, _ = frame_size
    p11 = _to_iso_2d(pose_landmarks[LANDMARK_LEFT_SHOULDER], frame_size)
    p12 = _to_iso_2d(pose_landmarks[LANDMARK_RIGHT_SHOULDER], frame_size)
    shoulder_px = _euclidean(p11, p12)

    if shoulder_px < 1.0:
        return None

    focal_px = (w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    return float(biacromial_width_m * focal_px / shoulder_px)
