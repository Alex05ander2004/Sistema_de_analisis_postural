"""
_config.py
----------
Carga de `config/thresholds.json` compartida por las herramientas de
`src/tools/`.

Cada herramienta abría el JSON por su cuenta y algunas asumían la resolución
640x480 en constantes propias. Eso es peligroso en este proyecto concreto:
`geometry.py` necesita el tamaño real del frame para trabajar en píxeles
isótropos, así que una herramienta que use un `frame_size` distinto al de la
app **mide en otra escala** y sus números no son comparables con los umbrales
(es el mismo tipo de error que documenta `docs/validation_report.md` §3.3.2).

Autor: Tesis Huisa Perez, UNSA 2026
"""

import json
from pathlib import Path
from typing import Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
THRESHOLDS_PATH = PROJECT_ROOT / "config" / "thresholds.json"


def load_thresholds() -> dict:
    """Carga config/thresholds.json. Lanza si no existe: sin umbrales, una
    herramienta de medición no debe seguir con valores inventados."""
    with open(THRESHOLDS_PATH, encoding="utf-8") as f:
        return json.load(f)


def frame_size_from(cam_cfg: dict) -> Tuple[int, int]:
    """(ancho, alto) de captura declarado en la configuración."""
    return (cam_cfg.get("resolution_width", 640),
            cam_cfg.get("resolution_height", 480))
