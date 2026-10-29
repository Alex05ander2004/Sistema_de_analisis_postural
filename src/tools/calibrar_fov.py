"""
calibrar_fov.py
---------------
Calcula el campo de visión horizontal (FOV) de la webcam a partir de UNA
medición con cinta métrica, para poner en config/thresholds.json →
camera.horizontal_fov_deg.

Por qué hace falta (una vez por cámara)
---------------------------------------
La distancia usuario-cámara se estima con el modelo estenopeico:

    d = ancho_biacromial × f_px / ancho_hombros_px
    f_px = (W/2) / tan(FOV/2)

El FOV por defecto (60°) es un valor típico, pero las webcams van de 55° a
78°. Con un FOV equivocado, la estimación de distancia sale proporcionalmente
mal y el control de encuadre de la calibración guiada (0.50–0.70 m) deja de
corresponder a la distancia real. La calibración POR PERSONA de la app
(postura neutra y EAR) NO corrige esto: es un ajuste de la cámara, no del
usuario. Se hace una vez por cámara y se reutiliza con todos los
participantes.

Cómo se usa
-----------
  1. Abre la app (python -m src.ui.app_ui) y siéntate a una distancia medida
     con cinta métrica, del lente a tus hombros (p. ej. 0.60 m).
  2. Lee la «Distancia» que muestra la tarjeta de calibración (p. ej. 0.79 m).
  3. Ejecuta:
         python src/tools/calibrar_fov.py --estimada 0.79 --real 0.60
  4. Copia el valor que imprime a camera.horizontal_fov_deg y reinicia la app.
  5. Comprueba: a la misma distancia real, la app debe leer ~0.60 m.

Limitación (a declarar en la tesis)
-----------------------------------
El FOV calculado absorbe también la diferencia entre TU ancho de hombros y la
media poblacional de 0.38 m. Por eso conviene calibrar con alguien de
constitución media y repetir con una segunda persona: si los dos FOV difieren
mucho, la causa es el ancho de hombros, no la cámara. La estimación de
distancia sigue siendo de orden de magnitud (±8 % por la variación individual
del ancho biacromial) y sirve para el encuadre, no como medición de precisión.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "thresholds.json"

MIN_FOV, MAX_FOV = 30.0, 120.0


def corrected_hfov(current_hfov_deg: float, estimated_m: float,
                   real_m: float, width_px: int = 640) -> float:
    """
    FOV horizontal que hace que la estimación coincida con la distancia real.

    d_est ∝ f ⇒ para que d_est pase a valer d_real con el mismo ancho de
    hombros en píxeles, la focal debe escalarse por d_real / d_est.
    """
    if estimated_m <= 0 or real_m <= 0:
        raise ValueError("Las distancias deben ser positivas.")
    if not MIN_FOV < current_hfov_deg < MAX_FOV:
        raise ValueError(f"FOV actual fuera de rango ({current_hfov_deg}°).")

    focal = (width_px / 2.0) / math.tan(math.radians(current_hfov_deg) / 2.0)
    new_focal = focal * real_m / estimated_m
    return math.degrees(2.0 * math.atan((width_px / 2.0) / new_focal))


def _read_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Calcula camera.horizontal_fov_deg a partir de una medición.")
    ap.add_argument("--estimada", type=float, required=True,
                    help="Distancia que muestra la app (m)")
    ap.add_argument("--real", type=float, required=True,
                    help="Distancia medida con cinta métrica (m)")
    ap.add_argument("--fov-actual", type=float, default=None,
                    help="FOV con el que se obtuvo la lectura (default: el del config)")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = ap.parse_args(argv)

    cfg = _read_config(args.config) if args.config.exists() else {}
    cam = cfg.get("camera", {})
    current = args.fov_actual or cam.get("horizontal_fov_deg", 60.0)
    width = cam.get("resolution_width", 640)

    try:
        new = corrected_hfov(current, args.estimada, args.real, width)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 1

    error_pct = 100.0 * (args.estimada - args.real) / args.real
    print(f"\nLectura de la app : {args.estimada:.2f} m")
    print(f"Distancia real    : {args.real:.2f} m  (error {error_pct:+.0f} %)")
    print(f"FOV con el que se midió: {current:.1f}°")
    print(f"\n>>> camera.horizontal_fov_deg = {new:.1f}\n")

    if not MIN_FOV < new < MAX_FOV or not 50.0 <= new <= 85.0:
        print("[AVISO] Ese FOV queda fuera del rango habitual de webcams "
              "(55–78°). Repite la medición: revisa la cinta, que midas "
              "hasta los hombros y que estés de frente.")
    if abs(error_pct) < 3:
        print("La lectura ya coincide con la realidad (error < 3 %): "
              "no hace falta cambiar nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
