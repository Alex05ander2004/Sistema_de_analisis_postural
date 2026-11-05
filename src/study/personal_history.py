"""
personal_history.py
-------------------
Resumen diario para la ventana «Mi historial» (OE 1.3.2.5: reportes
históricos de salud postural para el usuario final).

Usa los mismos cálculos que el análisis del investigador
(src/tools/field_analysis.py), para que lo que ve el usuario y lo que se
reporta en la tesis salgan de la misma definición: tiempo ponderado por
fila, huecos > 5 s sin contar, filas a < 3 FPS (cámara compartida) fuera.

Por día:
  minutos medidos, % del tiempo en buena postura (Δθc <= umbral), Δθc
  medio, pausas activas por hora, tramo más largo sin levantarse, avisos
  recibidos y PERCLOS medio.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import time
from collections import defaultdict
from typing import Dict, List, Optional

from src.tools import field_analysis as fa


def daily_history(history, thresholds: dict, days: int = 14,
                  participant_code: Optional[str] = None,
                  now: Optional[float] = None) -> List[dict]:
    """
    Una fila por día con uso en los últimos `days` días, del más reciente al
    más antiguo. `history` es un HistoryLogger (o algo con rows_since y
    events_since).
    """
    cfg = fa.FieldConfig.from_thresholds(thresholds)
    now = time.time() if now is None else now
    since = now - days * 86400.0

    by_session: Dict[str, List[dict]] = defaultdict(list)
    for r in history.rows_since(since, participant_code):
        fps = r.get("fps")
        if fps is not None and 0 < fps < fa.MIN_VALID_FPS:
            continue
        by_session[r["session_id"]].append(r)

    by_day: Dict[str, List[tuple]] = defaultdict(list)
    for rows in by_session.values():
        for r, d in zip(rows, fa.row_durations(rows, cfg.max_gap_sec)):
            by_day[fa.local_date(r["timestamp"])].append((r, d))

    alerts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in history.events_since(since, participant_code):
        if e.get("notified") == 0:      # línea base: no se mostró
            continue
        alerts[fa.local_date(e["timestamp"])][e["alert_type"]] += 1

    out = []
    for day in sorted(by_day, reverse=True):
        pairs = sorted(by_day[day], key=lambda p: p[0]["timestamp"])
        rows = [p[0] for p in pairs]
        durs = [p[1] for p in pairs]
        ex = fa.exposure(rows, durs, cfg)
        br = fa.breaks_and_blocks(rows, durs, cfg)
        if ex["minutos"] < 1.0:
            continue
        pct_dev = ex.get("pct_desviado")
        day_alerts = alerts.get(day, {})
        out.append({
            "fecha": day,
            "minutos": ex["minutos"],
            "pct_buena_postura": None if pct_dev is None else 100.0 - pct_dev,
            "delta_theta_media": ex.get("delta_theta_media"),
            "pausas_por_hora": br["pausas_por_hora"],
            "tramo_max_sin_pausa_min": br["tramo_max_sin_pausa_min"],
            "avisos": sum(day_alerts.values()),
            "avisos_postura": (day_alerts.get("cervical_angle", 0)
                               + day_alerts.get("shoulder_asymmetry", 0)),
            "avisos_inactividad": day_alerts.get("sedentary", 0),
            "perclos_medio": ex.get("perclos_medio"),
        })
    return out


def week_comparison(rows: List[dict]) -> Optional[dict]:
    """
    Media de % de buena postura de los 7 días más recientes con uso frente a
    los 7 anteriores. None si no hay datos suficientes para comparar.
    """
    vals = [r["pct_buena_postura"] for r in rows
            if r.get("pct_buena_postura") is not None]
    if len(vals) < 4:
        return None
    recent, older = vals[:7], vals[7:14]
    if not older:
        return None
    a = sum(recent) / len(recent)
    b = sum(older) / len(older)
    return {"reciente": a, "anterior": b, "cambio": a - b}
