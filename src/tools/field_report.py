"""
field_report.py
---------------
Reporte HTML por participante para el especialista (fisioterapeuta,
ergónomo, médico ocupacional): un solo archivo, sin dependencias ni
conexión a internet, que se abre en cualquier navegador y se imprime a PDF.

Contenido
---------
  1. Datos del seguimiento: días de uso, días válidos, adherencia.
  2. Línea base frente a intervención para los indicadores principales.
  3. Gráficas por día: % del tiempo desviado, minutos de uso, autorreporte.
  4. Perfil por hora del día (a qué hora empieza a deteriorarse).
  5. Tabla diaria completa.
  6. Cómo leer los datos y sus límites (NO es un diagnóstico).

Autor: Tesis Huisa Perez, UNSA 2026
"""

import html
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from src.tools.field_analysis import (COMPARED, LOWER_IS_BETTER,
                                     TEST_BETWEEN, TEST_WITHIN_CONTROL,
                                     TEST_WITHIN_INTERV, band_labels)

BASE_COLOR = "#7A8BA6"
INTERV_COLOR = "#0E8F8A"
CONTROL_COLOR = "#C27C0E"      # control, periodo «después» (sin avisos)
PHASE_NAME = {"linea_base": "Línea base", "intervencion": "Intervención",
              "control": "Control"}


def _fmt(v, nd=1) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return html.escape(str(v))


def _bar_chart(title: str, labels: Sequence[str],
               values: Sequence[Optional[float]],
               colors: Sequence[str], unit: str = "",
               faded: Optional[Sequence[bool]] = None,
               ref_line: Optional[float] = None,
               ref_label: str = "") -> str:
    """Barras verticales en SVG; `faded` atenúa (p. ej. días no válidos)."""
    w, h, pad_l, pad_b, pad_t = 760, 220, 44, 34, 24
    n = max(1, len(values))
    nums = [v for v in values if isinstance(v, (int, float))]
    top = max(nums + ([ref_line] if ref_line else []) + [1e-9])
    top *= 1.15
    bw = (w - pad_l - 10) / n
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="{html.escape(title)}">']
    # Eje y: 0, mitad y máximo
    for frac in (0, 0.5, 1):
        y = h - pad_b - frac * (h - pad_b - pad_t)
        parts.append(f'<line x1="{pad_l}" x2="{w - 10}" y1="{y:.1f}" '
                     f'y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{y + 4:.1f}" '
                     f'text-anchor="end" class="tick">'
                     f'{top / 1.15 * frac:.0f}{unit}</text>')
    for i, v in enumerate(values):
        x = pad_l + i * bw
        if isinstance(v, (int, float)):
            bh = (v / top) * (h - pad_b - pad_t)
            op = 0.35 if faded and faded[i] else 1.0
            parts.append(
                f'<rect x="{x + bw * 0.15:.1f}" y="{h - pad_b - bh:.1f}" '
                f'width="{bw * 0.7:.1f}" height="{max(bh, 0.5):.1f}" '
                f'fill="{colors[i]}" fill-opacity="{op}" rx="2">'
                f'<title>{html.escape(labels[i])}: {v:.1f}{unit}</title></rect>')
        if n <= 31:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{h - pad_b + 14}" '
                         f'text-anchor="middle" class="tick">'
                         f'{html.escape(labels[i])}</text>')
    if ref_line:
        y = h - pad_b - (ref_line / top) * (h - pad_b - pad_t)
        parts.append(f'<line x1="{pad_l}" x2="{w - 10}" y1="{y:.1f}" '
                     f'y2="{y:.1f}" class="ref"/>')
        parts.append(f'<text x="{w - 12}" y="{y - 4:.1f}" text-anchor="end" '
                     f'class="tick">{html.escape(ref_label)}</text>')
    parts.append("</svg>")
    return (f'<figure><figcaption>{html.escape(title)}</figcaption>'
            f'{"".join(parts)}</figure>')


def _line_chart(title: str, labels: Sequence[str],
                series: Dict[str, Sequence[Optional[float]]],
                colors: Dict[str, str], ymax: float = 10.0) -> str:
    w, h, pad_l, pad_b, pad_t = 760, 200, 44, 34, 20
    n = max(1, len(labels))
    step = (w - pad_l - 20) / max(1, n - 1)
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="{html.escape(title)}">']
    for frac in (0, 0.5, 1):
        y = h - pad_b - frac * (h - pad_b - pad_t)
        parts.append(f'<line x1="{pad_l}" x2="{w - 10}" y1="{y:.1f}" '
                     f'y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{y + 4:.1f}" '
                     f'text-anchor="end" class="tick">{ymax * frac:.0f}</text>')
    for i, lab in enumerate(labels):
        if n <= 31:
            parts.append(f'<text x="{pad_l + i * step:.1f}" '
                         f'y="{h - pad_b + 14}" text-anchor="middle" '
                         f'class="tick">{html.escape(lab)}</text>')
    legend = []
    for name, vals in series.items():
        pts = [(pad_l + i * step,
                h - pad_b - (v / ymax) * (h - pad_b - pad_t))
               for i, v in enumerate(vals) if isinstance(v, (int, float))]
        if len(pts) > 1:
            d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            parts.append(f'<polyline points="{d}" fill="none" '
                         f'stroke="{colors[name]}" stroke-width="2"/>')
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" '
                         f'fill="{colors[name]}"/>')
        legend.append(f'<span><i style="background:{colors[name]}"></i>'
                      f'{html.escape(name)}</span>')
    parts.append("</svg>")
    return (f'<figure><figcaption>{html.escape(title)}</figcaption>'
            f'{"".join(parts)}<div class="legend">{"".join(legend)}</div>'
            f'</figure>')


_CSS = """
:root { --ink:#1d2433; --muted:#5b6578; --line:#d9dee7; --bg:#ffffff;
        --soft:#f4f6f9; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.5 system-ui, "Segoe UI", sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 32px 20px 60px; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 36px 0 10px; border-bottom: 1px solid var(--line);
     padding-bottom: 6px; }
.sub { color: var(--muted); margin: 0 0 20px; }
.kpis { display:grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
        gap: 10px; }
.kpi { background: var(--soft); border-radius: 8px; padding: 10px 12px; }
.kpi b { display:block; font-size: 22px; }
.kpi span { color: var(--muted); font-size: 13px; }
table { width:100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line);
         text-align: right; }
th:first-child, td:first-child { text-align: left; }
th { background: var(--soft); font-weight: 600; }
.wrap { overflow-x: auto; }
figure { margin: 18px 0; }
figcaption { font-weight: 600; margin-bottom: 4px; }
svg { width: 100%; height: auto; }
svg .grid { stroke: var(--line); stroke-width: 1; }
svg .ref { stroke: #c0392b; stroke-dasharray: 4 3; stroke-width: 1.2; }
svg .tick { fill: var(--muted); font-size: 11px; }
.legend { display:flex; gap: 16px; font-size: 12px; color: var(--muted); }
.legend i { display:inline-block; width:10px; height:10px; border-radius:2px;
            margin-right:5px; vertical-align: -1px; }
.note { background: #fff8e6; border-left: 4px solid #e0a100;
        padding: 10px 14px; border-radius: 4px; font-size: 14px; }
.better { color: #1e7d4f; } .worse { color: #b03a2e; }
@media print { main { padding: 0; } h2 { break-after: avoid; }
               figure, table { break-inside: avoid; } }
"""

_LOWER_IS_BETTER = LOWER_IS_BETTER


def build_report(summary: dict, days: Sequence[dict], hourly: Sequence[dict],
                 dev_threshold: float, bands: Sequence[float]) -> str:
    code = html.escape(summary["participante"])
    labels = [d["fecha"][5:] for d in days]
    cut = summary.get("dia_inicio_despues")
    if summary.get("grupo") == "control":
        colors = [CONTROL_COLOR if cut and d["dia_uso"] >= cut else BASE_COLOR
                  for d in days]
    else:
        colors = [BASE_COLOR if d["fase"] == "linea_base" else INTERV_COLOR
                  for d in days]
    faded = [d["dia_valido"] != "si" for d in days]

    # --- Comparación ----------------------------------------------------
    comp_rows = []
    for key, label in COMPARED:
        b, i, c = (summary.get(f"{key}_base"), summary.get(f"{key}_interv"),
                   summary.get(f"{key}_cambio"))
        cls = ""
        if isinstance(c, (int, float)) and abs(c) > 1e-9:
            good = (c < 0) == (key in _LOWER_IS_BETTER)
            cls = "better" if good else "worse"
        comp_rows.append(f"<tr><td>{html.escape(label)}</td><td>{_fmt(b)}</td>"
                         f"<td>{_fmt(i)}</td><td class='{cls}'>"
                         f"{'+' if isinstance(c, (int, float)) and c > 0 else ''}"
                         f"{_fmt(c)}</td></tr>")

    # --- Bandas de exposición (todas las fases) --------------------------
    blabs = band_labels(bands)
    band_tot = {lab: sum(d.get(f"min_banda_{lab}") or 0 for d in days)
                for lab in blabs}
    tot = sum(band_tot.values()) or 1
    def _pretty(lab: str) -> str:
        if lab.startswith("menos_"):
            return f"menos de {lab[6:]}°"
        if lab.endswith("_o_mas"):
            return f"{lab[:-6]}° o más"
        a, b = lab.split("_")
        return f"{a}–{b}°"

    band_rows = "".join(
        f"<tr><td>{_pretty(lab)}</td>"
        f"<td>{band_tot[lab] / 60:.1f} h</td>"
        f"<td>{100 * band_tot[lab] / tot:.1f} %</td></tr>"
        for lab in blabs)

    # --- Perfil horario ----------------------------------------------------
    hours = sorted({h["hora"] for h in hourly})
    hour_vals = {(h["fase"], h["hora"]): h for h in hourly}
    prof_labels, prof_vals, prof_colors = [], [], []
    for hr in hours:
        for ph, col in (("linea_base", BASE_COLOR),
                        ("intervencion", INTERV_COLOR)):
            v = hour_vals.get((ph, hr))
            prof_labels.append(f"{hr}h" if ph == "linea_base" else "")
            prof_vals.append(v["pct_desviado"] if v and v["minutos"] >= 10
                             else None)
            prof_colors.append(col)

    # --- Tabla diaria --------------------------------------------------------
    cols = [("fecha", "Fecha"), ("fase", "Fase"), ("dia_valido", "Válido"),
            ("minutos", "Min"), ("bloque_continuo_max_min", "Bloque máx"),
            ("pct_desviado", "% desviado"), ("episodio_max_s", "Episodio máx (s)"),
            ("alertas_detectadas", "Alertas"), ("alertas_mostradas", "Mostradas"),
            ("perclos_medio", "PERCLOS"), ("pausas", "Pausas"),
            ("tramo_max_sin_pausa_min", "Sin pausa máx (min)"),
            ("dolor_cuello", "Cuello"), ("cansancio_visual", "Visual"),
            ("somnolencia", "Sueño")]
    head = "".join(f"<th>{c[1]}</th>" for c in cols)
    body = ""
    for d in days:
        cells = []
        for k, _ in cols:
            v = d.get(k)
            if k == "fase":
                v = PHASE_NAME.get(v, v)
            nd = 3 if k == "perclos_medio" else 0 if k in (
                "minutos", "episodio_max_s", "bloque_continuo_max_min",
                "tramo_max_sin_pausa_min") else 1
            cells.append(f"<td>{_fmt(v, nd)}</td>")
        body += f"<tr>{''.join(cells)}</tr>"

    kpis = [
        (summary["dias_uso"], "días con uso"),
        (summary["dias_validos"], "días válidos"),
        (f'{summary["dias_validos_linea_base"]} / '
         f'{summary["dias_validos_intervencion"]}', "válidos base / intervención"),
        (_fmt(summary["minutos_medios_por_dia"], 0), "min de uso por día"),
        (summary["dias_con_autorreporte"], "días con autorreporte"),
        ("Sí" if summary["valido_para_analisis"] == "si" else "No",
         "válido para el análisis"),
    ]
    control_note = (
        "<p><b>Grupo de control:</b> esta persona usó la app sin recibir "
        "nunca avisos. En la comparación, «línea base» e «intervención» son "
        "los días antes y después del corte"
        + (f" (día de uso {cut})" if cut else "") +
        ": el día en que habrían empezado los avisos con la misma regla que "
        "el otro grupo (5 días de al menos 2 h). En las gráficas, ocre = "
        "después del corte, todavía sin avisos.</p>"
        if summary.get("grupo") == "control" else "")
    kpi_html = "".join(f'<div class="kpi"><b>{html.escape(str(v))}</b>'
                       f'<span>{html.escape(t)}</span></div>' for v, t in kpis)

    legend = (f'<div class="legend"><span><i style="background:{BASE_COLOR}">'
              f'</i>Línea base (sin avisos)</span><span><i style="background:'
              f'{INTERV_COLOR}"></i>Intervención (con avisos)</span>'
              f'<span>Barras claras = día no válido</span></div>')

    charts = [
        _bar_chart("% del tiempo con la cabeza adelantada más de "
                   f"{dev_threshold:g}° respecto a su postura neutra",
                   labels, [d["pct_desviado"] for d in days], colors, " %",
                   faded),
        legend,
        _bar_chart("Minutos de seguimiento por día", labels,
                   [d["minutos"] for d in days], colors, "", faded),
        _bar_chart("Tramo más largo sentado sin pausa (min)", labels,
                   [d["tramo_max_sin_pausa_min"] for d in days], colors, "",
                   faded, ref_line=60, ref_label="60 min"),
        _line_chart("Autorreporte al terminar la jornada (0–10)", labels,
                    {"Dolor de cuello": [d["dolor_cuello"] for d in days],
                     "Cansancio visual": [d["cansancio_visual"] for d in days],
                     "Somnolencia": [d["somnolencia"] for d in days]},
                    {"Dolor de cuello": "#b03a2e",
                     "Cansancio visual": "#2d6cdf",
                     "Somnolencia": "#8e44ad"}),
    ]
    prof = (_bar_chart("% del tiempo desviado según la hora del día "
                       "(gris: línea base · verde: intervención)",
                       prof_labels, prof_vals, prof_colors, " %")
            if prof_vals else "<p>Sin datos suficientes.</p>")

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reporte {code}</title><style>{_CSS}</style></head>
<body><main>
<h1>Reporte de seguimiento · {code}</h1>
{control_note}
<p class="sub">Monitor Postural y Fatiga (Tesis UNSA 2026) · generado el
{datetime.now():%Y-%m-%d %H:%M} · versión(es) de la app:
{html.escape(summary["versiones_app"] or "—")}</p>

<div class="note"><b>Esto no es un diagnóstico.</b> Son mediciones
objetivas de exposición y conducta registradas por una cámara web durante el
trabajo frente a la pantalla. Complementan la evaluación clínica del
especialista; no la sustituyen.</div>

<h2>1. Resumen del seguimiento</h2>
<div class="kpis">{kpi_html}</div>

<h2>2. Línea base frente a intervención</h2>
<p>Media de los días válidos de cada fase. En la línea base la app medía sin
avisar; en la intervención avisaba. Verde = mejora, rojo = empeora.</p>
<div class="wrap"><table><tr><th>Indicador</th><th>Línea base</th>
<th>Intervención</th><th>Cambio</th></tr>{"".join(comp_rows)}</table></div>

<h2>3. Evolución día a día</h2>
{"".join(charts)}

<h2>4. Exposición cervical acumulada</h2>
<p>Tiempo total en cada banda de desviación respecto a su postura neutra
(medida al calibrar cada día), con la postura medible.</p>
<div class="wrap"><table><tr><th>Desviación</th><th>Tiempo</th><th>%</th></tr>
{band_rows}</table></div>

<h2>5. Perfil por hora del día</h2>
{prof}

<h2>6. Tabla diaria</h2>
<div class="wrap"><table><tr>{head}</tr>{body}</table></div>

<h2>7. Cómo leer estos datos</h2>
<ul>
<li><b>Desviación cervical (Δθc)</b>: cuánto se adelanta la cabeza respecto a
la postura erguida que la persona adoptó al calibrar ese día. No es el ángulo
craneovertebral clínico: con una cámara frontal ese ángulo absoluto no se
puede medir con fiabilidad. Para el CVA, usar una foto lateral.</li>
<li><b>Episodio desviado</b>: tiempo seguido por encima del umbral de
{dev_threshold:g}°. Un episodio sostenido dura al menos lo que tarda la app en
avisar.</li>
<li><b>PERCLOS</b>: fracción del último minuto con los ojos cerrados. Por
encima de 0.15 se asocia a somnolencia (literatura de conducción).</li>
<li><b>Pausa</b>: al menos 60 s sin que la cámara detecte a la persona.
<b>Tramo sin pausa</b>: tiempo seguido frente a la pantalla entre pausas.</li>
<li><b>Día válido</b>: con suficiente uso y un bloque continuo largo; los días
no válidos se muestran atenuados y no entran en la comparación.</li>
<li>El autorreporte es la percepción de la persona al terminar la jornada
(0 = nada, 10 = lo peor imaginable).</li>
</ul>
</main></body></html>"""


_PAGE_CSS = (
    "html{background:#fff;color-scheme:light}"
    "body{font-family:Segoe UI,Arial,sans-serif;max-width:960px;"
    "margin:24px auto;padding:0 16px;color:#222;background:#fff}"
    "h1{font-size:22px}h2{font-size:17px;margin:28px 0 6px}"
    "h3{font-size:15px;margin:14px 0 2px}small{color:#666;font-weight:400}"
    ".panel{border-bottom:1px solid #eee}p,li{color:#444;font-size:13px}"
    "table{border-collapse:collapse;font-size:13px;margin:8px 0}"
    "th,td{border-bottom:1px solid #e3e3e3;padding:5px 9px;text-align:right}"
    "th:first-child,td:first-child{text-align:left}th{background:#f4f6f9}"
    ".legend{display:flex;gap:16px;font-size:12px;color:#555}"
    ".legend i{display:inline-block;width:10px;height:10px;border-radius:2px;"
    "margin-right:5px}")


def _after_start(s: dict) -> Optional[int]:
    """Día de uso en que empieza el «después» (avisos o corte del control)."""
    return s.get("dia_inicio_despues") or s.get("dia_inicio_avisos")


def evolution_html(summaries: Sequence[dict],
                   days_by_code: Dict[str, List[dict]],
                   key: str = "pct_desviado",
                   label: str = "% del tiempo con desviación cervical "
                                "sobre el umbral") -> str:
    """
    Evolución por participante: un panel por persona, día de uso en el eje x,
    el indicador en el eje y y una línea discontinua donde empieza SU periodo
    «después» (los avisos, o el corte equivalente en el control). Primero el
    grupo con avisos y luego el control.
    """
    order = sorted(summaries, key=lambda s: (
        s.get("grupo") == "control", s["participante"]))
    max_day = max([d["dia_uso"] for s in order
                   for d in days_by_code.get(s["participante"], [])] or [1])
    vals = [d[key] for s in order
            for d in days_by_code.get(s["participante"], [])
            if isinstance(d.get(key), (int, float))]
    vmax = max(vals + [10.0]) * 1.1
    W, H, L, R, T, B = 900, 150, 70, 20, 16, 30
    X = lambda day: L + (day - 0.5) / max_day * (W - L - R)
    Y = lambda v: T + (1 - v / vmax) * (H - T - B)
    panels = []
    for s in order:
        code = s["participante"]
        control = s.get("grupo") == "control"
        start = _after_start(s)
        days = days_by_code.get(code, [])

        def period(d):
            if control:
                return ("despues_control" if start and d["dia_uso"] >= start
                        else "antes")
            return "despues" if d["fase"] == "intervencion" else "antes"

        color_of = {"antes": BASE_COLOR, "despues": INTERV_COLOR,
                    "despues_control": CONTROL_COLOR}
        pts = [(d["dia_uso"], d[key], d["dia_valido"] == "si", period(d))
               for d in days if isinstance(d.get(key), (int, float))]
        parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
                 f'aria-label="{html.escape(code)}">']
        parts.append(f'<line x1="{L}" y1="{H - B}" x2="{W - R}" y2="{H - B}" '
                     f'stroke="#999"/>')
        parts.append(f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H - B}" '
                     f'stroke="#999"/>')
        for v in (0, vmax / 2):
            parts.append(f'<text x="{L - 6}" y="{Y(v) + 4:.0f}" '
                         f'text-anchor="end" font-size="11" fill="#555">'
                         f'{v:.0f}</text>')
        for day in range(1, max_day + 1):
            parts.append(f'<text x="{X(day):.0f}" y="{H - 12}" '
                         f'text-anchor="middle" font-size="10" fill="#777">'
                         f'{day}</text>')
        if start:
            xs = X(start - 0.5)
            parts.append(f'<line x1="{xs:.0f}" y1="{T}" x2="{xs:.0f}" '
                         f'y2="{H - B}" stroke="#333" stroke-dasharray="5 4"/>')
        for seg, color in color_of.items():
            seg_pts = [(x, v) for x, v, _, f in pts if f == seg]
            if len(seg_pts) > 1:
                path = " ".join(f"{X(x):.1f},{Y(v):.1f}" for x, v in seg_pts)
                parts.append(f'<polyline points="{path}" fill="none" '
                             f'stroke="{color}" stroke-width="2"/>')
        for x, v, ok, f in pts:
            color = color_of[f]
            fill = color if ok else "white"
            parts.append(f'<circle cx="{X(x):.1f}" cy="{Y(v):.1f}" r="4" '
                         f'fill="{fill}" stroke="{color}" stroke-width="1.5">'
                         f'<title>Día {x}: {v:.1f}</title></circle>')
        parts.append("</svg>")
        napv = s.get(f"nap_{key}")
        if control:
            tag = ("control (sin avisos)"
                   + (f" · corte el día {start}" if start else ""))
        else:
            tag = (f"avisos desde el día {start}" if start
                   else "sin avisos aún")
        nap_txt = "" if napv is None else f" · NAP {napv:.2f}"
        ok_txt = ("" if s.get("valido_para_analisis") == "si"
                  else " · no entra en el análisis (pocos días válidos)")
        panels.append(f'<div class="panel"><h3>{html.escape(code)} '
                      f'<small>{tag}{nap_txt}{ok_txt}</small></h3>'
                      f'{"".join(parts)}</div>')
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Evolución por participante</title>
<style>{_PAGE_CSS}</style></head><body>
<h1>Evolución por participante: {html.escape(label)}</h1>
<p>Un panel por participante, primero el grupo con avisos y después el
control. Gris: línea base (sin avisos). Verde: con avisos. Ocre: control
después del corte (sigue sin avisos). La línea discontinua marca dónde empieza
el periodo «después»: en el control es el día en que habrían empezado los
avisos con la misma regla (5 días de al menos 2 h). Punto relleno = día
válido; hueco = día con poco uso (no entra en el análisis). NAP: proporción de
pares de días en que el «después» es mejor que el «antes» (0.5 = sin cambio,
1 = mejora total).</p>
<div class="legend"><span><i style="background:{BASE_COLOR}"></i>Antes (línea base)</span>
<span><i style="background:{INTERV_COLOR}"></i>Después, con avisos</span>
<span><i style="background:{CONTROL_COLOR}"></i>Después, control sin avisos</span></div>
{''.join(panels)}
</body></html>"""


def _fmt_p(p) -> str:
    if not isinstance(p, (int, float)):
        return "—"
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def group_comparison_html(summaries: Sequence[dict],
                          days_by_code: Dict[str, List[dict]],
                          tests: Sequence[dict],
                          key: str = "pct_desviado",
                          label: str = "% del tiempo con desviación cervical "
                                       "sobre el umbral") -> str:
    """
    Figura del diseño con preprueba, posprueba y grupo de control:
      1. antes y después de cada persona, por grupo, con la mediana;
      2. el cambio de cada persona por grupo (lo que compara Mann-Whitney);
      3. la media diaria de cada grupo por día de uso;
    y la tabla de las tres pruebas del indicador.
    """
    valid = [s for s in summaries if s.get("valido_para_analisis") == "si"]
    groups = (("intervencion", "Con avisos", INTERV_COLOR),
              ("control", "Control (sin avisos)", CONTROL_COLOR))
    members = {g: [s for s in valid
                   if (s.get("grupo") == "control") == (g == "control")
                   and isinstance(s.get(f"{key}_base"), (int, float))
                   and isinstance(s.get(f"{key}_interv"), (int, float))]
               for g, _, _ in groups}

    def _median(xs):
        xs = sorted(xs)
        n = len(xs)
        return None if not n else (xs[n // 2] if n % 2
                                   else (xs[n // 2 - 1] + xs[n // 2]) / 2)

    # 1. Antes y después
    allv = [s[f"{key}_{p}"] for g in members for s in members[g]
            for p in ("base", "interv")]
    vmax = max(allv + [10.0]) * 1.12
    W, H, T, B = 440, 300, 20, 40
    Y = lambda v: T + (1 - v / vmax) * (H - T - B)
    prepost = []
    for g, name, color in groups:
        x0, x1 = 100, 280
        p = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
             f'aria-label="{html.escape(name)}">']
        for v in (0, vmax / 2, vmax / 1.12):
            p.append(f'<line x1="60" x2="{W - 20}" y1="{Y(v):.1f}" '
                     f'y2="{Y(v):.1f}" stroke="#eee"/>')
            p.append(f'<text x="54" y="{Y(v) + 4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="#555">{v:.0f}</text>')
        for x, t in ((x0, "Antes"), (x1, "Después")):
            p.append(f'<text x="{x}" y="{H - 14}" text-anchor="middle" '
                     f'font-size="12" fill="#333">{t}</text>')
        for s in members[g]:
            b, i = s[f"{key}_base"], s[f"{key}_interv"]
            p.append(f'<line x1="{x0}" y1="{Y(b):.1f}" x2="{x1}" '
                     f'y2="{Y(i):.1f}" stroke="{color}" stroke-opacity="0.45" '
                     f'stroke-width="1.5"><title>{html.escape(s["participante"])}'
                     f': {b:.1f} → {i:.1f}</title></line>')
            for x, v in ((x0, b), (x1, i)):
                p.append(f'<circle cx="{x}" cy="{Y(v):.1f}" r="3.5" '
                         f'fill="{color}" fill-opacity="0.6"/>')
        mb = _median([s[f"{key}_base"] for s in members[g]])
        mi = _median([s[f"{key}_interv"] for s in members[g]])
        if mb is not None:
            p.append(f'<line x1="{x0}" y1="{Y(mb):.1f}" x2="{x1}" '
                     f'y2="{Y(mi):.1f}" stroke="#222" stroke-width="3.5"/>')
            p.append(f'<text x="{x1 + 10}" y="{Y(mi) + 4:.1f}" font-size="12" '
                     f'fill="#222">mediana {mb:.1f} → {mi:.1f}</text>')
        p.append("</svg>")
        prepost.append(f'<div style="flex:1;min-width:280px"><h3>{name} '
                       f'<small>n = {len(members[g])}</small></h3>'
                       f'{"".join(p)}</div>')

    # 2. Cambio por persona
    ch = {g: [(s["participante"], s[f"{key}_interv"] - s[f"{key}_base"])
              for s in members[g]] for g, _, _ in groups}
    allc = [c for g in ch for _, c in ch[g]] + [0.0]
    lo, hi = min(allc), max(allc)
    span = max(hi - lo, 1.0)
    lo, hi = lo - 0.1 * span, hi + 0.1 * span
    W2, H2, L2, R2 = 900, 190, 170, 30
    X2 = lambda v: L2 + (v - lo) / (hi - lo) * (W2 - L2 - R2)
    c = [f'<svg viewBox="0 0 {W2} {H2}" width="100%" role="img" '
         f'aria-label="Cambio por persona">']
    c.append(f'<line x1="{X2(0):.1f}" x2="{X2(0):.1f}" y1="10" y2="{H2 - 44}" '
             f'stroke="#999" stroke-dasharray="4 3"/>')
    for k in range(5):
        v = lo + k * (hi - lo) / 4
        c.append(f'<text x="{X2(v):.1f}" y="{H2 - 26}" text-anchor="middle" '
                 f'font-size="11" fill="#555">{v:+.0f}</text>')
    c.append(f'<text x="{(L2 + W2 - R2) / 2:.0f}" y="{H2 - 6}" '
             f'text-anchor="middle" font-size="11" fill="#555">cambio '
             f'(después − antes, en las unidades del indicador)</text>')
    for row, (g, name, color) in enumerate(groups):
        y = 45 + row * 60
        c.append(f'<text x="{L2 - 12}" y="{y + 4}" text-anchor="end" '
                 f'font-size="12" fill="#333">{name}</text>')
        for n, (code, v) in enumerate(ch[g]):
            jitter = ((n % 5) - 2) * 4
            c.append(f'<circle cx="{X2(v):.1f}" cy="{y + jitter}" r="5" '
                     f'fill="{color}" fill-opacity="0.75"><title>'
                     f'{html.escape(code)}: {v:+.1f}</title></circle>')
        m = _median([v for _, v in ch[g]])
        if m is not None:
            c.append(f'<line x1="{X2(m):.1f}" x2="{X2(m):.1f}" y1="{y - 16}" '
                     f'y2="{y + 16}" stroke="#222" stroke-width="3"/>')
    c.append("</svg>")

    # 3. Media diaria por grupo
    max_day = max([d["dia_uso"] for s in valid
                   for d in days_by_code.get(s["participante"], [])] or [1])
    series = {}
    for g, name, color in groups:
        vals = []
        for day in range(1, max_day + 1):
            xs = [d[key] for s in members[g]
                  for d in days_by_code.get(s["participante"], [])
                  if d["dia_uso"] == day and d["dia_valido"] == "si"
                  and isinstance(d.get(key), (int, float))]
            vals.append(sum(xs) / len(xs) if len(xs) >= 2 else None)
        series[name] = vals
    starts = [_after_start(s) for g in members for s in members[g]
              if _after_start(s)]
    W3, H3, L3, R3, T3, B3 = 900, 240, 60, 20, 16, 34
    dvals = [v for vs in series.values() for v in vs if v is not None]
    dmax = max(dvals + [10.0]) * 1.12
    X3 = lambda day: L3 + (day - 0.5) / max_day * (W3 - L3 - R3)
    Y3 = lambda v: T3 + (1 - v / dmax) * (H3 - T3 - B3)
    e = [f'<svg viewBox="0 0 {W3} {H3}" width="100%" role="img" '
         f'aria-label="Media diaria por grupo">']
    for v in (0, dmax / 2, dmax / 1.12):
        e.append(f'<line x1="{L3}" x2="{W3 - R3}" y1="{Y3(v):.1f}" '
                 f'y2="{Y3(v):.1f}" stroke="#eee"/>')
        e.append(f'<text x="{L3 - 6}" y="{Y3(v) + 4:.1f}" text-anchor="end" '
                 f'font-size="11" fill="#555">{v:.0f}</text>')
    for day in range(1, max_day + 1):
        e.append(f'<text x="{X3(day):.1f}" y="{H3 - 12}" text-anchor="middle" '
                 f'font-size="10" fill="#777">{day}</text>')
    if starts:
        ms = _median(starts)
        e.append(f'<line x1="{X3(ms - 0.5):.1f}" x2="{X3(ms - 0.5):.1f}" '
                 f'y1="{T3}" y2="{H3 - B3}" stroke="#333" '
                 f'stroke-dasharray="5 4"/>')
        e.append(f'<text x="{X3(ms - 0.5) + 6:.1f}" y="{T3 + 10}" '
                 f'font-size="11" fill="#333">empiezan los avisos '
                 f'(mediana: día {ms:g})</text>')
    for (g, name, color) in groups:
        pts = [(X3(k + 1), Y3(v)) for k, v in enumerate(series[name])
               if v is not None]
        if len(pts) > 1:
            e.append(f'<polyline points="'
                     f'{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" '
                     f'fill="none" stroke="{color}" stroke-width="2.5"/>')
        for x, y in pts:
            e.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" '
                     f'fill="{color}"/>')
    e.append("</svg>")

    # Tabla de pruebas
    rows = [t for t in tests if t.get("indicador") == key]
    trs = []
    for t in rows:
        n = (f'{t["n_intervencion"]} + {t["n_control"]}'
             if t["prueba"] == TEST_BETWEEN else
             t["n_intervencion"] if t["prueba"] == TEST_WITHIN_INTERV
             else t["n_control"])
        med = (t["mediana_cambio_intervencion"]
               if t["prueba"] != TEST_WITHIN_CONTROL
               else t["mediana_cambio_control"])
        med_txt = "—" if not isinstance(med, (int, float)) else f"{med:+.1f}"
        if t["prueba"] == TEST_BETWEEN and isinstance(
                t["mediana_cambio_control"], (int, float)):
            med_txt += f' vs {t["mediana_cambio_control"]:+.1f}'
        r_txt = ("—" if not isinstance(t["r_biserial"], (int, float))
                 else f'{t["r_biserial"]:+.2f}')
        trs.append(f'<tr><td>{html.escape(t["nombre_prueba"])}</td>'
                   f'<td>{n if n is not None else "—"}</td><td>{med_txt}</td>'
                   f'<td>{_fmt_p(t["p_una_cola"])}</td>'
                   f'<td>{_fmt_p(t["p_dos_colas"])}</td><td>{r_txt}</td>'
                   f'<td style="text-align:left">{html.escape(t["nota"])}'
                   f'</td></tr>')
    excluded = [s["participante"] for s in summaries
                if s.get("valido_para_analisis") != "si"]
    excl_txt = (f" No entran en el análisis por tener pocos días válidos: "
                f"{', '.join(excluded)}." if excluded else "")
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Comparación entre grupos</title>
<style>{_PAGE_CSS}</style></head><body>
<h1>Comparación entre grupos: {html.escape(label)}</h1>
<p>Diseño con preprueba, posprueba y grupo de control.
«Antes» = media de los días válidos de la línea base (todos sin avisos);
«después» = media de los días válidos siguientes (con avisos en un grupo, sin
avisos en el control). Solo participantes válidos para el análisis.{excl_txt}</p>
<div class="legend"><span><i style="background:{INTERV_COLOR}"></i>Con avisos</span>
<span><i style="background:{CONTROL_COLOR}"></i>Control (sin avisos)</span></div>

<h2>1. Antes y después de cada persona</h2>
<p>Cada línea fina es una persona; la gruesa, la mediana del grupo.</p>
<div style="display:flex;flex-wrap:wrap;gap:12px">{''.join(prepost)}</div>

<h2>2. Cambio de cada persona (después − antes)</h2>
<p>Cada punto es una persona; la raya negra, la mediana del grupo. A la
izquierda de la línea discontinua, el indicador bajó. La prueba principal
(Mann-Whitney) compara estas dos filas: si quien recibe avisos cambia más que
quien no los recibe, la diferencia no se explica por el paso del tiempo ni por
saberse observado, que afectan a los dos grupos.</p>
{''.join(c)}

<h2>3. Media diaria de cada grupo</h2>
<p>Media de los días válidos por día de uso (con al menos 2 personas ese
día). Orientativa: no todas las personas empiezan los avisos el mismo día de
uso y los días cortos no entran.</p>
{''.join(e)}

<h2>4. Pruebas</h2>
<div style="overflow-x:auto"><table>
<tr><th>Prueba</th><th>n</th><th>Mediana del cambio</th><th>p (una cola)</th>
<th>p (dos colas)</th><th>r</th><th style="text-align:left">Nota</th></tr>
{''.join(trs)}
</table></div>
<p>p de una cola: en la dirección de la hipótesis (que el indicador baje),
declarada antes de ver los datos. r: tamaño del efecto de rangos biserial
(−1 a 1; en Mann-Whitney, −1 = todo el grupo con avisos bajó más que todo el
control). Orientación: |r| ≥ 0.1 pequeño, ≥ 0.3 medio, ≥ 0.5 grande.</p>
</body></html>"""


def write_reports(out_dir: Path, summaries: Sequence[dict],
                  days_by_code: Dict[str, List[dict]],
                  hourly_by_code: Dict[str, List[dict]],
                  dev_threshold: float, bands: Sequence[float],
                  tests: Sequence[dict] = ()) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    ev = out_dir / "evolucion_participantes.html"
    ev.write_text(evolution_html(summaries, days_by_code), encoding="utf-8")
    paths.append(ev)
    gc = out_dir / "comparacion_grupos.html"
    gc.write_text(group_comparison_html(summaries, days_by_code, tests),
                  encoding="utf-8")
    paths.append(gc)
    for s in summaries:
        code = s["participante"]
        p = out_dir / f"reporte_{code}.html"
        p.write_text(build_report(s, days_by_code.get(code, []),
                                  hourly_by_code.get(code, []),
                                  dev_threshold, bands), encoding="utf-8")
        paths.append(p)
    return paths
