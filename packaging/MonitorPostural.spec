# -*- mode: python ; coding: utf-8 -*-
"""
Receta de PyInstaller para el ejecutable que se entrega a los participantes.

    .venv\\Scripts\\python -m PyInstaller packaging\\MonitorPostural.spec --noconfirm

Resultado: dist\\MonitorPostural\\MonitorPostural.exe (carpeta, no archivo
único: arranca en segundos en vez de descomprimir ~400 MB en cada inicio, y
el antivirus la marca mucho menos).

Qué se incluye además del código:
  - config/thresholds.json y assets/ (solo lectura; los datos del usuario van
    a %LOCALAPPDATA%\\MonitorPostural, ver src/paths.py)
  - los modelos de MediaPipe (.tflite / .binarypb), que PyInstaller no ve
    porque se cargan por ruta en tiempo de ejecución
  - el cliente de ventana de Flet (flet/bin/flet/flet.exe); sin él, Flet
    intentaría DESCARGARLO de internet en el primer arranque
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs,
                                     collect_submodules)

ROOT = Path(SPECPATH).parent
SITE = Path(os.__file__).parent / "site-packages"
if not (SITE / "flet").exists():          # venv: Lib/site-packages
    import flet
    SITE = Path(flet.__file__).parent.parent

datas = [
    (str(ROOT / "config" / "thresholds.json"), "config"),
    (str(ROOT / "assets"), "assets"),
    (str(SITE / "flet" / "bin"), "flet/bin"),
]
datas += collect_data_files(
    "mediapipe", includes=["**/*.tflite", "**/*.binarypb", "**/*.txt"])
binaries = collect_dynamic_libs("mediapipe")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    # numpy 2.x carga numpy._core.* de forma dinámica y PyInstaller no lo
    # detecta ("No module named numpy._core._exceptions" al arrancar).
    hiddenimports=["pystray._win32", "winotify", "psutil"]
    + collect_submodules("numpy._core"),
    # scipy (y jax, que arrastra) solo los usa la prueba de Wilcoxon del
    # exportador del investigador; la app no la ejecuta (~300 MB menos).
    excludes=["tkinter", "pytest", "IPython", "notebook", "PyInstaller",
              "scipy", "jax", "jaxlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MonitorPostural",
    icon=str(ROOT / "assets" / "icon.ico"),
    console=False,
    upx=False,
    version=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="MonitorPostural",
    upx=False,
)
