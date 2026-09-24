"""
smoothing.py
------------
Filtrado temporal de las métricas antes de alimentar el FSM.

Por qué hace falta
------------------
BlazePose y Face Mesh re-estiman cada frame de forma independiente, así que
las métricas llegan con jitter de alta frecuencia aun con el sujeto inmóvil.
En la bitácora de la sesión del 2026-07-12 (`data/history.db`, tabla
`metrics_log`) ΔE oscila entre -20.5° y +3.0° dentro de una misma sesión sin
que el usuario cambiara de postura: ruido de estimación, no señal.

Alimentar el FSM con esa señal cruda produce dos fallos:

  1. **Chattering en el umbral.** Una métrica que cruza el umbral 30 veces por
     segundo reinicia el temporizador continuamente y la alerta nunca se
     confirma aunque la postura de riesgo sea real (falsos negativos).
  2. **Picos espurios.** Un solo frame mal estimado puede disparar la
     condición y, si coincide con el final de una racha, adelantar una alerta.

Estrategia (dos etapas, barata y sin dependencias)
--------------------------------------------------
  1. **Mediana móvil** de ventana corta → elimina outliers de 1-2 frames
     (frames mal estimados) sin arrastrar su valor al promedio.
  2. **Media móvil exponencial (EMA)** → suaviza el resto y define la
     constante de tiempo de respuesta del sistema.

El retardo introducido es del orden de la constante de tiempo de la EMA
(~0.3 s con los valores por defecto), despreciable frente a las ventanas de
alerta de 3-5 s del FSM, pero suficiente para estabilizar la lectura que ve
el usuario en el panel de telemetría.

Autor: Tesis Huisa Perez, UNSA 2026
"""

from collections import deque
from statistics import median
from typing import Deque, Optional


class MetricSmoother:
    """
    Filtro mediana + EMA para una métrica escalar continua.

    Uso::

        smoother = MetricSmoother(median_window=5, alpha=0.35)
        theta_filtrado = smoother.update(theta_crudo)

    Parameters
    ----------
    median_window:
        Nº de muestras de la mediana móvil. Impar y pequeño (3-7). Con 5
        muestras a ~20 FPS se rechazan outliers de hasta 2 frames seguidos.
    alpha:
        Factor de la EMA en (0, 1]. Más alto = responde más rápido y suaviza
        menos. 0.35 a 20 FPS equivale a una constante de tiempo ~0.14 s
        (t = T_frame / alpha), es decir ~0.3 s para alcanzar el 90%.
    """

    def __init__(self, median_window: int = 5, alpha: float = 0.35):
        if median_window < 1:
            raise ValueError("median_window debe ser >= 1")
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha debe estar en (0, 1]")

        self.median_window = median_window
        self.alpha = alpha
        self._buffer: Deque[float] = deque(maxlen=median_window)
        self._ema: Optional[float] = None

    def update(self, value: float) -> float:
        """Incorpora una muestra cruda y devuelve el valor filtrado."""
        self._buffer.append(float(value))
        med = median(self._buffer)

        if self._ema is None:
            self._ema = med          # arranque sin transitorio desde 0
        else:
            self._ema = self.alpha * med + (1.0 - self.alpha) * self._ema

        return self._ema

    @property
    def value(self) -> Optional[float]:
        """Último valor filtrado, o None si aún no hay muestras."""
        return self._ema

    def reset(self) -> None:
        """
        Vacía el filtro.

        Debe llamarse cuando se pierde la detección: al recuperarla el sujeto
        puede estar en otra postura, y arrastrar el estado anterior mezclaría
        dos posturas distintas en una sola lectura.
        """
        self._buffer.clear()
        self._ema = None


class MetricSmootherBank:
    """
    Conjunto de MetricSmoother indexados por nombre, para no repetir la
    construcción de cuatro filtros idénticos en el hilo de inferencia.

    Uso::

        bank = MetricSmootherBank(["theta_c", "delta_e", "ear", "mar"])
        theta = bank.update("theta_c", theta_crudo)
        bank.reset()          # al perder la detección
    """

    def __init__(self, names, median_window: int = 5, alpha: float = 0.35):
        self._smoothers = {
            name: MetricSmoother(median_window=median_window, alpha=alpha)
            for name in names
        }

    def update(self, name: str, value: float) -> float:
        return self._smoothers[name].update(value)

    def get(self, name: str) -> Optional[float]:
        return self._smoothers[name].value

    def reset(self, name: Optional[str] = None) -> None:
        """Reinicia un filtro concreto, o todos si ``name`` es None."""
        if name is None:
            for s in self._smoothers.values():
                s.reset()
        else:
            self._smoothers[name].reset()


class DebouncedState:
    """
    Estado discreto que solo cambia tras mantenerse estable un tiempo.

    Por qué hace falta además del filtro numérico
    ---------------------------------------------
    El filtro mediana + EMA estabiliza un valor continuo, pero un aviso que
    depende de si ese valor está a un lado u otro de un límite sigue
    parpadeando cuando el valor ronda el límite. En la sesión de prueba del
    2026-09-24 la distancia estimada cruzó 30 veces el límite de 0.70 m en
    129 muestras sin que el usuario se moviera: el aviso de encuadre aparecía
    y desaparecía constantemente.

    Aquí un estado nuevo solo se muestra si se mantiene `hold_sec` seguidos,
    y el estado neutro (sin aviso) solo vuelve tras `clear_sec`. Mostrar un
    problema exige más confirmación que retirarlo: un aviso que se va rápido
    molesta poco; uno que aparece por un frame de ruido sí molesta.

    Uso::

        hint = DebouncedState(neutral="", hold_sec=1.5, clear_sec=1.0)
        shown = hint.update("too_far", now)   # sigue "" hasta 1.5 s estable
    """

    def __init__(self, neutral: str = "", hold_sec: float = 1.5,
                 clear_sec: float = 1.0):
        if hold_sec < 0 or clear_sec < 0:
            raise ValueError("hold_sec y clear_sec deben ser >= 0")
        self._neutral = neutral
        self._hold_sec = hold_sec
        self._clear_sec = clear_sec
        self._shown = neutral
        self._pending: Optional[str] = None
        self._pending_since: float = 0.0

    @property
    def value(self) -> str:
        """Estado actualmente mostrado."""
        return self._shown

    def update(self, candidate: str, now: float) -> str:
        """
        Propone el estado del frame actual y devuelve el que debe mostrarse.

        Parameters
        ----------
        candidate:
            Estado que indican las métricas de este frame.
        now:
            Instante del frame (reloj monótono).
        """
        if candidate == self._shown:
            self._pending = None
            return self._shown

        if candidate != self._pending:
            self._pending = candidate
            self._pending_since = now

        hold = self._clear_sec if candidate == self._neutral else self._hold_sec
        if now - self._pending_since >= hold:
            self._shown = candidate
            self._pending = None
        return self._shown

    def reset(self) -> None:
        """Vuelve al estado neutro sin retardo."""
        self._shown = self._neutral
        self._pending = None
