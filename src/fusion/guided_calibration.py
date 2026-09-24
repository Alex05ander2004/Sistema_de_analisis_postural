"""
guided_calibration.py
---------------------
Calibración guiada: el usuario decide cuándo está listo, el sistema comprueba
el encuadre y solo entonces mide su postura neutra y su ojo abierto.

Por qué existe
--------------
Las dos métricas centrales del sistema se miden **respecto al propio
usuario**, porque con una cámara frontal sus valores absolutos no son fiables
(`docs/validation_report.md` §3.6 y §3.7):

  - θc se evalúa como desviación sobre la postura neutra;
  - el umbral de ojo cerrado es una fracción del EAR de ojo abierto.

Hasta ahora esa referencia se tomaba en silencio durante los primeros 20 s
de monitoreo. Si en ese momento el usuario estaba encorvado, fuera de rango o
distraído, esa postura pasaba a ser "neutra" y nada lo advertía: todas las
mediciones posteriores de la sesión quedaban sesgadas.

La calibración guiada controla las condiciones de medida, que es lo que da
validez a una referencia:

  1. No empieza hasta que el usuario pulsa "Calibrar", y ese botón solo se
     habilita con el encuadre correcto (rostro, hombros visibles y distancia
     dentro del rango validado por el experto).
  2. Una cuenta atrás deja al usuario colocarse antes de medir.
  3. Si durante la medición se pierde el encuadre más de un instante, la
     medición se reinicia: una referencia que mezcla dos posturas no
     representa ninguna.
  4. Si no se consigue una postura estable en un tiempo razonable, falla con
     un motivo concreto en lugar de aceptar una referencia dudosa.

Fases
-----
    idle ──start()──▶ countdown ──(3 s)──▶ collecting ──▶ done
                         ▲                     │   └──(timeout)──▶ failed
                         └──(encuadre perdido)─┘

Autor: Tesis Huisa Perez, UNSA 2026
"""

import logging
from dataclasses import dataclass, field
from statistics import mean
from typing import Optional

logger = logging.getLogger(__name__)

IDLE = "idle"
COUNTDOWN = "countdown"
COLLECTING = "collecting"
DONE = "done"
FAILED = "failed"


@dataclass
class FramingCheck:
    """Condiciones de encuadre necesarias para calibrar."""
    face: bool = False
    body: bool = False
    distance_ok: bool = False
    distance_m: Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.face and self.body and self.distance_ok

    def as_dict(self) -> dict:
        return {"face": self.face, "body": self.body,
                "distance_ok": self.distance_ok,
                "distance_m": self.distance_m, "ok": self.ok}


@dataclass
class CalibrationStatus:
    """Estado publicado para la interfaz."""
    phase: str = IDLE
    progress: float = 0.0
    countdown_remaining: float = 0.0
    message: str = ""
    checks: FramingCheck = field(default_factory=FramingCheck)
    result: Optional[dict] = None

    def as_dict(self) -> dict:
        return {"phase": self.phase, "progress": round(self.progress, 3),
                "countdown_remaining": round(self.countdown_remaining, 1),
                "message": self.message, "checks": self.checks.as_dict(),
                "result": self.result}


class GuidedCalibration:
    """
    Orquesta la calibración de la postura neutra y del EAR sobre un FusionFSM.

    Todo su estado se toca desde un solo hilo (el de inferencia); la interfaz
    solo lee la copia que se publica en cada frame.
    """

    def __init__(self, fsm, min_distance_m: float, max_distance_m: float,
                 countdown_sec: float = 3.0,
                 framing_grace_sec: float = 1.0,
                 timeout_sec: float = 60.0):
        """
        Parameters
        ----------
        fsm:
            FusionFSM cuyos calibradores se alimentan.
        min_distance_m, max_distance_m:
            Rango de distancia recomendado (el validado por el experto). Se
            exige el rango estricto, no la banda de tolerancia: la referencia
            debe tomarse en las condiciones de uso previstas.
        countdown_sec:
            Tiempo para colocarse antes de empezar a medir.
        framing_grace_sec:
            Pérdida de encuadre que se tolera sin reiniciar la medición. Un
            frame suelto sin detección no debe tirar 15 s de calibración.
        timeout_sec:
            Tiempo máximo midiendo sin completar la calibración.
        """
        self._fsm = fsm
        self._min_d = min_distance_m
        self._max_d = max_distance_m
        self.countdown_sec = countdown_sec
        self.framing_grace_sec = framing_grace_sec
        self.timeout_sec = timeout_sec

        self._phase = IDLE
        self._phase_since = 0.0
        self._bad_since: Optional[float] = None
        self._message = ""
        self._distances: list = []
        self._last_progress = {"posture": 0.0, "ear": 0.0}
        self._result: Optional[dict] = None
        self._last_restart: str = ""
        self._status = CalibrationStatus()

    # ------------------------------------------------------------------

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def status(self) -> CalibrationStatus:
        return self._status

    def check_framing(self, metrics) -> FramingCheck:
        d = metrics.distance_m
        return FramingCheck(
            face=bool(metrics.face_detected),
            body=bool(metrics.pose_detected
                      and getattr(metrics, "pose_visible", True)),
            distance_ok=d is not None and self._min_d <= d <= self._max_d,
            distance_m=d,
        )

    def start(self, now: float) -> None:
        """Descarta la calibración anterior y empieza la cuenta atrás."""
        self._fsm.recalibrate_all()
        self._enter(COUNTDOWN, now)
        self._message = "Colócate y mantén la postura."
        self._result = None
        self._last_restart = ""
        logger.info("Calibración guiada iniciada.")

    def cancel(self) -> None:
        self._phase = IDLE
        self._message = "Calibración cancelada."

    # ------------------------------------------------------------------

    def step(self, metrics, now: float) -> CalibrationStatus:
        """Avanza la calibración con las métricas de un frame."""
        checks = self.check_framing(metrics)

        if self._phase in (COUNTDOWN, COLLECTING):
            if not checks.ok:
                if self._bad_since is None:
                    self._bad_since = now
                elif now - self._bad_since > self.framing_grace_sec:
                    self._restart(now, self._framing_message(checks))
            else:
                self._bad_since = None
                if self._phase == COUNTDOWN:
                    if now - self._phase_since >= self.countdown_sec:
                        self._enter(COLLECTING, now)
                        self._message = ("Midiendo: quédate quieto, recto, "
                                         "mirando la pantalla.")
                        # El aviso del reinicio solo se veía durante los 3 s
                        # de cuenta atrás: se mantiene mientras se repite la
                        # medición, para que el usuario sepa qué evitar.
                        if self._last_restart:
                            self._message += (f" (Se reinició: "
                                              f"{self._last_restart})")
                elif self._phase == COLLECTING:
                    self._collect(metrics, checks, now)

        self._status = self._build_status(checks, now)
        return self._status

    # ------------------------------------------------------------------

    def _collect(self, metrics, checks: FramingCheck, now: float) -> None:
        self._fsm.feed_calibration(metrics)
        if checks.distance_m is not None:
            self._distances.append(checks.distance_m)

        prog = self._fsm.calibration_progress(now)

        # Un calibrador que retrocede se ha reiniciado a sí mismo porque la
        # medición no era válida. Se informa en lugar de dejar al usuario
        # mirando una barra que vuelve atrás sin explicación.
        if prog["posture"] + 0.2 < self._last_progress["posture"]:
            self._message = ("Te moviste demasiado: se repite la medición de "
                             "postura. Quédate quieto.")
        if prog["ear"] + 0.2 < self._last_progress["ear"]:
            self._message = ("No se pudo medir el ojo abierto: mira la "
                             "pantalla con normalidad, sin entornar los ojos.")
        self._last_progress = {"posture": prog["posture"], "ear": prog["ear"]}

        if prog["posture_done"] and prog["ear_done"]:
            self._finish(now)
        elif now - self._phase_since > self.timeout_sec:
            self._phase = FAILED
            self._message = ("No se logró una medición estable en "
                             f"{self.timeout_sec:.0f} s. Revisa la luz y el "
                             "encuadre, y vuelve a intentarlo.")
            logger.warning("Calibración guiada fallida por tiempo.")

    def _finish(self, now: float) -> None:
        st = self._fsm.get_timers_status()
        self._result = {
            "cervical_baseline": st.get("cervical_baseline"),
            "ear_baseline": st.get("ear_baseline"),
            "ear_threshold": st.get("ear_threshold"),
            "distance_m": (round(mean(self._distances), 3)
                           if self._distances else None),
        }
        self._phase = DONE
        self._message = "Calibración completada."
        logger.info("Calibración guiada completada: %s", self._result)

    def _restart(self, now: float, reason: str) -> None:
        self._fsm.recalibrate_all()
        self._enter(COUNTDOWN, now)
        self._message = f"{reason} Reiniciando…"
        self._last_restart = reason
        logger.info("Calibración reiniciada: %s", reason)

    def _enter(self, phase: str, now: float) -> None:
        self._phase = phase
        self._phase_since = now
        self._bad_since = None
        self._distances = []
        self._last_progress = {"posture": 0.0, "ear": 0.0}

    def _framing_message(self, checks: FramingCheck) -> str:
        if not checks.face:
            return "se perdió el rostro; mira hacia la pantalla."
        if not checks.body:
            return "no se veían bien los hombros; ajusta el encuadre."
        d = checks.distance_m
        if d is not None and d < self._min_d:
            return f"demasiado cerca ({d:.2f} m); aléjate un poco."
        if d is not None and d > self._max_d:
            return f"demasiado lejos ({d:.2f} m); acércate un poco."
        return "se perdió el encuadre."

    def _build_status(self, checks: FramingCheck, now: float) -> CalibrationStatus:
        progress = 0.0
        remaining = 0.0
        if self._phase == COUNTDOWN:
            remaining = max(0.0, self.countdown_sec - (now - self._phase_since))
        elif self._phase == COLLECTING:
            prog = self._fsm.calibration_progress(now)
            progress = min(prog["posture"], prog["ear"])
        elif self._phase == DONE:
            progress = 1.0
        return CalibrationStatus(phase=self._phase, progress=progress,
                                 countdown_remaining=remaining,
                                 message=self._message, checks=checks,
                                 result=self._result)
