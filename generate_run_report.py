#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from report_ui import (
    chart_div, esc, fmt, fmt_bytes, html_page, metric_card, metrics_grid, table_html,
)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def nested(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(part, default)
    return current


def binary_geometry_section(run: Path, summary: dict[str, Any]) -> str:
    files = summary.get("files", []) if isinstance(summary.get("files"), list) else []
    rows = [item for item in files if isinstance(item, dict)]
    if not rows:
        return ""
    thumbnail_parts: list[str] = []
    for row in rows[:4]:
        observed = row.get("observed_heatmap") or row.get("heatmap")
        residual = row.get("residual_heatmap")
        histogram = row.get("byte_histogram")
        if not observed:
            continue
        if residual:
            images = (
                '<div class="geometry-thumb-pair">'
                + (f'<a href="binary_geometry_report.html"><img src="{esc(histogram)}" alt="Histogram bajtów {esc(row.get("file"))}"><span>histogram bajtów</span></a>' if histogram else '')
                + f'<a href="binary_geometry_report.html"><img src="{esc(observed)}" alt="Zaobserwowane przejścia bajtowe {esc(row.get("file"))}"><span>liczności</span></a>'
                + f'<a href="binary_geometry_report.html"><img src="{esc(residual)}" alt="Reszty Pearsona przejść bajtowych {esc(row.get("file"))}"><span>vs niezależność</span></a>'
                + '</div>'
            )
        else:
            images = (
                '<div class="geometry-thumb-pair geometry-thumb-single">'
                f'<a href="binary_geometry_report.html"><img src="{esc(observed)}" alt="Rozkład par bajtów {esc(row.get("file"))}"><span>rozkład par</span></a>'
                '</div>'
            )
        thumbnail_parts.append(
            '<figure class="geometry-thumb">'
            f'<figcaption><strong>{esc(row.get("file"))}</strong></figcaption>' + images + '</figure>'
        )
    thumbnails = "".join(thumbnail_parts)
    table = table_html(
        ["Plik", "H(X)", "H(Y)", "H(X,Y)", "H(Y|X)", "MI ponad shuffle", "Cramer V", "|r| p99", "corr lag-1"],
        [[
            row.get("file"), fmt(row.get("x_entropy_bits"), 8), fmt(row.get("y_entropy_bits"), 8),
            fmt(row.get("pair_entropy_bits"), 8), fmt(row.get("conditional_entropy_y_given_x_bits"), 8),
            fmt(row.get("excess_mutual_information_bits"), 8), fmt(row.get("transition_cramers_v"), 8),
            fmt(row.get("pearson_residual_abs_p99"), 8), fmt(row.get("byte_serial_correlation"), 8),
        ] for row in rows],
        compact=True,
    )
    diagram = (
        '<img class="pipeline-diagram" src="dual_weave_stagger2_checkerboard.svg" '
        'alt="Dual weave row-major stagger-2 checkerboard">'
        if (run / "dual_weave_stagger2_checkerboard.svg").is_file() else ""
    )
    return (
        '<section><h2>Geometria 2D/3D plików BIN</h2>'
        '<p><a href="binary_geometry_report.html">Otwórz pełny interaktywny raport 2D/3D</a> · '
        '<a href="binary_geometry_summary.json">JSON</a> · '
        '<a href="binary_geometry_metrics.csv">CSV</a></p>'
        '<p class="muted">Dla każdego strumienia pokazujemy histogram 256 wartości bajtów, surowe liczności przejść oraz reszty Pearsona względem modelu niezależnych marginesów. '
        'Osie są zawsze takie same: X = bieżący bajt B<sub>n</sub>, Y = następny bajt B<sub>n+1</sub>. '
        'Obok raportowane są H(X), H(Y), H(X,Y), H(Y|X), MI ponad shuffle i Cramer V.</p>'
        + diagram
        + f'<div class="geometry-grid">{thumbnails}</div>'
        + table
        + '</section>'
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    summary = load(run / "runner_summary.json")
    config = load(run / "runner_config.json")
    complete = load(run / "output_complete.json")
    ready = load(run / "READY.json")
    failure = load(run / "run_failed.json")
    geometry = load(run / "binary_geometry_summary.json")
    analyses_container = summary.get("analyses", {}) if isinstance(summary.get("analyses"), dict) else {}

    stage_definitions = [
        ("conditioned", "SHA3-512", "analysis_conditioned"),
        ("vn", "Von Neumann", "analysis_vn"),
        ("selected_raw", "Temporal masked RAW", "analysis_selected_raw"),
        ("temporal_raw", "Temporal RAW", "analysis_temporal_raw"),
        ("direct_lsb", "Direct LSB", "analysis_direct_lsb"),
    ]
    stages: list[dict[str, Any]] = []
    for name, label, directory in stage_definitions:
        data = analyses_container.get(name, {}) if isinstance(analyses_container.get(name), dict) else {}
        if not data:
            data = load(run / directory / "summary.json")
        if not data:
            continue
        lag1 = data.get("lag1", {}) if isinstance(data.get("lag1"), dict) else {}
        stages.append({
            "name": name,
            "label": label,
            "directory": directory,
            "bytes": data.get("total_bytes"),
            "p1": data.get("p1"),
            "lag1_phi": lag1.get("phi"),
            "hmin": data.get("byte_min_entropy_bits_per_byte"),
            "chi_square": data.get("byte_chi_square"),
            "chi_p": data.get("byte_chi_square_p_value"),
        })

    status = "FAILED" if failure else "READY" if ready else "COMPLETE" if complete else "INCOMPLETE"
    status_class = "bad" if failure else "good" if status in {"READY", "COMPLETE"} else "warn"
    final = next((item for item in stages if item["name"] == "conditioned"), {})
    health = complete.get("health", {}) if isinstance(complete.get("health"), dict) else {}
    clipping = complete.get("active_clipping", {}) if isinstance(complete.get("active_clipping"), dict) else {}
    shadow = complete.get("shadow", {}) if isinstance(complete.get("shadow"), dict) else {}
    conditioner = complete.get("conditioner", {}) if isinstance(complete.get("conditioner"), dict) else {}

    pipeline_label = (
        "Temporal LSB → mask → health → SHA3-512"
        if config.get("von_neumann_stage") is False
        else "Temporal LSB → mask → VN + SHA3-512"
    )
    cards = [
        metric_card("Status", status, complete.get("app_version") or failure.get("app_version") or "", status_class),
        metric_card("Pipeline", pipeline_label, f'conditioner input {config.get("conditioner_input_bits", "n/a")} bit'),
        metric_card("Finalne P(1)", fmt(final.get("p1"), 8), f"odchylenie {fmt(abs(float(final['p1'])-.5) if isinstance(final.get('p1'), (int,float)) else None, 7)}"),
        metric_card("Finalne |φ| lag-1", fmt(abs(float(final["lag1_phi"])) if isinstance(final.get("lag1_phi"), (int, float)) else None, 8)),
        metric_card("Hmin bajtu SHA3", fmt(final.get("hmin"), 8), f"χ² p={fmt(final.get('chi_p'), 7)}"),
        metric_card("RCT / APT", f"{health.get('rct_failures', 0)} / {health.get('apt_failures', 0)}", "failures", "bad" if health.get("latched") else "good"),
        metric_card("Clipping", "LATCH" if clipping.get("latched") else "OK", f"failures {clipping.get('failures', 0)}", "bad" if clipping.get("latched") else "good"),
        metric_card("Mask retention", fmt(shadow.get("active_retention"), 7), f"Jaccard {fmt(shadow.get('jaccard'), 7)}"),
        metric_card("SHA3 output", fmt_bytes(conditioner.get("written_bytes")), f"{fmt(conditioner.get('compression_ratio'), 5)}:1 compression"),
    ]

    labels = [item["label"] for item in stages]
    plot_specs = [
        {
            "id": "stage-correlation",
            "data": [{
                "type": "bar",
                "x": labels,
                "y": [abs(float(item["lag1_phi"])) if isinstance(item.get("lag1_phi"), (int, float)) else None for item in stages],
                "text": [fmt(item.get("lag1_phi"), 6) for item in stages],
                "textposition": "auto",
                "hovertemplate": "%{x}<br>|φ|=%{y:.8f}<extra></extra>",
            }],
            "layout": {"xaxis": {"title": "Etap pipeline"}, "yaxis": {"title": "|φ| lag-1", "rangemode": "tozero"}},
        },
        {
            "id": "stage-bias",
            "data": [{
                "type": "bar",
                "x": labels,
                "y": [abs(float(item["p1"]) - .5) if isinstance(item.get("p1"), (int, float)) else None for item in stages],
                "text": [fmt(item.get("p1"), 7) for item in stages],
                "textposition": "auto",
                "hovertemplate": "%{x}<br>|P(1)-0.5|=%{y:.8f}<extra></extra>",
            }],
            "layout": {"xaxis": {"title": "Etap pipeline"}, "yaxis": {"title": "Bias bezwzględny", "rangemode": "tozero"}},
        },
        {
            "id": "stage-hmin",
            "data": [{
                "type": "bar",
                "x": labels,
                "y": [item.get("hmin") for item in stages],
                "text": [fmt(item.get("hmin"), 6) for item in stages],
                "textposition": "auto",
                "hovertemplate": "%{x}<br>Hmin=%{y:.7f} bit/bajt<extra></extra>",
            }],
            "layout": {"xaxis": {"title": "Etap pipeline"}, "yaxis": {"title": "Empiryczna Hmin [bit/bajt]", "range": [0, 8]}},
        },
        {
            "id": "stage-chi-p",
            "data": [{
                "type": "bar",
                "x": labels,
                "y": [item.get("chi_p") for item in stages],
                "text": [fmt(item.get("chi_p"), 5) for item in stages],
                "textposition": "auto",
                "hovertemplate": "%{x}<br>χ² p=%{y:.7g}<extra></extra>",
            }],
            "layout": {"xaxis": {"title": "Etap pipeline"}, "yaxis": {"title": "χ² p-value", "range": [0, 1]}},
        },
    ]

    stage_table = table_html(
        ["Etap", "Bajty", "P(1)", "|P(1)-0.5|", "φ lag-1", "Hmin bajtu", "χ²", "χ² p"],
        [[
            item["label"], fmt_bytes(item.get("bytes")), fmt(item.get("p1"), 8),
            fmt(abs(float(item["p1"]) - .5) if isinstance(item.get("p1"), (int, float)) else None, 8),
            fmt(item.get("lag1_phi"), 8), fmt(item.get("hmin"), 8),
            fmt(item.get("chi_square"), 8), fmt(item.get("chi_p"), 8),
        ] for item in stages],
        compact=True,
    )

    config_rows = [[key, fmt(value, 10)] for key, value in config.items()]
    file_rows = []
    for item in ready.get("files", []) if isinstance(ready.get("files"), list) else []:
        if isinstance(item, dict):
            name = str(item.get("name", ""))
            file_rows.append([f'<a href="{esc(name)}">{esc(name)}</a>', fmt_bytes(item.get("bytes")), item.get("sha256", "")])
    # table_html escapes all fields. Build file table separately to keep links clickable.
    file_table = ""
    if file_rows:
        body_rows = "".join(
            f'<tr><td>{row[0]}</td><td>{esc(row[1])}</td><td><code>{esc(row[2])}</code></td></tr>' for row in file_rows
        )
        file_table = f'<div class="table-scroll"><table class="compact"><thead><tr><th>Plik</th><th>Rozmiar</th><th>SHA-256</th></tr></thead><tbody>{body_rows}</tbody></table></div>'

    analysis_links = "".join(
        f'<li><a href="{esc(item["directory"])}/summary.json">{esc(item["label"])} — summary</a> · '
        f'<a href="{esc(item["directory"])}/lag_correlation.svg">lag SVG</a> · '
        f'<a href="{esc(item["directory"])}/chunks.csv">chunks CSV</a></li>'
        for item in stages
    )
    dual_link = (
        '<a href="dual_weave_report.html">Dual weave report</a>'
        if (run / "dual_weave_report.html").exists() else ""
    )
    geometry_link = (
        '<a href="binary_geometry_report.html">Geometria 2D/3D</a>'
        if geometry else ""
    )
    body = (
        metrics_grid(cards)
        + '<div class="chart-grid">'
        + chart_div("stage-correlation", "Korelacja na etapach pipeline", "Mniejsza wartość bezwzględna jest lepsza.", 350)
        + chart_div("stage-bias", "Bias na etapach pipeline", "Odchylenie P(1) od idealnego 0,5.", 350)
        + '</div><div class="chart-grid">'
        + chart_div("stage-hmin", "Empiryczna Hmin bajtu", "Metryka histogramowa, nie formalna estymacja SP 800-90B.", 350)
        + chart_div("stage-chi-p", "Chi-square p-value", "Pomaga wykryć nierównomierność bajtów; nie jest samodzielnym kryterium jakości.", 350)
        + '</div>'
        + '<section><h2>Najważniejsze metryki</h2>' + stage_table + '</section>'
        + binary_geometry_section(run, geometry)
        + '<details><summary>Konfiguracja przebiegu</summary>' + table_html(["Parametr", "Wartość"], config_rows, compact=True) + '</details>'
        + (f'<details><summary>Pliki wynikowe i sumy SHA-256</summary>{file_table}</details>' if file_table else "")
        + f'<section><h2>Szczegółowe analizy</h2><p>{dual_link} {geometry_link}</p><ul>{analysis_links}</ul></section>'
        + '<section><h2>Metadane</h2><p><a href="runner_summary.json">runner_summary.json</a> · <a href="output_complete.json">output_complete.json</a> · <a href="READY.json">READY.json</a> · <a href="console.log">console.log</a></p></section>'
    )
    navigation = '<a href="../">Katalog nadrzędny</a>' + dual_link + geometry_link
    document = html_page(
        title=f"Raport przebiegu — {run.name}",
        subtitle=f"Status: {status}",
        navigation=navigation,
        body=body,
        plot_specs=plot_specs,
        extra_css="""
.pipeline-diagram{display:block;width:100%;max-height:620px;object-fit:contain;background:#0d1117;border-radius:10px;margin:12px 0}
.geometry-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:14px;margin:14px 0}
.geometry-thumb{margin:0}.geometry-thumb>figcaption{color:var(--muted);font-size:.78rem;margin:0 0 6px;overflow-wrap:anywhere}.geometry-thumb-pair{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.geometry-thumb-pair a{display:block}.geometry-thumb-pair.geometry-thumb-single{grid-template-columns:minmax(0,1fr)}.geometry-thumb-pair img{display:block;width:100%;border:1px solid var(--border);border-radius:9px;background:#f5f5f5}.geometry-thumb-pair span{display:block;color:var(--muted);font-size:.72rem;margin-top:4px;text-align:center}
@media(max-width:700px){.geometry-grid{grid-template-columns:1fr}.geometry-thumb-pair{grid-template-columns:1fr}}
""",
    )
    (run / "run_report.html").write_text(document, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
