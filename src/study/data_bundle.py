"""
data_bundle.py
--------------
"Exportar mis datos": empaqueta en un .zip lo que el participante debe
enviar al investigador.

Contenido del .zip
------------------
  history.db           copia CONSISTENTE de la bitácora (API de backup de
                       SQLite: incluye lo que aún esté en el archivo -wal)
  estudio_local.json   perfil del equipo (código, modo, FOV de su cámara)
  thresholds.json      configuración con la que midió la app
  logs/app.log*        registro técnico de la app (diagnóstico a distancia)
  LEEME.txt            qué es el archivo y qué contiene (y qué NO contiene)

No hay imágenes: la app nunca guarda fotogramas, solo números. El código de
participante es un seudónimo; la tabla que lo vincula con el nombre la guarda
solo el investigador, fuera de este equipo.

El investigador analiza los .zip recibidos con:
    python src/tools/export_study.py --db <carpeta con los .zip o .db>

Autor: Tesis Huisa Perez, UNSA 2026
"""

import logging
import os
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def desktop_dir() -> Path:
    """
    Escritorio real del usuario.

    En Windows con OneDrive el Escritorio suele estar en
    %USERPROFILE%\\OneDrive\\Escritorio, no en %USERPROFILE%\\Desktop: se
    pregunta al sistema (SHGetFolderPathW) en vez de suponer la ruta.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            CSIDL_DESKTOPDIRECTORY = 0x10
            if ctypes.windll.shell32.SHGetFolderPathW(
                    None, CSIDL_DESKTOPDIRECTORY, None, 0, buf) == 0:
                p = Path(buf.value)
                if p.is_dir():
                    return p
        except Exception:
            logger.debug("SHGetFolderPathW no disponible.", exc_info=True)
    for cand in (Path.home() / "Desktop", Path.home() / "Escritorio",
                 Path.home()):
        if cand.is_dir():
            return cand
    return Path.cwd()


def _readme(code: str, version: str, n_sessions: int, n_days: int) -> str:
    return (
        "DATOS DEL ESTUDIO - Monitor Postural y Fatiga (Tesis UNSA 2026)\n"
        "================================================================\n\n"
        f"Participante: {code or '(sin código)'}\n"
        f"Exportado:    {datetime.now():%Y-%m-%d %H:%M}\n"
        f"Versión app:  {version}\n"
        f"Sesiones:     {n_sessions}   Días con uso: {n_days}\n\n"
        "Qué contiene\n"
        "  - Mediciones numéricas de postura y fatiga (ángulos, apertura de\n"
        "    ojos y boca, distancia a la pantalla), una por segundo.\n"
        "  - Las alertas detectadas y tus respuestas a la encuesta diaria.\n\n"
        "Qué NO contiene\n"
        "  - Ninguna imagen ni video: la app no guarda fotogramas.\n"
        "  - Tu nombre: solo el código de participante.\n\n"
        "Envía este archivo .zip al investigador tal como está.\n"
    )


def create_bundle(history_logger, profile_path: Path, config_path: Path,
                  participant: str, app_version: str,
                  dest_dir: Optional[Path] = None,
                  log_dir: Optional[Path] = None) -> Path:
    """Crea el .zip y devuelve su ruta."""
    dest_dir = Path(dest_dir) if dest_dir else desktop_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    name = f"Estudio_{participant or 'SIN_CODIGO'}_{stamp}.zip"
    zip_path = dest_dir / name

    with tempfile.TemporaryDirectory() as tmp:
        db_copy = history_logger.backup_to(Path(tmp) / "history.db")

        import sqlite3
        con = sqlite3.connect(str(db_copy))
        try:
            n_ses = con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            n_days = con.execute(
                "SELECT COUNT(DISTINCT date(started_at, 'unixepoch', "
                "'localtime')) FROM sessions").fetchone()[0]
        finally:
            con.close()

        part_path = zip_path.with_suffix(".zip.part")
        with zipfile.ZipFile(part_path, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            zf.write(db_copy, "history.db")
            for src, arc in ((profile_path, "estudio_local.json"),
                             (config_path, "thresholds.json")):
                if Path(src).exists():
                    zf.write(src, arc)
            # Los logs (sin imágenes ni nombres) permiten diagnosticar a
            # distancia: cámara perdida, errores, días sin datos.
            if log_dir is not None and Path(log_dir).is_dir():
                for lf in sorted(Path(log_dir).glob("app.log*")):
                    zf.write(lf, f"logs/{lf.name}")
            zf.writestr("LEEME.txt",
                        _readme(participant, app_version, n_ses, n_days))
        # Renombrar al final: si se corta a medias no queda un .zip que
        # parezca válido.
        os.replace(part_path, zip_path)

    logger.info("Datos exportados en %s", zip_path)
    return zip_path
