"""
benchmark_mediapipe.py
-----------------------
Mide la latencia REAL del pipeline completo (captura + BlazePose + Face Mesh
+ geometry + FSM) usando la cámara física, para reemplazar los valores
"estimados" del Capítulo IV con mediciones reales.

A diferencia de tests/test_performance.py (que solo mide geometry.py + FSM
con landmarks sintéticos, sin MediaPipe), este script sí ejecuta la
inferencia real de BlazePose y Face Mesh sobre frames de cámara.

Mide dos configuraciones en la misma ejecución para que la comparación sea
justa (misma cámara, misma luz, misma sesión):

  - "secuencial": pose y face uno detrás de otro en el mismo hilo, que es lo
    que hacía la app antes. Latencia total = pose + face.
  - "paralelo": los dos modelos lanzados a la vez a un ThreadPoolExecutor(2)
    sobre el mismo buffer RGB, que es lo que hace la app ahora. Latencia
    total ~= max(pose, face).

Uso:
  python src/tools/benchmark_mediapipe.py [--frames 300] [--source -1]
  python src/tools/benchmark_mediapipe.py --mode parallel
  python src/tools/benchmark_mediapipe.py --synthetic     # sin cámara

Autor: Fase 4 — Tesis Huisa Perez, UNSA 2026
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean, median

import numpy as np

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.capture.video_thread import VideoThread
from src.vision.pose_estimator import PoseEstimator
from src.vision.face_estimator import FaceEstimator
from src.vision.geometry import (
    calculate_cervical_angle,
    calculate_shoulder_asymmetry,
    calculate_avg_ear,
    calculate_mouth_opening,
)
from src.fusion.fusion_fsm import FusionFSM, SensorMetrics

sys.path.insert(0, str(PROJECT_ROOT / "src" / "ui"))


def _detect_camera_source(max_index: int = 4, black_std_threshold: float = 3.0) -> int:
    import cv2
    for idx in range(max_index):
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = cap.read()
        cap.release()
        if ok and frame is not None and frame.std() > black_std_threshold:
            return idx
    return 0


class _SyntheticSource:
    """
    Fuente de frames sin cámara, para poder medir el coste de los modelos en un
    equipo sin webcam disponible.

    Aviso de interpretación: sobre ruido MediaPipe no detecta a nadie y se
    queda en la fase de detección, sin llegar a ejecutar la red de landmarks.
    Los tiempos absolutos NO son comparables con los de cámara real; sirve
    para comparar secuencial vs. paralelo y como prueba de humo, no para las
    cifras del Capítulo IV.
    """

    def __init__(self, width=640, height=480, seed=0):
        rng = np.random.default_rng(seed)
        self._frames = [rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
                        for _ in range(8)]
        self._i = 0

    def start(self):
        return self

    def stop(self):
        pass

    def get_frame(self, timeout=0.0):
        self._i += 1
        return self._frames[self._i % len(self._frames)]


def _pctl(data, p):
    ordered = sorted(data)
    return ordered[max(0, int(p / 100 * len(ordered)) - 1)]


def benchmark(n_frames: int = 300, source: int = -1,
              mode: str = "both", synthetic: bool = False) -> dict:
    import cv2

    with open(PROJECT_ROOT / "config" / "thresholds.json", encoding="utf-8") as f:
        thresholds = json.load(f)
    mp_cfg = thresholds.get("mediapipe", {})
    cam_cfg = thresholds.get("camera", {})
    width = cam_cfg.get("resolution_width", 640)
    height = cam_cfg.get("resolution_height", 480)
    frame_size = (width, height)

    if synthetic:
        vt = _SyntheticSource(width, height)
        source = -1
    else:
        if source < 0:
            source = _detect_camera_source()
            print(f"Camara autodetectada: indice {source}")
        vt = VideoThread(source=source, width=width, height=height, target_fps=30)

    pose_est = PoseEstimator(
        model_complexity=mp_cfg.get("pose_model_complexity", 1),
        draw_landmarks=False,
    )
    face_est = FaceEstimator(
        refine_landmarks=mp_cfg.get("face_refine_landmarks", False),
        draw_landmarks=False,
    )
    fsm = FusionFSM(thresholds=thresholds, on_alert=None, cooldown_sec=0.0)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mediapipe")

    vt.start()
    if not synthetic:
        time.sleep(1.0)  # dejar que la camara se estabilice

    def _metrics(pose_result, face_result):
        m = SensorMetrics(pose_detected=pose_result.detected,
                          face_detected=face_result.detected)
        if pose_result.detected and len(pose_result.landmarks) >= 13:
            m.cervical_angle = calculate_cervical_angle(
                pose_result.landmarks, frame_size)
            m.shoulder_asymmetry = calculate_shoulder_asymmetry(
                pose_result.landmarks, frame_size)
        if face_result.detected and len(face_result.landmarks) >= 300:
            m.ear_avg = calculate_avg_ear(
                face_result.landmarks,
                FaceEstimator.LEFT_EYE_EAR_INDICES,
                FaceEstimator.RIGHT_EYE_EAR_INDICES,
                frame_size)
            m.mouth_opening = calculate_mouth_opening(
                face_result.landmarks, frame_size=frame_size)
        fsm.update(m)
        return m

    def _run(parallel: bool) -> dict:
        pose_ms, face_ms, geom_ms, total_ms = [], [], [], []
        n_pose = n_face = 0
        collected = 0
        t_start = time.perf_counter()

        while collected < n_frames:
            frame = vt.get_frame(timeout=0.5)
            if frame is None:
                continue

            t0 = time.perf_counter()
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame_rgb.flags.writeable = False

            if parallel:
                # Con los dos modelos solapados, cronometrarlos por separado no
                # dice nada (se pisan en el reloj de pared): lo que importa aqui
                # es el bloque conjunto, que es lo que fija el FPS.
                t_p0 = time.perf_counter()
                fut_pose = executor.submit(pose_est.process, frame_rgb, True)
                fut_face = executor.submit(face_est.process, frame_rgb, True)
                pose_result = fut_pose.result()
                face_result = fut_face.result()
                t_p1 = time.perf_counter()
                pose_ms.append((t_p1 - t_p0) * 1000.0)
            else:
                t_p0 = time.perf_counter()
                pose_result = pose_est.process(frame_rgb, is_rgb=True)
                t_p1 = time.perf_counter()
                face_result = face_est.process(frame_rgb, is_rgb=True)
                t_p2 = time.perf_counter()
                pose_ms.append((t_p1 - t_p0) * 1000.0)
                face_ms.append((t_p2 - t_p1) * 1000.0)

            t_g0 = time.perf_counter()
            _metrics(pose_result, face_result)
            t_g1 = time.perf_counter()

            geom_ms.append((t_g1 - t_g0) * 1000.0)
            total_ms.append((t_g1 - t0) * 1000.0)
            n_pose += int(pose_result.detected)
            n_face += int(face_result.detected)
            collected += 1

        wall = time.perf_counter() - t_start
        out = {
            "n_frames": n_frames,
            "wall_clock_fps": round(n_frames / wall, 1) if wall > 0 else None,
            "pose_detected_pct": round(100 * n_pose / n_frames, 1),
            "face_detected_pct": round(100 * n_face / n_frames, 1),
            "geometry_fsm_latency_ms": {
                "mean": round(mean(geom_ms), 4),
                "p95": round(_pctl(geom_ms, 95), 4),
            },
            "total_pipeline_latency_ms": {
                "mean": round(mean(total_ms), 2),
                "p50": round(median(total_ms), 2),
                "p95": round(_pctl(total_ms, 95), 2),
                "p99": round(_pctl(total_ms, 99), 2),
            },
            "equivalent_fps_from_total_latency": round(1000.0 / mean(total_ms), 1),
        }
        if parallel:
            out["pose_and_face_parallel_latency_ms"] = {
                "mean": round(mean(pose_ms), 2),
                "p95": round(_pctl(pose_ms, 95), 2),
            }
        else:
            out["pose_latency_ms"] = {
                "mean": round(mean(pose_ms), 2),
                "p50": round(median(pose_ms), 2),
                "p95": round(_pctl(pose_ms, 95), 2),
            }
            out["face_latency_ms"] = {
                "mean": round(mean(face_ms), 2),
                "p50": round(median(face_ms), 2),
                "p95": round(_pctl(face_ms, 95), 2),
            }
        return out

    try:
        results = {
            "camera_source": source,
            "synthetic": synthetic,
            "config": {
                "pose_model_complexity": mp_cfg.get("pose_model_complexity", 1),
                "face_refine_landmarks": mp_cfg.get("face_refine_landmarks", False),
                "resolution": f"{width}x{height}",
            },
        }
        if mode in ("sequential", "both"):
            print("Midiendo modo SECUENCIAL...")
            results["sequential"] = _run(parallel=False)
        if mode in ("parallel", "both"):
            print("Midiendo modo PARALELO...")
            results["parallel"] = _run(parallel=True)

        if "sequential" in results and "parallel" in results:
            seq = results["sequential"]["total_pipeline_latency_ms"]["mean"]
            par = results["parallel"]["total_pipeline_latency_ms"]["mean"]
            results["speedup"] = {
                "latencia_secuencial_ms": seq,
                "latencia_paralelo_ms": par,
                "reduccion_pct": round(100 * (seq - par) / seq, 1) if seq else None,
                "factor": round(seq / par, 2) if par else None,
            }
    finally:
        vt.stop()
        executor.shutdown(wait=True)
        pose_est.close()
        face_est.close()

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark real de latencia con MediaPipe")
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--source", type=int, default=-1, help="-1 = autodetectar")
    parser.add_argument("--mode", choices=("sequential", "parallel", "both"),
                        default="both",
                        help="Que configuracion medir (por defecto ambas)")
    parser.add_argument("--synthetic", action="store_true",
                        help="Usar frames sinteticos en vez de camara "
                             "(tiempos NO comparables con camara real)")
    args = parser.parse_args()

    r = benchmark(n_frames=args.frames, source=args.source,
                  mode=args.mode, synthetic=args.synthetic)
    print(json.dumps(r, indent=2, ensure_ascii=False))
