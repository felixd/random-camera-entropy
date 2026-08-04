#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create the single-file production decision report.

The HTML is the review artifact: every case, exact parameters, health status,
source diagnostics, conditioner output diagnostics and dual-weave variants are
embedded directly in the document as both tables and machine-readable JSON.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any

from report_ui import (
    RawHtml, chart_div, esc, fmt, html_page, metric_card, metrics_grid,
    rate_span, rate_unit_selector, table_html,
)

APP_VERSION = "2026.08.04.camera-entropy-production-assessment-report.7.13.0"
STAGE_ORDER = (
    "lsb-mode-width", "temporal-pairing", "spatial-serialization",
    "entropy-credit", "conditioner", "dual-weave", "reproducibility",
)
STAGE_LABELS = {
    "lsb-mode-width": "1. Tryb próbkowania i szerokość LSB",
    "temporal-pairing": "2. Odstęp i sposób parowania ramek",
    "spatial-serialization": "3. Selekcja przestrzenna i serializacja",
    "entropy-credit": "4. Czułość na przypisany entropy credit",
    "conditioner": "5. Rozmiar wejścia SHA3-512",
    "dual-weave": "6. Dual weave: order, alignment i lag",
    "reproducibility": "7. Powtarzalność finalistów",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def nested(record: dict[str, Any], *keys: str) -> Any:
    current: Any = record
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def select_rate(record: dict[str, Any], names: tuple[str, ...]) -> tuple[float | None, str]:
    rates = record.get("rates", {}) if isinstance(record.get("rates"), dict) else {}
    for name in names:
        value = finite(rates.get(name))
        if value is not None and value > 0:
            return value, name
    return None, ""


def merge_parameters(case: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key, value in case.get("parameters", {}).items() if isinstance(case.get("parameters"), dict) else []:
        merged[key] = value
    for key, value in config.items():
        if key.startswith("_") or key in {"command_line", "rtsp_url"}:
            continue
        merged[key] = value
    return merged


def collect_case(root: Path, state_path: Path) -> dict[str, Any]:
    case = read_json(state_path)
    run_rel = str(case.get("run_dir") or "")
    run = root / run_rel if run_rel else root / "runs" / str(case.get("id") or "")
    config = read_json(run / "runner_config.json")
    complete = read_json(run / "output_complete.json")
    failed = read_json(run / "run_failed.json")
    terminal = complete or failed
    bitplanes = read_json(run / "lsb_bitplane_summary.json")
    stream_lsb = read_json(run / "stream_lsb_summary.json")
    stream_aggregate = stream_lsb.get("aggregate", {}) if isinstance(stream_lsb.get("aggregate"), dict) else {}
    conditioned = read_json(run / "analysis_conditioned" / "summary.json")
    raw_rate, raw_basis = select_rate(terminal, ("raw_change_bps_lifetime", "raw_change_bps_10s", "raw_change_bps_current"))
    masked_rate, masked_basis = select_rate(terminal, ("masked_bps_lifetime", "masked_bps_10s", "masked_bps_current"))
    output_rate = finite(nested(terminal, "conditioner", "output_bps_until_complete"))
    health = terminal.get("health", {}) if isinstance(terminal.get("health"), dict) else {}
    bits = int(config.get("lsb_bits", case.get("parameters", {}).get("LSB_BITS", 1)) or 1)
    hmin_symbol = finite(stream_aggregate.get("symbol_min_entropy_bits_per_symbol"))
    hmin_basis = "stream-all" if hmin_symbol is not None else "validation-file"
    if hmin_symbol is None:
        hmin_symbol = finite(bitplanes.get("symbol_min_entropy_bits_per_symbol"))
    symbols_per_second = masked_rate / bits if masked_rate is not None and bits > 0 else None
    empirical_hmin_rate = symbols_per_second * hmin_symbol if symbols_per_second is not None and hmin_symbol is not None else None
    credit = finite(config.get("entropy_credit_bits_per_pixel", case.get("parameters", {}).get("ENTROPY_CREDIT_BITS_PER_PIXEL")))
    credited_rate = symbols_per_second * credit if symbols_per_second is not None and credit is not None else None
    output_p1 = finite(conditioned.get("p1"))
    output_lag1 = finite(nested(conditioned, "lag1", "phi"))
    status = "failed" if failed else "complete" if complete else str(case.get("status") or "incomplete")
    stream_hmin_per_bit = finite(stream_aggregate.get("symbol_min_entropy_bits_per_input_bit"))
    validation_hmin_per_bit = finite(bitplanes.get("symbol_min_entropy_bits_per_input_bit"))
    cross_phi = finite(stream_aggregate.get("max_abs_cross_plane_phi"))
    cross_mi = finite(stream_aggregate.get("max_cross_plane_mutual_information_bits"))
    if cross_phi is None:
        cross_phi = finite(bitplanes.get("max_abs_cross_plane_phi"))
    if cross_mi is None:
        cross_mi = finite(bitplanes.get("max_cross_plane_mutual_information_bits"))
    row = {
        "index": case.get("index"), "id": case.get("id"), "stage": case.get("stage"), "label": case.get("label"),
        "status": status, "exit_code": case.get("exit_code"), "run_dir": run_rel,
        "log": case.get("log"), "started_utc": case.get("started_utc"), "ended_utc": case.get("ended_utc"),
        "sample_mode": config.get("sample_mode", case.get("parameters", {}).get("SAMPLE_MODE")),
        "lsb_bits": bits, "pairing_mode": config.get("pairing_mode", case.get("parameters", {}).get("PAIRING_MODE")),
        "pair_lag_frames": config.get("pair_lag_frames", case.get("parameters", {}).get("PAIR_LAG_FRAMES")),
        "spatial_mask_pattern": config.get("spatial_mask_pattern", case.get("parameters", {}).get("SPATIAL_MASK_PATTERN")),
        "spatial_sampling": config.get("spatial_sampling", case.get("parameters", {}).get("SPATIAL_SAMPLING")),
        "serialization_order": config.get("serialization_order", case.get("parameters", {}).get("SERIALIZATION_ORDER")),
        "conditioner_input_bits": config.get("conditioner_input_bits", case.get("parameters", {}).get("CONDITIONER_INPUT_BITS")),
        "entropy_credit_bits_per_pixel": credit, "active_pixels": terminal.get("active_pixels"),
        "raw_input_bps": raw_rate, "raw_rate_basis": raw_basis,
        "masked_input_bps": masked_rate, "masked_rate_basis": masked_basis,
        "pixel_symbols_per_second": symbols_per_second, "credited_entropy_bps": credited_rate,
        "empirical_hmin_bps": empirical_hmin_rate, "conditioned_output_bps": output_rate,
        "time_to_target_seconds": finite(nested(terminal, "conditioner", "time_to_target_seconds")),
        "symbol_hmin": hmin_symbol,
        "hmin_per_input_bit": stream_hmin_per_bit if stream_hmin_per_bit is not None else validation_hmin_per_bit,
        "hmin_basis": hmin_basis,
        "validation_hmin_per_input_bit": validation_hmin_per_bit,
        "stream_total_symbols": stream_aggregate.get("total_symbols"),
        "stream_pair_updates": stream_aggregate.get("pair_updates"),
        "stream_window_count": stream_lsb.get("window_count"),
        "worst_window_hmin_per_input_bit": finite(stream_lsb.get("worst_window_hmin_per_input_bit")),
        "max_window_abs_bit_bias": finite(stream_lsb.get("max_window_abs_bit_bias")),
        "max_window_abs_bit_lag1_phi": finite(stream_lsb.get("max_window_abs_bit_lag1_phi")),
        "stream_windows": stream_lsb.get("windows", []) if isinstance(stream_lsb.get("windows"), list) else [],
        "max_cross_plane_phi": cross_phi,
        "max_cross_plane_mi": cross_mi,
        "output_byte_hmin": finite(conditioned.get("byte_min_entropy_bits_per_byte")),
        "output_p1": output_p1, "output_p1_deviation": abs(output_p1 - .5) if output_p1 is not None else None,
        "output_lag1": output_lag1, "output_abs_lag1": abs(output_lag1) if output_lag1 is not None else None,
        "output_chi_square_p": finite(conditioned.get("byte_chi_square_p_value")),
        "health_latched": bool(health.get("latched") or terminal.get("health_latched")),
        "rct_failures": int(health.get("rct_failures") or 0), "apt_failures": int(health.get("apt_failures") or 0),
        "failure_reason": str(failed.get("reason") or terminal.get("error") or ""),
        "parameters": merge_parameters(case, config),
        "report": f"{run_rel}/run_report.html" if (run / "run_report.html").is_file() else "",
        "bitplane_report": f"{run_rel}/lsb_bitplane_report.html" if (run / "lsb_bitplane_report.html").is_file() else "",
        "stream_lsb_json": f"{run_rel}/stream_lsb_summary.json" if stream_lsb else "",
        "failure_json": f"{run_rel}/run_failed.json" if failed else "",
    }
    dual = read_json(run / "dual_weave_report.json")
    variants: list[dict[str, Any]] = []
    for item in dual.get("results", []) if isinstance(dual.get("results"), list) else []:
        if isinstance(item, dict):
            variants.append({"case_id": row["id"], "case_label": row["label"], "pair_lag_frames": row["pair_lag_frames"], **item})
    row["dual_weave_variants"] = variants
    return row


def safe_link(path: str, label: str) -> str:
    return f'<a href="{esc(path)}">{esc(label)}</a>' if path else ""


def links(row: dict[str, Any]) -> RawHtml:
    values = [safe_link(str(row.get(key) or ""), label) for key, label in (("report", "raport"), ("bitplane_report", "LSB plik"), ("stream_lsb_json", "LSB cały dataset"), ("failure_json", "błąd"), ("log", "log"))]
    return RawHtml(" · ".join(value for value in values if value) or "—")


def stage_rows(cases: list[dict[str, Any]], stage: str) -> list[dict[str, Any]]:
    return [row for row in cases if row.get("stage") == stage]


def healthy(row: dict[str, Any]) -> bool:
    return row.get("status") == "complete" and not row.get("health_latched") and not row.get("rct_failures") and not row.get("apt_failures")


def automated_shortlist(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pool = [row for row in cases if row.get("stage") == "lsb-mode-width" and healthy(row)]
    def score(row: dict[str, Any]) -> float:
        hmin = finite(row.get("hmin_per_input_bit")) or 0.0
        rate = finite(row.get("conditioned_output_bps")) or 0.0
        phi = abs(finite(row.get("max_cross_plane_phi")) or 0.0)
        bits = int(row.get("lsb_bits") or 1)
        mode_bonus = 0.10 if row.get("sample_mode") == "xor" else 0.04 if row.get("sample_mode") == "delta" else 0.0
        width_penalty = 0.04 * max(0, bits - 2)
        return hmin + mode_bonus - phi - width_penalty + min(0.15, math.log10(max(rate, 1.0)) / 100.0)
    return sorted(pool, key=score, reverse=True)[:5]


def aggregate_repeats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("id") or "").rsplit("-repeat-", 1)[0]
        groups.setdefault(key, []).append(row)
    output = []
    for key, items in sorted(groups.items()):
        rates = [value for item in items if (value := finite(item.get("conditioned_output_bps"))) is not None]
        hmins = [value for item in items if (value := finite(item.get("hmin_per_input_bit"))) is not None]
        output.append({
            "candidate": key, "runs": len(items), "healthy": sum(healthy(item) for item in items),
            "rate_mean_bps": statistics.fmean(rates) if rates else None,
            "rate_stdev_bps": statistics.stdev(rates) if len(rates) > 1 else 0.0 if rates else None,
            "rate_cv": statistics.stdev(rates) / statistics.fmean(rates) if len(rates) > 1 and statistics.fmean(rates) else 0.0 if rates else None,
            "hmin_per_bit_min": min(hmins) if hmins else None, "hmin_per_bit_mean": statistics.fmean(hmins) if hmins else None,
        })
    return output


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    config = read_json(root / "assessment_config.json")
    cases = [collect_case(root, path) for path in sorted((root / "cases").glob("*.json"))]
    dual_variants = [variant for row in cases for variant in row.get("dual_weave_variants", [])]
    repeats = aggregate_repeats(stage_rows(cases, "reproducibility"))
    shortlist = automated_shortlist(cases)
    complete = sum(row.get("status") == "complete" for row in cases)
    failed = sum(row.get("status") == "failed" for row in cases)
    incomplete = len(cases) - complete - failed
    health_failures = sum(1 for row in cases if row.get("health_latched") or row.get("rct_failures") or row.get("apt_failures"))
    bundle = {
        "schema": "camera-entropy-production-assessment-v1", "app_version": APP_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign": root.name, "configuration": config,
        "status": {"total": len(cases), "complete": complete, "failed": failed, "incomplete": incomplete, "health_failures": health_failures},
        "automated_shortlist": [{key: row.get(key) for key in ("id", "label", "sample_mode", "lsb_bits", "hmin_per_input_bit", "symbol_hmin", "conditioned_output_bps", "max_cross_plane_phi", "max_cross_plane_mi")} for row in shortlist],
        "reproducibility": repeats, "dual_weave_variants": dual_variants, "cases": cases,
        "review_notes": [
            "The automated shortlist is a screening aid, not a formal entropy claim.",
            "Final production approval should prefer zero health failures, reproducibility and conservative entropy credit.",
            "All supported LSB widths are limited to 1..4.",
        ],
    }
    (root / "production_assessment_summary.json").write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    fields = [
        "index", "id", "stage", "label", "status", "exit_code", "sample_mode", "lsb_bits", "pairing_mode", "pair_lag_frames",
        "spatial_mask_pattern", "spatial_sampling", "serialization_order", "conditioner_input_bits", "entropy_credit_bits_per_pixel",
        "active_pixels", "masked_input_bps", "credited_entropy_bps", "empirical_hmin_bps", "conditioned_output_bps", "time_to_target_seconds",
        "symbol_hmin", "hmin_per_input_bit", "max_cross_plane_phi", "max_cross_plane_mi", "output_byte_hmin", "output_p1", "output_abs_lag1",
        "output_chi_square_p", "health_latched", "rct_failures", "apt_failures", "failure_reason", "run_dir", "log",
    ]
    with (root / "production_assessment_cases.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore"); writer.writeheader(); writer.writerows(cases)

    labels = [str(row.get("id")) for row in cases]
    plot_specs: list[dict[str, Any]] = [
        {
            "id": "all-rates", "rateAxis": "y", "rateTitle": "Przepustowość",
            "data": [
                {"type": "bar", "name": "Masked", "x": labels, "y": [row.get("masked_input_bps") for row in cases]},
                {"type": "bar", "name": "Empiryczne Hmin", "x": labels, "y": [row.get("empirical_hmin_bps") for row in cases]},
                {"type": "bar", "name": "SHA3", "x": labels, "y": [row.get("conditioned_output_bps") for row in cases]},
            ],
            "layout": {"barmode": "group", "xaxis": {"title": "Case", "tickangle": -45}, "yaxis": {"rangemode": "tozero"}},
        },
    ]

    plot_specs.extend([
        {
            "id": "all-source-quality",
            "data": [
                {"type": "bar", "name": "Hmin/input bit", "x": labels, "y": [row.get("hmin_per_input_bit") for row in cases]},
                {"type": "bar", "name": "max |phi| cross-plane", "x": labels, "y": [row.get("max_cross_plane_phi") for row in cases]},
                {"type": "bar", "name": "max MI [bit]", "x": labels, "y": [row.get("max_cross_plane_mi") for row in cases]},
            ],
            "layout": {"barmode": "group", "xaxis": {"title": "Case", "tickangle": -45}, "yaxis": {"rangemode": "tozero"}},
        },
        {
            "id": "all-output-quality",
            "data": [
                {"type": "bar", "name": "|P(1)-0.5|", "x": labels, "y": [row.get("output_p1_deviation") for row in cases]},
                {"type": "bar", "name": "|phi lag-1|", "x": labels, "y": [row.get("output_abs_lag1") for row in cases]},
            ],
            "layout": {"barmode": "group", "xaxis": {"title": "Case", "tickangle": -45}, "yaxis": {"title": "Odchylenie bezwzględne", "rangemode": "tozero"}},
        },
        {
            "id": "all-output-hmin",
            "data": [
                {"type": "bar", "name": "Hmin byte", "x": labels, "y": [row.get("output_byte_hmin") for row in cases]},
                {"type": "scatter", "mode": "markers", "name": "chi-square p", "x": labels, "y": [row.get("output_chi_square_p") for row in cases], "yaxis": "y2"},
            ],
            "layout": {"xaxis": {"title": "Case", "tickangle": -45}, "yaxis": {"title": "Hmin [bit/bajt]", "rangemode": "tozero"}, "yaxis2": {"title": "chi-square p", "overlaying": "y", "side": "right", "range": [0, 1]}},
        },
        {
            "id": "all-time",
            "data": [{"type": "bar", "name": "Czas do celu", "x": labels, "y": [row.get("time_to_target_seconds") for row in cases]}],
            "layout": {"xaxis": {"title": "Case", "tickangle": -45}, "yaxis": {"title": "sekundy", "rangemode": "tozero"}},
        },
        {
            "id": "all-health",
            "data": [
                {"type": "bar", "name": "RCT", "x": labels, "y": [row.get("rct_failures") for row in cases]},
                {"type": "bar", "name": "APT", "x": labels, "y": [row.get("apt_failures") for row in cases]},
                {"type": "bar", "name": "Latch", "x": labels, "y": [1 if row.get("health_latched") else 0 for row in cases]},
            ],
            "layout": {"barmode": "stack", "xaxis": {"title": "Case", "tickangle": -45}, "yaxis": {"title": "Liczba / flaga", "rangemode": "tozero", "dtick": 1}},
        },
    ])

    lsb = stage_rows(cases, "lsb-mode-width")
    lsb_labels = [str(row.get("id")) for row in lsb]
    plot_specs.extend([
        {
            "id": "lsb-rate", "rateAxis": "y", "rateTitle": "Przepustowość",
            "data": [
                {"type": "bar", "name": "Masked", "x": lsb_labels, "y": [row.get("masked_input_bps") for row in lsb]},
                {"type": "bar", "name": "SHA3", "x": lsb_labels, "y": [row.get("conditioned_output_bps") for row in lsb]},
            ], "layout": {"barmode": "group", "xaxis": {"tickangle": -30}, "yaxis": {"rangemode": "tozero"}},
        },
        {
            "id": "lsb-quality",
            "data": [
                {"type": "bar", "name": "Hmin/symbol", "x": lsb_labels, "y": [row.get("symbol_hmin") for row in lsb]},
                {"type": "bar", "name": "Hmin/input bit", "x": lsb_labels, "y": [row.get("hmin_per_input_bit") for row in lsb]},
                {"type": "bar", "name": "max |φ|", "x": lsb_labels, "y": [row.get("max_cross_plane_phi") for row in lsb]},
            ], "layout": {"barmode": "group", "xaxis": {"tickangle": -30}, "yaxis": {"rangemode": "tozero"}},
        },
    ])

    temporal = stage_rows(cases, "temporal-pairing")
    plot_specs.append({
        "id": "temporal-rate", "rateAxis": "y", "rateTitle": "SHA3",
        "data": [
            {"type": "scatter", "mode": "lines+markers", "name": name,
             "x": [row.get("pair_lag_frames") for row in temporal if f"{row.get('sample_mode')}-lsb{row.get('lsb_bits')}-{row.get('pairing_mode')}" == name],
             "y": [row.get("conditioned_output_bps") for row in temporal if f"{row.get('sample_mode')}-lsb{row.get('lsb_bits')}-{row.get('pairing_mode')}" == name]}
            for name in sorted({f"{row.get('sample_mode')}-lsb{row.get('lsb_bits')}-{row.get('pairing_mode')}" for row in temporal})
        ], "layout": {"xaxis": {"title": "pair lag k", "dtick": 1}, "yaxis": {"rangemode": "tozero"}},
    })

    spatial = stage_rows(cases, "spatial-serialization")
    plot_specs.append({
        "id": "spatial-rate", "rateAxis": "y", "rateTitle": "Przepustowość",
        "data": [
            {"type": "bar", "name": "Masked", "x": [row.get("id") for row in spatial], "y": [row.get("masked_input_bps") for row in spatial]},
            {"type": "bar", "name": "SHA3", "x": [row.get("id") for row in spatial], "y": [row.get("conditioned_output_bps") for row in spatial]},
        ], "layout": {"barmode": "group", "xaxis": {"tickangle": -30}, "yaxis": {"rangemode": "tozero"}},
    })

    credit_rows = stage_rows(cases, "entropy-credit")
    plot_specs.extend([
        {
            "id": "credit-rate", "rateAxis": "y", "rateTitle": "Przepustowość",
            "data": [
                {"type": "scatter", "mode": "lines+markers", "name": f"xor LSB{bits}",
                 "x": [row.get("entropy_credit_bits_per_pixel") for row in credit_rows if row.get("lsb_bits") == bits],
                 "y": [row.get("conditioned_output_bps") for row in credit_rows if row.get("lsb_bits") == bits]}
                for bits in sorted({int(row.get("lsb_bits") or 1) for row in credit_rows})
            ],
            "layout": {"xaxis": {"title": "Entropy credit [bit/pixel]"}, "yaxis": {"rangemode": "tozero"}},
        },
        {
            "id": "credit-input",
            "data": [
                {"type": "scatter", "mode": "lines+markers", "name": f"xor LSB{bits}",
                 "x": [row.get("entropy_credit_bits_per_pixel") for row in credit_rows if row.get("lsb_bits") == bits],
                 "y": [row.get("conditioner_input_bits") for row in credit_rows if row.get("lsb_bits") == bits]}
                for bits in sorted({int(row.get("lsb_bits") or 1) for row in credit_rows})
            ],
            "layout": {"xaxis": {"title": "Entropy credit [bit/pixel]"}, "yaxis": {"title": "Minimum input SHA3 [bit]", "rangemode": "tozero"}},
        },
    ])

    conditioner = stage_rows(cases, "conditioner")
    plot_specs.append({
        "id": "conditioner-sweep", "rateAxis": "y", "rateTitle": "SHA3",
        "data": [{"type": "scatter", "mode": "lines+markers", "name": "SHA3 output", "x": [row.get("conditioner_input_bits") for row in conditioner], "y": [row.get("conditioned_output_bps") for row in conditioner]}],
        "layout": {"xaxis": {"title": "Conditioner input [bit]", "type": "log"}, "yaxis": {"rangemode": "tozero"}},
    })

    if dual_variants:
        dual_labels = [f"k{row.get('pair_lag_frames')} {row.get('order')}/{row.get('alignment')}" for row in dual_variants]
        plot_specs.extend([
            {"id": "dual-throughput", "data": [{"type": "bar", "x": dual_labels, "y": [row.get("throughput_ratio_vs_checkerboard") for row in dual_variants], "name": "× checkerboard"}], "layout": {"xaxis": {"tickangle": -35}, "yaxis": {"title": "× baseline", "rangemode": "tozero"}}},
            {"id": "dual-correlation", "data": [{"type": "bar", "x": dual_labels, "y": [row.get("conditioner_input_positional_worst_abs_phi") for row in dual_variants], "name": "max |φ|"}], "layout": {"xaxis": {"tickangle": -35}, "yaxis": {"title": "max |φ|", "rangemode": "tozero"}}},
        ])

    repeat_rows = stage_rows(cases, "reproducibility")
    plot_specs.append({
        "id": "repeat-rate", "rateAxis": "y", "rateTitle": "SHA3",
        "data": [
            {"type": "scatter", "mode": "markers", "name": candidate,
             "x": list(range(1, 1 + len([row for row in repeat_rows if str(row.get('id')).startswith(candidate + '-repeat-')]))),
             "y": [row.get("conditioned_output_bps") for row in repeat_rows if str(row.get("id")).startswith(candidate + "-repeat-")]}
            for candidate in sorted({str(row.get("id")).rsplit("-repeat-", 1)[0] for row in repeat_rows})
        ], "layout": {"xaxis": {"title": "Powtórzenie", "dtick": 1}, "yaxis": {"rangemode": "tozero"}},
    })

    shortlist_text = " · ".join(str(row.get("id")) for row in shortlist) or "brak zdrowych kandydatów"
    cards = [
        metric_card("Przypadki", len(cases), f"complete {complete} · failed {failed} · incomplete {incomplete}", "good" if failed == 0 and incomplete == 0 else "warn"),
        metric_card("Health failures", health_failures, "RCT / APT / latch", "good" if health_failures == 0 else "bad"),
        metric_card("Zakres LSB", "1–4", "maksymalnie cztery dolne bity"),
        metric_card("Poziom kampanii", config.get("level", "—"), f"{config.get('case_count', len(cases))} zaplanowanych przypadków"),
    ]

    common_headers = [
        "Case", "Status", "Tryb", "LSB", "Pairing/k", "Maska", "Order", "Input SHA3", "Credit",
        "Aktywne px", "Raw", "Masked", "Credited", "Emp. Hmin", "SHA3", "Czas [s]",
        "Hmin/symbol", "Hmin/bit", "max |φ|", "max MI", "Output Hmin/B", "|P1-0.5|", "Output |φ1|", "χ² p", "Health", "Linki",
    ]
    def row_cells(row: dict[str, Any]) -> list[Any]:
        return [
            row.get("id"), row.get("status"), row.get("sample_mode"), row.get("lsb_bits"), f"{row.get('pairing_mode')}/k{row.get('pair_lag_frames')}",
            row.get("spatial_mask_pattern") or row.get("spatial_sampling"), row.get("serialization_order"), row.get("conditioner_input_bits"),
            fmt(row.get("entropy_credit_bits_per_pixel"), 6), row.get("active_pixels"),
            rate_span(row.get("raw_input_bps")), rate_span(row.get("masked_input_bps")), rate_span(row.get("credited_entropy_bps")),
            rate_span(row.get("empirical_hmin_bps")), rate_span(row.get("conditioned_output_bps")), fmt(row.get("time_to_target_seconds"), 6),
            fmt(row.get("symbol_hmin"), 7), fmt(row.get("hmin_per_input_bit"), 7), fmt(row.get("max_cross_plane_phi"), 7), fmt(row.get("max_cross_plane_mi"), 7),
            fmt(row.get("output_byte_hmin"), 7), fmt(row.get("output_p1_deviation"), 7), fmt(row.get("output_abs_lag1"), 7), fmt(row.get("output_chi_square_p"), 6),
            "FAIL" if not healthy(row) else "OK", links(row),
        ]

    sections = []
    chart_map = {
        "lsb-mode-width": '<div class="chart-grid">' + chart_div("lsb-rate", "Przepustowość trybów i szerokości", "Wszystkie 12 obsługiwanych kombinacji.", 390) + chart_div("lsb-quality", "Entropia i zależności", "Hmin na symbol/bit oraz korelacja między płaszczyznami.", 390) + '</div>',
        "temporal-pairing": chart_div("temporal-rate", "Wpływ pairingu i laga", "Każda seria to tryb, szerokość LSB i sposób parowania.", 410),
        "spatial-serialization": chart_div("spatial-rate", "Warianty przestrzenne", "Pełny zestaw publicznych masek, offsetów i porządków serializacji.", 410),
        "entropy-credit": '<div class="chart-grid">' + chart_div("credit-rate", "Entropy credit a przepustowość", "Credit jest budżetem wejścia, nie wynikiem estymacji. Wykres pokazuje konsekwencje operacyjne.", 390) + chart_div("credit-input", "Entropy credit a minimalny blok SHA3", "Niższy credit wymaga większej kompresji wejścia do 512 bitów wyjścia.", 390) + '</div>',
        "conditioner": chart_div("conditioner-sweep", "Sweep wejścia SHA3-512", "Wpływ stopnia kompresji na finalną przepustowość.", 390),
        "dual-weave": '<div class="chart-grid">' + chart_div("dual-throughput", "Dual weave — przepustowość względna", "Wszystkie order/alignment dla k=2,4,8.", 400) + chart_div("dual-correlation", "Dual weave — korelacja pozycyjna", "Niższa wartość jest lepsza.", 400) + '</div>' if dual_variants else '',
        "reproducibility": chart_div("repeat-rate", "Powtarzalność przepustowości finalistów", "Rozrzut między powtórzeniami na tym samym datasecie.", 380),
    }
    for stage in STAGE_ORDER:
        rows = stage_rows(cases, stage)
        if not rows:
            continue
        parameter_details = "".join(
            '<details><summary>' + esc(str(row.get("id"))) + ' — parametry</summary>'
            + table_html(["Parametr", "Wartość"], [[key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value] for key, value in sorted(row.get("parameters", {}).items())], compact=True)
            + (f'<p class="callout bad"><strong>Failure:</strong> {esc(row.get("failure_reason"))}</p>' if row.get("failure_reason") else '')
            + '</details>' for row in rows
        )
        sections.append(
            f'<section id="{esc(stage)}"><h2>{esc(STAGE_LABELS.get(stage, stage))}</h2><p class="muted">Przypadki: {len(rows)}. Wszystkie parametry każdego przebiegu są pokazane poniżej.</p></section>'
            + chart_map.get(stage, "")
            + '<section>' + table_html(common_headers, [row_cells(row) for row in rows], compact=True) + '</section>'
            + '<details><summary>Dokładne parametry wszystkich przypadków tej sekcji</summary>' + parameter_details + '</details>'
        )

    repeat_table = table_html(
        ["Kandydat", "Runs", "Healthy", "Średnia SHA3", "σ SHA3", "CV", "Min Hmin/bit", "Śr. Hmin/bit"],
        [[item.get("candidate"), item.get("runs"), item.get("healthy"), rate_span(item.get("rate_mean_bps")), rate_span(item.get("rate_stdev_bps")), fmt(item.get("rate_cv"), 6), fmt(item.get("hmin_per_bit_min"), 7), fmt(item.get("hmin_per_bit_mean"), 7)] for item in repeats], compact=True,
    )
    dual_table = table_html(
        ["Case", "k", "Order", "Alignment", "× baseline", "SHA3", "max |φ| pozycyjne", "SHA3 |φ1|", "Hmin byte", "Health"],
        [[item.get("case_id"), item.get("pair_lag_frames"), item.get("order"), item.get("alignment"), fmt(item.get("throughput_ratio_vs_checkerboard"), 6), rate_span(item.get("conditioned_bps_until_complete")), fmt(item.get("conditioner_input_positional_worst_abs_phi"), 7), fmt(abs(float(item["conditioned_lag1_phi"])) if finite(item.get("conditioned_lag1_phi")) is not None else None, 7), fmt(item.get("conditioned_hmin_byte"), 7), "FAIL" if item.get("health_latched") else "OK"] for item in dual_variants], compact=True,
    ) if dual_variants else "<p>Brak wyników dual weave.</p>"

    full_table = table_html(common_headers, [row_cells(row) for row in cases], compact=True)
    coverage = config.get("coverage", {}) if isinstance(config.get("coverage"), dict) else {}
    coverage_table = table_html(["Obszar", "Pokrycie"], [[key, value] for key, value in coverage.items()], compact=True)
    source_parameters = config.get("source", {}) if isinstance(config.get("source"), dict) else {}
    common_parameters = config.get("common_environment", {}) if isinstance(config.get("common_environment"), dict) else {}
    campaign_parameters = {
        "schema": config.get("schema"), "app_version": config.get("app_version"),
        "created_utc": config.get("created_utc"), "campaign": config.get("campaign"),
        "level": config.get("level"), "max_lsb_bits": config.get("max_lsb_bits"),
        "case_count": config.get("case_count"),
    }
    campaign_config_table = table_html(
        ["Parametr", "Wartość"],
        [[key, value] for key, value in campaign_parameters.items()], compact=True,
    )
    source_config_table = table_html(
        ["Parametr źródła", "Wartość"],
        [[key, value] for key, value in sorted(source_parameters.items())], compact=True,
    ) if source_parameters else "<p>Źródło korzystało z wartości domyślnych procesu nadrzędnego.</p>"
    common_config_table = table_html(
        ["Wspólny parametr", "Wartość"],
        [[key, value] for key, value in sorted(common_parameters.items())], compact=True,
    ) if common_parameters else "<p>Brak wspólnych nadpisań.</p>"
    body = (
        '<div class="callout good"><strong>Plik do ostatecznej oceny:</strong> ten HTML zawiera pełne parametry, wyniki i osadzony JSON. Wystarczy przesłać tylko <code>production_assessment_report.html</code> albo podać link do niego.</div>'
        + '<div class="callout warn"><strong>Automatyczny screening:</strong> ' + esc(shortlist_text) + '. To ranking pomocniczy; produkcyjny entropy credit wymaga osobnej, konserwatywnej decyzji.</div>'
        + metrics_grid(cards)
        + '<section><h2>Konfiguracja całej kampanii</h2>' + campaign_config_table
        + '<h3>Źródło</h3>' + source_config_table
        + '<h3>Wspólne parametry wszystkich przebiegów</h3>' + common_config_table + '</section>'
        + '<section><h2>Pokrycie testów</h2>' + coverage_table + '</section>'
        + chart_div("all-rates", "Wszystkie przypadki — wspólne porównanie przepustowości", "Wykres zawiera te same wartości co tabele. Duża liczba przypadków jest celowa; szczegółowe wykresy są w sekcjach.", 520)
        + '<div class="chart-grid">'
        + chart_div("all-source-quality", "Wszystkie przypadki — jakość źródła", "Hmin na pobrany bit oraz zależności między płaszczyznami.", 430)
        + chart_div("all-output-quality", "Wszystkie przypadki — bias i korelacja SHA3", "Odchylenie P(1) i korelacja lag-1 finalnego pliku.", 430)
        + '</div><div class="chart-grid">'
        + chart_div("all-output-hmin", "Wszystkie przypadki — Hmin bajtu i χ²", "Diagnostyka finalnego pliku; nie jest dowodem entropii wejścia.", 430)
        + chart_div("all-time", "Wszystkie przypadki — czas osiągnięcia celu", "Porównanie kosztu obliczeniowego i przepustowości końcowej.", 430)
        + '</div>'
        + chart_div("all-health", "Wszystkie przypadki — RCT/APT/latch", "Każda wartość niezerowa wymaga wyjaśnienia i wyklucza automatyczny shortlist.", 360)
        + ''.join(sections)
        + '<section><h2>Dual weave — tabela wszystkich wariantów wewnętrznych</h2>' + dual_table + '</section>'
        + '<section><h2>Powtarzalność — agregaty</h2>' + repeat_table + '</section>'
        + '<details><summary>Pełna tabela wszystkich przypadków</summary>' + full_table + '</details>'
        + '<section><h2>Jak przekazać raport do oceny</h2><p>Prześlij ten jeden plik HTML. Dane maszynowe są osadzone w elemencie <code>#production-assessment-data</code>; raport nie wymaga osobnych CSV/JSON do analizy.</p></section>'
        + '<script type="application/json" id="production-assessment-data">' + json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/") + '</script>'
    )
    navigation = rate_unit_selector() + '<a href="production_assessment_summary.json">JSON</a><a href="production_assessment_cases.csv">CSV</a>'
    document = html_page(title="Camera Entropy — kompleksowa ocena produkcyjna", subtitle=f"{root.name} · {config.get('level', 'unknown')} · {len(cases)} przypadków", navigation=navigation, body=body, plot_specs=plot_specs, standalone=True)
    (root / "production_assessment_report.html").write_text(document, encoding="utf-8")
    print(json.dumps({"campaign": root.name, "total": len(cases), "complete": complete, "failed": failed, "incomplete": incomplete}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
