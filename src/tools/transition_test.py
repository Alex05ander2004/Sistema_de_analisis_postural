"""
transition_test.py
-------------------
Graba θc en vivo mientras el usuario transiciona de postura normal a la
posición más adelantada posible, imprimiendo cada muestra en consola en
tiempo real para ver la trayectoria completa (no solo promedios de fase).

Sirve para comprobar la **sensibilidad diferencial** de θc: que la métrica
responda a un cambio real de postura por encima del ruido de estimación.
Es la prueba que en la sesión del 2026-07-12 dio +13° entre baseline y pico
(`docs/validation_report.md` §4.4).

Para la calibración completa de umbrales del Capítulo IV usa
`calibration_mode.py`, que registra además ΔE, EAR, MAR, las componentes
sagital/lateral y la distancia estimada.

Uso:
  python src/tools/transition_test.py [--source -1] [--normal-sec 5]
                                      [--transition-sec 10] [--warmup-sec 10]
  (--source -1 = autodetectar la cámara; es el valor por defecto)

Autor: Fase 4 — Tesis Huisa Perez, UNSA 2026
"""

import argparse
import csv
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.capture.video_thread import VideoThread
from src.capture.camera_source import resolve_camera_source
from src.vision.pose_estimator import PoseEstimator
from src.vision.geometry import calculate_cervical_angle
from src.tools._config import load_thresholds, frame_size_from


def _record_phase(vt, pose_est, frame_size, label: str,
                  duration_sec: float, t0: float) -> list:
    """Captura θc durante `duration_sec` y devuelve las muestras de la fase."""
    samples = []
    t_end = time.perf_counter() + duration_sec
    while time.perf_counter() < t_end:
        frame = vt.get_frame(timeout=0.2)
        if frame is None:
            continue
        pr = pose_est.process(frame)
        if pr.detected and len(pr.landmarks) >= 13:
            angle = calculate_cervical_angle(pr.landmarks, frame_size)
            elapsed = time.perf_counter() - t0
            samples.append((elapsed, angle, label))
            print(f"  t={elapsed:4.1f}s  θc={angle:6.2f}°", flush=True)
    return samples


def run(source: int = -1, warmup_sec: float = 10.0,
        normal_sec: float = 5.0, transition_sec: float = 10.0) -> Path:
    thresholds = load_thresholds()
    cam_cfg = thresholds.get("camera", {})
    mp_cfg = thresholds.get("mediapipe", {})
    frame_size = frame_size_from(cam_cfg)

    resolved = resolve_camera_source(
        source if source is not None else cam_cfg.get("source_index", -1),
        verbose=False)
    autodetected = source is None or source < 0

    print(f"\nCámara #{resolved}"
          f"{' (autodetectada)' if autodetected else ' (forzada por --source)'}")

    vt = VideoThread(source=resolved,
                     width=frame_size[0], height=frame_size[1],
                     target_fps=cam_cfg.get("target_fps", 30))
    pose_est = PoseEstimator(
        model_complexity=mp_cfg.get("pose_model_complexity", 1),
        draw_landmarks=False,
    )
    vt.start()

    try:
        print(f"Calentando cámara ({warmup_sec:.0f}s, ignora esta parte)...",
              flush=True)
        t_end = time.perf_counter() + warmup_sec
        while time.perf_counter() < t_end:
            vt.get_frame(timeout=0.2)

        samples = []
        t0 = time.perf_counter()

        print("\n" + "=" * 55)
        print(f"  AHORA: quédate en postura NORMAL ({normal_sec:.0f}s)")
        print("=" * 55, flush=True)
        samples += _record_phase(vt, pose_est, frame_size, "normal",
                                 normal_sec, t0)

        print("\n" + "=" * 55)
        print("  AHORA: muévete LENTAMENTE hacia la posición MÁS")
        print(f"  ADELANTADA que puedas y quédate ahí ({transition_sec:.0f}s)")
        print("=" * 55, flush=True)
        samples += _record_phase(vt, pose_est, frame_size, "transicion",
                                 transition_sec, t0)
    finally:
        vt.stop()
        pose_est.close()

    print("\n" + "=" * 55)
    print("  done")
    print("=" * 55, flush=True)

    if not samples:
        print("\n[AVISO] No se registró ninguna muestra: no se detectó la pose "
              "en ningún frame. Revisa el encuadre y la cámara seleccionada.")
        return None

    # Nombre con timestamp: un nombre fijo sobrescribía la toma anterior sin
    # avisar, y estas tomas son irrepetibles (dependen del sujeto y la luz).
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = PROJECT_ROOT / "data" / f"transition_test_{ts}.csv"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["elapsed_sec", "theta_c", "label"])
        w.writerows(samples)
    print(f"CSV: {out_path}")

    normal = [a for _, a, l in samples if l == "normal"]
    trans  = [a for _, a, l in samples if l == "transicion"]
    if normal and trans:
        print(f"\nBaseline (normal): {sum(normal)/len(normal):6.2f}°  "
              f"(n={len(normal)})")
        print(f"Pico (adelantada): {max(trans):6.2f}°")
        print(f"Diferencia:        {max(trans) - sum(normal)/len(normal):+6.2f}°")

    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transición continua de postura — traza θc en vivo")
    parser.add_argument("--source", type=int, default=-1,
                        help="Índice de cámara; -1 = autodetectar (default)")
    parser.add_argument("--warmup-sec", type=float, default=10.0)
    parser.add_argument("--normal-sec", type=float, default=5.0)
    parser.add_argument("--transition-sec", type=float, default=10.0)
    args = parser.parse_args()

    run(source=args.source,
        warmup_sec=args.warmup_sec,
        normal_sec=args.normal_sec,
        transition_sec=args.transition_sec)
