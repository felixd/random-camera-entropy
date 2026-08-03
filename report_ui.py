#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared HTML/Plotly helpers for focused camera-entropy reports.

The Plotly runtime is vendored in ``static/vendor`` and is loaded from the
persistent control server. Reports remain readable without JavaScript because
all important numbers are also rendered as ordinary HTML tables/cards.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Iterable


RATE_UNITS: dict[str, float] = {
    "bit/s": 1.0,
    "kbit/s": 1_000.0,
    "kB/s": 8_000.0,
    "MiB/s": 8.0 * 1024.0 * 1024.0,
    "MB/s": 8_000_000.0,
}
DEFAULT_RATE_UNIT = "kB/s"


class RawHtml(str):
    """Explicitly trusted HTML fragment produced by report helpers."""


PLOTLY_SRC = "/static/vendor/plotly-3.3.1.min.js"
PLOTLY_FILE = Path(__file__).resolve().parent / "static" / "vendor" / "plotly-3.3.1.min.js"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def fmt(value: Any, digits: int = 7, empty: str = "—") -> str:
    if value is None:
        return empty
    if isinstance(value, bool):
        return "tak" if value else "nie"
    if isinstance(value, float):
        if value != value:
            return empty
        return f"{value:.{digits}g}"
    return str(value)


def fmt_percent(value: Any, digits: int = 3, empty: str = "—") -> str:
    if not isinstance(value, (int, float)):
        return empty
    return f"{100.0 * float(value):.{digits}f}%"


def fmt_rate(value: Any, empty: str = "—", unit: str = DEFAULT_RATE_UNIT) -> str:
    """Format a bit-per-second value in an explicitly selected unit.

    Reports default to kB/s so source, conditioner and file throughput can be
    compared on one familiar byte-oriented scale. Decimal kB/MB and binary MiB
    are intentionally distinct.
    """
    if not isinstance(value, (int, float)):
        return empty
    factor = RATE_UNITS.get(unit)
    if factor is None:
        raise ValueError(f"unsupported rate unit: {unit}")
    scaled = float(value) / factor
    decimals = 1 if abs(scaled) >= 1000 else 2 if abs(scaled) >= 100 else 3
    return f"{scaled:.{decimals}f} {unit}"


def rate_span(value: Any, *, empty: str = "—", unit: str = DEFAULT_RATE_UNIT) -> RawHtml:
    if not isinstance(value, (int, float)):
        return RawHtml(esc(empty))
    return RawHtml(
        f'<span class="rate-value" data-rate-bps="{float(value):.17g}">'
        f'{esc(fmt_rate(value, empty=empty, unit=unit))}</span>'
    )


def rate_unit_selector(*, selected: str = DEFAULT_RATE_UNIT, label: str = "Jednostka przepustowości") -> str:
    options = "".join(
        f'<option value="{esc(unit)}"{" selected" if unit == selected else ""}>{esc(unit)}</option>'
        for unit in RATE_UNITS
    )
    return (
        '<label class="rate-unit-control">'
        f'<span>{esc(label)}</span><select class="rate-unit-select">{options}</select></label>'
    )


def fmt_bytes(value: Any, empty: str = "—") -> str:
    if not isinstance(value, (int, float)):
        return empty
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024.0
    return empty


def metric_card(label: str, value: Any, detail: Any = "", status: str = "") -> str:
    status_class = f" metric-{status}" if status in {"good", "warn", "bad"} else ""
    value_html = str(value) if isinstance(value, RawHtml) else esc(value)
    detail_value = str(detail) if isinstance(detail, RawHtml) else esc(detail)
    detail_html = f'<div class="metric-detail">{detail_value}</div>' if detail else ""
    return (
        f'<div class="metric-card{status_class}">'
        f'<div class="metric-label">{esc(label)}</div>'
        f'<div class="metric-value">{value_html}</div>{detail_html}</div>'
    )


def metrics_grid(cards: Iterable[str]) -> str:
    return '<div class="metrics-grid">' + "".join(cards) + "</div>"


def _cell_html(value: Any) -> str:
    return str(value) if isinstance(value, RawHtml) else esc(value)


def table_html(headers: list[str], rows: Iterable[Iterable[Any]], *, compact: bool = False) -> str:
    class_name = "compact" if compact else ""
    head = "".join(f"<th>{esc(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_cell_html(item)}</td>" for item in row) + "</tr>"
        for row in rows
    )
    return f'<div class="table-scroll"><table class="{class_name}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def chart_div(chart_id: str, title: str, description: str = "", height: int = 360) -> str:
    desc = f'<p class="chart-description">{esc(description)}</p>' if description else ""
    return (
        '<section class="chart-section">'
        f'<div class="chart-heading"><h2>{esc(title)}</h2>{desc}</div>'
        f'<div id="{esc(chart_id)}" class="plot" style="height:{int(height)}px" '
        f'aria-label="{esc(title)}"></div>'
        '<noscript><p class="chart-fallback">Wykres wymaga JavaScript. Wszystkie wartości są dostępne w tabelach raportu.</p></noscript>'
        '</section>'
    )


def _safe_json(value: Any) -> str:
    # Avoid terminating an inline script if a label unexpectedly contains </script>.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def plotly_script(specs: list[dict[str, Any]], *, inline_runtime: bool = False) -> str:
    payload = _safe_json(specs)
    if inline_runtime:
        try:
            runtime = PLOTLY_FILE.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"cannot embed Plotly runtime: {PLOTLY_FILE}") from exc
        runtime_tag = f"<script>{runtime}</script>"
    else:
        runtime_tag = f'<script src="{PLOTLY_SRC}"></script>'
    return f"""
{runtime_tag}
<script>
(() => {{
  const specs = {payload};
  const rateUnits = {{"bit/s":1,"kbit/s":1000,"kB/s":8000,"MiB/s":8388608,"MB/s":8000000}};
  let rateUnit = document.querySelector('.rate-unit-select')?.value || 'kB/s';
  const formatRate = (bps) => {{
    const value = Number(bps) / rateUnits[rateUnit];
    const digits = Math.abs(value) >= 1000 ? 1 : Math.abs(value) >= 100 ? 2 : 3;
    return `${{value.toFixed(digits)}} ${{rateUnit}}`;
  }};
  const refreshRateText = () => document.querySelectorAll('[data-rate-bps]').forEach((node) => {{
    const value = Number(node.dataset.rateBps);
    node.textContent = Number.isFinite(value) ? formatRate(value) : '—';
  }});
  const rootStyle = getComputedStyle(document.documentElement);
  const colors = [
    rootStyle.getPropertyValue('--series-1').trim(),
    rootStyle.getPropertyValue('--series-2').trim(),
    rootStyle.getPropertyValue('--series-3').trim(),
    rootStyle.getPropertyValue('--series-4').trim(),
    rootStyle.getPropertyValue('--series-5').trim(),
    rootStyle.getPropertyValue('--series-6').trim()
  ];
  const baseLayout = {{
    autosize: true,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: {{color: rootStyle.getPropertyValue('--text').trim(), family: 'system-ui, sans-serif', size: 12}},
    margin: {{l: 66, r: 24, t: 22, b: 58}},
    hovermode: 'closest',
    legend: {{orientation: 'h', y: 1.14, x: 0}},
    xaxis: {{gridcolor: rootStyle.getPropertyValue('--grid').trim(), zerolinecolor: rootStyle.getPropertyValue('--border').trim()}},
    yaxis: {{gridcolor: rootStyle.getPropertyValue('--grid').trim(), zerolinecolor: rootStyle.getPropertyValue('--border').trim()}}
  }};
  const config = {{responsive: true, displaylogo: false, scrollZoom: false, modeBarButtonsToRemove: ['lasso2d','select2d']}};
  const supportsWebGL = (() => {{
    try {{
      const canvas = document.createElement('canvas');
      return Boolean(window.WebGLRenderingContext && (canvas.getContext('webgl2') || canvas.getContext('webgl')));
    }} catch (_) {{ return false; }}
  }})();
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[character]));
  const fallback = (element, spec, error) => {{
    if (error) console.error('Plotly chart failed', spec.id, error);
    const reason = escapeHtml(spec.staticReason || (error && error.message ? error.message : 'WebGL jest wyłączony lub niedostępny.'));
    element.dataset.plotlyFallback = '1';
    if (spec.fallbackImage) {{
      element.innerHTML = `<figure class="plot-static-fallback"><img src="${{spec.fallbackImage}}" alt="Statyczny wykres zastępczy"><figcaption>Widok statyczny: ${{reason}}</figcaption></figure>`;
    }} else {{
      element.innerHTML = `<p class="chart-fallback">Nie udało się wyświetlić wykresu: ${{reason}}</p>`;
    }}
  }};
  if (!window.Plotly) {{
    document.querySelectorAll('.plot').forEach((node) => {{
      node.innerHTML = '<p class="chart-fallback">Nie udało się załadować lokalnej biblioteki Plotly. Dane pozostają dostępne w tabelach.</p>';
    }});
    return;
  }}
  const renderAll = () => {{
    refreshRateText();
    specs.forEach((spec) => {{
      const element = document.getElementById(spec.id);
      if (!element) return;
      if (spec.staticUnderCsp || (spec.requiresWebGL && !supportsWebGL)) {{ fallback(element, spec); return; }}
      const data = (spec.data || []).map((trace, index) => {{
        const copy = Object.assign({{}}, trace);
        if (spec.rateAxis === 'y' && Array.isArray(copy.y)) {{
          copy.customdata = copy.y.slice();
          copy.y = copy.y.map((value) => value == null ? null : Number(value) / rateUnits[rateUnit]);
          copy.hovertemplate = copy.hovertemplate || `%{{x}}<br>%{{y:.4g}} ${{rateUnit}}<extra></extra>`;
        }}
        if (spec.rateAxis === 'x' && Array.isArray(copy.x)) {{
          copy.customdata = copy.x.slice();
          copy.x = copy.x.map((value) => value == null ? null : Number(value) / rateUnits[rateUnit]);
          copy.hovertemplate = copy.hovertemplate || `%{{y}}<br>%{{x:.4g}} ${{rateUnit}}<extra></extra>`;
        }}
        if (!copy.marker && ['bar','scatter'].includes(copy.type || 'scatter')) copy.marker = {{color: colors[index % colors.length]}};
        if (!copy.line && (copy.type || 'scatter') === 'scatter') copy.line = {{color: colors[index % colors.length], width: 2}};
        return copy;
      }});
      const layout = Object.assign({{}}, baseLayout, spec.layout || {{}});
      layout.xaxis = Object.assign({{}}, baseLayout.xaxis, (spec.layout || {{}}).xaxis || {{}});
      layout.yaxis = Object.assign({{}}, baseLayout.yaxis, (spec.layout || {{}}).yaxis || {{}});
      if (spec.rateAxis === 'y') layout.yaxis.title = spec.rateTitle ? `${{spec.rateTitle}} [${{rateUnit}}]` : rateUnit;
      if (spec.rateAxis === 'x') layout.xaxis.title = spec.rateTitle ? `${{spec.rateTitle}} [${{rateUnit}}]` : rateUnit;
      try {{
        Promise.resolve(Plotly.react(element, data, layout, config)).catch((error) => fallback(element, spec, error));
      }} catch (error) {{ fallback(element, spec, error); }}
    }});
  }};
  document.querySelectorAll('.rate-unit-select').forEach((select) => select.addEventListener('change', (event) => {{
    rateUnit = event.target.value;
    document.querySelectorAll('.rate-unit-select').forEach((other) => {{ other.value = rateUnit; }});
    renderAll();
  }}));
  renderAll();
}})();
</script>
"""


BASE_CSS = r"""
:root{
  color-scheme:dark;
  --bg:#0d1117;--panel:#161b22;--panel-2:#1c2128;--border:#30363d;
  --grid:#252b33;--text:#f0f6fc;--muted:#8b949e;--accent:#58a6ff;
  --good:#3fb950;--warn:#d29922;--bad:#f85149;
  --series-1:#58a6ff;--series-2:#3fb950;--series-3:#d29922;
  --series-4:#bc8cff;--series-5:#f778ba;--series-6:#ff7b72;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
main{max-width:1720px;margin:0 auto;padding:24px}
.page-head{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;flex-wrap:wrap;margin-bottom:18px}
h1{margin:0 0 7px;font-size:clamp(1.55rem,2.6vw,2.25rem)}h2{margin:0;font-size:1.08rem}h3{margin:.2rem 0 .7rem;font-size:1rem}
p{line-height:1.5}.subtitle,.muted,.chart-description{color:var(--muted)}
.nav-links{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.rate-unit-control{display:inline-flex;align-items:center;gap:8px;color:var(--muted);font-size:.82rem}.rate-unit-control select{background:var(--panel-2);color:var(--text);border:1px solid var(--border);border-radius:7px;padding:6px 8px}
.badge{display:inline-flex;align-items:center;border:1px solid var(--border);border-radius:999px;padding:5px 10px;background:var(--panel);font-size:.85rem}
.badge.good{border-color:color-mix(in srgb,var(--good) 65%,var(--border));color:var(--good)}
.badge.warn{border-color:color-mix(in srgb,var(--warn) 65%,var(--border));color:var(--warn)}
.badge.bad{border-color:color-mix(in srgb,var(--bad) 65%,var(--border));color:var(--bad)}
.metrics-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:16px 0 20px}
.metric-card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:14px;min-height:108px}
.metric-card.metric-good{border-left:4px solid var(--good)}.metric-card.metric-warn{border-left:4px solid var(--warn)}.metric-card.metric-bad{border-left:4px solid var(--bad)}
.metric-label{font-size:.78rem;text-transform:uppercase;letter-spacing:.045em;color:var(--muted);margin-bottom:8px}
.metric-value{font-size:1.48rem;font-weight:650;overflow-wrap:anywhere}.metric-detail{font-size:.82rem;color:var(--muted);margin-top:6px}
section,.panel{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.chart-section{overflow:hidden}.chart-heading{margin-bottom:8px}.chart-description{margin:5px 0 0;font-size:.9rem}.plot{width:100%;min-height:260px}
.plot-static-fallback{height:100%;margin:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px}.plot-static-fallback img{display:block;max-width:100%;max-height:calc(100% - 35px);object-fit:contain;border-radius:8px}.plot-static-fallback figcaption{color:var(--muted);font-size:.82rem;text-align:center}.chart-fallback{display:flex;align-items:center;justify-content:center;height:100%;min-height:160px;color:var(--muted);border:1px dashed var(--border);border-radius:8px;padding:18px;text-align:center}
.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.chart-grid>section{margin:0}
.table-scroll{overflow:auto;border:1px solid var(--border);border-radius:10px}
table{width:100%;border-collapse:collapse;background:var(--panel);font-size:.82rem}th,td{padding:9px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top;white-space:nowrap}th{position:sticky;top:0;background:var(--panel-2);color:var(--muted);z-index:1}tr:last-child td{border-bottom:0}tr:hover td{background:rgba(88,166,255,.035)}table.compact{font-size:.76rem}table.compact th,table.compact td{padding:7px 8px}
code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}code{overflow-wrap:anywhere}pre{white-space:pre-wrap;word-break:break-word;background:var(--panel-2);border:1px solid var(--border);border-radius:10px;padding:12px}
details{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:12px}summary{cursor:pointer;font-weight:650}.callout{border-left:4px solid var(--accent);background:var(--panel);padding:14px 16px;border-radius:8px;margin:14px 0}.callout.good{border-left-color:var(--good)}.callout.warn{border-left-color:var(--warn)}.callout.bad{border-left-color:var(--bad)}
.kv{display:grid;grid-template-columns:minmax(180px,320px) minmax(0,1fr);gap:0;border:1px solid var(--border);border-radius:10px;overflow:hidden}.kv>*{padding:8px 10px;border-bottom:1px solid var(--border)}.kv>*:nth-last-child(-n+2){border-bottom:0}.kv .key{color:var(--muted);background:var(--panel-2)}
@media(max-width:960px){.chart-grid{grid-template-columns:1fr}}
@media(max-width:640px){main{padding:15px}.metrics-grid{grid-template-columns:1fr 1fr}.metric-value{font-size:1.18rem}.kv{grid-template-columns:1fr}.kv .key{border-bottom:0}.page-head{display:block}.nav-links{margin-top:10px}}
@media(max-width:430px){.metrics-grid{grid-template-columns:1fr}}
"""


def html_page(
    *,
    title: str,
    subtitle: str = "",
    navigation: str = "",
    body: str,
    plot_specs: list[dict[str, Any]] | None = None,
    extra_css: str = "",
    extra_js: str = "",
    standalone: bool = False,
) -> str:
    subtitle_html = f'<p class="subtitle">{esc(subtitle)}</p>' if subtitle else ""
    plots = plotly_script(plot_specs or [], inline_runtime=standalone) if plot_specs else ""
    custom_script = f"<script>{extra_js}</script>" if extra_js else ""
    return f'''<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{BASE_CSS}\n{extra_css}</style></head><body><main><header class="page-head"><div><h1>{esc(title)}</h1>{subtitle_html}</div><nav class="nav-links">{navigation}</nav></header>{body}</main>{plots}{custom_script}</body></html>'''
