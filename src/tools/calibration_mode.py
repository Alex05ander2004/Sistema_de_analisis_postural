"""
calibration_mode.py
-------------------
Herramienta de calibración para ajuste fino de umbrales ergonómicos.

Ejecuta el pipeline completo (cámara → MediaPipe → geometry) en modo
"silencioso": NO dispara alertas, solo registra métricas en consola y en
un archivo CSV para análisis posterior.

Uso para el Capítulo IV (objetivo 1.3.2.1 — calibración de umbrales):
  1. Ejecutar durante 5-10 minutos en postura CORRECTA → anotar rangos normales.
  2. Ejecutar durante 2 minutos con CABEZA ADELANTADA → verificar que θc sube.
  3. Ejecutar durante 2 minutos CERRANDO OJOS frecuentemente → verificar EAR.
  4. Comparar percentiles obtenidos con umbrales de la literatura y ajustar
     thresholds.json si los datos de usuario real lo justifican.

Salida:
  - Tabla en consola con θc, ΔE, EAR, apertura bucal, FPS cada segundo
  - Archivo CSV en data/calibration_TIMESTAMP.csv

Ejecutar:
  python src/tools/calibration_mode.py [--source 0] [--duration 300]

Autor: Fase 4 — Tesis Huisa Perez, UNSA 2026
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2

if sys.platform == "win32":
    # La consola Windows usa cp1252 por defecto, que no puede codificar
    # los símbolos que este script imprime (θ, Δ, °, etc.).
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.capture.video_thread import VideoThread
from src.vision.pose_estimator import PoseEstimator
from src.vision.face_estimator import FaceEstimator
from src.vision.geometry import (
    calculate_cervical_angle,
    calculate_cervical_components,
    calculate_shoulder_asymmetry,
    calculate_avg_ear,
    calculate_ear,
    calculate_mouth_opening,
    estimate_distance_m,
)

logging.basicConfig(level=logging.WARNING)  # Silenciar MediaPipe


def _bring_window_to_front(window_title: str) -> None:
    """
    cv2.imshow crea la ventana sin foco — en Windows suele quedar detrás
    de la terminal/IDE activo y el usuario nunca la nota. La trae al frente
    una sola vez cuando aparece por primera vez.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, window_title)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            ctypes.windll.user32.BringWindowToTop(hwnd)
    except Exception:
        pass  # nunca bloquear la calibración por esto


def load_thresholds() -> dict:
    path = PROJECT_ROOT / "config" / "thresholds.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def calibrate(source: int = 0, duration_sec: int = 300,
              show_video: bool = True) -> None:
    """
    Ejecuta el modo de calibración.

    Parameters
    ----------
    source:
        Índice de cámara.
    duration_sec:
        Duración máxima en segundos (0 = indefinida).
    show_video:
        Si True, muestra la ventana de OpenCV con landmarks.
    """
    thresholds = load_thresholds()
    mp_cfg = thresholds.get("mediapipe", {})
    cam_cfg = thresholds.get("camera", {})

    # Rutas de salida
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = data_dir / f"calibration_{ts}.csv"

    # Cabecera del CSV
    fieldnames = [
        "timestamp", "elapsed_sec",
        "theta_c", "theta_sagital", "theta_lateral", "delta_e",
        "ear_left", "ear_right", "ear_avg",
        "mouth_opening",
        "distance_m",
        "pose_detected", "face_detected",
        "fps",
    ]

    # Tamaño de frame y FOV: geometry.py necesita el primero para trabajar en
    # píxeles isótropos y el segundo para estimar la distancia. Sin pasarlos,
    # la calibración mediría en una escala distinta a la de la app.
    frame_size = (cam_cfg.get("resolution_width", 640),
                  cam_cfg.get("resolution_height", 480))
    hfov_deg = cam_cfg.get("horizontal_fov_deg", 60.0)

    LEFT_EYE  = FaceEstimator.LEFT_EYE_EAR_INDICES
    RIGHT_EYE = FaceEstimator.RIGHT_EYE_EAR_INDICES

    print(f"\n{'='*62}")
    print(f"  MODO CALIBRACIÓN — Sistema Postural y Fatiga")
    print(f"{'='*62}")
    print(f"  Cámara:          #{source}")
    print(f"  Duración:        {'indefinida' if duration_sec == 0 else f'{duration_sec}s'}")
    print(f"  Salida CSV:      {csv_path.name}")
    print(f"  [Ctrl+C] para detener\n")

    # Cabecera de consola
    hdr = (f"{'t(s)':>6} | {'θc(°)':>7} | {'ΔE(°)':>7} | "
           f"{'EAR_L':>6} | {'EAR_R':>6} | {'EAR_avg':>7} | "
           f"{'Boca':>5} | {'Pose':>4} | {'Cara':>4} | {'FPS':>5}")
    sep = "-" * len(hdr)
    print(hdr)
    print(sep)

    vt = VideoThread(source=source,
                     width=cam_cfg.get("resolution_width", 640),
                     height=cam_cfg.get("resolution_height", 480),
                     target_fps=cam_cfg.get("target_fps", 30))

    pose_est = PoseEstimator(
        model_complexity=mp_cfg.get("pose_model_complexity", 1),
        min_detection_confidence=mp_cfg.get("pose_min_detection_confidence", 0.5),
        min_tracking_confidence=mp_cfg.get("pose_min_tracking_confidence", 0.5),
        draw_landmarks=show_video,
    )
    face_est = FaceEstimator(
        refine_landmarks=mp_cfg.get("face_refine_landmarks", True),
        min_detection_confidence=mp_cfg.get("face_min_detection_confidence", 0.5),
        min_tracking_confidence=mp_cfg.get("face_min_tracking_confidence", 0.5),
        draw_landmarks=show_video,
    )

    rows = []
    fps_counter = 0
    fps_ts = time.perf_counter()
    fps_actual = 0.0
    log_ts = time.perf_counter()
    start_time = time.perf_counter()
    frame_n = 0
    window_name = "Calibracion — [Q] para salir"
    window_focused = False

    vt.start()

    try:
        while True:
            elapsed = time.perf_counter() - start_time
            if duration_sec > 0 and elapsed >= duration_sec:
                break

            frame = vt.get_frame(timeout=0.05)
            if frame is None:
                continue

            frame_n += 1
            fps_counter += 1

            # --- FPS ---
            if (time.perf_counter() - fps_ts) >= 1.0:
                fps_actual = fps_counter / (time.perf_counter() - fps_ts)
                fps_counter = 0
                fps_ts = time.perf_counter()

            # --- Inferencia (una sola conversión de color, igual que la app) ---
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame_rgb.flags.writeable = False
            pose_result = pose_est.process(frame_rgb, is_rgb=True)
            face_result = face_est.process(frame_rgb, is_rgb=True)

            ann = frame
            pose_est.draw(ann, pose_result)
            face_est.draw(ann, face_result)

            # --- Métricas ---
            theta_c = delta_e = ear_l = ear_r = ear_avg = mouth = 0.0
            theta_sag = theta_lat = 0.0
            distancia = None

            if pose_result.detected and len(pose_result.landmarks) >= 13:
                try:
                    theta_c, theta_sag, theta_lat = calculate_cervical_components(
                        pose_result.landmarks, frame_size)
                    delta_e = calculate_shoulder_asymmetry(
                        pose_result.landmarks, frame_size)
                    distancia = estimate_distance_m(
                        pose_result.landmarks, frame_size, hfov_deg)
                except Exception:
                    pass

            if face_result.detected and len(face_result.landmarks) >= 300:
                try:
                    ear_l   = calculate_ear(face_result.landmarks, LEFT_EYE, frame_size)
                    ear_r   = calculate_ear(face_result.landmarks, RIGHT_EYE, frame_size)
                    ear_avg = (ear_l + ear_r) / 2.0
                    mouth   = calculate_mouth_opening(
                        face_result.landmarks, frame_size=frame_size)
                except Exception:
                    pass

            # --- Log a CSV cada frame ---
            row = {
                "timestamp":     datetime.now().isoformat(),
                "elapsed_sec":   round(elapsed, 2),
                "theta_c":       round(theta_c, 3),
                "theta_sagital": round(theta_sag, 3),
                "theta_lateral": round(theta_lat, 3),
                "delta_e":       round(delta_e, 3),
                "ear_left":      round(ear_l, 4),
                "ear_right":     round(ear_r, 4),
                "ear_avg":       round(ear_avg, 4),
                "mouth_opening": round(mouth, 4),
                "distance_m":    round(distancia, 3) if distancia is not None else "",
                "pose_detected": int(pose_result.detected),
                "face_detected": int(face_result.detected),
                "fps":           round(fps_actual, 1),
            }
            rows.append(row)

            # --- Imprimir a consola cada segundo ---
            if (time.perf_counter() - log_ts) >= 1.0:
                log_ts = time.perf_counter()
                pose_ok = "✓" if pose_result.detected else "✗"
                face_ok = "✓" if face_result.detected else "✗"
                print(
                    f"{elapsed:>6.1f} | {theta_c:>7.2f} | {delta_e:>7.2f} | "
                    f"{ear_l:>6.3f} | {ear_r:>6.3f} | {ear_avg:>7.3f} | "
                    f"{mouth:>5.3f} | {pose_ok:>4} | {face_ok:>4} | {fps_actual:>5.1f}"
                )

            # --- Mostrar video (opcional) ---
            if show_video:
                cv2.imshow(window_name, ann)
                if not window_focused:
                    # cv2.imshow crea la ventana detrás de la terminal/IDE activo
                    # sin foco — forzarla al frente una sola vez para que sea visible.
                    _bring_window_to_front(window_name)
                    window_focused = True
                key = cv2.waitKey(1) & 0xFF
                if key in (ord('q'), ord('Q'), 27):
                    break

    except KeyboardInterrupt:
        print("\n  [Ctrl+C] detectado — deteniendo...")
    finally:
        vt.stop()
        pose_est.close()
        face_est.close()
        if show_video:
            cv2.destroyAllWindows()

        # Guardar CSV
        if rows:
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            print(f"\n  CSV guardado: {csv_path}")
            _print_summary(rows, thresholds)
        else:
            print("\n  Sin datos para guardar.")


def _print_summary(rows: list, thresholds: dict) -> None:
    """Imprime estadísticas de resumen al final de la calibración."""
    if not rows:
        return

    import statistics as stats

    theta_vals = [r["theta_c"] for r in rows if r["pose_detected"]]
    ear_vals   = [r["ear_avg"] for r in rows if r["face_detected"] and r["ear_avg"] > 0]
    fps_vals   = [r["fps"] for r in rows if r["fps"] > 0]

    post = thresholds.get("postural", {})
    fat  = thresholds.get("fatigue", {})

    print(f"\n{'='*62}")
    print(f"  RESUMEN DE CALIBRACIÓN")
    print(f"{'='*62}")
    print(f"  Frames totales : {len(rows)}")
    print(f"  Duración       : {rows[-1]['elapsed_sec']:.1f} s")
    print()

    if theta_vals:
        print(f"  Ángulo cervical (θc):")
        print(f"    Promedio : {stats.mean(theta_vals):.2f}°")
        print(f"    Mínimo   : {min(theta_vals):.2f}°")
        print(f"    Máximo   : {max(theta_vals):.2f}°")
        p95 = sorted(theta_vals)[int(0.95 * len(theta_vals))]
        print(f"    P95      : {p95:.2f}°")
        pct_over = 100 * sum(1 for v in theta_vals if v > post.get("cervical_angle_max_deg", 15)) / len(theta_vals)
        print(f"    % sobre umbral ({post.get('cervical_angle_max_deg', 15)}°): {pct_over:.1f}%")

    print()

    if ear_vals:
        print(f"  EAR promedio:")
        print(f"    Promedio : {stats.mean(ear_vals):.4f}")
        print(f"    Mínimo   : {min(ear_vals):.4f}")
        p5 = sorted(ear_vals)[int(0.05 * len(ear_vals))]
        print(f"    P5       : {p5:.4f}  ← referencia para umbral")
        pct_under = 100 * sum(1 for v in ear_vals if v <= fat.get("ear_threshold", 0.21)) / len(ear_vals)
        print(f"    % bajo umbral ({fat.get('ear_threshold', 0.21)}): {pct_under:.1f}%")

    print()

    if fps_vals:
        print(f"  FPS del sistema:")
        print(f"    Promedio : {stats.mean(fps_vals):.1f}")
        print(f"    Mínimo   : {min(fps_vals):.1f}")
        print(f"    Meta ≥ 30: {'CUMPLE' if stats.mean(fps_vals) >= 30 else 'NO CUMPLE'}")

    print(f"\n{'='*62}")
    print(f"  Revisa los datos en el CSV para ajustar config/thresholds.json")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Modo de calibración — Sistema Monitoreo Postural y Fatiga"
    )
    parser.add_argument("--source", type=int, default=0,
                        help="Índice de cámara (default: 0)")
    parser.add_argument("--duration", type=int, default=0,
                        help="Duración en segundos (0=indefinida)")
    parser.add_argument("--no-video", action="store_true",
                        help="Deshabilitar ventana de video")
    args = parser.parse_args()

    calibrate(source=args.source,
              duration_sec=args.duration,
              show_video=not args.no_video)
