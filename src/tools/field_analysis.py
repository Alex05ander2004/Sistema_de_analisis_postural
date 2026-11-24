"""
field_analysis.py
-----------------
Indicadores del estudio de campo (uso diario durante ~15 días), calculados a
partir de metrics_log (una fila por segundo), events y self_reports.

Lo usa src/tools/export_study.py; aquí solo hay funciones puras, testeables.

Qué se calcula y por qué
------------------------
Las alertas solas dicen poco: con un enfriamiento de 30 s, el número de
alertas por hora tiene un techo y depende de la configuración. Lo que un
especialista necesita es la EXPOSICIÓN, como en los métodos ergonómicos de
observación (RULA, REBA): cuánto tiempo, cuánta flexión y en tramos de
cuánta duración. Por día y participante:

  Exposición cervical   minutos y % del tiempo en cada banda de Δθc (desviación
                        respecto a la postura neutra calibrada ese día),
                        % del tiempo por encima del umbral, episodio más
                        largo y episodios sostenidos por hora.
  Hombros               % del tiempo con asimetría > umbral y lado habitual.
  Inclinación lateral   mediana con signo (patrón habitual hacia un lado).
  Distancia             mediana y % del tiempo más cerca que el mínimo.
  Fatiga                PERCLOS medio y p95, minutos con PERCLOS alto,
                        bostezos detectados.
  Pausas activas        nº de ausencias ≥ 60 s, tiempo ausente, y el tramo
                        más largo sentado sin pausa (inactividad física).
  Deterioro             primera hora frente a última hora de la jornada.
  Autorreporte          dolor de cuello, cansancio visual y somnolencia (0–10).

Ponderación por tiempo
----------------------
Cada fila pesa lo que dura (hasta la siguiente fila), con un máximo de
`max_gap_sec`: un hueco mayor es una pausa con la cámara apagada y no cuenta
como tiempo medido. Así los indicadores no dependen de la cadencia de
registro (las bases antiguas registraban cada 30 frames, no cada segundo).

Limitación a declarar
---------------------
Δθc es RELATIVO a la postura neutra de cada día, no el ángulo craneovertebral
clínico (la cámara frontal no lo permite). Las bandas describen cuánto se
aleja la persona de su propia postura erguida, no un diagnóstico.

Autor: Tesis Huisa Perez, UNSA 2026
"""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean, median
from typing import Dict, List, Optional, Sequence, Tuple

FIELD_PHASES = ("linea_base", "intervencion", "control")

# Filas registradas a menos de estos FPS no son mediciones: la cámara estaba
# compartida con otra app (videollamada) y entregaba ~1 frame/s sin imagen
# útil (prueba con Google Meet, 2026-10-29). Se descartan como "sin datos";
# si se contaran, parecerían ausencias de la persona (pausas activas falsas).
MIN_VALID_FPS = 3.0


@dataclass
class FieldConfig:
    dev_threshold: float = 15.0          # postural.cervical_deviation_max_deg
    shoulder_threshold: float = 10.0     # postural.shoulder_asymmetry_max_deg
    perclos_threshold: float = 0.15      # fatigue.perclos_threshold
    min_distance_m: float = 0.50         # camera.min_distance_m
    cervical_window_sec: float = 5.0     # postural.cervical_alert_window_sec
    bands: Tuple[float, ...] = (10.0, 20.0, 30.0)
    max_gap_sec: float = 5.0             # hueco mayor = sin datos (pausa)
    break_min_sec: float = 60.0          # ausencia mínima para contar pausa
    block_gap_sec: float = 300.0         # pausa breve que no rompe un bloque
    valid_day_min_minutes: float = 120.0
    valid_day_min_block_minutes: float = 60.0
    baseline_days: int = 5               # study.field.baseline_days
    baseline_min_minutes: float = 120.0  # study.field.baseline_min_minutes
    min_valid_days_baseline: int = 3
    min_valid_days_intervention: int = 6

    @classmethod
    def from_thresholds(cls, cfg: dict) -> "FieldConfig":
        post = cfg.get("postural", {})
        fat = cfg.get("fatigue", {})
        cam = cfg.get("camera", {})
        fld = cfg.get("study", {}).get("field", {})
        return cls(
            dev_threshold=float(post.get("cervical_deviation_max_deg", 15.0)),
            shoulder_threshold=float(post.get("shoulder_asymmetry_max_deg", 10.0)),
            perclos_threshold=float(fat.get("perclos_threshold", 0.15)),
            min_distance_m=float(cam.get("min_distance_m", 0.50)),
            cervical_window_sec=float(post.get("cervical_alert_window_sec", 5.0)),
            break_min_sec=float(cfg.get("sedentary", {}).get("break_min_sec", 60.0)),
            valid_day_min_minutes=float(fld.get("valid_day_min_minutes", 120)),
            baseline_days=int(fld.get("baseline_days", 5)),
            baseline_min_minutes=float(fld.get("baseline_min_minutes", 120)),
            valid_day_min_block_minutes=float(
                fld.get("valid_day_min_block_minutes", 60)),
            min_valid_days_baseline=int(
                fld.get("analysis_min_valid_days_baseline", 3)),
            min_valid_days_intervention=int(
                fld.get("analysis_min_valid_days_intervention", 6)),
        )


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def local_date(ts: float) -> str:
    return datetime.fromtimestamp(ts).date().isoformat()


def local_hour(ts: float) -> int:
    return datetime.fromtimestamp(ts).hour


def _r(v, nd=2):
    return "" if v is None else round(v, nd)


def _wmean(pairs) -> Optional[float]:
    """Media ponderada de (valor, peso)."""
    tot = sum(w for _, w in pairs)
    return None if tot <= 0 else sum(v * w for v, w in pairs) / tot


def _percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def row_durations(rows: Sequence[dict], max_gap: float = 5.0,
                  default: float = 1.0) -> List[float]:
    """
    Duración de cada fila (hasta la siguiente), acotada a `max_gap`.

    Las filas deben ser de UNA sesión y estar ordenadas. La última fila dura
    la cadencia típica de la sesión.
    """
    ts = [r["timestamp"] for r in rows]
    diffs = [b - a for a, b in zip(ts, ts[1:])]
    typical = median([d for d in diffs if 0 < d <= max_gap] or [default])
    out = [d if 0 < d <= max_gap else 0.0 for d in diffs]
    out.append(typical if rows else 0.0)
    return out


def _present(r: dict) -> bool:
    return bool(r.get("pose_detected")) or bool(r.get("face_detected"))


def _valid_dev(r: dict) -> Optional[float]:
    if r.get("pose_valid") == 0:
        return None
    return r.get("cervical_deviation")


def band_labels(bands: Sequence[float]) -> List[str]:
    edges = list(bands)
    labels = [f"menos_{edges[0]:g}"]
    labels += [f"{a:g}_{b:g}" for a, b in zip(edges, edges[1:])]
    labels.append(f"{edges[-1]:g}_o_mas")
    return labels


def _band_index(dev: float, bands: Sequence[float]) -> int:
    return bisect_right(list(bands), dev)


def correction_after(event_ts: float, limit: float,
                     ts: Sequence[float], rows: Sequence[dict],
                     max_gap: Optional[float] = None) -> Optional[float]:
    """
    Segundos desde event_ts hasta la primera fila con Δθc <= limit.

    Con `max_gap`, un hueco mayor entre filas (pausa, fin de la sesión)
    corta la búsqueda: no se sabe qué hizo la persona mientras tanto, y sin
    el corte una alerta del final del día se "corregía" a la mañana siguiente.
    """
    i = bisect_right(ts, event_ts)
    prev = event_ts
    for r in rows[i:]:
        if max_gap is not None and r["timestamp"] - prev > max_gap:
            return None
        prev = r["timestamp"]
        dev = r.get("cervical_deviation")
        if dev is not None and dev <= limit:
            return r["timestamp"] - event_ts
    return None


# ---------------------------------------------------------------------------
# Indicadores de un conjunto de filas (un día, una hora…)
# ---------------------------------------------------------------------------

def exposure(rows: Sequence[dict], durs: Sequence[float],
             cfg: FieldConfig) -> dict:
    """Indicadores ponderados por tiempo sobre filas ya ordenadas."""
    total = sum(durs)
    labels = band_labels(cfg.bands)
    band_t = [0.0] * len(labels)
    valid_t = over_t = 0.0
    dev_pairs, sh_pairs, lat_vals, sh_vals = [], [], [], []
    sh_over_t = sh_t = 0.0
    dist_vals, close_t, dist_t = [], 0.0, 0.0
    perclos_pairs, perclos_vals, perclos_high_t = [], [], 0.0
    present_t = 0.0

    # Episodios por encima del umbral cervical
    episodes: List[float] = []
    cur = 0.0
    prev_ts = None

    for r, d in zip(rows, durs):
        if prev_ts is not None and r["timestamp"] - prev_ts > cfg.max_gap_sec:
            if cur > 0:
                episodes.append(cur)
            cur = 0.0
        prev_ts = r["timestamp"]

        if _present(r):
            present_t += d
        dev = _valid_dev(r)
        if dev is not None:
            valid_t += d
            band_t[_band_index(dev, cfg.bands)] += d
            dev_pairs.append((dev, d))
            if dev > cfg.dev_threshold:
                over_t += d
                cur += d
            elif cur > 0:
                episodes.append(cur)
                cur = 0.0
            lat = r.get("cervical_lateral")
            if lat is not None:
                lat_vals.append(lat)
            sh = r.get("shoulder_asymmetry")
            if sh is not None:
                sh_t += d
                sh_vals.append(sh)
                sh_pairs.append((abs(sh), d))
                if abs(sh) > cfg.shoulder_threshold:
                    sh_over_t += d
        dist = r.get("distance_m")
        if dist is not None and r.get("pose_detected"):
            dist_vals.append(dist)
            dist_t += d
            if dist < cfg.min_distance_m:
                close_t += d
        if r.get("face_detected") and r.get("perclos") is not None:
            p = r["perclos"]
            perclos_pairs.append((p, d))
            perclos_vals.append(p)
            if p >= cfg.perclos_threshold:
                perclos_high_t += d
    if cur > 0:
        episodes.append(cur)

    hours = total / 3600.0
    sustained = [e for e in episodes if e >= cfg.cervical_window_sec]
    sh_med = median(sh_vals) if sh_vals else None
    out = {
        "minutos": total / 60.0,
        "pct_presente": None if total <= 0 else 100 * present_t / total,
        "pct_postura_medible": None if total <= 0 else 100 * valid_t / total,
        "delta_theta_media": _wmean(dev_pairs),
        "delta_theta_p95": _percentile([v for v, _ in dev_pairs], 0.95),
        "pct_desviado": None if valid_t <= 0 else 100 * over_t / valid_t,
        "episodio_max_s": max(episodes) if episodes else 0.0,
        "episodios_sostenidos": len(sustained),
        "episodios_sostenidos_por_hora": (len(sustained) / hours
                                          if hours > 0 else None),
        "pct_hombros_asimetricos": (None if sh_t <= 0
                                    else 100 * sh_over_t / sh_t),
        "delta_e_mediana": sh_med,
        "hombro_mas_bajo": (None if sh_med is None else
                            "nivelados" if abs(sh_med) < 3 else
                            "izquierdo" if sh_med > 0 else "derecho"),
        "inclinacion_lateral_mediana": median(lat_vals) if lat_vals else None,
        "distancia_mediana_m": median(dist_vals) if dist_vals else None,
        "pct_demasiado_cerca": (None if dist_t <= 0
                                else 100 * close_t / dist_t),
        "perclos_medio": _wmean(perclos_pairs),
        "perclos_p95": _percentile(perclos_vals, 0.95),
        "min_perclos_alto": perclos_high_t / 60.0,
    }
    for lab, t in zip(labels, band_t):
        out[f"min_banda_{lab}"] = t / 60.0
        out[f"pct_banda_{lab}"] = None if valid_t <= 0 else 100 * t / valid_t
    return out


def breaks_and_blocks(rows: Sequence[dict], durs: Sequence[float],
                      cfg: FieldConfig) -> dict:
    """
    Pausas activas e inactividad.

    - Pausa: ausencia (ni cuerpo ni cara) de al menos break_min_sec.
    - Tramo sentado: tiempo presente entre dos pausas; un hueco sin datos de
      más de block_gap_sec también lo corta (no se sabe qué pasó).
    - Bloque de seguimiento: tiempo medido sin huecos > block_gap_sec,
      esté o no presente la persona (para el criterio de día válido).
    """
    n_breaks, break_t = 0, 0.0
    absent_run = 0.0
    sit, sit_max = 0.0, 0.0
    block, block_max = 0.0, 0.0
    prev_ts = None
    for r, d in zip(rows, durs):
        if prev_ts is not None:
            gap = r["timestamp"] - prev_ts
            if gap > cfg.block_gap_sec:
                block_max = max(block_max, block)
                block = 0.0
                sit_max = max(sit_max, sit)
                sit = 0.0
                absent_run = 0.0
        prev_ts = r["timestamp"]
        block += d
        if _present(r):
            if absent_run >= cfg.break_min_sec:
                n_breaks += 1
                break_t += absent_run
                sit_max = max(sit_max, sit)
                sit = 0.0
            absent_run = 0.0
            sit += d
        else:
            absent_run += d
    if absent_run >= cfg.break_min_sec:
        n_breaks += 1
        break_t += absent_run
    sit_max = max(sit_max, sit)
    block_max = max(block_max, block)
    total_h = sum(durs) / 3600.0
    return {
        "pausas": n_breaks,
        "pausas_por_hora": n_breaks / total_h if total_h > 0 else None,
        "min_en_pausa": break_t / 60.0,
        "tramo_max_sin_pausa_min": sit_max / 60.0,
        "bloque_continuo_max_min": block_max / 60.0,
    }


def first_vs_last_hour(rows: Sequence[dict], durs: Sequence[float],
                       cfg: FieldConfig, min_minutes: float = 120.0) -> dict:
    """Δθc, % desviado y PERCLOS en la primera y la última hora medidas."""
    keys = ("delta_theta_media", "pct_desviado", "perclos_medio")
    empty = {f"{k}_{h}": None for k in keys for h in ("1a_hora", "ult_hora")}
    if sum(durs) < min_minutes * 60:
        return empty
    acc, first_end = 0.0, 0
    for i, d in enumerate(durs):
        acc += d
        if acc >= 3600:
            first_end = i + 1
            break
    acc, last_start = 0.0, len(durs)
    for i in range(len(durs) - 1, -1, -1):
        acc += durs[i]
        if acc >= 3600:
            last_start = i
            break
    a = exposure(rows[:first_end], durs[:first_end], cfg)
    b = exposure(rows[last_start:], durs[last_start:], cfg)
    out = {}
    for k in keys:
        out[f"{k}_1a_hora"] = a[k]
        out[f"{k}_ult_hora"] = b[k]
    return out


# ---------------------------------------------------------------------------
# Agregación por participante y día
# ---------------------------------------------------------------------------

@dataclass
class ParticipantData:
    code: str
    rows: List[dict] = field(default_factory=list)     # con _phase, _dur
    events: List[dict] = field(default_factory=list)   # con _phase
    suppressed: List[dict] = field(default_factory=list)  # en enfriamiento
    reports: List[dict] = field(default_factory=list)
    versions: set = field(default_factory=set)
    hashes: set = field(default_factory=set)


def collect_field_data(sessions: Sequence[dict],
                       reports_by_code: Dict[str, List[dict]],
                       cfg: FieldConfig) -> Dict[str, ParticipantData]:
    """Agrupa por participante las filas y eventos de las sesiones de campo."""
    out: Dict[str, ParticipantData] = {}
    for ses in sessions:
        phase = ses.get("fase") or ""
        if phase not in FIELD_PHASES:
            continue
        pd = out.setdefault(ses["participante"],
                            ParticipantData(ses["participante"]))
        rows = [r for r in ses["rows"]
                if r.get("fps") is None or r["fps"] == 0
                or r["fps"] >= MIN_VALID_FPS]
        durs = row_durations(rows, cfg.max_gap_sec)
        # Se anotan en la propia fila, sin copiarla: con cientos de miles de
        # filas por participante, la copia duplicaba la memoria.
        for r, d in zip(rows, durs):
            r["_phase"], r["_dur"] = phase, d
            pd.rows.append(r)
        for e in ses["events"]:
            e["_phase"] = phase
            pd.events.append(e)
        for e in ses.get("suppressed", []):
            e["_phase"] = phase
            pd.suppressed.append(e)
        if ses.get("version"):
            pd.versions.add(ses["version"])
        if ses.get("config_hash"):
            pd.hashes.add(ses["config_hash"])
    for code, pd in out.items():
        pd.rows.sort(key=lambda r: r["timestamp"])
        pd.events.sort(key=lambda e: e["timestamp"])
        pd.reports = reports_by_code.get(code, [])
    return out


def _latest_report(reports: Sequence[dict], day: str) -> Optional[dict]:
    mine = [r for r in reports if r["report_date"] == day]
    if not mine:
        return None
    answered = [r for r in mine if not r.get("skipped")]
    pool = answered or mine
    return max(pool, key=lambda r: r["created_at"])


def daily_summary(pd: ParticipantData, cfg: FieldConfig) -> List[dict]:
    """Una fila por día de uso del participante."""
    by_day: Dict[str, List[dict]] = defaultdict(list)
    for r in pd.rows:
        by_day[local_date(r["timestamp"])].append(r)
    ev_by_day: Dict[str, List[dict]] = defaultdict(list)
    for e in pd.events:
        ev_by_day[local_date(e["timestamp"])].append(e)
    sup_by_day: Dict[str, int] = defaultdict(int)
    for e in pd.suppressed:
        sup_by_day[local_date(e["timestamp"])] += 1

    out = []
    for n, day in enumerate(sorted(by_day), start=1):
        rows = by_day[day]
        durs = [r["_dur"] for r in rows]
        phases = defaultdict(float)
        for r in rows:
            phases[r["_phase"]] += r["_dur"]
        phase = max(phases, key=phases.get)

        ex = exposure(rows, durs, cfg)
        br = breaks_and_blocks(rows, durs, cfg)
        fl = first_vs_last_hour(rows, durs, cfg)
        evs = ev_by_day.get(day, [])
        hours = ex["minutos"] / 60.0
        by_type = defaultdict(int)
        for e in evs:
            by_type[e["alert_type"]] += 1
        notified = sum(1 for e in evs if e.get("notified") in (1, None))

        rep = _latest_report(pd.reports, day)

        valid = (ex["minutos"] >= cfg.valid_day_min_minutes
                 and br["bloque_continuo_max_min"]
                 >= cfg.valid_day_min_block_minutes)
        row = {
            "participante": pd.code, "fecha": day, "dia_uso": n,
            "fase": phase,
            "fase_mixta": "si" if len(phases) > 1 else "no",
            "dia_valido": "si" if valid else "no",
        }
        row.update(ex)
        row.update(br)
        row.update({
            "alertas_detectadas": len(evs),
            # Episodios confirmados durante el enfriamiento: con ellos se
            # cuentan TODAS las detecciones, no solo las que se avisaron.
            "detecciones_en_enfriamiento": sup_by_day.get(day, 0),
            "alertas_mostradas": notified,
            "alertas_detectadas_por_hora": (len(evs) / hours
                                            if hours > 0 else None),
            "alertas_cervical": by_type["cervical_angle"],
            "alertas_hombros": by_type["shoulder_asymmetry"],
            "alertas_ojo": by_type["eye_fatigue"],
            "alertas_bostezo": by_type["yawn"],
            "alertas_somnolencia": by_type["drowsiness"],
            "alertas_inactividad": by_type["sedentary"],
        })
        row.update(fl)
        row.update({
            "dolor_cuello": rep.get("neck_pain") if rep else None,
            "cansancio_visual": rep.get("eye_strain") if rep else None,
            "somnolencia": rep.get("sleepiness") if rep else None,
            "autorreporte": ("" if not rep else "omitido" if rep.get("skipped")
                             else "retrospectivo" if rep.get("retrospective")
                             else "si"),
        })
        out.append(row)
    return out


def hourly_profile(pd: ParticipantData, cfg: FieldConfig) -> List[dict]:
    """Por fase y hora del día: minutos, Δθc, % desviado y PERCLOS."""
    groups: Dict[Tuple[str, int], List[dict]] = defaultdict(list)
    for r in pd.rows:
        groups[(r["_phase"], local_hour(r["timestamp"]))].append(r)
    out = []
    for (phase, hour) in sorted(groups):
        rows = groups[(phase, hour)]
        ex = exposure(rows, [r["_dur"] for r in rows], cfg)
        out.append({
            "participante": pd.code, "fase": phase, "hora": hour,
            "minutos": ex["minutos"],
            "delta_theta_media": ex["delta_theta_media"],
            "pct_desviado": ex["pct_desviado"],
            "perclos_medio": ex["perclos_medio"],
        })
    return out


# Indicadores que se comparan línea base frente a intervención.
COMPARED = (
    ("pct_desviado", "% del tiempo con Δθc > umbral"),
    ("delta_theta_media", "Δθc medio (°)"),
    ("episodio_max_s", "Episodio más largo desviado (s)"),
    ("episodios_sostenidos_por_hora", "Episodios sostenidos por hora"),
    ("alertas_detectadas_por_hora", "Alertas detectadas por hora"),
    ("pct_hombros_asimetricos", "% del tiempo con hombros asimétricos"),
    ("pct_demasiado_cerca", "% del tiempo demasiado cerca"),
    ("perclos_medio", "PERCLOS medio"),
    ("pausas_por_hora", "Pausas activas por hora"),
    ("tramo_max_sin_pausa_min", "Tramo más largo sin pausa (min)"),
    ("dolor_cuello", "Dolor de cuello (0–10)"),
    ("cansancio_visual", "Cansancio visual (0–10)"),
    ("somnolencia", "Somnolencia (0–10)"),
)


# Para estos indicadores, BAJAR es mejorar (el resto: subir es mejorar).
LOWER_IS_BETTER = {
    "pct_desviado", "delta_theta_media", "episodio_max_s",
    "episodios_sostenidos_por_hora", "alertas_detectadas_por_hora",
    "pct_hombros_asimetricos", "pct_demasiado_cerca", "perclos_medio",
    "tramo_max_sin_pausa_min", "dolor_cuello",
    "cansancio_visual", "somnolencia",
}


# Indicadores con tamaño del efecto NAP: (clave, ¿menor es mejor?).
NAP_INDICATORS = (
    ("pct_desviado", True),
    ("pausas_por_hora", False),
)


def nap(base: Sequence[float], interv: Sequence[float],
        lower_is_better: bool = True) -> Optional[float]:
    """
    Non-overlap of All Pairs (Parker y Vannest, 2009): proporción de pares
    (día de línea base, día de intervención) en que la intervención es
    mejor; los empates cuentan 0.5. 0.5 = sin efecto; 1 = todos mejoran.
    Orientación: 0.50–0.65 débil, 0.66–0.92 medio, 0.93–1 fuerte.
    """
    if not base or not interv:
        return None
    score = 0.0
    for b in base:
        for i in interv:
            if i == b:
                score += 0.5
            elif (i < b) == lower_is_better:
                score += 1.0
    return score / (len(base) * len(interv))


def control_cut_day(days: Sequence[dict], cfg: FieldConfig) -> Optional[int]:
    """
    Día de uso en que empieza el periodo «después» de un participante de
    control: el primero tras completar `baseline_days` días con al menos
    `baseline_min_minutes`. Es la MISMA regla con la que la app empieza los
    avisos del grupo de intervención (field_study.decide_phase), para que
    los dos grupos tengan la misma línea base.
    """
    qualified = 0
    for d in sorted(days, key=lambda d: d["dia_uso"]):
        if qualified >= cfg.baseline_days:
            return d["dia_uso"]
        if (d.get("minutos") or 0) >= cfg.baseline_min_minutes:
            qualified += 1
    return None


def participant_summary(pd: ParticipantData, days: Sequence[dict],
                        cfg: FieldConfig, planned_days: int = 15) -> dict:
    """
    Una fila por participante, lista para una prueba pareada (Wilcoxon o t
    pareada): media de los días VÁLIDOS de cada fase y su diferencia.
    Columnas _base = antes (línea base), _interv = después (con avisos en el
    grupo de intervención; sin avisos en el control).
    """
    valid = [d for d in days if d["dia_valido"] == "si"]
    is_control = any(d["fase"] == "control" for d in days)
    if is_control:
        # El control nunca recibe avisos: su «después» empieza cuando
        # completa la misma línea base que el grupo de intervención.
        cut = control_cut_day(days, cfg)
        base = [d for d in valid if cut is None or d["dia_uso"] < cut]
        interv = [d for d in valid if cut is not None and d["dia_uso"] >= cut]
    else:
        base = [d for d in valid if d["fase"] == "linea_base"]
        interv = [d for d in valid if d["fase"] == "intervencion"]
    ok = (len(base) >= cfg.min_valid_days_baseline
          and len(interv) >= cfg.min_valid_days_intervention)
    first_interv = next((d["dia_uso"] for d in days
                         if d["fase"] == "intervencion"), None)
    post_start = cut if is_control else first_interv
    reports = [d for d in days if d["autorreporte"] in ("si", "retrospectivo")]
    out = {
        "participante": pd.code,
        "grupo": "control" if is_control else "intervencion",
        "dia_inicio_avisos": first_interv,
        "dia_inicio_despues": post_start,
        "dias_uso": len(days),
        "dias_validos": len(valid),
        "dias_validos_linea_base": len(base),
        "dias_validos_intervencion": len(interv),
        "valido_para_analisis": "si" if ok else "no",
        "dias_con_autorreporte": len(reports),
        "minutos_totales": sum(d["minutos"] for d in days),
        "minutos_medios_por_dia": (mean(d["minutos"] for d in days)
                                   if days else None),
        "adherencia_pct": 100 * len(valid) / planned_days if planned_days else None,
        "versiones_app": ";".join(sorted(pd.versions)),
        "configuraciones": ";".join(sorted(pd.hashes)),
        "configuracion_unica": "si" if len(pd.hashes) <= 1 else "NO",
    }
    for key, _ in COMPARED:
        b = [d[key] for d in base if isinstance(d.get(key), (int, float))]
        i = [d[key] for d in interv if isinstance(d.get(key), (int, float))]
        mb = mean(b) if b else None
        mi = mean(i) if i else None
        out[f"{key}_base"] = mb
        out[f"{key}_interv"] = mi
        out[f"{key}_cambio"] = (mi - mb) if (mb is not None
                                             and mi is not None) else None
    for key, lower in NAP_INDICATORS:
        b = [d[key] for d in base if isinstance(d.get(key), (int, float))]
        i = [d[key] for d in interv if isinstance(d.get(key), (int, float))]
        out[f"nap_{key}"] = nap(b, i, lower)
    return out


def _paired_rank_biserial(diffs: Sequence[float]) -> Optional[float]:
    """r de rangos biserial de Wilcoxon: (W+ - W-) / (W+ + W-) sobre las
    diferencias no nulas; -1 = todos bajan, +1 = todos suben."""
    nz = [d for d in diffs if abs(d) > 1e-12]
    if not nz:
        return None
    ranked = sorted(nz, key=abs)
    ranks, k = {}, 0
    while k < len(ranked):                  # rangos medios en empates
        j = k
        while j + 1 < len(ranked) and abs(ranked[j + 1]) == abs(ranked[k]):
            j += 1
        for m in range(k, j + 1):
            ranks.setdefault(abs(ranked[m]), (k + j + 2) / 2)
        k = j + 1
    wp = sum(ranks[abs(d)] for d in nz if d > 0)
    wn = sum(ranks[abs(d)] for d in nz if d < 0)
    return (wp - wn) / (wp + wn)


def _cliff_delta(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """r de rangos biserial de Mann-Whitney (= delta de Cliff):
    (pares a > b - pares a < b) / (na * nb). -1 = todo `a` por debajo de
    todo `b`; 0 = sin diferencia."""
    if not a or not b:
        return None
    gt = sum(1 for x in a for y in b if x > y)
    lt = sum(1 for x in a for y in b if x < y)
    return (gt - lt) / (len(a) * len(b))


TEST_WITHIN_INTERV = "wilcoxon_intervencion"
TEST_WITHIN_CONTROL = "wilcoxon_control"
TEST_BETWEEN = "mann_whitney_cambio"
TEST_NAMES = {
    TEST_WITHIN_INTERV: "Wilcoxon: grupo con avisos, antes vs después",
    TEST_WITHIN_CONTROL: "Wilcoxon: grupo control, antes vs después",
    TEST_BETWEEN: "Mann-Whitney: cambio con avisos vs cambio del control",
}


def group_tests(summaries: Sequence[dict]) -> List[dict]:
    """
    Diseño con preprueba, posprueba y grupo de control.
    Con los participantes válidos para el análisis, por indicador:

      1. Wilcoxon de rangos con signo en el grupo de intervención: ¿cambia
         del antes (línea base) al después (con avisos)?
      2. Lo mismo en el grupo de control (después = sin avisos): ¿cambia
         solo con el tiempo o por saberse observado?
      3. PRUEBA PRINCIPAL. U de Mann-Whitney sobre el cambio (después -
         antes) de cada persona: ¿cambia más quien recibe avisos que quien
         no? Comparar el cambio, y no solo el «después», descuenta las
         diferencias entre personas que ya había antes de empezar.

    p_una_cola va en la dirección de la hipótesis (mejorar: bajar, salvo en
    los indicadores en que subir es mejor) y debe declararse antes de ver
    los datos; también se da p_dos_colas. Tamaño del efecto: r de rangos
    biserial (-1 a 1; negativo = el primer grupo/periodo queda más bajo).
    Las pruebas son exactas con muestras pequeñas y se indica cuando hay
    demasiado pocas personas para poder llegar a p < 0.05.
    """
    try:
        from scipy.stats import mannwhitneyu, wilcoxon
    except ImportError:            # pragma: no cover - solo sin scipy
        mannwhitneyu = wilcoxon = None
    from math import comb

    valid = [s for s in summaries if s.get("valido_para_analisis") == "si"]
    groups = {
        "intervencion": [s for s in valid if s.get("grupo") != "control"],
        "control": [s for s in valid if s.get("grupo") == "control"],
    }
    out = []
    for key, label in COMPARED:
        lower = key in LOWER_IS_BETTER
        alt = "less" if lower else "greater"
        pairs = {}
        for g, members in groups.items():
            pairs[g] = [(s[f"{key}_base"], s[f"{key}_interv"])
                        for s in members
                        if isinstance(s.get(f"{key}_base"), (int, float))
                        and isinstance(s.get(f"{key}_interv"), (int, float))]
        changes = {g: [i - b for b, i in p] for g, p in pairs.items()}

        def row(test):
            return {"indicador": key, "descripcion": label,
                    "prueba": test, "nombre_prueba": TEST_NAMES[test],
                    "mejora_si": "baja" if lower else "sube",
                    "n_intervencion": None, "n_control": None,
                    "mediana_antes": None, "mediana_despues": None,
                    "mediana_cambio_intervencion": None,
                    "mediana_cambio_control": None,
                    "estadistico": None, "p_una_cola": None,
                    "p_dos_colas": None, "r_biserial": None, "nota": ""}

        # 1 y 2: dentro de cada grupo
        for g, test in (("intervencion", TEST_WITHIN_INTERV),
                        ("control", TEST_WITHIN_CONTROL)):
            p, d = pairs[g], changes[g]
            r = row(test)
            r[f"n_{g}"] = len(p)
            if p:
                r["mediana_antes"] = median(b for b, _ in p)
                r["mediana_despues"] = median(i for _, i in p)
                r[f"mediana_cambio_{g}"] = median(d)
            r["r_biserial"] = _paired_rank_biserial(d)
            if wilcoxon is not None and any(abs(x) > 1e-12 for x in d):
                try:
                    one = wilcoxon(d, zero_method="wilcox", alternative=alt)
                    two = wilcoxon(d, zero_method="wilcox")
                    r["estadistico"] = float(two.statistic)
                    r["p_una_cola"] = float(one.pvalue)
                    r["p_dos_colas"] = float(two.pvalue)
                except ValueError as exc:
                    r["nota"] = str(exc)
            if len(p) < 6:                  # exacta: mínimo 1/2^n
                r["nota"] = (r["nota"] + " " if r["nota"] else "") + (
                    "menos de 5 personas: Wilcoxon no puede dar p < 0.05"
                    if len(p) < 5 else
                    "5 personas: solo la prueba de una cola puede dar "
                    "p < 0.05")
            out.append(r)

        # 3: cambio de un grupo frente al del otro
        ci, cc = changes["intervencion"], changes["control"]
        r = row(TEST_BETWEEN)
        r["n_intervencion"], r["n_control"] = len(ci), len(cc)
        r["mediana_cambio_intervencion"] = median(ci) if ci else None
        r["mediana_cambio_control"] = median(cc) if cc else None
        r["r_biserial"] = _cliff_delta(ci, cc)
        if mannwhitneyu is not None and ci and cc:
            try:
                # Exacta con muestras pequeñas sin empates (con empates o
                # muchos datos, scipy elige la aproximación adecuada).
                method = ("exact" if len(set(ci + cc)) == len(ci + cc)
                          and len(ci) + len(cc) <= 40 else "auto")
                one = mannwhitneyu(ci, cc, alternative=alt, method=method)
                two = mannwhitneyu(ci, cc, alternative="two-sided",
                                   method=method)
                r["estadistico"] = float(two.statistic)
                r["p_una_cola"] = float(one.pvalue)
                r["p_dos_colas"] = float(two.pvalue)
            except ValueError as exc:
                r["nota"] = str(exc)
        if not ci or not cc or 1 / comb(len(ci) + len(cc), len(ci)) > 0.05:
            r["nota"] = (r["nota"] + " " if r["nota"] else "") + (
                "muy pocas personas en algún grupo: Mann-Whitney no puede "
                "dar p < 0.05")
        out.append(r)
    return out


def round_row(row: dict, nd: int = 2) -> dict:
    return {k: (round(v, nd) if isinstance(v, float) else
                "" if v is None else v) for k, v in row.items()}
