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

from src.tools.field_analysis import COMPARED, band_labels

BASE_COLOR = "#7A8BA6"
INTERV_COLOR = "#0E8F8A"
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

# Para estos indicadores, BAJAR es mejorar.
_LOWER_IS_BETTER = {
    "pct_desviado", "delta_theta_media", "episodio_max_s",
    "episodios_sostenidos_por_hora", "alertas_detectadas_por_hora",
    "pct_hombros_asimetricos", "pct_demasiado_cerca", "perclos_medio",
    "correccion_mediana_s", "tramo_max_sin_pausa_min", "dolor_cuello",
    "cansancio_visual", "somnolencia",
}


def build_report(summary: dict, days: Sequence[dict], hourly: Sequence[dict],
                 dev_threshold: float, bands: Sequence[float]) -> str:
    code = html.escape(summary["participante"])
    labels = [d["fecha"][5:] for d in days]
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
        "los días antes y después de un corte de referencia, para ver si "
        "cambió solo con el paso del tiempo.</p>"
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


def multiple_baseline_html(summaries: Sequence[dict],
                           days_by_code: Dict[str, List[dict]],
                           key: str = "pct_desviado",
                           label: str = "% del tiempo con desviación cervical "
                                        "sobre el umbral") -> str:
    """
    Gráfico de línea base múltiple: un panel por participante, día de uso en
    el eje x, el indicador en el eje y y una línea discontinua donde empiezan
    SUS avisos. Los de intervención van ordenados por día de inicio y el
    control al final. Es la figura central de un diseño de caso único.
    """
    order = sorted(summaries, key=lambda s: (
        s.get("grupo") == "control", s.get("dia_inicio_avisos") or 99,
        s["participante"]))
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
        days = days_by_code.get(code, [])
        pts = [(d["dia_uso"], d[key], d["dia_valido"] == "si", d["fase"])
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
        start = s.get("dia_inicio_avisos")
        if start:
            xs = X(start - 0.5)
            parts.append(f'<line x1="{xs:.0f}" y1="{T}" x2="{xs:.0f}" '
                         f'y2="{H - B}" stroke="#333" stroke-dasharray="5 4"/>')
        for seg in ("linea_base", "intervencion", "control"):
            seg_pts = [(x, v) for x, v, _, f in pts if f == seg]
            if len(seg_pts) > 1:
                color = INTERV_COLOR if seg == "intervencion" else BASE_COLOR
                path = " ".join(f"{X(x):.1f},{Y(v):.1f}" for x, v in seg_pts)
                parts.append(f'<polyline points="{path}" fill="none" '
                             f'stroke="{color}" stroke-width="2"/>')
        for x, v, ok, f in pts:
            color = INTERV_COLOR if f == "intervencion" else BASE_COLOR
            fill = color if ok else "white"
            parts.append(f'<circle cx="{X(x):.1f}" cy="{Y(v):.1f}" r="4" '
                         f'fill="{fill}" stroke="{color}" stroke-width="1.5">'
                         f'<title>Día {x}: {v:.1f}</title></circle>')
        parts.append("</svg>")
        napv = s.get(f"nap_{key}")
        tag = ("control (sin avisos)" if s.get("grupo") == "control"
               else f"avisos desde el día {start}" if start else "sin avisos aún")
        nap_txt = "" if napv is None else f" · NAP {napv:.2f}"
        panels.append(f'<div class="panel"><h3>{html.escape(code)} '
                      f'<small>{tag}{nap_txt}</small></h3>{"".join(parts)}</div>')
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Línea base múltiple</title>
<style>html{{background:#fff;color-scheme:light}}body{{font-family:Segoe UI,Arial,sans-serif;max-width:960px;margin:24px auto;color:#222;background:#fff}}
h1{{font-size:22px}}h3{{font-size:15px;margin:14px 0 2px}}small{{color:#666;font-weight:400}}
.panel{{border-bottom:1px solid #eee}}p{{color:#444;font-size:13px}}</style></head><body>
<h1>Línea base múltiple: {html.escape(label)}</h1>
<p>Un panel por participante. Gris: días sin avisos (línea base o control); verde: días con avisos.
La línea discontinua marca el día en que empezaron los avisos de ese participante. Punto relleno = día
válido; hueco = día con poco uso (no entra en el análisis). NAP: proporción de pares de días en que la
fase con avisos es mejor que la línea base (0.5 = sin efecto, 1 = mejora total). En el control, el NAP
usa un corte de referencia y muestra el cambio sin avisos.</p>
{''.join(panels)}
</body></html>"""


def write_reports(out_dir: Path, summaries: Sequence[dict],
                  days_by_code: Dict[str, List[dict]],
                  hourly_by_code: Dict[str, List[dict]],
                  dev_threshold: float, bands: Sequence[float]) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    mb = out_dir / "linea_base_multiple.html"
    mb.write_text(multiple_baseline_html(summaries, days_by_code),
                  encoding="utf-8")
    paths.append(mb)
    for s in summaries:
        code = s["participante"]
        p = out_dir / f"reporte_{code}.html"
        p.write_text(build_report(s, days_by_code.get(code, []),
                                  hourly_by_code.get(code, []),
                                  dev_threshold, bands), encoding="utf-8")
        paths.append(p)
    return paths
