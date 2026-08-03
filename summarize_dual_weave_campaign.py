#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any

from report_ui import (
    chart_div, esc, fmt, fmt_rate, html_page, metric_card, metrics_grid, rate_span, rate_unit_selector, table_html,
)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def finite_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(float(value))
    return values


def aggregate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((str(row.get("order")), str(row.get("alignment"))), []).append(row)
    result: list[dict[str, Any]] = []
    for (order, alignment), items in sorted(grouped.items()):
        throughput = finite_values(items, "throughput_ratio_vs_checkerboard")
        throughput_bps = finite_values(items, "conditioned_bps_until_complete")
        positional = finite_values(items, "conditioner_input_positional_worst_abs_phi")
        conditioned_phi = [abs(value) for value in finite_values(items, "conditioned_lag1_phi")]
        hmin = finite_values(items, "conditioned_hmin_byte")
        chi_p = finite_values(items, "conditioned_chi_p")
        time_to_target = finite_values(items, "time_to_target_seconds")
        result.append({
            "order": order,
            "alignment": alignment,
            "runs": len(items),
            "latches": sum(1 for item in items if item.get("health_latched")),
            "throughput_ratio_mean": statistics.fmean(throughput) if throughput else None,
            "throughput_ratio_min": min(throughput) if throughput else None,
            "throughput_bps_mean": statistics.fmean(throughput_bps) if throughput_bps else None,
            "positional_abs_phi_mean": statistics.fmean(positional) if positional else None,
            "positional_abs_phi_max": max(positional) if positional else None,
            "conditioned_abs_lag1_phi_max": max(conditioned_phi) if conditioned_phi else None,
            "conditioned_hmin_min": min(hmin) if hmin else None,
            "conditioned_chi_p_min": min(chi_p) if chi_p else None,
            "time_to_target_mean": statistics.fmean(time_to_target) if time_to_target else None,
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_dir", type=Path)
    args = parser.parse_args()
    root = args.campaign_dir.resolve()
    rows: list[dict[str, Any]] = []
    for run in sorted(path for path in root.iterdir() if path.is_dir()):
        report = load(run / "dual_weave_report.json")
        complete = load(run / "output_complete.json")
        pairing = complete.get("pairing", {}) if isinstance(complete.get("pairing"), dict) else {}
        for result in report.get("results", []) if isinstance(report.get("results"), list) else []:
            if isinstance(result, dict):
                rows.append({"run": run.name, "k": pairing.get("lag_frames"), **result})

    aggregates = aggregate_rows(rows)
    eligible = [item for item in aggregates if item.get("latches") == 0 and isinstance(item.get("positional_abs_phi_max"), (int, float))]
    best = min(
        eligible,
        key=lambda item: (
            float(item.get("positional_abs_phi_max") or math.inf),
            -float(item.get("throughput_ratio_mean") or 0.0),
        ),
        default=None,
    )
    output = {"campaign": root.name, "rows": rows, "aggregate_by_variant": aggregates, "diagnostic_best": best}
    (root / "dual_weave_campaign_summary.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["run", "k", "order", "alignment"]
    with (root / "dual_weave_campaign_summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with (root / "dual_weave_campaign_aggregate.csv").open("w", newline="", encoding="utf-8") as stream:
        aggregate_fields = list(aggregates[0]) if aggregates else ["order", "alignment", "runs"]
        writer = csv.DictWriter(stream, fieldnames=aggregate_fields)
        writer.writeheader()
        writer.writerows(aggregates)

    labels = [f"{row.get('run')} · {row.get('order')}/{row.get('alignment')}" for row in rows]
    plot_specs = [
        {
            "id": "campaign-throughput",
            "data": [{
                "type": "bar",
                "x": labels,
                "y": [row.get("throughput_ratio_vs_checkerboard") for row in rows],
                "text": [fmt_rate(row.get("conditioned_bps_until_complete")) for row in rows],
                "textposition": "auto",
                "hovertemplate": "%{x}<br>%{y:.3f}× baseline<br>%{text}<extra></extra>",
            }],
            "layout": {
                "xaxis": {"title": "Przebieg i wariant", "tickangle": -30},
                "yaxis": {"title": "Wielokrotność checkerboard", "rangemode": "tozero"},
                "shapes": [{"type": "line", "x0": -0.5, "x1": max(len(labels)-0.5, .5), "y0": 2, "y1": 2, "line": {"dash": "dot", "width": 1}}],
            },
        },
        {
            "id": "campaign-absolute-throughput", "rateAxis": "y", "rateTitle": "SHA3-512",
            "data": [{
                "type": "bar", "x": labels,
                "y": [row.get("conditioned_bps_until_complete") for row in rows],
                "name": "SHA3-512",
            }],
            "layout": {"xaxis": {"title": "Przebieg i wariant", "tickangle": -30}, "yaxis": {"rangemode": "tozero"}},
        },
        {
            "id": "campaign-positional",
            "data": [
                {
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": f"{order}/{alignment}",
                    "x": [item.get("run") for item in rows if str(item.get("order")) == order and str(item.get("alignment")) == alignment],
                    "y": [item.get("conditioner_input_positional_worst_abs_phi") for item in rows if str(item.get("order")) == order and str(item.get("alignment")) == alignment],
                    "hovertemplate": f"{order}/{alignment}<br>%{{x}}<br>|φ|=%{{y:.7f}}<extra></extra>",
                }
                for order, alignment in sorted({(str(row.get("order")), str(row.get("alignment"))) for row in rows})
            ],
            "layout": {
                "xaxis": {"title": "Przebieg"},
                "yaxis": {"title": "Najgorsze |φ| pozycyjne", "rangemode": "tozero"},
            },
        },
        {
            "id": "campaign-boundary",
            "data": [
                {
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": f"{row.get('run')} · {row.get('alignment')}",
                    "x": [1023, 1024, 1025],
                    "y": [
                        abs(float(row[f"input_phi_lag_{lag}"])) if isinstance(row.get(f"input_phi_lag_{lag}"), (int, float)) else None
                        for lag in (1023, 1024, 1025)
                    ],
                    "hovertemplate": f"{row.get('run')} · {row.get('alignment')}<br>lag=%{{x}}<br>|φ|=%{{y:.7f}}<extra></extra>",
                }
                for row in rows
            ],
            "layout": {
                "xaxis": {"title": "Lag", "dtick": 1},
                "yaxis": {"title": "|φ| przy granicy połówek", "rangemode": "tozero"},
            },
        },
        {
            "id": "campaign-output",
            "data": [
                {
                    "type": "bar",
                    "name": "|φ| lag-1 SHA3",
                    "x": labels,
                    "y": [abs(float(row["conditioned_lag1_phi"])) if isinstance(row.get("conditioned_lag1_phi"), (int, float)) else None for row in rows],
                    "hovertemplate": "%{x}<br>|φ|=%{y:.7f}<extra></extra>",
                }
            ],
            "layout": {
                "xaxis": {"title": "Przebieg i wariant", "tickangle": -30},
                "yaxis": {"title": "|φ| lag-1 po SHA3", "rangemode": "tozero"},
            },
        },
    ]

    cards = []
    if best:
        cards.extend([
            metric_card("Najlepszy wariant", f"{best.get('order')} / {best.get('alignment')}", "diagnostyczny ranking całej kampanii", "good"),
            metric_card("Średnia przepustowość", rate_span(best.get("throughput_bps_mean")), f"{fmt(best.get('throughput_ratio_mean'), 5)}× baseline"),
            metric_card("Maks. |φ| pozycyjne", fmt(best.get("positional_abs_phi_max"), 7), f"średnia {fmt(best.get('positional_abs_phi_mean'), 7)}"),
            metric_card("Najgorsze χ² p SHA3", fmt(best.get("conditioned_chi_p_min"), 6), f"Hmin min {fmt(best.get('conditioned_hmin_min'), 7)}"),
        ])
    else:
        cards.append(metric_card("Wynik", "Brak zdrowego kompletnego wariantu", "sprawdź latches i raporty przebiegów", "bad"))

    aggregate_table = table_html(
        ["Order", "Alignment", "Runs", "Latches", "Śr. × baseline", "Śr. przepustowość", "Maks. |φ| pozycyjne", "Maks. |φ| SHA3", "Min Hmin", "Min χ² p"],
        [[
            item.get("order"), item.get("alignment"), item.get("runs"), item.get("latches"),
            fmt(item.get("throughput_ratio_mean"), 6), rate_span(item.get("throughput_bps_mean")),
            fmt(item.get("positional_abs_phi_max"), 7), fmt(item.get("conditioned_abs_lag1_phi_max"), 7),
            fmt(item.get("conditioned_hmin_min"), 7), fmt(item.get("conditioned_chi_p_min"), 6),
        ] for item in aggregates],
        compact=True,
    )
    run_table = table_html(
        ["Run", "k", "Order", "Alignment", "× baseline", "Przepustowość", "Czas", "Maks. |φ| pozycyjne", "Offset", "|φ| 1023", "|φ| 1024", "|φ| 1025", "SHA3 |φ|", "Hmin", "χ² p", "Health"],
        [[
            row.get("run"), row.get("k"), row.get("order"), row.get("alignment"),
            fmt(row.get("throughput_ratio_vs_checkerboard"), 5), rate_span(row.get("conditioned_bps_until_complete")),
            f"{fmt(row.get('time_to_target_seconds'), 6)} s", fmt(row.get("conditioner_input_positional_worst_abs_phi"), 7),
            row.get("conditioner_input_positional_worst_lag"),
            fmt(abs(float(row["input_phi_lag_1023"])) if isinstance(row.get("input_phi_lag_1023"), (int, float)) else None, 7),
            fmt(abs(float(row["input_phi_lag_1024"])) if isinstance(row.get("input_phi_lag_1024"), (int, float)) else None, 7),
            fmt(abs(float(row["input_phi_lag_1025"])) if isinstance(row.get("input_phi_lag_1025"), (int, float)) else None, 7),
            fmt(abs(float(row["conditioned_lag1_phi"])) if isinstance(row.get("conditioned_lag1_phi"), (int, float)) else None, 7),
            fmt(row.get("conditioned_hmin_byte"), 7), fmt(row.get("conditioned_chi_p"), 6),
            "LATCH" if row.get("health_latched") else "OK",
        ] for row in rows],
        compact=True,
    )
    report_links = "".join(
        f'<li><a href="{esc(path.name)}/dual_weave_report.html">{esc(path.name)} — dual weave</a> · '
        f'<a href="{esc(path.name)}/run_report.html">raport główny</a></li>'
        for path in sorted(item for item in root.iterdir() if item.is_dir())
    )
    verdict = (
        f"Najlepszym wariantem diagnostycznym kampanii jest {best.get('order')} / {best.get('alignment')}. "
        "Wybór minimalizuje najgorszą korelację pozycyjną, a następnie maksymalizuje przepustowość."
        if best else "Kampania nie zawiera kompletnego wariantu bez latcha."
    )
    body = (
        f'<div class="callout {"good" if best else "bad"}"><strong>Wniosek:</strong> {esc(verdict)}</div>'
        + metrics_grid(cards)
        + '<div class="chart-grid">'
        + chart_div("campaign-throughput", "Przepustowość względem checkerboardu", "Każdy słupek używa własnego czasu osiągnięcia celu conditionera.", 390)
        + chart_div("campaign-absolute-throughput", "Bezwzględna przepustowość SHA3-512", "Jednostkę można zmienić u góry raportu.", 390)
        + chart_div("campaign-positional", "Korelacja pozycyjna między C0 i C1", "Najważniejsza metryka do oceny skuteczności stagger.", 390)
        + '</div><div class="chart-grid">'
        + chart_div("campaign-boundary", "Lagi 1023–1025", "Sprawdzenie granicy pomiędzy 1024 bitami C0 i 1024 bitami C1.", 360)
        + chart_div("campaign-output", "Korelacja finalnego SHA3", "Kontrola jakości wyjścia; nie zastępuje estymacji entropii wejścia.", 360)
        + '</div>'
        + '<section><h2>Podsumowanie wariantów</h2>' + aggregate_table + '</section>'
        + '<details open><summary>Wyniki wszystkich przebiegów</summary>' + run_table + '</details>'
        + f'<section><h2>Raporty przebiegów</h2><ul>{report_links}</ul></section>'
        + '<section><h2>Kryteria decyzji</h2><p>Priorytetem jest zero latchy, następnie mała korelacja pozycyjna C0↔C1, brak pików przy 1023–1025 i 2047–2049, około 2× przepustowości checkerboardu oraz stabilne wyjście SHA3. Raport diagnostyczny nie zastępuje SP 800-90B non-IID.</p></section>'
    )
    navigation = (
        rate_unit_selector()
        + '<a href="dual_weave_campaign_summary.json">JSON</a>'
        '<a href="dual_weave_campaign_summary.csv">Przebiegi CSV</a>'
        '<a href="dual_weave_campaign_aggregate.csv">Agregaty CSV</a>'
    )
    doc = html_page(
        title="Dual weave — raport kampanii",
        subtitle=root.name,
        navigation=navigation,
        body=body,
        plot_specs=plot_specs,
    )
    (root / "dual_weave_campaign_report.html").write_text(doc, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
