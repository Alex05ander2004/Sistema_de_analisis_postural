"""
ab_test_cervical.py
--------------------
Prueba A/B sincronizada para verificar si θc distingue postura normal de
cabeza adelantada. Imprime marcadores de fase en vivo en consola y etiqueta
cada frame capturado con la fase activa en ese instante, de modo que la
asignación frame→fase no dependa de una temporización estimada a posteriori.

Alterna NORMAL / ADELANTADA dos veces para que el resultado no dependa de un
único par de fases (deriva de luz, cansancio del sujeto, reencuadre).

Para la calibración completa de umbrales del Capítulo IV usa
`calibration_mode.py`; esta herramienta responde solo a una pregunta:
¿la señal separa las dos posturas por encima del ruido?

Uso:
  python src/tools/ab_test_cervical.py [--source -1] [--phase-sec 8]
  (--source -1 = autodetectar la cámara; es el valor por defecto)

Autor: Fase 4 — Tesis Huisa Perez, UNSA 2026
"""

import argparse
import csv
import sys
import time
from pathlib import Path
from statistics import mean, stdev

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.capture.video_thread import VideoThread
from src.capture.camera_source import resolve_camera_source
from src.vision.pose_estimator import PoseEstimator
from src.vision.geometry import calculate_cervical_angle
from src.tools._config import load_thresholds, frame_size_from


def run(source: int = -1, phase_sec: float = 8.0,
        warmup_sec: float = 10.0) -> Path:
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

    phases = [("NORMAL", phase_sec), ("ADELANTADA", phase_sec),
              ("NORMAL", phase_sec), ("ADELANTADA", phase_sec)]
    samples = {"NORMAL": [], "ADELANTADA": []}
    rows = []

    try:
        print(f"Calentando cámara ({warmup_sec:.0f}s, ignora esta parte)...",
              flush=True)
        t_end = time.perf_counter() + warmup_sec
        while time.perf_counter() < t_end:
            vt.get_frame(timeout=0.2)

        t0 = time.perf_counter()
        for phase_idx, (phase_name, dur) in enumerate(phases):
            print(f"\n{'='*50}\n  AHORA: {phase_name}  ({dur:.0f}s)\n{'='*50}",
                  flush=True)
            t_end = time.perf_counter() + dur
            n = 0
            while time.perf_counter() < t_end:
                frame = vt.get_frame(timeout=0.2)
                if frame is None:
                    continue
                pr = pose_est.process(frame)
                if pr.detected and len(pr.landmarks) >= 13:
                    angle = calculate_cervical_angle(pr.landmarks, frame_size)
                    samples[phase_name].append(angle)
                    rows.append((round(time.perf_counter() - t0, 3),
                                 round(angle, 3), phase_name, phase_idx))
                    n += 1
            print(f"  ({n} frames capturados en esta fase)")
    finally:
        vt.stop()
        pose_est.close()

    print(f"\n{'='*50}\n  RESULTADO\n{'='*50}")
    for name, vals in samples.items():
        if len(vals) >= 2:
            print(f"  {name}: n={len(vals)}  media={mean(vals):.2f}°  "
                  f"min={min(vals):.2f}°  max={max(vals):.2f}°  "
                  f"std={stdev(vals):.2f}°")
        elif vals:
            print(f"  {name}: n={len(vals)}  valor={vals[0]:.2f}° "
                  f"(muestras insuficientes para desviación)")
        else:
            print(f"  {name}: sin muestras — no se detectó la pose")

    if samples["NORMAL"] and samples["ADELANTADA"]:
        diff = mean(samples["ADELANTADA"]) - mean(samples["NORMAL"])
        print(f"\n  Diferencia (ADELANTADA - NORMAL): {diff:+.2f}°")

    if not rows:
        print("\n[AVISO] No se registró ninguna muestra: no se detectó la pose "
              "en ningún frame. Revisa el encuadre y la cámara seleccionada.")
        return None

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = PROJECT_ROOT / "data" / f"ab_test_cervical_{ts}.csv"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["elapsed_sec", "theta_c", "fase", "fase_idx"])
        w.writerows(rows)
    print(f"\nCSV: {out_path}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Prueba A/B de θc: postura normal vs. cabeza adelantada")
    parser.add_argument("--source", type=int, default=-1,
                        help="Índice de cámara; -1 = autodetectar (default)")
    parser.add_argument("--phase-sec", type=float, default=8.0,
                        help="Duración de cada fase en segundos (default: 8)")
    parser.add_argument("--warmup-sec", type=float, default=10.0)
    args = parser.parse_args()

    run(source=args.source, phase_sec=args.phase_sec,
        warmup_sec=args.warmup_sec)
