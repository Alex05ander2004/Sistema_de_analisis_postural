"""
field_study.py
--------------
Lógica del estudio de campo, sin dependencias de la interfaz (testeable).

Diseño A-B intra-sujeto
-----------------------
Cada participante usa la app en su propio equipo durante ~15 días de uso:

  A. Línea base      primeros `baseline_days` días VÁLIDOS: la app mide y
                     registra las alertas que habría emitido, pero NO las
                     muestra. Describe cómo se sienta la persona sin el
                     sistema.
  B. Intervención    el resto: la app mide, registra y avisa.

Comparar A con B en la misma persona es lo que permite atribuir un cambio al
sistema y no a la costumbre o al azar.

Línea base múltiple con control (diseño elegido, 2026-11-12)
-----------------------------------------------------------
Los participantes del grupo de intervención no empiezan a recibir avisos el
mismo día: cada uno tiene su propia duración de línea base
(`dias_linea_base` del perfil, p. ej. 5, 7 o 9 días, sorteados). Si cada uno
mejora justo cuando empiezan SUS avisos, y no antes, la mejora se atribuye al
sistema y no a una causa común (la semana, la novedad, saberse observado).
El grupo de control usa la app todo el estudio sin recibir nunca avisos (fase
"control"): muestra qué cambia solo por el paso del tiempo. Ver Kratochwill
et al. (2010).

Por qué "días válidos" y no días de calendario
----------------------------------------------
Si la línea base fueran los 5 primeros días de calendario, un fin de semana o
un día con 10 minutos de uso la dejarían casi vacía. Se cuentan los días con al
menos `baseline_min_minutes` de seguimiento real. La fase se decide al iniciar
cada sesión con los días ANTERIORES a hoy, así que todo un día queda en la
misma fase aunque se acumulen minutos durante la jornada.

Perfil local
------------
data/estudio_local.json guarda lo que es propio de ESE equipo y ese
participante, para que config/thresholds.json sea idéntico en los 25 equipos:

    {"participante": "P07", "modo": "campo", "inscrito": "2026-11-03",
     "camera_hfov_deg": 63.2, "grupo": "intervencion",
     "dias_linea_base": 7}

Autor: Tesis Huisa Perez, UNSA 2026
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

MODE_LAB = "laboratorio"
MODE_FIELD = "campo"
VALID_MODES = (MODE_LAB, MODE_FIELD)

PHASE_VALIDATION = "validacion"
PHASE_BASELINE = "linea_base"
PHASE_INTERVENTION = "intervencion"
PHASE_CONTROL = "control"

GROUP_INTERVENTION = "intervencion"
GROUP_CONTROL = "control"
VALID_GROUPS = (GROUP_INTERVENTION, GROUP_CONTROL)

# Rango admitido para la línea base de un participante (días de uso).
MIN_BASELINE_DAYS, MAX_BASELINE_DAYS = 1, 20


def normalize_code(code: str) -> str:
    """Mayúsculas y sin espacios: "p 07" y "P07" son el mismo participante."""
    return "".join(str(code or "").split()).upper()


# ---------------------------------------------------------------------------
# Perfil local del equipo
# ---------------------------------------------------------------------------

@dataclass
class LocalProfile:
    participante: str = ""
    modo: str = MODE_LAB
    inscrito: str = ""
    camera_hfov_deg: Optional[float] = None
    grupo: str = GROUP_INTERVENTION
    dias_linea_base: Optional[int] = None   # None = el de la configuración

    def baseline_days(self, default: int) -> int:
        """Días de línea base de ESTE participante."""
        return int(self.dias_linea_base) if self.dias_linea_base else int(default)

    @classmethod
    def load(cls, path: Path) -> "LocalProfile":
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            logger.exception("Perfil local ilegible (%s); se ignora.", path)
            return cls()
        prof = cls(
            participante=normalize_code(raw.get("participante", "")),
            modo=raw.get("modo", MODE_LAB),
            inscrito=str(raw.get("inscrito", "") or ""),
            camera_hfov_deg=raw.get("camera_hfov_deg"),
            grupo=raw.get("grupo", GROUP_INTERVENTION),
            dias_linea_base=raw.get("dias_linea_base"),
        )
        if prof.grupo not in VALID_GROUPS:
            logger.warning("Grupo '%s' no válido en el perfil; se usa '%s'.",
                           prof.grupo, GROUP_INTERVENTION)
            prof.grupo = GROUP_INTERVENTION
        try:
            dias = int(prof.dias_linea_base) if prof.dias_linea_base else None
        except (TypeError, ValueError):
            dias = None
        prof.dias_linea_base = (dias if dias and MIN_BASELINE_DAYS <= dias
                                <= MAX_BASELINE_DAYS else None)
        if prof.modo not in VALID_MODES:
            logger.warning("Modo '%s' no válido en el perfil; se usa '%s'.",
                           prof.modo, MODE_LAB)
            prof.modo = MODE_LAB
        if prof.camera_hfov_deg is not None:
            try:
                fov = float(prof.camera_hfov_deg)
            except (TypeError, ValueError):
                fov = None
            prof.camera_hfov_deg = fov if fov and 30.0 < fov < 120.0 else None
        return prof

    def save(self, path: Path) -> None:
        """Escritura atómica: un corte de luz no deja un JSON a medias."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ---------------------------------------------------------------------------
# Fase del estudio
# ---------------------------------------------------------------------------

def decide_phase(minutes_by_date: Dict[str, float], today: str,
                 baseline_days: int, min_minutes: float
                 ) -> Tuple[str, int, int]:
    """
    Fase de campo para las sesiones que empiezan hoy.

    Returns
    -------
    (fase, dia_de_uso, dias_base_completados)
        dia_de_uso: 1 el primer día con uso, 2 el siguiente, etc. (hoy
        incluido). dias_base_completados: días previos que cuentan para la
        línea base.
    """
    previous = {d: m for d, m in minutes_by_date.items() if d < today}
    qualified = sum(1 for m in previous.values() if m >= min_minutes)
    day_number = len(previous) + 1
    phase = (PHASE_BASELINE if qualified < max(0, int(baseline_days))
             else PHASE_INTERVENTION)
    return phase, day_number, qualified


def field_phase(profile: "LocalProfile", minutes_by_date: Dict[str, float],
                today: str, default_baseline_days: int, min_minutes: float
                ) -> Tuple[str, int, int]:
    """
    Fase de campo de ESTE participante: su propia línea base y, si es del
    grupo de control, fase "control" todo el estudio (nunca avisa).
    """
    phase, day, done = decide_phase(
        minutes_by_date, today, profile.baseline_days(default_baseline_days),
        min_minutes)
    if profile.grupo == GROUP_CONTROL:
        phase = PHASE_CONTROL
    return phase, day, done


def pending_report_date(use_dates, reported_dates, today: str) -> Optional[str]:
    """
    Último día de uso ANTERIOR a hoy sin autorreporte, o None.

    Solo se pregunta por el último: preguntar al participante por cómo se
    sentía hace tres días no da un dato fiable.
    """
    previous = sorted(d for d in use_dates if d < today)
    if not previous:
        return None
    last = previous[-1]
    return None if last in reported_dates else last


def today_iso() -> str:
    return date.today().isoformat()
