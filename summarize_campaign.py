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

from report_ui import chart_div, esc, fmt, html_page, metric_card, metrics_grid, table_html


def get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(part, default)
    return current


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def masked_spatial_metric(complete: dict[str, Any], key: str) -> Any:
    pixel = complete.get("pixel_correlation", {}) if isinstance(complete.get("pixel_correlation"), dict) else {}
    masked = pixel.get("masked", {}) if isinstance(pixel.get("masked"), dict) else {}
    return masked.get(key, pixel.get(key))


def summary_for(run: Path) -> dict[str, Any]:
    complete = load(run / "output_complete.json")
    failed = load(run / "run_failed.json")
    row: dict[str, Any] = {
        "run": run.name,
        "status": "complete" if complete else ("failed" if failed else "incomplete"),
        "app_version": complete.get("app_version") or failed.get("app_version"),
        "spatial_sampling": complete.get("spatial_sampling") or failed.get("spatial_sampling"),
        "measured_fps": get(complete, "mode.measured_fps"),
        "pair_delta_mean_s": get(complete, "pairing.pair_delta_seconds_mean"),
        "active_pixels": complete.get("active_pixels"),
        "clip_failures": get(complete, "active_clipping.failures", get(failed, "active_clipping.failures")),
        "clip_latched": get(complete, "active_clipping.latched", get(failed, "active_clipping.latched")),
        "control_checks": get(complete, "camera_control_monitor.checks"),
        "control_mismatches": get(complete, "camera_control_monitor.mismatches"),
        "control_latched": get(complete, "camera_control_monitor.latched"),
        "rct_failures": get(complete, "health.rct_failures", get(failed, "health.rct_failures")),
        "apt_failures": get(complete, "health.apt_failures", get(failed, "health.apt_failures")),
        "shadow_active_retention": get(complete, "shadow.active_retention"),
        "shadow_jaccard": get(complete, "shadow.jaccard"),
        "shadow_latched": get(complete, "shadow.latched", get(failed, "shadow.latched")),
        "spatial_max_abs_phi": masked_spatial_metric(complete, "max_abs_phi"),
        "spatial_worst_direction": masked_spatial_metric(complete, "worst_direction"),
        "spatial_worst_distance": masked_spatial_metric(complete, "worst_distance"),
        "conditioned_bytes": get(complete, "conditioner.written_bytes"),
        "conditioner_input_bits": get(complete, "conditioner.input_bits_per_block"),
        "conditioner_compression_ratio": get(complete, "conditioner.compression_ratio"),
        "conditioner_latched": get(complete, "conditioner.latched", get(failed, "conditioner.latched")),
        "conditioned_bps_until_complete": get(complete, "conditioner.output_bps_until_complete", get(complete, "conditioner.output_bps_lifetime")),
    }
    for key, directory in (("conditioned", "analysis_conditioned"), ("vn", "analysis_vn"), ("selected_raw", "analysis_selected_raw")):
        data = load(run / directory / "summary.json")
        row[f"{key}_p1"] = data.get("p1")
        row[f"{key}_lag1_phi"] = get(data, "lag1.phi")
        row[f"{key}_byte_hmin"] = data.get("byte_min_entropy_bits_per_byte")
        row[f"{key}_chi_square"] = data.get("byte_chi_square")
        row[f"{key}_chi_p"] = data.get("byte_chi_square_p_value")
    return row


def finite(rows: list[dict[str, Any]], key: str, absolute: bool = False) -> list[float]:
    output: list[float] = []
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            output.append(abs(float(value)) if absolute else float(value))
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    runs = sorted(path for path in campaign.iterdir() if path.is_dir() and ((path / "output_complete.json").exists() or (path / "run_failed.json").exists()))
    if not runs:
        raise SystemExit("Nie znaleziono przebiegów")
    rows = [summary_for(path) for path in runs]

    csv_path = campaign / "qualification_summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    metric_keys = (
        "conditioned_lag1_phi", "conditioned_byte_hmin", "conditioned_chi_square",
        "conditioned_chi_p", "vn_lag1_phi", "selected_raw_lag1_phi",
        "spatial_max_abs_phi", "shadow_active_retention", "shadow_jaccard",
        "measured_fps", "conditioned_bps_until_complete",
    )
    metrics: dict[str, Any] = {}
    for key in metric_keys:
        values = finite(rows, key)
        if values:
            metrics[key] = {
                "count": len(values), "mean": statistics.fmean(values),
                "min": min(values), "max": max(values), "pstdev": statistics.pstdev(values),
            }
    complete_count = sum(row.get("status") == "complete" for row in rows)
    latch_count = sum(bool(row.get("clip_latched") or row.get("control_latched") or row.get("shadow_latched") or row.get("conditioner_latched") or row.get("rct_failures") or row.get("apt_failures")) for row in rows)
    report = {"campaign": str(campaign), "runs": rows, "aggregate": metrics, "complete_runs": complete_count, "latched_runs": latch_count}
    (campaign / "qualification_summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    run_labels = [row["run"] for row in rows]
    plot_specs = [
        {
            "id": "qualification-lag1",
            "data": [
                {"type": "scatter", "mode": "lines+markers", "name": "Selected RAW", "x": run_labels, "y": [abs(float(row["selected_raw_lag1_phi"])) if isinstance(row.get("selected_raw_lag1_phi"), (int, float)) else None for row in rows]},
                {"type": "scatter", "mode": "lines+markers", "name": "Von Neumann", "x": run_labels, "y": [abs(float(row["vn_lag1_phi"])) if isinstance(row.get("vn_lag1_phi"), (int, float)) else None for row in rows]},
                {"type": "scatter", "mode": "lines+markers", "name": "SHA3-512", "x": run_labels, "y": [abs(float(row["conditioned_lag1_phi"])) if isinstance(row.get("conditioned_lag1_phi"), (int, float)) else None for row in rows]},
            ],
            "layout": {"xaxis": {"title": "Przebieg"}, "yaxis": {"title": "|φ| lag-1", "rangemode": "tozero"}},
        },
        {
            "id": "qualification-output",
            "data": [
                {"type": "bar", "name": "Hmin bajtu", "x": run_labels, "y": [row.get("conditioned_byte_hmin") for row in rows], "yaxis": "y"},
                {"type": "scatter", "mode": "lines+markers", "name": "χ² p", "x": run_labels, "y": [row.get("conditioned_chi_p") for row in rows], "yaxis": "y2"},
            ],
            "layout": {
                "xaxis": {"title": "Przebieg"},
                "yaxis": {"title": "Hmin [bit/bajt]", "range": [0, 8]},
                "yaxis2": {"title": "χ² p-value", "overlaying": "y", "side": "right", "range": [0, 1], "gridcolor": "rgba(0,0,0,0)"},
            },
        },
        {
            "id": "qualification-mask",
            "data": [
                {"type": "scatter", "mode": "lines+markers", "name": "Retencja aktywnej", "x": run_labels, "y": [row.get("shadow_active_retention") for row in rows]},
                {"type": "scatter", "mode": "lines+markers", "name": "Jaccard", "x": run_labels, "y": [row.get("shadow_jaccard") for row in rows]},
            ],
            "layout": {"xaxis": {"title": "Przebieg"}, "yaxis": {"title": "Współczynnik", "range": [0.9, 1.001]}},
        },
        {
            "id": "qualification-spatial",
            "data": [{
                "type": "bar", "x": run_labels, "y": [row.get("spatial_max_abs_phi") for row in rows],
                "text": [f"{row.get('spatial_worst_direction') or '—'} d={row.get('spatial_worst_distance') or '—'}" for row in rows],
                "textposition": "auto", "hovertemplate": "%{x}<br>max |φ|=%{y:.7f}<br>%{text}<extra></extra>",
            }],
            "layout": {"xaxis": {"title": "Przebieg"}, "yaxis": {"title": "Maks. przestrzenne |φ|", "rangemode": "tozero"}},
        },
    ]

    conditioned_abs = finite(rows, "conditioned_lag1_phi", absolute=True)
    hmins = finite(rows, "conditioned_byte_hmin")
    chi_ps = finite(rows, "conditioned_chi_p")
    cards = [
        metric_card("Przebiegi", f"{complete_count}/{len(rows)} complete", f"latches: {latch_count}", "good" if complete_count == len(rows) and latch_count == 0 else "warn"),
        metric_card("Maks. |φ| SHA3", fmt(max(conditioned_abs) if conditioned_abs else None, 8), f"średnia {fmt(statistics.fmean(conditioned_abs) if conditioned_abs else None, 8)}"),
        metric_card("Min Hmin SHA3", fmt(min(hmins) if hmins else None, 8), "bit/bajt"),
        metric_card("Zakres χ² p", f"{fmt(min(chi_ps) if chi_ps else None, 6)} – {fmt(max(chi_ps) if chi_ps else None, 6)}"),
        metric_card("Mask retention min", fmt(min(finite(rows, "shadow_active_retention")) if finite(rows, "shadow_active_retention") else None, 8)),
        metric_card("Maks. spatial |φ|", fmt(max(finite(rows, "spatial_max_abs_phi")) if finite(rows, "spatial_max_abs_phi") else None, 8)),
    ]

    focus_table = table_html(
        ["Run", "Status", "FPS", "Δt pary", "Spatial max |φ|", "RAW |φ|", "VN |φ|", "SHA3 |φ|", "Hmin SHA3", "χ² p", "Retention", "Jaccard", "RCT", "APT", "Latches"],
        [[
            row.get("run"), row.get("status"), fmt(row.get("measured_fps"), 6), fmt(row.get("pair_delta_mean_s"), 6),
            fmt(row.get("spatial_max_abs_phi"), 8),
            fmt(abs(float(row["selected_raw_lag1_phi"])) if isinstance(row.get("selected_raw_lag1_phi"), (int, float)) else None, 8),
            fmt(abs(float(row["vn_lag1_phi"])) if isinstance(row.get("vn_lag1_phi"), (int, float)) else None, 8),
            fmt(abs(float(row["conditioned_lag1_phi"])) if isinstance(row.get("conditioned_lag1_phi"), (int, float)) else None, 8),
            fmt(row.get("conditioned_byte_hmin"), 8), fmt(row.get("conditioned_chi_p"), 7),
            fmt(row.get("shadow_active_retention"), 8), fmt(row.get("shadow_jaccard"), 8),
            row.get("rct_failures"), row.get("apt_failures"),
            "YES" if row.get("clip_latched") or row.get("control_latched") or row.get("shadow_latched") or row.get("conditioner_latched") else "NO",
        ] for row in rows], compact=True,
    )
    all_headers = list(rows[0])
    full_table = table_html(all_headers, [[fmt(row.get(header), 9) for header in all_headers] for row in rows], compact=True)
    aggregate_table = table_html(
        ["Metryka", "Średnia", "Min", "Max", "σ populacji"],
        [[key, fmt(value.get("mean"), 9), fmt(value.get("min"), 9), fmt(value.get("max"), 9), fmt(value.get("pstdev"), 9)] for key, value in metrics.items()],
        compact=True,
    )
    report_links = "".join(
        f'<li><a href="{esc(path.name)}/run_report.html">{esc(path.name)} — raport</a></li>' for path in runs
    )
    verdict_class = "good" if complete_count == len(rows) and latch_count == 0 else "warn"
    verdict = (
        "Wszystkie przebiegi zakończyły się bez latchy. Porównaj wykresy powtarzalności oraz wykonaj formalną estymację non-IID przed deklaracją entropii."
        if verdict_class == "good" else
        "Kampania zawiera przebieg niekompletny lub latch. Sprawdź szczegółowe raporty przed dalszą kwalifikacją."
    )
    body = (
        f'<div class="callout {verdict_class}"><strong>Wniosek:</strong> {esc(verdict)}</div>'
        + metrics_grid(cards)
        + '<div class="chart-grid">'
        + chart_div("qualification-lag1", "Powtarzalność korelacji", "Porównanie selected RAW, VN i finalnego SHA3.", 370)
        + chart_div("qualification-output", "Jakość finalnego wyjścia", "Hmin histogramowa oraz p-value chi-square w każdym przebiegu.", 370)
        + '</div><div class="chart-grid">'
        + chart_div("qualification-mask", "Stabilność maski", "Retencja aktywnej maski i współczynnik Jaccarda.", 350)
        + chart_div("qualification-spatial", "Pozostała korelacja przestrzenna", "Maksymalna wartość |φ| po aktywnej masce.", 350)
        + '</div>'
        + '<section><h2>Najważniejsze wyniki</h2>' + focus_table + '</section>'
        + '<details><summary>Agregaty statystyczne</summary>' + aggregate_table + '</details>'
        + '<details><summary>Pełna tabela pól raportowych</summary>' + full_table + '</details>'
        + f'<section><h2>Raporty przebiegów</h2><ul>{report_links}</ul></section>'
        + '<section><p class="muted">Raport jest diagnostyczny i nie zastępuje estymacji SP 800-90B.</p></section>'
    )
    navigation = '<a href="qualification_summary.json">JSON</a><a href="qualification_summary.csv">CSV</a>'
    document = html_page(
        title="Camera entropy — raport kwalifikacyjny",
        subtitle=campaign.name,
        navigation=navigation,
        body=body,
        plot_specs=plot_specs,
    )
    (campaign / "qualification_report.html").write_text(document, encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
