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

    timestamp: float = field(default_factory=time.time)
    monotonic: float = field(default_factory=time.perf_counter)

    face_detected: bool = True
    pose_detected: bool = True
    pose_valid: bool = True   # landmarks visibles Y distancia dentro de rango


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

        # Análisis de parpadeo / PERCLOS
        self._ear_threshold = fat.get("ear_threshold", 0.21)
        self._perclos_enabled = bool(fat.get("perclos_enabled", True))
        self._perclos_threshold = fat.get("perclos_threshold", 0.15)
        self._perclos_window_sec = fat.get("perclos_window_sec", 60.0)
        self.blink_analyzer = BlinkAnalyzer(
            ear_threshold=self._ear_threshold,
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

        logger.info(
            "FusionFSM inicializado (θc_max=%.1f°, ΔE_max=%.1f°, EAR<=%.2f, "
            "PERCLOS=%s, cooldown=%.0fs)",
            self._cervical_max, self._shoulder_max, self._ear_threshold,
            f"{self._perclos_threshold:.2f}" if self._perclos_enabled else "off",
            cooldown_sec,
        )

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
            if self._timer_cervical.update(
                    abs(metrics.cervical_angle) > self._cervical_max, now):
                alert = self._try_fire_alert(
                    AlertType.CERVICAL_ANGLE,
                    metric_value=metrics.cervical_angle,
                    threshold=self._cervical_max,
                    duration_sec=self._timer_cervical.elapsed(now),
                    now=now,
                    message=(
                        f"Cabeza adelantada detectada: θc={metrics.cervical_angle:.1f}° "
                        f"(máx {self._cervical_max}°) por "
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
            self.blink_analyzer.update(metrics.ear_avg, now)

            if self._timer_ear.update(metrics.ear_avg <= self._ear_threshold, now):
                alert = self._try_fire_alert(
                    AlertType.EYE_FATIGUE,
                    metric_value=metrics.ear_avg,
                    threshold=self._ear_threshold,
                    duration_sec=self._timer_ear.elapsed(now),
                    now=now,
                    message=(
                        f"Fatiga ocular: EAR={metrics.ear_avg:.3f} "
                        f"<= {self._ear_threshold} por "
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
        }

    def reset_all(self) -> None:
        """Reinicia todos los temporizadores (útil al cambiar de usuario)."""
        self._timer_cervical.reset()
        self._timer_shoulder.reset()
        self._timer_ear.reset()
        self._timer_yawn.reset()
        self.blink_analyzer.reset()
        self._last_alert.clear()
        logger.info("FusionFSM: todos los temporizadores reiniciados.")
