"""
export_study.py
---------------
Exporta los datos del estudio a archivos CSV listos para Excel, SPSS, R o
Python, y calcula los indicadores del Capítulo IV.

Qué produce (en --output-dir)
-----------------------------
  resumen_sesiones.csv   Una fila por sesión/participante: tiempo de uso,
                         alertas por hora, % del tiempo desviado, Δθc, PERCLOS,
                         tiempo de corrección tras la alerta, evolución entre
                         la primera y la segunda mitad de la sesión.
  segmentos.csv          Una fila por tramo etiquetado del protocolo: qué
                         condición era, qué alerta se esperaba, cuál se obtuvo
                         y con qué latencia.
  matriz_confusion.csv   VP / FN / FP / VN y sensibilidad o especificidad por
                         condición, y la precisión global con su intervalo de
                         confianza de Wilson al 95 %.
  eventos.csv            Cada alerta con su condición y el tiempo que tardó el
                         participante en corregir la postura.
  metricas_detalle.csv   Todas las filas de metrics_log con el participante y
                         el tiempo relativo, para gráficos de evolución.

Cómo se calcula la precisión
----------------------------
Cada tramo del protocolo (una condición mantenida varios segundos) se puntúa:

  - condición CON alerta esperada (p. ej. cabeza adelantada → cervical_angle):
      VP si el sistema emitió esa alerta durante el tramo, FN si no.
  - condición SIN alerta esperada (postura correcta):
      VN si no hubo ninguna alerta, FP si hubo alguna.

Una alerta cuenta para un tramo si ocurre desde su inicio hasta `--grace-sec`
después de su final (el filtro y el temporizador de 5 s retrasan la alerta).
Los tramos más cortos que `--min-segment-sec` no se puntúan, porque el sistema
no tiene tiempo físico de alertar (ventana de 3–8 s).

Limitaciones que hay que declarar en la tesis
---------------------------------------------
  - El intervalo de Wilson asume tramos independientes, pero los tramos de una
    misma persona no lo son: con 20–30 participantes el intervalo real es algo
    más ancho. Conviene reportarlo también por participante.
  - El enfriamiento entre alertas (alert_cooldown_sec) puede suprimir una
    alerta correcta si el mismo tipo ya saltó poco antes: el protocolo evita
    tramos del mismo tipo seguidos.
  - La resolución de metrics_log es de una fila cada ~1.3 s, así que la
    latencia y el tiempo de corrección tienen esa incertidumbre.

Uso
---
  python src/tools/export_study.py
  python src/tools/export_study.py --db data/history.db otra/history.db
  python src/tools/export_study.py --output-dir resultados/ronda1

Autor: Tesis Huisa Perez, UNSA 2026
"""

import argparse
import csv
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional, Sequence, Tuple

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "history.db"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "thresholds.json"

NO_CODE = "SIN_CODIGO"
FREE_USE = ("", "libre")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def wilson_interval(k: int, n: int, z: float = 1.96
                    ) -> Tuple[Optional[float], Optional[float]]:
    """Intervalo de confianza de Wilson para una proporción k/n."""
    if n <= 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def _iso(ts: Optional[float]) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _r(v, nd=3):
    return "" if v is None else round(v, nd)


def _pct(num: int, den: int) -> Optional[float]:
    return None if den == 0 else 100.0 * num / den


def _as_list(x) -> List[str]:
    if x is None:
        return []
    return [x] if isinstance(x, str) else list(x)


def load_conditions(config_path: Path) -> Dict[str, dict]:
    """Condiciones del protocolo definidas en config/thresholds.json → study."""
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for c in cfg.get("study", {}).get("conditions", []):
        out[c["id"]] = {
            "label": c.get("label", c["id"]),
            "expected": _as_list(c.get("expected_alert")),
            "scored": bool(c.get("scored", True)),
        }
    return out


def load_dev_threshold(config_path: Path) -> float:
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        return float(cfg["postural"].get("cervical_deviation_max_deg", 15.0))
    except (OSError, ValueError, KeyError):
        return 15.0


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def read_sessions(db_path: Path) -> List[dict]:
    """Sesiones de una base, con sus métricas y eventos."""
    if not db_path.exists():
        raise FileNotFoundError(f"Base de datos no encontrada: {db_path}")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
        has_code = "participant_code" in cols
        mcols = {r["name"] for r in con.execute("PRAGMA table_info(metrics_log)")}
        ecols = {r["name"] for r in con.execute("PRAGMA table_info(events)")}

        sessions = []
        for s in con.execute("SELECT * FROM sessions ORDER BY started_at"):
            code = (s["participant_code"] if has_code else None) or NO_CODE
            rows = [dict(r) for r in con.execute(
                "SELECT * FROM metrics_log WHERE session_id=? ORDER BY timestamp",
                (s["id"],))]
            events = [dict(r) for r in con.execute(
                "SELECT * FROM events WHERE session_id=? ORDER BY timestamp",
                (s["id"],))]
            for r in rows:
                r.setdefault("condition_label", None)
            for e in events:
                e.setdefault("condition_label", None)
            sessions.append({
                "fuente": db_path.name,
                "id": s["id"], "participante": code,
                "inicio": s["started_at"], "fin": s["ended_at"],
                "notas": s["notes"] or "",
                "rows": rows, "events": events,
                "_mcols": mcols, "_ecols": ecols,
            })
        return sessions
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Segmentos y puntuación
# ---------------------------------------------------------------------------

def sample_period(rows: Sequence[dict], default: float = 1.3) -> float:
    ts = [r["timestamp"] for r in rows]
    diffs = [b - a for a, b in zip(ts, ts[1:]) if 0 < b - a < 10]
    return median(diffs) if diffs else default


def build_segments(rows: Sequence[dict], period: float) -> List[dict]:
    """Agrupa filas consecutivas con la misma condición no vacía."""
    segs: List[dict] = []
    cur = None
    for r in rows:
        lab = r.get("condition_label") or ""
        if not lab:
            cur = None
            continue
        if cur is None or cur["condicion"] != lab:
            cur = {"condicion": lab, "inicio": r["timestamp"],
                   "fin": r["timestamp"], "rows": []}
            segs.append(cur)
        cur["fin"] = r["timestamp"]
        cur["rows"].append(r)
    for s in segs:
        s["fin"] += period            # la última fila cubre un periodo más
        s["duracion"] = s["fin"] - s["inicio"]
    return segs


def score_segment(seg: dict, events: Sequence[dict], cond: Optional[dict],
                  grace: float, min_seconds: float) -> dict:
    """Puntúa un tramo: VP/FN/FP/VN, o '' si no se puntúa."""
    in_window = [e for e in events
                 if seg["inicio"] <= e["timestamp"] <= seg["fin"] + grace]
    types = [e["alert_type"] for e in in_window]
    out = {"alertas": ";".join(types), "latencia_s": None, "resultado": "",
           "motivo_no_puntuado": ""}

    if cond is None:
        out["motivo_no_puntuado"] = "condición no definida en la configuración"
        return out
    if not cond["scored"]:
        out["motivo_no_puntuado"] = "condición sin puntuar (uso libre)"
        return out
    if seg["duracion"] < min_seconds:
        out["motivo_no_puntuado"] = (
            f"tramo de {seg['duracion']:.0f} s < {min_seconds:.0f} s")
        return out

    expected = cond["expected"]
    if expected:
        hits = [e for e in in_window if e["alert_type"] in expected]
        if hits:
            out["resultado"] = "VP"
            out["latencia_s"] = hits[0]["timestamp"] - seg["inicio"]
        else:
            out["resultado"] = "FN"
    else:
        out["resultado"] = "FP" if in_window else "VN"
    return out


def correction_seconds(event: dict, rows: Sequence[dict]) -> Optional[float]:
    """
    Segundos desde una alerta cervical hasta que la desviación vuelve por
    debajo de su umbral. None si no hay datos de desviación o no se corrigió.
    """
    if event["alert_type"] != "cervical_angle" or event.get("threshold") is None:
        return None
    limit = event["threshold"]
    for r in rows:
        if r["timestamp"] <= event["timestamp"]:
            continue
        dev = r.get("cervical_deviation")
        if dev is not None and dev <= limit:
            return r["timestamp"] - event["timestamp"]
    return None


# ---------------------------------------------------------------------------
# Resúmenes
# ---------------------------------------------------------------------------

def _vals(rows: Sequence[dict], key: str) -> List[float]:
    return [r[key] for r in rows if r.get(key) is not None]


def summarize_session(ses: dict, dev_threshold: float,
                      seg_rows: Sequence[dict]) -> dict:
    rows, events = ses["rows"], ses["events"]
    t0 = ses["inicio"]
    t1 = ses["fin"] or (rows[-1]["timestamp"] if rows else t0)
    dur_min = max(0.0, (t1 - t0) / 60.0)

    by_type = {}
    for e in events:
        by_type[e["alert_type"]] = by_type.get(e["alert_type"], 0) + 1

    # Δθc: solo filas con pose válida y desviación disponible
    valid = [r for r in rows if r.get("pose_valid") != 0]
    dev = _vals(valid, "cervical_deviation")
    over = sum(1 for d in dev if d > dev_threshold)

    libre = [r for r in valid
             if (r.get("condition_label") or "") in FREE_USE]
    dev_libre = _vals(libre, "cervical_deviation")
    over_libre = sum(1 for d in dev_libre if d > dev_threshold)

    # Evolución: alertas en cada mitad de la sesión
    mid = t0 + (t1 - t0) / 2.0
    first = sum(1 for e in events if e["timestamp"] < mid)
    second = len(events) - first

    corr = [c for c in (correction_seconds(e, rows) for e in events)
            if c is not None]
    cervical = [e for e in events if e["alert_type"] == "cervical_angle"]

    perclos = _vals(rows, "perclos")
    p95 = None
    if dev:
        ordered = sorted(dev)
        p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]

    return {
        "fuente": ses["fuente"], "sesion": ses["id"],
        "participante": ses["participante"],
        "inicio": _iso(t0), "fin": _iso(ses["fin"]),
        "sesion_cerrada": "si" if ses["fin"] else "no",
        "duracion_min": _r(dur_min, 2),
        "filas_metricas": len(rows),
        "fps_mediana": _r(median(_vals(rows, "fps")) if _vals(rows, "fps") else None, 1),
        "theta_neutro_deg": _r(median(_vals(rows, "cervical_baseline"))
                               if _vals(rows, "cervical_baseline") else None, 2),
        "umbral_ear": _r(median(_vals(rows, "ear_threshold"))
                         if _vals(rows, "ear_threshold") else None, 3),
        "distancia_mediana_m": _r(median(_vals(rows, "distance_m"))
                                  if _vals(rows, "distance_m") else None, 3),
        "delta_theta_media": _r(mean(dev) if dev else None, 2),
        "delta_theta_p95": _r(p95, 2),
        "pct_tiempo_desviado": _r(_pct(over, len(dev)), 1),
        "filas_uso_libre": len(dev_libre),
        "pct_desviado_uso_libre": _r(_pct(over_libre, len(dev_libre)), 1),
        "perclos_medio": _r(mean(perclos) if perclos else None, 3),
        "alertas_total": len(events),
        "alertas_por_hora": _r(len(events) / (dur_min / 60.0)
                               if dur_min > 0 else None, 2),
        "alertas_cervical": by_type.get("cervical_angle", 0),
        "alertas_hombros": by_type.get("shoulder_asymmetry", 0),
        "alertas_ojo": by_type.get("eye_fatigue", 0),
        "alertas_bostezo": by_type.get("yawn", 0),
        "alertas_somnolencia": by_type.get("drowsiness", 0),
        "alertas_1a_mitad": first,
        "alertas_2a_mitad": second,
        "alertas_cervicales_corregidas": len(corr),
        "alertas_cervicales_total": len(cervical),
        "correccion_mediana_s": _r(median(corr) if corr else None, 1),
        "notas": ses["notas"],
    }


def confusion_table(seg_rows: Sequence[dict],
                    conditions: Dict[str, dict]) -> List[dict]:
    """Matriz por condición y global, con intervalos de Wilson."""
    table = []
    scored = [s for s in seg_rows if s["resultado"]]
    tot = {"VP": 0, "FN": 0, "FP": 0, "VN": 0}

    for cid, cond in conditions.items():
        mine = [s for s in scored if s["condicion"] == cid]
        if not mine:
            continue
        c = {k: sum(1 for s in mine if s["resultado"] == k) for k in tot}
        for k in tot:
            tot[k] += c[k]
        if cond["expected"]:
            ok, n, metric = c["VP"], c["VP"] + c["FN"], "sensibilidad"
        else:
            ok, n, metric = c["VN"], c["VN"] + c["FP"], "especificidad"
        lo, hi = wilson_interval(ok, n)
        # En segmentos.csv la latencia vacía es "" (no None): hay que filtrar
        # por tipo, o la mediana mezcla cadenas con números.
        lats = [s["latencia_s"] for s in mine
                if isinstance(s["latencia_s"], (int, float))]
        table.append({
            "condicion": cid, "etiqueta": cond["label"],
            "alerta_esperada": ";".join(cond["expected"]) or "ninguna",
            "tramos": n, "VP": c["VP"], "FN": c["FN"], "FP": c["FP"],
            "VN": c["VN"], "metrica": metric,
            "valor_pct": _r(_pct(ok, n), 1),
            "ic95_inf_pct": _r(None if lo is None else 100 * lo, 1),
            "ic95_sup_pct": _r(None if hi is None else 100 * hi, 1),
            "latencia_mediana_s": _r(median(lats) if lats else None, 1),
        })

    n_all = sum(tot.values())
    ok_all = tot["VP"] + tot["VN"]
    lo, hi = wilson_interval(ok_all, n_all)
    table.append({
        "condicion": "GLOBAL", "etiqueta": "Precisión global",
        "alerta_esperada": "", "tramos": n_all, "VP": tot["VP"],
        "FN": tot["FN"], "FP": tot["FP"], "VN": tot["VN"],
        "metrica": "precision", "valor_pct": _r(_pct(ok_all, n_all), 1),
        "ic95_inf_pct": _r(None if lo is None else 100 * lo, 1),
        "ic95_sup_pct": _r(None if hi is None else 100 * hi, 1),
        "latencia_mediana_s": "",
    })
    return table


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------

def write_csv(path: Path, rows: Sequence[dict],
              fieldnames: Optional[Sequence[str]] = None) -> None:
    """utf-8 con BOM: Excel en español abre bien tildes y la letra θ."""
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


METRIC_COLUMNS = [
    "cervical_angle", "cervical_deviation", "cervical_baseline",
    "cervical_sagittal", "cervical_lateral", "neck_ratio",
    "shoulder_asymmetry", "ear_avg", "ear_threshold", "mouth_opening",
    "perclos", "blink_rate_per_min", "distance_m", "fps",
    "face_detected", "pose_detected", "pose_valid",
]


def export(db_paths: Sequence[Path], out_dir: Path, config_path: Path,
           grace: float = 5.0, min_seconds: float = 15.0) -> dict:
    conditions = load_conditions(config_path)
    dev_threshold = load_dev_threshold(config_path)

    sessions: List[dict] = []
    for p in db_paths:
        sessions.extend(read_sessions(p))

    out_dir.mkdir(parents=True, exist_ok=True)
    summaries, all_segments, event_rows, detail = [], [], [], []

    for ses in sessions:
        rows, events = ses["rows"], ses["events"]
        period = sample_period(rows)
        segs = build_segments(rows, period)

        seg_rows = []
        for seg in segs:
            res = score_segment(seg, events, conditions.get(seg["condicion"]),
                                grace, min_seconds)
            devs = _vals(seg["rows"], "cervical_deviation")
            valid_pct = (100.0 * sum(1 for r in seg["rows"]
                                     if r.get("pose_valid") != 0)
                         / len(seg["rows"]))
            seg_rows.append({
                "fuente": ses["fuente"], "sesion": ses["id"],
                "participante": ses["participante"],
                "condicion": seg["condicion"],
                "inicio": _iso(seg["inicio"]),
                "t_inicio_s": _r(seg["inicio"] - ses["inicio"], 1),
                "duracion_s": _r(seg["duracion"], 1),
                "alertas": res["alertas"],
                "resultado": res["resultado"],
                "latencia_s": _r(res["latencia_s"], 1),
                "delta_theta_media": _r(mean(devs) if devs else None, 2),
                "ear_media": _r(mean(_vals(seg["rows"], "ear_avg"))
                                if _vals(seg["rows"], "ear_avg") else None, 3),
                "pct_pose_valida": _r(valid_pct, 0),
                "motivo_no_puntuado": res["motivo_no_puntuado"],
            })
        all_segments.extend(seg_rows)
        summaries.append(summarize_session(ses, dev_threshold, seg_rows))

        for e in events:
            event_rows.append({
                "fuente": ses["fuente"], "sesion": ses["id"],
                "participante": ses["participante"],
                "hora": _iso(e["timestamp"]),
                "t_rel_s": _r(e["timestamp"] - ses["inicio"], 1),
                "tipo": e["alert_type"],
                "condicion": e.get("condition_label") or "",
                "valor": _r(e["metric_value"], 3),
                "umbral": _r(e["threshold"], 3),
                "duracion_s": _r(e["duration_sec"], 1),
                "segundos_hasta_corregir": _r(correction_seconds(e, rows), 1),
            })

        for r in rows:
            d = {"fuente": ses["fuente"], "sesion": ses["id"],
                 "participante": ses["participante"],
                 "hora": _iso(r["timestamp"]),
                 "t_rel_s": _r(r["timestamp"] - ses["inicio"], 1),
                 "condicion": r.get("condition_label") or ""}
            for c in METRIC_COLUMNS:
                d[c] = r.get(c)
            detail.append(d)

    confusion = confusion_table(all_segments, conditions)

    write_csv(out_dir / "resumen_sesiones.csv", summaries)
    write_csv(out_dir / "segmentos.csv", all_segments)
    write_csv(out_dir / "matriz_confusion.csv", confusion)
    write_csv(out_dir / "eventos.csv", event_rows,
              ["fuente", "sesion", "participante", "hora", "t_rel_s", "tipo",
               "condicion", "valor", "umbral", "duracion_s",
               "segundos_hasta_corregir"])
    write_csv(out_dir / "metricas_detalle.csv", detail,
              ["fuente", "sesion", "participante", "hora", "t_rel_s",
               "condicion"] + METRIC_COLUMNS)

    return {"sessions": summaries, "segments": all_segments,
            "confusion": confusion, "events": event_rows,
            "out_dir": out_dir}


def print_summary(result: dict) -> None:
    ses = result["sessions"]
    parts = sorted({s["participante"] for s in ses})
    print(f"\nSesiones: {len(ses)} · participantes: {len(parts)} "
          f"({', '.join(parts[:8])}{'…' if len(parts) > 8 else ''})")
    abiertas = [s for s in ses if s["sesion_cerrada"] == "no"]
    if abiertas:
        print(f"[AVISO] {len(abiertas)} sesión(es) sin cerrar: la app se cerró "
              f"sin pasar por «Salir», así que su duración es aproximada.")
    sin_codigo = [s for s in ses if s["participante"] == NO_CODE]
    if sin_codigo:
        print(f"[AVISO] {len(sin_codigo)} sesión(es) SIN código de "
              f"participante: no se pueden atribuir a nadie.")

    print("\nMatriz de confusión (tramos puntuados):")
    print(f"  {'condición':24s} {'tramos':>6s} {'VP':>4s} {'FN':>4s} "
          f"{'FP':>4s} {'VN':>4s}  {'valor':>7s}  IC 95 %")
    for c in result["confusion"]:
        ic = (f"[{c['ic95_inf_pct']}, {c['ic95_sup_pct']}]"
              if c["ic95_inf_pct"] != "" else "—")
        v = f"{c['valor_pct']} %" if c["valor_pct"] != "" else "—"
        print(f"  {c['condicion']:24s} {c['tramos']:6d} {c['VP']:4d} "
              f"{c['FN']:4d} {c['FP']:4d} {c['VN']:4d}  {v:>7s}  {ic}")
    sin_puntuar = sum(1 for s in result["segments"] if not s["resultado"])
    if sin_puntuar:
        print(f"\n  ({sin_puntuar} tramos no puntuados; ver la columna "
              f"motivo_no_puntuado de segmentos.csv)")
    print(f"\nArchivos en: {result['out_dir']}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Exporta los datos del estudio a CSV y calcula la "
                    "precisión del sistema.")
    ap.add_argument("--db", type=Path, nargs="+", default=[DEFAULT_DB],
                    help="Una o varias bases SQLite (p. ej. las de cada PC "
                         "de los participantes)")
    ap.add_argument("--output-dir", type=Path, default=None,
                    help="Carpeta de salida (por defecto data/estudio_<fecha>)")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--grace-sec", type=float, default=5.0,
                    help="Segundos tras el fin de un tramo en que una alerta "
                         "aún se le atribuye (default: 5)")
    ap.add_argument("--min-segment-sec", type=float, default=15.0,
                    help="Duración mínima para puntuar un tramo (default: 15)")
    args = ap.parse_args(argv)

    out = args.output_dir or (
        PROJECT_ROOT / "data" / f"estudio_{datetime.now():%Y%m%d_%H%M%S}")
    try:
        result = export(args.db, out, args.config,
                        grace=args.grace_sec, min_seconds=args.min_segment_sec)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 1
    print_summary(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
