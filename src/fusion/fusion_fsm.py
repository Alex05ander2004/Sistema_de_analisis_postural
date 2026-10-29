"""
fusion_fsm.py
-------------
Módulo de Fusión Multimodal (FSM) — Máquina de Estados Finitos.

Implementa los temporizadores independientes definidos en el Capítulo III de
la tesis para evitar falsos positivos ante eventos transitorios:

  IDLE -> OBSERVING -> ALERTED -> COOLDOWN -> IDLE

Reglas de disparo (Capítulo III.3.3.3). Los valores entre paréntesis son los
de `config/thresholds.json`; el código lee siempre el JSON, nunca estas cifras:
  - Postura fuera de rango (θc > 30°)      -> alerta tras > 5.0 s continuos
  - Asimetría de hombros (|ΔE| > 10°)      -> alerta tras > 8.0 s continuos
  - Fatiga ocular (EAR <= 0.21)            -> alerta tras > 3.0 s continuos
  - Parpadeo fisiológico (100-400 ms)      -> IGNORADO, no genera alerta
  - Bostezo (MAR >= 0.45)                  -> alerta tras > 3.0 s continuos
  - PERCLOS por encima del umbral          -> alerta de somnolencia

La ventana de hombros es distinta de la cervical a petición expresa del
experto (Bloque B2): la asimetría escapular es un patrón más crónico y una
ventana más larga evita disparos por gestos transitorios (alcanzar el ratón,
girarse). El valor 8.0 s es PROVISIONAL, de ingeniería — ver
`docs/validation_report.md` §8.

Reloj inyectable
----------------
Todos los temporizadores leen el tiempo a través de `self._clock`, no de
`time.perf_counter()` directamente. Eso permite (a) dirigir el FSM con el
timestamp real de cada frame en vez del instante en que se procesó, y (b)
escribir tests deterministas de ventanas de 3-5 s sin dormir 5 s.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from statistics import median
from typing import Callable, Deque, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tipos de alerta que puede generar el sistema
# ---------------------------------------------------------------------------

class AlertType(Enum):
    CERVICAL_ANGLE = "cervical_angle"
    SHOULDER_ASYMM = "shoulder_asymmetry"
    EYE_FATIGUE    = "eye_fatigue"
    YAWN           = "yawn"
    DROWSINESS     = "drowsiness"   # PERCLOS sostenido


class FsmState(Enum):
    IDLE      = auto()   # Condición dentro de rango normal
    OBSERVING = auto()   # Condición detectada, esperando ventana temporal
    ALERTED   = auto()   # Alerta confirmada y disparada
    COOLDOWN  = auto()   # Esperando cooldown antes de volver a IDLE


# ---------------------------------------------------------------------------
# Métricas de entrada normalizadas para el FSM
# ---------------------------------------------------------------------------

@dataclass
class SensorMetrics:
    """
    Snapshot de métricas calculadas en un frame dado.

    Notas sobre los dos relojes
    ---------------------------
    `timestamp` es hora de pared (`time.time()`) y es el que se persiste: es
    lo único que permite correlacionar la tabla `metrics_log` con la tabla
    `events` y con la hora real de la sesión de prueba. `monotonic` es un
    reloj monótono para medir *duraciones* dentro del FSM, inmune a cambios
    de hora del sistema.
    """
    cervical_angle: float = 0.0        # θc en grados (3D)
    cervical_sagittal: float = 0.0     # componente sagital (cabeza adelantada)
    cervical_lateral: float = 0.0      # componente frontal (inclinación lateral)
    shoulder_asymmetry: float = 0.0    # ΔE en grados
    ear_avg: float = 0.4               # EAR promedio (0=cerrado, ~0.35=abierto)
    mouth_opening: float = 0.0         # MAR (apertura bucal normalizada)
    distance_m: Optional[float] = None  # distancia estimada cámara-usuario
    perclos: float = 0.0               # fracción de tiempo con ojos cerrados
    blink_rate_per_min: float = 0.0    # parpadeos por minuto
    ear_threshold: Optional[float] = None  # umbral de ojo cerrado vigente
                                           # (None = calibrando)
    cervical_baseline: Optional[float] = None   # θc de la postura neutra del
                                                # usuario (None = calibrando)
    cervical_deviation: Optional[float] = None  # θc − línea base
    neck_ratio: Optional[float] = None          # indicador 2D cuello/hombros
    condition_label: str = ""   # condición del protocolo de estudio en curso
                                # ("" = ninguna); ver HistoryLogger

    timestamp: float = field(default_factory=time.time)
    monotonic: float = field(default_factory=time.perf_counter)

    face_detected: bool = True
    pose_detected: bool = True
    pose_valid: bool = True   # landmarks visibles Y distancia dentro de rango
    pose_visible: bool = True  # solo visibilidad de orejas y hombros (sin la
                               # distancia): la calibración guiada informa de
                               # cada problema de encuadre por separado


# ---------------------------------------------------------------------------
# Evento de alerta serializable (JSON)
# ---------------------------------------------------------------------------

@dataclass
class AlertEvent:
    """
    Evento de alerta generado por el FSM.
    Se serializa a JSON para enviarse hacia la UI y el logger.
    """
    alert_type: AlertType
    metric_value: float
    threshold: float
    duration_sec: float
    timestamp: float = field(default_factory=time.time)
    message: str = ""

    def to_json(self) -> str:
        """Serializa el evento a JSON atómico (thread-safe)."""
        return json.dumps({
            "alert_type": self.alert_type.value,
            "metric_value": round(self.metric_value, 3),
            "threshold": round(self.threshold, 3),
            "duration_sec": round(self.duration_sec, 2),
            "timestamp": self.timestamp,
            "message": self.message,
        })


# ---------------------------------------------------------------------------
# Temporizador individual para una condición
# ---------------------------------------------------------------------------

class ConditionTimer:
    """
    Temporizador de condición continua.

    Rastrea cuánto tiempo seguido se ha mantenido una condición. Si la
    condición se rompe durante más de `grace_ms`, el temporizador se reinicia.

    El periodo de gracia
    --------------------
    Sirve para que una interrupción **muy breve** no borre una racha real.
    En el caso de la fatiga ocular, un párpado que cae no se queda quieto:
    hay microaperturas de pocas décimas de segundo dentro de un cierre
    somnoliento. Sin periodo de gracia cada microapertura reinicia el conteo
    y la alerta de 3 s nunca llega a confirmarse (falso negativo).

    El valor natural para ese periodo es `blink_max_ms` (400 ms), la duración
    máxima de un parpadeo fisiológico: por debajo de eso la interrupción es
    indistinguible de un parpadeo; por encima, el ojo está genuinamente
    abierto y la racha debe reiniciarse. Un usuario despierto parpadea cada
    3-5 s, muy por encima del periodo de gracia, así que el filtro no puede
    encadenar parpadeos normales hasta formar una falsa alerta.

    (La implementación anterior comparaba `blink_min_ms <= gap <= blink_max_ms`
    con `gap` medido desde el primer frame inactivo. Como ese `gap` arranca en
    0 y 0 < blink_min_ms, la condición nunca se cumplía y el temporizador se
    reiniciaba en el primer frame inactivo: el filtro de parpadeo era código
    muerto. Ver tests/test_fusion_fsm.py::TestConditionTimer.)
    """

    def __init__(self, window_sec: float,
                 grace_ms: float = 0.0,
                 clock: Callable[[], float] = time.perf_counter):
        """
        Parameters
        ----------
        window_sec:
            Duración mínima continua para confirmar la condición.
        grace_ms:
            Interrupciones de la condición más cortas que este valor no
            reinician la racha. 0 = sin tolerancia.
        clock:
            Fuente de tiempo monótona. Inyectable para tests.
        """
        self.window_sec = window_sec
        self.grace_ms = grace_ms
        self._clock = clock

        self._start_time: Optional[float] = None
        self._false_since: Optional[float] = None
        self._confirmed = False

    def update(self, condition_active: bool,
               now: Optional[float] = None) -> bool:
        """
        Actualiza el temporizador con el estado actual de la condición.

        Parameters
        ----------
        condition_active:
            True si la condición de riesgo se cumple en este frame.
        now:
            Instante del frame (reloj monótono). Si es None se lee del reloj.

        Returns
        -------
        bool
            True si la condición ha sido confirmada (superó la ventana).
        """
        if now is None:
            now = self._clock()

        if condition_active:
            if self._start_time is None:
                self._start_time = now
                logger.debug("ConditionTimer: racha iniciada")
            self._false_since = None

            elapsed = now - self._start_time
            if elapsed >= self.window_sec and not self._confirmed:
                logger.info("ConditionTimer: condición CONFIRMADA tras %.2f s", elapsed)
                self._confirmed = True
            return self._confirmed

        # --- condición inactiva ---
        if self._start_time is None:
            # No había racha que proteger.
            self._false_since = None
            return False

        if self._false_since is None:
            self._false_since = now

        gap_ms = (now - self._false_since) * 1000.0
        if self.grace_ms > 0 and gap_ms <= self.grace_ms:
            # Interrupción dentro del periodo de gracia -> conservar la racha.
            return self._confirmed

        self.reset()
        return False

    def reset(self) -> None:
        """Reinicia el temporizador."""
        self._start_time = None
        self._false_since = None
        self._confirmed = False

    def elapsed(self, now: Optional[float] = None) -> float:
        """Duración de la racha actual (0.0 si no hay racha)."""
        if self._start_time is None:
            return 0.0
        if now is None:
            now = self._clock()
        return max(0.0, now - self._start_time)

    @property
    def elapsed_sec(self) -> float:
        """Tiempo transcurrido desde que comenzó la racha actual."""
        return self.elapsed()


# ---------------------------------------------------------------------------
# Detector de parpadeo, tasa de parpadeo y PERCLOS
# ---------------------------------------------------------------------------

class BlinkAnalyzer:
    """
    Deriva del EAR tres indicadores de fatiga complementarios entre sí.

    Por qué no basta con "EAR <= umbral durante 3 s"
    ------------------------------------------------
    Esa regla solo detecta el ojo **cerrado tres segundos seguidos**, que es
    un microsueño, no fatiga visual: un trabajador con astenopia mantiene los
    ojos abiertos. Por eso se añaden los dos indicadores que la literatura de
    fatiga sí asocia al estado del operador:

    PERCLOS
        Porcentaje de tiempo con el ojo cerrado dentro de una ventana móvil.
        Es la medida de somnolencia mejor validada en conducción y
        teleoperación (Wierwille & Ellsworth, 1994; Dinges & Grace, 1998,
        informe FHWA-MCRT-98-006). Integra cierres parciales y parpadeos
        largos que la regla de 3 s ignora por completo.

    Tasa de parpadeo (parpadeos/minuto)
        El experto la mencionó explícitamente (Bloque C1 del protocolo) como
        el indicador clínico de fatiga visual que él usaría. Todavía **no**
        dispara alertas: el propio experto no fijó valores de corte y este
        sistema no debe inventarlos. Se mide, se registra y queda lista para
        cuando el Bloque C1 se complete con un oftalmólogo.

    Un parpadeo se cuenta cuando el EAR baja del umbral y vuelve a subir
    dentro de la ventana fisiológica [blink_min_ms, blink_max_ms]. Los
    cierres más largos no son parpadeos y se excluyen del conteo (pero sí
    cuentan para PERCLOS).
    """

    def __init__(self,
                 ear_threshold: float = 0.21,
                 blink_min_ms: float = 100.0,
                 blink_max_ms: float = 400.0,
                 window_sec: float = 60.0,
                 clock: Callable[[], float] = time.perf_counter):
        self.ear_threshold = ear_threshold
        self.blink_min_ms = blink_min_ms
        self.blink_max_ms = blink_max_ms
        self.window_sec = window_sec
        self._clock = clock

        # (instante, ojo_cerrado) por frame, recortado a la ventana móvil
        self._samples: Deque[Tuple[float, bool]] = deque()
        # instantes de los parpadeos confirmados dentro de la ventana
        self._blinks: Deque[float] = deque()

        self._closed_since: Optional[float] = None

    def update(self, ear: float, now: Optional[float] = None) -> None:
        """Incorpora la muestra de EAR de un frame."""
        if now is None:
            now = self._clock()

        closed = ear <= self.ear_threshold
        self._samples.append((now, closed))

        if closed:
            if self._closed_since is None:
                self._closed_since = now
        elif self._closed_since is not None:
            duration_ms = (now - self._closed_since) * 1000.0
            if self.blink_min_ms <= duration_ms <= self.blink_max_ms:
                self._blinks.append(now)
            self._closed_since = None

        self._trim(now)

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_sec
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        while self._blinks and self._blinks[0] < cutoff:
            self._blinks.popleft()

    @property
    def perclos(self) -> float:
        """
        Fracción de frames con el ojo cerrado en la ventana móvil [0, 1].

        Se calcula por fracción de muestras y no por integración temporal
        porque el pipeline entrega frames a cadencia aproximadamente
        constante; con FPS muy inestable convendría ponderar por Δt.
        """
        if not self._samples:
            return 0.0
        closed = sum(1 for _, c in self._samples if c)
        return closed / len(self._samples)

    @property
    def blink_rate_per_min(self) -> float:
        """Parpadeos por minuto extrapolados desde la ventana móvil."""
        if not self._samples:
            return 0.0
        span = self._samples[-1][0] - self._samples[0][0]
        if span < 1.0:      # muestra demasiado corta para extrapolar
            return 0.0
        return len(self._blinks) * 60.0 / span

    @property
    def window_filled_sec(self) -> float:
        """Segundos de datos acumulados en la ventana móvil."""
        if len(self._samples) < 2:
            return 0.0
        return self._samples[-1][0] - self._samples[0][0]

    def reset(self) -> None:
        """Vacía la ventana (al perder la cara o cambiar de usuario)."""
        self._samples.clear()
        self._blinks.clear()
        self._closed_since = None


# ---------------------------------------------------------------------------
# Umbral de ojo cerrado adaptado a cada usuario
# ---------------------------------------------------------------------------

# Rango de EAR de ojo abierto que se acepta como calibración válida. Fuera de
# él la calibración se descarta y se repite: un valor así indica que durante
# la calibración el usuario tenía los ojos entornados, miraba hacia otro lado
# o la malla facial no estaba bien ajustada, y derivar un umbral de ahí sería
# peor que no tenerlo.
_VALID_BASELINE_RANGE = (0.12, 0.45)


class EarBaselineCalibrator:
    """
    Calibra el EAR de ojo abierto de cada usuario y deriva de él su umbral de
    ojo cerrado.

    El problema que resuelve
    ------------------------
    En la sesión de prueba del 2026-09-24 el EAR del usuario con los ojos
    **abiertos** tuvo una mediana de 0.200, por debajo del umbral fijo de
    0.21. El sistema clasificó como "ojo cerrado" el 64% de las muestras y
    disparó una alerta de somnolencia cada 30 s con el usuario despierto.

    No es ruido: es un desplazamiento sistemático de la escala. El umbral
    0.21 procede de Soukupová & Čech (2016), que lo definieron sobre los 68
    landmarks de dlib. Face Mesh sitúa los puntos del párpado en posiciones
    distintas del contorno, así que la escala del EAR no es transferible
    entre modelos de landmarks. A eso se suma la variación anatómica entre
    personas y el ángulo de la cámara (una webcam de portátil por debajo de
    los ojos estrecha la hendidura palpebral aparente). La propia tesis ya
    lo anotaba como trabajo futuro: "el umbral único de 0.21 ignora la
    variabilidad anatómica del ojo entre personas".

    Método
    ------
    1. Durante los primeros `calibration_sec` con cara detectada se acumulan
       muestras de EAR.
    2. La línea base es la **mediana**: con ~15-20 parpadeos por minuto el ojo
       está cerrado un 5-8% del tiempo, y la mediana es insensible a esa
       fracción, así que estima el EAR de ojo abierto aunque el usuario
       parpadee con normalidad durante la calibración.
    3. Umbral = `ratio` × línea base, acotado a `bounds`.

    Por qué ratio = 0.65 (ajustado con dos sesiones reales)
    ------------------------------------------------------
    El punto de partida fue 0.70: la proporción que guarda el umbral de la
    literatura (≈0.21) con el EAR de ojo abierto habitual en dlib (≈0.30).

      - Sesión 1: línea base 0.200. Con 0.70 y 0.65, 2.4% de muestras
        "cerradas", frente al 63.9% del umbral fijo.
      - Sesión 2: la calibración captó los ojos más abiertos (0.257, el
        usuario miraba la pantalla atento al arrancar) que durante el trabajo
        (0.236, un 9% más relajados). Con 0.70, el 8.3% de las muestras de
        trabajo quedaron por debajo del umbral y el PERCLOS rozó 0.141 frente
        al límite de 0.15: una falsa somnolencia a la espera de ocurrir. Con
        0.65, 3.6%.

    Un cierre real (EAR ~0.10) queda muy por debajo de ambos umbrales, así
    que 0.65 no resta sensibilidad a lo que importa. Sigue siendo un valor de
    ingeniería, PROVISIONAL, a declarar como tal en el Capítulo IV.

    Recalibración
    -------------
    `reset()` la reinicia. El FSM lo hace al reanudar tras una pausa y tras
    una ausencia prolongada de la cara, porque ambos casos pueden significar
    otra persona u otra colocación de la cámara.
    """

    def __init__(self,
                 calibration_sec: float = 20.0,
                 ratio: float = 0.65,
                 bounds: Tuple[float, float] = (0.10, 0.25),
                 min_samples: int = 60):
        if not 0.0 < ratio < 1.0:
            raise ValueError("ratio debe estar en (0, 1)")
        if bounds[0] >= bounds[1]:
            raise ValueError("bounds debe ser (mínimo, máximo) con mínimo < máximo")
        self.calibration_sec = calibration_sec
        self.ratio = ratio
        self.bounds = bounds
        self.min_samples = min_samples

        self._samples: list = []
        self._started_at: Optional[float] = None
        self._baseline: Optional[float] = None
        self._threshold: Optional[float] = None

    @property
    def is_calibrated(self) -> bool:
        return self._threshold is not None

    @property
    def baseline(self) -> Optional[float]:
        """EAR de ojo abierto estimado, o None si aún no hay calibración."""
        return self._baseline

    @property
    def threshold(self) -> Optional[float]:
        """Umbral de ojo cerrado del usuario, o None si aún no hay calibración."""
        return self._threshold

    def progress(self, now: float) -> float:
        """Fracción [0, 1] de la calibración completada, para la interfaz."""
        if self.is_calibrated:
            return 1.0
        if self._started_at is None:
            return 0.0
        by_time = (now - self._started_at) / self.calibration_sec
        by_count = len(self._samples) / self.min_samples
        return max(0.0, min(1.0, min(by_time, by_count)))

    def update(self, ear: float, now: float) -> bool:
        """
        Incorpora una muestra. Devuelve True solo en el frame en que la
        calibración se completa, para que el FSM pueda reaccionar una vez.
        """
        if self.is_calibrated:
            return False
        if self._started_at is None:
            self._started_at = now
        self._samples.append(float(ear))

        if (now - self._started_at < self.calibration_sec
                or len(self._samples) < self.min_samples):
            return False

        baseline = median(self._samples)
        lo, hi = _VALID_BASELINE_RANGE
        if not lo <= baseline <= hi:
            logger.warning(
                "Calibración de EAR descartada: línea base %.3f fuera de "
                "[%.2f, %.2f]. Se repite (¿ojos entornados, mirada desviada?).",
                baseline, lo, hi)
            self._samples.clear()
            self._started_at = now
            return False

        self._baseline = baseline
        self._threshold = min(max(self.ratio * baseline, self.bounds[0]),
                              self.bounds[1])
        self._samples.clear()
        logger.info(
            "EAR calibrado: línea base de ojo abierto %.3f -> umbral de ojo "
            "cerrado %.3f (ratio %.2f)", baseline, self._threshold, self.ratio)
        return True

    def reset(self) -> None:
        self._samples.clear()
        self._started_at = None
        self._baseline = None
        self._threshold = None


# ---------------------------------------------------------------------------
# Postura de referencia para θc
# ---------------------------------------------------------------------------

class PostureBaselineCalibrator:
    """
    Registra la postura neutra del usuario para medir θc **respecto a ella**.

    El problema que resuelve
    ------------------------
    θc es un ángulo 3D y su componente sagital (cabeza adelantada) depende
    casi por completo de la profundidad `z` de MediaPipe. Con una sola cámara
    frontal esa profundidad no es una medición física:

      - el origen de `z` es el punto medio de la **cadera**, que en un
        escritorio queda fuera del encuadre (visibilidad medida: 0.000), así
        que el modelo extrapola un origen que nunca ve;
      - en el diagnóstico del 2026-09-24, con el usuario sentado recto,
        MediaPipe situó las orejas 1.30 alturas de cuello **por delante** de
        los hombros (~29 cm, físicamente imposible);
      - resultado: θc = 52.9° en vivo (componente sagital 52.5°), frente a
        11.0° calculado solo con x e y. En la sesión de 4.2 min, mediana
        47.9° y **8 alertas cervicales falsas**.

    El sesgo es sistemático para una persona y una colocación de cámara, así
    que se cancela midiendo la **desviación** respecto a su postura neutra:

        Δθc = θc − θc_referencia

    Es el mismo principio que la calibración del EAR, y es práctica habitual
    en la monitorización postural con webcam.

    Limitaciones (a declarar en el Capítulo IV)
    -------------------------------------------
      - Supone que el usuario está sentado recto durante la calibración. Si se
        calibra encorvado, esa postura pasa a ser "neutra". Por eso la
        interfaz lo pide explícitamente y la bandeja permite recalibrar.
      - Cancela el sesgo de `z` pero no su ganancia: Δθc no se traduce a
        centímetros de forma fiable. El umbral de desviación debe fijarse
        empíricamente (medición D.3), no derivarse del 30° absoluto del
        experto.

    Método
    ------
    Mediana de θc durante `calibration_sec` con pose válida. Se descarta y se
    repite si la postura no fue estable (rango intercuartílico mayor que
    `max_iqr_deg`): una calibración hecha mientras el usuario se movía no
    representa ninguna postura concreta.
    """

    def __init__(self, calibration_sec: float = 20.0,
                 min_samples: int = 60, max_iqr_deg: float = 15.0):
        self.calibration_sec = calibration_sec
        self.min_samples = min_samples
        self.max_iqr_deg = max_iqr_deg
        self._samples: list = []
        self._started_at: Optional[float] = None
        self._baseline: Optional[float] = None

    @property
    def is_calibrated(self) -> bool:
        return self._baseline is not None

    @property
    def baseline(self) -> Optional[float]:
        """θc de la postura neutra del usuario, o None si aún calibra."""
        return self._baseline

    def progress(self, now: float) -> float:
        """Fracción [0, 1] de la calibración completada, para la interfaz."""
        if self.is_calibrated:
            return 1.0
        if self._started_at is None:
            return 0.0
        by_time = (now - self._started_at) / self.calibration_sec
        by_count = len(self._samples) / self.min_samples
        return max(0.0, min(1.0, min(by_time, by_count)))

    def deviation(self, theta_c: float) -> Optional[float]:
        """Δθc respecto a la postura neutra, o None si aún calibra."""
        if self._baseline is None:
            return None
        return theta_c - self._baseline

    def update(self, theta_c: float, now: float) -> bool:
        """
        Incorpora una muestra. Devuelve True solo en el frame en que la
        calibración se completa.
        """
        if self.is_calibrated:
            return False
        if self._started_at is None:
            self._started_at = now
        self._samples.append(float(theta_c))

        if (now - self._started_at < self.calibration_sec
                or len(self._samples) < self.min_samples):
            return False

        ordered = sorted(self._samples)
        n = len(ordered)
        iqr = ordered[(3 * n) // 4] - ordered[n // 4]
        if iqr > self.max_iqr_deg:
            logger.warning(
                "Calibración de postura descartada: la postura no fue estable "
                "(rango intercuartílico %.1f° > %.1f°). Se repite.",
                iqr, self.max_iqr_deg)
            self._samples.clear()
            self._started_at = now
            return False

        self._baseline = median(self._samples)
        self._samples.clear()
        logger.info("Postura de referencia calibrada: θc neutro = %.1f° "
                    "(rango intercuartílico %.1f°)", self._baseline, iqr)
        return True

    def reset(self) -> None:
        self._samples.clear()
        self._started_at = None
        self._baseline = None


# ---------------------------------------------------------------------------
# FSM principal
# ---------------------------------------------------------------------------

class FusionFSM:
    """
    Máquina de estados de fusión multimodal.

    Centraliza todos los temporizadores independientes y genera AlertEvent
    cuando se confirma una condición de riesgo.

    Uso típico::

        fsm = FusionFSM(thresholds, on_alert=my_callback)
        alerts = fsm.update(metrics)     # en cada frame
    """

    def __init__(self,
                 thresholds: dict,
                 on_alert: Optional[Callable[[AlertEvent], None]] = None,
                 cooldown_sec: float = 30.0,
                 clock: Callable[[], float] = time.perf_counter):
        """
        Parameters
        ----------
        thresholds:
            Diccionario de umbrales (cargado desde thresholds.json).
        on_alert:
            Callback llamado cuando se confirma una alerta. Recibe un
            AlertEvent. Debe ser thread-safe (se llama desde el hilo de
            inferencia).
        cooldown_sec:
            Tiempo de espera entre alertas del mismo tipo.
        clock:
            Fuente de tiempo monótona. Inyectable para tests deterministas.
        """
        self.thresholds = thresholds
        self.on_alert = on_alert
        self.cooldown_sec = cooldown_sec
        self._clock = clock

        post = thresholds.get("postural", {})
        fat  = thresholds.get("fatigue", {})

        blink_max_ms = fat.get("blink_max_ms", 400)
        blink_min_ms = fat.get("blink_min_ms", 100)

        # Temporizadores independientes por condición
        self._timer_cervical = ConditionTimer(
            window_sec=post.get("cervical_alert_window_sec", 5.0),
            clock=clock,
        )
        self._timer_shoulder = ConditionTimer(
            # Fallback 8.0 s, no 5.0: la ventana de hombros debe ser distinta
            # de la cervical incluso si falta el JSON (petición del experto).
            window_sec=post.get("shoulder_alert_window_sec", 8.0),
            clock=clock,
        )
        self._timer_ear = ConditionTimer(
            window_sec=fat.get("ear_alert_window_sec", 3.0),
            grace_ms=blink_max_ms,
            clock=clock,
        )
        self._timer_yawn = ConditionTimer(
            window_sec=fat.get("yawn_alert_window_sec", 3.0),
            clock=clock,
        )

        # Umbral de ojo cerrado: fijo (literatura) o adaptado al usuario.
        #
        # El valor por defecto en CÓDIGO es "fixed", para que una
        # configuración antigua sin esta clave conserve su comportamiento;
        # config/thresholds.json activa "adaptive" explícitamente. Ver
        # EarBaselineCalibrator para el porqué.
        self._fixed_ear_threshold = fat.get("ear_threshold", 0.21)
        mode = str(fat.get("ear_threshold_mode", "fixed")).lower()
        if mode not in ("fixed", "adaptive"):
            logger.warning("fatigue.ear_threshold_mode='%s' no es válido; "
                           "usando 'fixed'.", mode)
            mode = "fixed"
        self._ear_mode = mode
        self._ear_calibrator: Optional[EarBaselineCalibrator] = None
        if mode == "adaptive":
            bounds = fat.get("ear_threshold_bounds", [0.10, 0.25])
            self._ear_calibrator = EarBaselineCalibrator(
                calibration_sec=fat.get("ear_calibration_sec", 20.0),
                ratio=fat.get("ear_adaptive_ratio", 0.65),
                bounds=(float(bounds[0]), float(bounds[1])),
            )
        # Una ausencia de la cara más larga que esto fuerza a recalibrar:
        # puede haberse sentado otra persona o haberse movido la cámara.
        self._ear_recalibrate_after = fat.get(
            "ear_recalibrate_after_absence_sec", 30.0)
        self._face_last_seen: Optional[float] = None

        self._perclos_enabled = bool(fat.get("perclos_enabled", True))
        self._perclos_threshold = fat.get("perclos_threshold", 0.15)
        self._perclos_window_sec = fat.get("perclos_window_sec", 60.0)
        self.blink_analyzer = BlinkAnalyzer(
            ear_threshold=self._fixed_ear_threshold,
            blink_min_ms=blink_min_ms,
            blink_max_ms=blink_max_ms,
            window_sec=self._perclos_window_sec,
            clock=clock,
        )

        # Cooldowns por tipo de alerta (instante monótono del último disparo)
        self._last_alert: dict[AlertType, float] = {}

        # Umbrales
        self._cervical_max = post.get("cervical_angle_max_deg", 30.0)
        self._shoulder_max = post.get("shoulder_asymmetry_max_deg", 10.0)
        self._mouth_threshold = fat.get("mouth_opening_threshold", 0.45)

        # θc absoluto o relativo a la postura neutra del usuario.
        #
        # Igual que con el EAR, el valor por defecto en CÓDIGO es "absolute"
        # para que una configuración antigua conserve su comportamiento;
        # config/thresholds.json activa "reference". Ver
        # PostureBaselineCalibrator para el porqué.
        cmode = str(post.get("cervical_mode", "absolute")).lower()
        if cmode not in ("absolute", "reference"):
            logger.warning("postural.cervical_mode='%s' no es válido; "
                           "usando 'absolute'.", cmode)
            cmode = "absolute"
        self._cervical_mode = cmode
        self._cervical_dev_max = post.get("cervical_deviation_max_deg", 15.0)
        self._posture_calibrator: Optional[PostureBaselineCalibrator] = None
        if cmode == "reference":
            self._posture_calibrator = PostureBaselineCalibrator(
                calibration_sec=post.get("posture_calibration_sec", 20.0),
                max_iqr_deg=post.get("posture_calibration_max_iqr_deg", 15.0),
            )
        self._posture_recalibrate_after = post.get(
            "posture_recalibrate_after_absence_sec", 30.0)
        self._pose_last_seen: Optional[float] = None

        logger.info(
            "FusionFSM inicializado (θc=%s, ΔE_max=%.1f°, EAR=%s, "
            "PERCLOS=%s, cooldown=%.0fs)",
            (f"referencia, Δ>{self._cervical_dev_max:.1f}°"
             if cmode == "reference" else f"absoluto >{self._cervical_max:.1f}°"),
            self._shoulder_max,
            ("adaptativo" if self._ear_mode == "adaptive"
             else f"<={self._fixed_ear_threshold:.2f}"),
            f"{self._perclos_threshold:.2f}" if self._perclos_enabled else "off",
            cooldown_sec,
        )

    # ------------------------------------------------------------------

    @property
    def ear_threshold(self) -> Optional[float]:
        """
        Umbral de ojo cerrado vigente. En modo adaptativo es None mientras la
        calibración del usuario no ha terminado.
        """
        if self._ear_calibrator is None:
            return self._fixed_ear_threshold
        return self._ear_calibrator.threshold

    @property
    def ear_calibrating(self) -> bool:
        return self._ear_calibrator is not None and not self._ear_calibrator.is_calibrated

    def recalibrate_ear(self) -> None:
        """
        Descarta la calibración del EAR y vacía la ventana de PERCLOS.

        La ventana se vacía porque sus muestras se clasificaron con el umbral
        anterior: mezclar dos criterios en un mismo PERCLOS no tendría sentido.
        """
        if self._ear_calibrator is not None:
            self._ear_calibrator.reset()
            self.blink_analyzer.reset()
            self._timer_ear.reset()
            logger.info("Calibración de EAR reiniciada.")

    def _cervical_condition(self, metrics: SensorMetrics, now: float):
        """
        Valor y límite con los que se evalúa la cabeza adelantada.

        Returns
        -------
        (valor, límite, texto) o None si aún no se puede evaluar
            En modo absoluto: θc frente a cervical_angle_max_deg.
            En modo referencia: Δθc frente a cervical_deviation_max_deg, y
            None mientras la postura neutra se está calibrando (no hay aún
            una referencia que dé sentido a la desviación).
        """
        theta = metrics.cervical_angle
        if self._posture_calibrator is None:
            return (abs(theta), self._cervical_max,
                    f"θc={theta:.1f}° (máx {self._cervical_max}°)")

        if not self._posture_calibrator.is_calibrated:
            self._posture_calibrator.update(theta, now)
            return None

        dev = self._posture_calibrator.deviation(theta)
        return (dev, self._cervical_dev_max,
                f"+{dev:.1f}° respecto a tu postura neutra "
                f"(máx +{self._cervical_dev_max}°)")

    @property
    def cervical_baseline(self) -> Optional[float]:
        """θc de la postura neutra (None si calibra o en modo absoluto)."""
        if self._posture_calibrator is None:
            return None
        return self._posture_calibrator.baseline

    @property
    def posture_calibrating(self) -> bool:
        return (self._posture_calibrator is not None
                and not self._posture_calibrator.is_calibrated)

    def recalibrate_posture(self) -> None:
        """Descarta la postura de referencia; se vuelve a calibrar."""
        if self._posture_calibrator is not None:
            self._posture_calibrator.reset()
            self._timer_cervical.reset()
            logger.info("Postura de referencia reiniciada.")

    def recalibrate_all(self) -> None:
        """
        Descarta la postura neutra y el EAR calibrados, para volver a medirlos
        (calibración guiada, o si otra persona ocupa el puesto).
        """
        self.recalibrate_posture()
        self.recalibrate_ear()

    # ------------------------------------------------------------------
    # Calibración guiada
    # ------------------------------------------------------------------

    @property
    def needs_calibration(self) -> bool:
        """True si falta alguna calibración para poder evaluar alertas."""
        return self.posture_calibrating or self.ear_calibrating

    def feed_calibration(self, metrics: SensorMetrics) -> None:
        """
        Alimenta SOLO los calibradores, sin evaluar ninguna alerta.

        La usa la calibración guiada, que ya ha comprobado el encuadre antes
        de llamar. Se separa de update() porque durante la calibración el
        usuario está siguiendo instrucciones: sus métricas describen la
        postura de referencia, no un riesgo, y no deben tocar ni los
        temporizadores ni la bitácora.
        """
        now = metrics.monotonic
        pc = self._posture_calibrator
        if (pc is not None and not pc.is_calibrated
                and metrics.pose_detected and metrics.pose_valid):
            pc.update(metrics.cervical_angle, now)

        ec = self._ear_calibrator
        if ec is not None and not ec.is_calibrated and metrics.face_detected:
            if ec.update(metrics.ear_avg, now):
                self.blink_analyzer.ear_threshold = ec.threshold

    def calibration_progress(self, now: Optional[float] = None) -> dict:
        """Progreso [0, 1] de cada calibración; 1.0 si ese modo no calibra."""
        if now is None:
            now = self._clock()
        pc, ec = self._posture_calibrator, self._ear_calibrator
        return {
            "posture": pc.progress(now) if pc is not None else 1.0,
            "ear":     ec.progress(now) if ec is not None else 1.0,
            "posture_done": pc is None or pc.is_calibrated,
            "ear_done":     ec is None or ec.is_calibrated,
        }

    def reset_timers(self) -> None:
        """
        Reinicia temporizadores, PERCLOS y cooldowns **conservando la
        calibración**.

        Es lo que corresponde al reanudar tras una pausa o al empezar el
        seguimiento: la racha anterior describe una postura que ya no está
        vigente, pero la persona y la cámara son las mismas. Borrar también
        la calibración (reset_all) obligaría a recalibrar en silencio, con el
        usuario quizá mal sentado — justo lo que la calibración guiada evita.
        """
        self._timer_cervical.reset()
        self._timer_shoulder.reset()
        self._timer_ear.reset()
        self._timer_yawn.reset()
        self.blink_analyzer.reset()
        self._last_alert.clear()
        self._face_last_seen = None
        self._pose_last_seen = None

    # ------------------------------------------------------------------

    def update(self, metrics: SensorMetrics) -> list[AlertEvent]:
        """
        Actualiza todos los temporizadores con las métricas del frame actual.

        Returns
        -------
        list[AlertEvent]
            Alertas confirmadas en este frame (normalmente vacía).
        """
        generated: list[AlertEvent] = []
        now = metrics.monotonic

        # --- Postura ---------------------------------------------------
        # `pose_valid` cubre dos casos en los que las métricas posturales
        # existen pero no significan nada: landmarks con visibilidad baja
        # (cabeza girada) y usuario fuera del rango de distancia validado por
        # el experto. En ambos, congelar el temporizador sería peor que
        # reiniciarlo: al recuperar la detección el sistema dispararía una
        # alerta acumulada durante un intervalo que nunca llegó a medir.
        if metrics.pose_detected and metrics.pose_valid:
            # Una ausencia larga puede significar otra persona u otra
            # colocación de la cámara: la postura de referencia ya no vale.
            if (self._posture_calibrator is not None
                    and self._posture_recalibrate_after > 0
                    and self._pose_last_seen is not None
                    and now - self._pose_last_seen > self._posture_recalibrate_after):
                logger.info("Usuario ausente %.0f s: se recalibra la postura.",
                            now - self._pose_last_seen)
                self.recalibrate_posture()
            self._pose_last_seen = now

            cervical = self._cervical_condition(metrics, now)
            if cervical is not None:
                value, limit, texto = cervical
                if self._timer_cervical.update(value > limit, now):
                    alert = self._try_fire_alert(
                        AlertType.CERVICAL_ANGLE,
                        metric_value=value,
                        threshold=limit,
                        duration_sec=self._timer_cervical.elapsed(now),
                        now=now,
                        message=(
                            f"Cabeza adelantada detectada: {texto} por "
                            f"{self._timer_cervical.elapsed(now):.1f}s"
                        ),
                    )
                    if alert:
                        generated.append(alert)

            if self._timer_shoulder.update(
                    abs(metrics.shoulder_asymmetry) > self._shoulder_max, now):
                alert = self._try_fire_alert(
                    AlertType.SHOULDER_ASYMM,
                    metric_value=metrics.shoulder_asymmetry,
                    threshold=self._shoulder_max,
                    duration_sec=self._timer_shoulder.elapsed(now),
                    now=now,
                    message=(
                        f"Asimetría de hombros: ΔE={metrics.shoulder_asymmetry:.1f}° "
                        f"(máx {self._shoulder_max}°) por "
                        f"{self._timer_shoulder.elapsed(now):.1f}s"
                    ),
                )
                if alert:
                    generated.append(alert)
        else:
            self._timer_cervical.reset()
            self._timer_shoulder.reset()

        # --- Fatiga ----------------------------------------------------
        if metrics.face_detected:
            # Una ausencia larga de la cara puede significar otra persona:
            # la calibración del EAR ya no le corresponde.
            if (self._ear_calibrator is not None
                    and self._ear_recalibrate_after > 0
                    and self._face_last_seen is not None
                    and now - self._face_last_seen > self._ear_recalibrate_after):
                logger.info("Cara ausente %.0f s: se recalibra el EAR.",
                            now - self._face_last_seen)
                self.recalibrate_ear()
            self._face_last_seen = now

            # Calibración en curso: ni PERCLOS ni fatiga ocular se evalúan,
            # porque todavía no hay un umbral que signifique algo para este
            # usuario. Alimentarlos con el umbral fijo reproduciría el error
            # que motivó la calibración.
            if self._ear_calibrator is not None and not self._ear_calibrator.is_calibrated:
                if self._ear_calibrator.update(metrics.ear_avg, now):
                    self.blink_analyzer.ear_threshold = self._ear_calibrator.threshold

            ear_thr = self.ear_threshold
            if ear_thr is not None:
                self.blink_analyzer.update(metrics.ear_avg, now)

                if self._timer_ear.update(metrics.ear_avg <= ear_thr, now):
                    alert = self._try_fire_alert(
                        AlertType.EYE_FATIGUE,
                        metric_value=metrics.ear_avg,
                        threshold=ear_thr,
                        duration_sec=self._timer_ear.elapsed(now),
                        now=now,
                        message=(
                            f"Fatiga ocular: EAR={metrics.ear_avg:.3f} "
                            f"<= {ear_thr:.3f} por "
                            f"{self._timer_ear.elapsed(now):.1f}s"
                        ),
                    )
                    if alert:
                        generated.append(alert)

            if self._timer_yawn.update(
                    metrics.mouth_opening >= self._mouth_threshold, now):
                alert = self._try_fire_alert(
                    AlertType.YAWN,
                    metric_value=metrics.mouth_opening,
                    threshold=self._mouth_threshold,
                    duration_sec=self._timer_yawn.elapsed(now),
                    now=now,
                    message=(
                        f"Bostezo detectado: apertura={metrics.mouth_opening:.2f} "
                        f">= {self._mouth_threshold}"
                    ),
                )
                if alert:
                    generated.append(alert)

            # PERCLOS: solo cuando la ventana móvil está razonablemente llena,
            # si no un par de frames con el ojo cerrado darían PERCLOS=1.0.
            if (self._perclos_enabled
                    and self.blink_analyzer.window_filled_sec
                    >= 0.5 * self._perclos_window_sec):
                perclos = self.blink_analyzer.perclos
                if perclos >= self._perclos_threshold:
                    alert = self._try_fire_alert(
                        AlertType.DROWSINESS,
                        metric_value=perclos,
                        threshold=self._perclos_threshold,
                        duration_sec=self.blink_analyzer.window_filled_sec,
                        now=now,
                        message=(
                            f"Somnolencia (PERCLOS): ojos cerrados el "
                            f"{perclos * 100:.0f}% del último minuto "
                            f"(umbral {self._perclos_threshold * 100:.0f}%)"
                        ),
                    )
                    if alert:
                        generated.append(alert)
        else:
            # Sin cara no hay EAR: congelar la racha permitiría que el usuario
            # se ausente 10 s y al volver reciba una alerta de fatiga por un
            # intervalo en el que no se midió nada.
            self._timer_ear.reset()
            self._timer_yawn.reset()

        return generated

    # ------------------------------------------------------------------

    def _try_fire_alert(self, alert_type: AlertType,
                        metric_value: float, threshold: float,
                        duration_sec: float, message: str,
                        now: Optional[float] = None) -> Optional[AlertEvent]:
        """
        Dispara una alerta si no está en cooldown.
        Resetea el temporizador correspondiente tras el disparo.
        """
        if now is None:
            now = self._clock()

        last = self._last_alert.get(alert_type)
        if last is not None and (now - last) < self.cooldown_sec:
            return None  # Todavía en cooldown

        self._last_alert[alert_type] = now

        event = AlertEvent(
            alert_type=alert_type,
            metric_value=metric_value,
            threshold=threshold,
            duration_sec=duration_sec,
            timestamp=time.time(),   # hora de pared para la bitácora
            message=message,
        )

        logger.warning("ALERTA: %s", message)

        # Resetear el temporizador para que la siguiente alerta exija de nuevo
        # la ventana completa en vez de re-disparar frame a frame.
        timer = self._get_timer(alert_type)
        if timer is not None:
            timer.reset()

        if self.on_alert:
            try:
                self.on_alert(event)
            except Exception as exc:
                logger.error("Error en on_alert callback: %s", exc)

        return event

    def _get_timer(self, alert_type: AlertType) -> Optional[ConditionTimer]:
        # DROWSINESS no tiene ConditionTimer: su "ventana" es la ventana móvil
        # de PERCLOS, y el cooldown es lo que evita el re-disparo continuo.
        return {
            AlertType.CERVICAL_ANGLE: self._timer_cervical,
            AlertType.SHOULDER_ASYMM: self._timer_shoulder,
            AlertType.EYE_FATIGUE:    self._timer_ear,
            AlertType.YAWN:           self._timer_yawn,
        }.get(alert_type)

    def get_timers_status(self) -> dict:
        """
        Estado actual de todos los temporizadores, para el panel de telemetría.
        """
        now = self._clock()
        return {
            "cervical_elapsed_sec": round(self._timer_cervical.elapsed(now), 2),
            "shoulder_elapsed_sec": round(self._timer_shoulder.elapsed(now), 2),
            "ear_elapsed_sec":      round(self._timer_ear.elapsed(now), 2),
            "yawn_elapsed_sec":     round(self._timer_yawn.elapsed(now), 2),
            "cervical_window_sec":  self._timer_cervical.window_sec,
            "shoulder_window_sec":  self._timer_shoulder.window_sec,
            "ear_window_sec":       self._timer_ear.window_sec,
            "yawn_window_sec":      self._timer_yawn.window_sec,
            "perclos":              round(self.blink_analyzer.perclos, 3),
            "blink_rate_per_min":   round(self.blink_analyzer.blink_rate_per_min, 1),
            # Umbral de ojo cerrado vigente (None mientras se calibra) y estado
            # de la calibración, para que el panel no pinte en rojo un ojo
            # abierto con un umbral que no corresponde al usuario.
            "ear_threshold": (round(self.ear_threshold, 3)
                              if self.ear_threshold is not None else None),
            "ear_calibrating":      self.ear_calibrating,
            "ear_baseline": (round(self._ear_calibrator.baseline, 3)
                             if self._ear_calibrator is not None
                             and self._ear_calibrator.baseline is not None
                             else None),
            "ear_calibration_progress": (
                round(self._ear_calibrator.progress(now), 2)
                if self._ear_calibrator is not None else 1.0),
            # Postura de referencia para θc (modo "reference").
            "cervical_mode":        self._cervical_mode,
            "cervical_baseline": (round(self.cervical_baseline, 2)
                                  if self.cervical_baseline is not None else None),
            "cervical_deviation_max": self._cervical_dev_max,
            "posture_calibrating":  self.posture_calibrating,
            "posture_calibration_progress": (
                round(self._posture_calibrator.progress(now), 2)
                if self._posture_calibrator is not None else 1.0),
        }

    def reset_all(self) -> None:
        """Reinicia todos los temporizadores (útil al cambiar de usuario)."""
        self._timer_cervical.reset()
        self._timer_shoulder.reset()
        self._timer_ear.reset()
        self._timer_yawn.reset()
        self.blink_analyzer.reset()
        self._last_alert.clear()
        # Tras una pausa puede ser otra persona o la cámara puede haberse
        # movido: la calibración del EAR se repite.
        if self._ear_calibrator is not None:
            self._ear_calibrator.reset()
        self._face_last_seen = None
        if self._posture_calibrator is not None:
            self._posture_calibrator.reset()
        self._pose_last_seen = None
        logger.info("FusionFSM: todos los temporizadores reiniciados.")
