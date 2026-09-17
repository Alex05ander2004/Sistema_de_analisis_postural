"""
ab_test_cervical.py
--------------------
Prueba A/B rápida y sincronizada para verificar si theta_c distingue
postura normal vs. cabeza adelantada. Imprime marcadores de fase en vivo
en consola (sin depender de temporización estimada por chat) y etiqueta
cada frame capturado con la fase activa en ese instante.

Uso: python src/tools/ab_test_cervical.py
"""

import sys
import time
from pathlib import Path
from statistics import mean, stdev

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.capture.video_thread import VideoThread
from src.vision.pose_estimator import PoseEstimator
from src.vision.geometry import calculate_cervical_angle

PHASE_SEC = 8
FRAME_SIZE = (640, 480)   # debe coincidir con la resolución de captura

vt = VideoThread(source=1, width=640, height=480, target_fps=30)
pose_est = PoseEstimator(draw_landmarks=False)
vt.start()

print("Calentando cámara (10s, ignora esta parte)...")
t_end = time.perf_counter() + 10
while time.perf_counter() < t_end:
    vt.get_frame(timeout=0.2)

phases = [
    ("NORMAL", PHASE_SEC),
    ("ADELANTADA", PHASE_SEC),
    ("NORMAL", PHASE_SEC),
    ("ADELANTADA", PHASE_SEC),
]

samples = {"NORMAL": [], "ADELANTADA": []}

for phase_name, dur in phases:
    print(f"\n{'='*50}\n  AHORA: {phase_name}  ({dur}s)\n{'='*50}")
    t_end = time.perf_counter() + dur
    n = 0
    while time.perf_counter() < t_end:
        frame = vt.get_frame(timeout=0.2)
        if frame is None:
            continue
        pr = pose_est.process(frame)
        if pr.detected and len(pr.landmarks) >= 13:
            angle = calculate_cervical_angle(pr.landmarks, FRAME_SIZE)
            samples[phase_name].append(angle)
            n += 1
    print(f"  ({n} frames capturados en esta fase)")

vt.stop()
pose_est.close()

print(f"\n{'='*50}\n  RESULTADO\n{'='*50}")
for name, vals in samples.items():
    if vals:
        print(f"  {name}: n={len(vals)}  media={mean(vals):.2f}°  "
              f"min={min(vals):.2f}°  max={max(vals):.2f}°  std={stdev(vals):.2f}°")

if samples["NORMAL"] and samples["ADELANTADA"]:
    diff = mean(samples["ADELANTADA"]) - mean(samples["NORMAL"])
    print(f"\n  Diferencia (ADELANTADA - NORMAL): {diff:+.2f}°")
