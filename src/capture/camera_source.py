"""
camera_source.py
----------------
Resolución del índice de cámara a usar, compartida por la app y por las
herramientas de medición.

Por qué existe este módulo
--------------------------
En el equipo de pruebas de esta tesis el índice 0 **abre correctamente pero
solo entrega frames negros** (es una cámara IR de Windows Hello; también
ocurre con cámaras virtuales de software de videoconferencia). La cámara real
está en el índice 1. Un `cv2.VideoCapture(0)` que "funciona" pero no ve nada
es el peor modo de fallo posible para una sesión de medición: no lanza ninguna
excepción, simplemente MediaPipe no detecta a nadie y el CSV sale vacío de
detecciones sin que nada avise.

Esta lógica vivía duplicada en `app_ui.py` y en `benchmark_mediapipe.py`, y
**faltaba justo en `calibration_mode.py`**, que era la herramienta con el
`--source 0` cableado por defecto — es decir, la que se iba a usar para
re-medir la tabla de θc del Capítulo IV era precisamente la única que no sabía
esquivar la cámara negra.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import logging
from typing import Optional

import cv2

logger = logging.getLogger(__name__)

# Desviación típica mínima de un frame para considerarlo "imagen real".
# Un frame completamente negro tiene std ≈ 0; incluso una escena muy oscura
# pero real supera este valor con holgura.
DEFAULT_BLACK_STD_THRESHOLD = 3.0

# Índices a sondear. Más de 4 rara vez aporta y cada sonda cuesta ~0.5-2 s
# porque abrir un dispositivo de captura es lento en Windows.
DEFAULT_MAX_INDEX = 4


def detect_camera_source(max_index: int = DEFAULT_MAX_INDEX,
                         black_std_threshold: float = DEFAULT_BLACK_STD_THRESHOLD,
                         verbose: bool = True) -> int:
    """
    Devuelve el primer índice de cámara que entrega una imagen real.

    Prueba los índices ``0..max_index-1``, abre cada uno, lee un frame y
    descarta los que no devuelven imagen o devuelven un frame prácticamente
    negro.

    Parameters
    ----------
    max_index:
        Número de índices a sondear.
    black_std_threshold:
        Desviación típica mínima del frame para aceptarlo como imagen real.
    verbose:
        Emitir logs del sondeo. Las herramientas de consola lo dejan en True
        para que quede constancia en la sesión de medición de qué cámara se
        usó.

    Returns
    -------
    int
        Índice utilizable, o 0 si ninguno pasó la prueba (para que el flujo
        siga y falle de forma visible más adelante en vez de aquí).
    """
    for idx in range(max_index):
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        cap.release()

        if ok and frame is not None and frame.std() > black_std_threshold:
            if verbose:
                logger.info("Cámara activa detectada en índice %d", idx)
            return idx

        if verbose:
            logger.warning(
                "Cámara en índice %d abre pero no entrega imagen real "
                "(descartada).", idx)

    if verbose:
        logger.warning("No se detectó ninguna cámara con imagen real; "
                       "usando índice 0 por defecto.")
    return 0


def resolve_camera_source(configured: Optional[int],
                          verbose: bool = True) -> int:
    """
    Traduce el valor de configuración de cámara a un índice concreto.

    Convención única en todo el proyecto (`config/thresholds.json` →
    `camera.source_index`, y el argumento ``--source`` de las herramientas):

      - ``-1`` o ``None`` → autodetectar
      - ``>= 0``          → forzar ese índice, sin sondeo

    Tener la convención en un solo sitio evita que una herramienta autodetecte
    y otra se quede clavada en el índice 0.
    """
    if configured is None or configured < 0:
        return detect_camera_source(verbose=verbose)
    if verbose:
        logger.info("Usando índice de cámara forzado: %d", configured)
    return configured
