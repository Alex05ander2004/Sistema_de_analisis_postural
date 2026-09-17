"""
stats_report.py
---------------
Generador de reportes estadísticos para el Capítulo IV de la tesis.

Lee los datos de la base de datos SQLite generada por HistoryLogger
y produce:
  1. Reporte de texto en consola (tabla de métricas)
  2. Reporte JSON exportable (para incluir en el Capítulo IV)
  3. Reporte CSV con todas las métricas de una sesión

Uso:
  python src/tools/stats_report.py
  python src/tools/stats_report.py --session <session_id>
  python src/tools/stats_report.py --list-sessions
  python src/tools/stats_report.py --export-all

Autor: Fase 4 — Tesis Huisa Perez, UNSA 2026
"""

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev

if sys.platform == "win32":
    # La consola Windows usa cp1252 por defecto, que no puede codificar
    # los símbolos que este script imprime (θ, ✅, ⚠️, etc.).
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DB = PROJECT_ROOT / "data" / "history.db"
DEFAULT_THRESHOLDS = PROJECT_ROOT / "config" / "thresholds.json"


# ---------------------------------------------------------------------------
# Conexión y helpers
# ---------------------------------------------------------------------------

def load_thresholds(path: Path = DEFAULT_THRESHOLDS) -> dict:
    """Carga config/thresholds.json para que los reportes usen los mismos
    umbrales vigentes en la app (evita que el reporte quede desincronizado
    tras una recalibración)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"[ERROR] Base de datos no encontrada: {db_path}")
        print("  Ejecuta primero la aplicación para generar datos.")
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _percentile(data: list, p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = max(0, int(p / 100 * len(sorted_data)) - 1)
    return sorted_data[idx]


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def list_sessions(db_path: Path) -> None:
    """Lista todas las sesiones registradas."""
    conn = _connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT s.id, s.started_at, s.ended_at, s.notes,
               COUNT(DISTINCT e.id) as n_events,
               COUNT(DISTINCT m.id) as n_frames
        FROM sessions s
        LEFT JOIN events e ON e.session_id = s.id
        LEFT JOIN metrics_log m ON m.session_id = s.id
        GROUP BY s.id
        ORDER BY s.started_at DESC
    """)
    rows = cur.fetchall()

    if not rows:
        print("No hay sesiones registradas aún.")
        return

    print(f"\n{'='*80}")
    print(f"  SESIONES REGISTRADAS ({len(rows)} total)")
    print(f"{'='*80}")
    print(f"  {'ID':>10} | {'Inicio':>19} | {'Duración':>9} | {'Alertas':>8} | {'Frames':>7} | Notas")
    print(f"  {'-'*10}-+-{'-'*19}-+-{'-'*9}-+-{'-'*8}-+-{'-'*7}-+--------")

    for row in rows:
        start = datetime.fromtimestamp(row["started_at"]).strftime("%Y-%m-%d %H:%M:%S")
        dur = ""
        if row["ended_at"] and row["started_at"]:
            secs = int(row["ended_at"] - row["started_at"])
            dur = f"{secs // 60}m {secs % 60}s"
        print(f"  {row['id'][:10]:>10} | {start:>19} | {dur:>9} | "
              f"{row['n_events']:>8} | {row['n_frames']:>7} | {row['notes'] or ''}")

    conn.close()


def show_session_stats(session_id: str | None, db_path: Path,
                       thresholds: dict | None = None) -> dict:
    """Muestra estadísticas completas de una sesión."""
    if thresholds is None:
        thresholds = load_thresholds()
    theta_max = thresholds.get("postural", {}).get("cervical_angle_max_deg", 30.0)
    ear_min = thresholds.get("fatigue", {}).get("ear_threshold", 0.21)

    conn = _connect(db_path)
    cur = conn.cursor()

    # Obtener sesión (más reciente si no se especifica)
    if session_id is None:
        cur.execute("SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            print("[ERROR] No hay sesiones registradas.")
            conn.close()
            return {}
        session_id = row["id"]

    # Datos de sesión
    cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    session = cur.fetchone()
    if not session:
        print(f"[ERROR] Sesión no encontrada: {session_id}")
        conn.close()
        return {}

    # Eventos
    cur.execute("""
        SELECT alert_type, COUNT(*) as cnt,
               AVG(duration_sec) as avg_dur,
               AVG(metric_value) as avg_val
        FROM events WHERE session_id = ?
        GROUP BY alert_type
    """, (session_id,))
    events_by_type = {row["alert_type"]: dict(row) for row in cur.fetchall()}

    # Métricas
    # NOTA: la columna se llama `cervical_angle`. La consulta anterior pedía
    # `theta_c as cervical_angle` y la herramienta entera fallaba con
    # "no such column: theta_c" en cuanto se ejecutaba — es decir, este
    # generador de reportes del Capítulo IV nunca llegó a producir un reporte.
    cur.execute("""
        SELECT timestamp, cervical_angle, cervical_sagittal, cervical_lateral,
               shoulder_asymmetry, ear_avg, mouth_opening, fps,
               distance_m, perclos, blink_rate_per_min,
               face_detected, pose_detected, pose_valid
        FROM metrics_log WHERE session_id = ?
        ORDER BY timestamp
    """, (session_id,))
    metrics = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not metrics:
        print(f"[AVISO] Sin métricas registradas para sesión {session_id[:10]}…")
        return {}

    # --- Calcular estadísticas ---
    theta_vals = [m["cervical_angle"] for m in metrics if m["pose_detected"] and m["cervical_angle"] is not None]
    ear_vals   = [m["ear_avg"] for m in metrics if m["face_detected"] and m["ear_avg"] and m["ear_avg"] > 0]
    fps_vals   = [m["fps"] for m in metrics if m["fps"] and m["fps"] > 0]
    shoulder_vals = [abs(m["shoulder_asymmetry"]) for m in metrics
                     if m["pose_detected"] and m["shoulder_asymmetry"] is not None]
    sagittal_vals = [m["cervical_sagittal"] for m in metrics
                     if m["pose_detected"] and m["cervical_sagittal"] is not None]
    dist_vals     = [m["distance_m"] for m in metrics if m["distance_m"] is not None]
    perclos_vals  = [m["perclos"] for m in metrics if m["perclos"] is not None]
    blink_vals    = [m["blink_rate_per_min"] for m in metrics
                     if m["blink_rate_per_min"]]
    shoulder_max_deg = thresholds.get("postural", {}).get("shoulder_asymmetry_max_deg", 10.0)
    pct_shoulder_risk = 100 * sum(1 for v in shoulder_vals if v > shoulder_max_deg) / max(len(shoulder_vals), 1)

    # Tiempo en riesgo (% de frames con θc > umbral o EAR <= umbral, según thresholds.json)
    pct_cervical_risk = 100 * sum(1 for v in theta_vals if v > theta_max) / max(len(theta_vals), 1)
    pct_fatigue_risk  = 100 * sum(1 for v in ear_vals if v <= ear_min) / max(len(ear_vals), 1)

    # Duración: si la sesión no llegó a cerrarse (crash o kill del proceso),
    # se estima con el rango de timestamps registrados en vez de mostrar N/A.
    duration_sec = None
    duration_source = None
    if session["ended_at"] and session["started_at"]:
        duration_sec = session["ended_at"] - session["started_at"]
        duration_source = "sessions"
    else:
        ts = [m["timestamp"] for m in metrics if m["timestamp"] is not None]
        if len(ts) >= 2:
            duration_sec = max(ts) - min(ts)
            duration_source = "metrics_log (sesión sin cerrar)"

    stats = {
        "session_id":         session_id,
        "started_at":         datetime.fromtimestamp(session["started_at"]).isoformat(),
        "duration_sec":       round(duration_sec, 1) if duration_sec else None,
        "duration_source":    duration_source,
        "total_frames":       len(metrics),
        "total_alerts":       sum(v["cnt"] for v in events_by_type.values()),
        "alerts_by_type":     {k: v["cnt"] for k, v in events_by_type.items()},
        "cervical_angle": {
            "mean_deg":   round(mean(theta_vals), 2) if theta_vals else None,
            "median_deg": round(median(theta_vals), 2) if theta_vals else None,
            "max_deg":    round(max(theta_vals), 2) if theta_vals else None,
            "p95_deg":    round(_percentile(theta_vals, 95), 2) if theta_vals else None,
            "threshold_deg": theta_max,
            "pct_over_threshold": round(pct_cervical_risk, 1),
        },
        "cervical_sagittal": {
            "mean_deg":   round(mean(sagittal_vals), 2) if sagittal_vals else None,
            "median_deg": round(median(sagittal_vals), 2) if sagittal_vals else None,
            "max_deg":    round(max(sagittal_vals), 2) if sagittal_vals else None,
            "_comment":   "Cabeza adelantada en el plano sagital. CVA clínico ~= 90 - este valor.",
        },
        "shoulder_asymmetry": {
            "mean_deg":   round(mean(shoulder_vals), 2) if shoulder_vals else None,
            "median_deg": round(median(shoulder_vals), 2) if shoulder_vals else None,
            "max_deg":    round(max(shoulder_vals), 2) if shoulder_vals else None,
            "p95_deg":    round(_percentile(shoulder_vals, 95), 2) if shoulder_vals else None,
            "threshold_deg": shoulder_max_deg,
            "pct_over_threshold": round(pct_shoulder_risk, 1),
        },
        "distance_m": {
            "mean":   round(mean(dist_vals), 3) if dist_vals else None,
            "min":    round(min(dist_vals), 3) if dist_vals else None,
            "max":    round(max(dist_vals), 3) if dist_vals else None,
            "pct_in_range": round(
                100 * sum(1 for v in dist_vals
                          if thresholds.get("camera", {}).get("min_distance_m", 0.50)
                          <= v <= thresholds.get("camera", {}).get("max_distance_m", 0.70))
                / len(dist_vals), 1) if dist_vals else None,
        },
        "fatigue_indicators": {
            "perclos_max":        round(max(perclos_vals), 4) if perclos_vals else None,
            "perclos_mean":       round(mean(perclos_vals), 4) if perclos_vals else None,
            "blink_rate_mean":    round(mean(blink_vals), 1) if blink_vals else None,
        },
        "ear": {
            "mean":           round(mean(ear_vals), 4) if ear_vals else None,
            "min":            round(min(ear_vals), 4) if ear_vals else None,
            "p5":             round(_percentile(ear_vals, 5), 4) if ear_vals else None,
            "threshold":      ear_min,
            "pct_under_threshold": round(pct_fatigue_risk, 1),
        },
        "fps": {
            "mean":  round(mean(fps_vals), 1) if fps_vals else None,
            "min":   round(min(fps_vals), 1) if fps_vals else None,
            "meets_target_30fps": (mean(fps_vals) >= 30) if fps_vals else False,
        },
        "detection_rate": {
            "pose_pct": round(100 * sum(1 for m in metrics if m["pose_detected"]) / len(metrics), 1),
            "face_pct": round(100 * sum(1 for m in metrics if m["face_detected"]) / len(metrics), 1),
            "pose_valid_pct": round(
                100 * sum(1 for m in metrics if m["pose_valid"]) / len(metrics), 1),
        },
    }

    # --- Imprimir reporte ---
    _print_report(stats, events_by_type)
    return stats


def _print_report(stats: dict, events_by_type: dict) -> None:
    """Imprime el reporte en consola."""
    sid = stats["session_id"][:10]
    dur = f"{stats['duration_sec']:.0f}s" if stats["duration_sec"] else "N/A"
    if stats.get("duration_source", "").startswith("metrics_log"):
        dur += " (estimada)"

    print(f"\n{'='*65}")
    print(f"  REPORTE DE SESIÓN — {sid}…")
    print(f"  Inicio: {stats['started_at']} | Duración: {dur}")
    print(f"  Frames: {stats['total_frames']} | Alertas totales: {stats['total_alerts']}")
    print(f"{'='*65}")

    # Ángulo cervical
    c = stats["cervical_angle"]
    if c["mean_deg"] is not None:
        print(f"\n  Ángulo Cervical (θc):")
        print(f"    Promedio   : {c['mean_deg']}°")
        print(f"    Mediana    : {c['median_deg']}°")
        print(f"    Máximo     : {c['max_deg']}°")
        print(f"    P95        : {c['p95_deg']}°")
        ok = "✅" if c["pct_over_threshold"] < 20 else "⚠️"
        print(f"    % en riesgo (>{c['threshold_deg']}°): {c['pct_over_threshold']}% {ok}")

    # Componente sagital
    sg = stats.get("cervical_sagittal", {})
    if sg.get("mean_deg") is not None:
        print(f"    Comp. sagital (cabeza adelantada): media {sg['mean_deg']}°, "
              f"máx {sg['max_deg']}°  → CVA ~= {90 - sg['mean_deg']:.1f}°")

    # Asimetría escapular
    sh = stats.get("shoulder_asymmetry", {})
    if sh.get("mean_deg") is not None:
        print(f"\n  Asimetría Escapular (ΔE):")
        print(f"    Promedio   : {sh['mean_deg']}°")
        print(f"    Máximo     : {sh['max_deg']}°")
        ok = "✅" if sh["pct_over_threshold"] < 20 else "⚠️"
        print(f"    % en riesgo (>{sh['threshold_deg']}°): {sh['pct_over_threshold']}% {ok}")

    # EAR
    e = stats["ear"]
    if e["mean"] is not None:
        print(f"\n  EAR (Fatiga Ocular):")
        print(f"    Promedio   : {e['mean']}")
        print(f"    Mínimo     : {e['min']}")
        print(f"    P5         : {e['p5']}  ← referencia para umbral")
        ok = "✅" if e["pct_under_threshold"] < 10 else "⚠️"
        print(f"    % bajo {e['threshold']}: {e['pct_under_threshold']}% {ok}")

    # FPS
    f = stats["fps"]
    if f["mean"] is not None:
        ok = "✅ CUMPLE" if f["meets_target_30fps"] else "❌ NO CUMPLE"
        print(f"\n  Rendimiento FPS:")
        print(f"    Promedio   : {f['mean']}")
        print(f"    Mínimo     : {f['min']}")
        print(f"    Meta ≥ 30  : {ok}")

    # Indicadores derivados de fatiga
    fi = stats.get("fatigue_indicators", {})
    if fi.get("perclos_max") is not None:
        print(f"\n  Indicadores de Fatiga:")
        print(f"    PERCLOS máx : {fi['perclos_max'] * 100:.1f}%")
        print(f"    PERCLOS med : {fi['perclos_mean'] * 100:.1f}%")
        if fi.get("blink_rate_mean") is not None:
            print(f"    Parpadeos/min (media): {fi['blink_rate_mean']}")

    # Distancia
    dm = stats.get("distance_m", {})
    if dm.get("mean") is not None:
        print(f"\n  Distancia cámara-usuario:")
        print(f"    Media / rango : {dm['mean']} m  ({dm['min']}–{dm['max']} m)")
        ok = "✅" if (dm["pct_in_range"] or 0) >= 80 else "⚠️"
        print(f"    % dentro del rango recomendado: {dm['pct_in_range']}% {ok}")

    # Tasa de detección
    d = stats["detection_rate"]
    print(f"\n  Tasa de Detección:")
    print(f"    Pose       : {d['pose_pct']}%")
    print(f"    Cara       : {d['face_pct']}%")
    if d.get("pose_valid_pct") is not None:
        print(f"    Pose válida (visible y a distancia correcta): {d['pose_valid_pct']}%")

    # Alertas por tipo
    if events_by_type:
        print(f"\n  Alertas por Tipo:")
        for alert_type, data in events_by_type.items():
            print(f"    {alert_type:30s}: {data['cnt']:>4}  "
                  f"(duración media: {data['avg_dur']:.1f}s)")

    print(f"\n{'='*65}\n")


def export_session(session_id: str | None, db_path: Path,
                   output_dir: Path) -> None:
    """Exporta los datos de sesión a JSON y CSV."""
    stats = show_session_stats(session_id, db_path)
    if not stats:
        return

    sid = stats["session_id"]
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    json_path = output_dir / f"session_{sid[:8]}_stats.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"  JSON exportado: {json_path}")

    # CSV de métricas crudas
    conn = _connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp,
               timestamp - (SELECT MIN(timestamp) FROM metrics_log
                            WHERE session_id = ?) as elapsed_sec,
               cervical_angle, cervical_sagittal, cervical_lateral,
               shoulder_asymmetry, ear_avg, mouth_opening,
               distance_m, perclos, blink_rate_per_min,
               fps, face_detected, pose_detected, pose_valid
        FROM metrics_log WHERE session_id = ?
        ORDER BY timestamp
    """, (sid, sid))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    if rows:
        csv_path = output_dir / f"session_{sid[:8]}_metrics.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"  CSV de métricas: {csv_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generador de reportes estadísticos — Tesis UNSA 2026"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help="Ruta a la base de datos SQLite")
    parser.add_argument("--session", type=str, default=None,
                        help="ID de sesión específica (default: más reciente)")
    parser.add_argument("--list-sessions", action="store_true",
                        help="Listar todas las sesiones disponibles")
    parser.add_argument("--export", action="store_true",
                        help="Exportar JSON y CSV de la sesión seleccionada")
    parser.add_argument("--output-dir", type=Path,
                        default=PROJECT_ROOT / "data" / "reports",
                        help="Directorio de salida para exportaciones")
    args = parser.parse_args()

    if args.list_sessions:
        list_sessions(args.db)
    elif args.export:
        export_session(args.session, args.db, args.output_dir)
    else:
        show_session_stats(args.session, args.db)
