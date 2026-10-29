"""
paths.py
--------
Dónde vive cada cosa, ejecutando desde el código fuente o empaquetado (.exe).

  Recursos (solo lectura): config/thresholds.json, assets/. Van dentro del
      paquete; en el .exe, junto a los módulos (sys._MEIPASS).
  Datos (escritura): history.db, estudio_local.json, logs/.
      - Desde el código fuente: <proyecto>/data/ (como siempre).
      - Empaquetado: %LOCALAPPDATA%\\MonitorPostural\\. La carpeta del .exe
        puede ser de solo lectura (Archivos de programa) o borrarse al
        actualizar la app, y los datos del estudio no pueden perderse con
        ella.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path

APP_DIR_NAME = "MonitorPostural"
FROZEN = bool(getattr(sys, "frozen", False))
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resource_root() -> Path:
    """Raíz de config/ y assets/."""
    if FROZEN and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return PROJECT_ROOT


def data_dir() -> Path:
    """Carpeta de datos del usuario (se crea si no existe)."""
    if FROZEN:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        d = base / APP_DIR_NAME
    else:
        d = PROJECT_ROOT / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_file(configured: str) -> Path:
    """
    Ruta de un archivo de datos configurado como "data/<nombre>".

    Desde el código fuente se respeta la ruta tal cual (relativa al
    proyecto); empaquetado, el archivo va a data_dir() con el mismo nombre.
    """
    if FROZEN:
        return data_dir() / Path(configured).name
    p = Path(configured)
    return p if p.is_absolute() else PROJECT_ROOT / p


def setup_file_logging(level=logging.INFO) -> Path:
    """
    Log a archivo con rotación (2 MB x 5) en data_dir()/logs/app.log.

    Imprescindible en el .exe: no hay consola, y sin archivo no habría
    forma de saber qué pasó en el equipo de un participante (cámara perdida,
    errores). El log viaja en el .zip de «Exportar mis datos».
    """
    log_dir = data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "app.log"
    root = logging.getLogger()
    if not any(isinstance(h, logging.handlers.RotatingFileHandler)
               and Path(h.baseFilename) == path for h in root.handlers):
        h = logging.handlers.RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        h.setLevel(level)
        root.addHandler(h)
        if root.level > level or root.level == logging.NOTSET:
            root.setLevel(level)
    return path
