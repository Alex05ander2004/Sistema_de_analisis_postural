"""
version.py
----------
Versión de la aplicación y huella de la configuración.

Ambas se guardan en cada sesión (sessions.app_version / config_hash). En un
estudio de campo con 25 equipos es la única forma de demostrar después que
todos los participantes usaron el mismo programa con los mismos umbrales: si
alguna sesión trae otra huella, ese equipo tenía un thresholds.json distinto.

Subir APP_VERSION en cada cambio que se entregue a los participantes, y NO
cambiar nada una vez congelada la versión del estudio.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import hashlib
from pathlib import Path

APP_VERSION = "1.1.0"


def config_hash(path) -> str:
    """Primeros 12 hex del SHA-256 del archivo ("" si no se puede leer)."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    # Normalizar fin de línea: el mismo JSON copiado con git en Windows
    # (CRLF) y en otro sistema (LF) no debe parecer otra configuración.
    data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()[:12]
