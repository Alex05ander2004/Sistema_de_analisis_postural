"""
transition_test.py
-------------------
Graba theta_c en vivo mientras el usuario transiciona de postura normal
a la posición más adelantada posible, imprimiendo cada muestra en consola
en tiempo real para ver la trayectoria completa (no solo promedios de fase).

Uso: python src/tools/transition_test.py
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.capture.video_thread import VideoThread
from src.vision.pose_estimator import PoseEstimator
from src.vision.geometry import calculate_cervical_angle

vt = VideoThread(source=1, width=640, height=480, target_fps=30)
FRAME_SIZE = (640, 480)   # debe coincidir con la resolución de captura
pose_est = PoseEstimator(draw_landmarks=False)
vt.start()

print("Calentando cámara (10s, ignora esta parte)...", flush=True)
t_end = time.perf_counter() + 10
while time.perf_counter() < t_end:
    vt.get_frame(timeout=0.2)

print("\n" + "="*55)
print("  AHORA: quédate en postura NORMAL (5s)")
print("="*55, flush=True)

samples = []  # (elapsed_sec, angle, label)
t0 = time.perf_counter()
t_end = t0 + 5
while time.perf_counter() < t_end:
    frame = vt.get_frame(timeout=0.2)
    if frame is None:
        continue
    pr = pose_est.process(frame)
    if pr.detected and len(pr.landmarks) >= 13:
        angle = calculate_cervical_angle(pr.landmarks, FRAME_SIZE)
        elapsed = time.perf_counter() - t0
        samples.append((elapsed, angle, "normal"))
        print(f"  t={elapsed:4.1f}s  θc={angle:6.2f}°", flush=True)

print("\n" + "="*55)
print("  AHORA: muévete LENTAMENTE hacia la posición MÁS")
print("  ADELANTADA que puedas y quédate ahí (10s)")
print("="*55, flush=True)

t1 = time.perf_counter()
t_end = t1 + 10
while time.perf_counter() < t_end:
    frame = vt.get_frame(timeout=0.2)
    if frame is None:
        continue
    pr = pose_est.process(frame)
    if pr.detected and len(pr.landmarks) >= 13:
        angle = calculate_cervical_angle(pr.landmarks, FRAME_SIZE)
        elapsed = time.perf_counter() - t0
        samples.append((elapsed, angle, "transicion"))
        print(f"  t={elapsed:4.1f}s  θc={angle:6.2f}°", flush=True)

vt.stop()
pose_est.close()

print("\n" + "="*55)
print("  done")
print("="*55, flush=True)

# Guardar CSV crudo para análisis posterior
import csv
out_path = PROJECT_ROOT / "data" / "transition_test.csv"
out_path.parent.mkdir(exist_ok=True)
with open(out_path, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["elapsed_sec", "theta_c", "label"])
    w.writerows(samples)
print(f"CSV: {out_path}")
