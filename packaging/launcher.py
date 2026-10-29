"""Punto de entrada del ejecutable (PyInstaller necesita un script)."""

import multiprocessing

from src.ui.app_ui import run

if __name__ == "__main__":
    multiprocessing.freeze_support()
    run()
