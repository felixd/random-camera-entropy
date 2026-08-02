#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused analysis for complementary dual-phase weave and staggered variants."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from report_ui import (
    chart_div, esc, fmt as ui_fmt, fmt_percent, fmt_rate, html_page,
    metric_card, metrics_grid, table_html,
)

BASE_LAGS = (
    -2049, -2048, -2047,
    -1281, -1280, -1279,
    -1026, -1025, -1024, -1023, -1022,
    -64, -32, -16, -8, -4, -3, -2, -1,
    0,
    1, 2, 3, 4, 8, 16, 32, 64,
    1022, 1023, 1024, 1025, 1026,
    1279, 1280, 1281,
    2047, 2048, 2049,
)
KEY_SERIAL_LAGS = (1022, 1023, 1024, 1025, 1026, 2047, 2048, 2049)
POSITIONAL_LAGS = (-2, -1, 0, 1, 2)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def unpack(path: Path) -> np.ndarray:
    if not path.is_file() or path.stat().st_size == 0:
        return np.empty(0, dtype=np.uint8)
    return np.unpackbits(np.fromfile(path, dtype=np.uint8), bitorder="big")


def correlation(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    n = min(a.size, b.size)
    if n <= 0:
        return {"pairs": 0, "phi": None, "mutual_information_bits": None, "same_rate": None}
    a = a[:n].astype(np.uint8, copy=False)
    b = b[:n].astype(np.uint8, copy=False)
    counts = np.bincount((a << 1) | b, minlength=4).astype(np.int64)
    n00, n01, n10, n11 = (int(x) for x in counts)
    den = math.sqrt((n10 + n11) * (n00 + n01) * (n01 + n11) * (n00 + n10))
    phi = (n11 * n00 - n10 * n01) / den if den else None
    total = n00 + n01 + n10 + n11
    mi = 0.0
    if total:
        table = ((n00, n01), (n10, n11))
        rows = (n00 + n01, n10 + n11)
        cols = (n00 + n10, n01 + n11)
        for i in range(2):
            for j in range(2):
                count = table[i][j]
                if count:
                    probability = count / total
                    mi += probability * math.log2(count * total / (rows[i] * cols[j]))
    return {
        "pairs": total,
        "n00": n00,
        "n01": n01,
        "n10": n10,
        "n11": n11,
        "phi": phi,
        "mutual_information_bits": mi,
        "same_rate": (n00 + n11) / total if total else None,
    }


def shifted(a: np.ndarray, b: np.ndarray, lag: int) -> tuple[np.ndarray, np.ndarray]:
    if lag == 0:
        n = min(a.size, b.size)
        return a[:n], b[:n]
    if lag > 0:
        n = min(a.size, b.size - lag)
        if n <= 0:
            return a[:0], b[:0]
        return a[:n], b[lag:lag + n]
    offset = -lag
    n = min(a.size - offset, b.size)
    if n <= 0:
        return a[:0], b[:0]
    return a[offset:offset + n], b[:n]


def analysis_summary(run: Path, stem: str) -> dict[str, Any]:
    return load_json(run / f"analysis_{stem}" / "summary.json")


def analysis_lags(run: Path, stem: str) -> dict[int, dict[str, Any]]:
    path = run / f"analysis_{stem}" / "lag_correlation.csv"
    result: dict[int, dict[str, Any]] = {}
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                lag = int(row["lag"])
                converted: dict[str, Any] = {"lag": lag}
                for key, value in row.items():
                    if key == "lag":
                        continue
                    try:
                        converted[key] = float(value) if value not in (None, "") else None
                    except ValueError:
                        converted[key] = value
                result[lag] = converted
    except OSError:
        pass
    return result


def lag1_phi(summary: dict[str, Any]) -> Any:
    value = summary.get("lag1")
    return value.get("phi") if isinstance(value, dict) else None


def block_positional_correlations(bits: np.ndarray, input_bits: int) -> list[dict[str, Any]]:
    if input_bits < 2 or input_bits % 2:
        return []
    blocks = bits.size // input_bits
    if blocks <= 0:
        return []
    matrix = bits[:blocks * input_bits].reshape(blocks, input_bits)
    half = input_bits // 2
    c0 = matrix[:, :half]
    c1 = matrix[:, half:]
    rows: list[dict[str, Any]] = []
    for lag in POSITIONAL_LAGS:
        if lag == 0:
            a, b = c0.reshape(-1), c1.reshape(-1)
        elif lag > 0:
            a, b = c0[:, :-lag].reshape(-1), c1[:, lag:].reshape(-1)
        else:
            offset = -lag
            a, b = c0[:, offset:].reshape(-1), c1[:, :-offset].reshape(-1)
        rows.append({"position_lag": lag, "blocks": blocks, **correlation(a, b)})
    return rows


def fmt(value: Any, digits: int = 8) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "tak" if value else "nie"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze dual-weave alignment outputs")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    complete = load_json(run / "output_complete.json")
    dual = complete.get("dual_weave", {}) if isinstance(complete.get("dual_weave"), dict) else load_json(run / "dual_weave_summary.json")
    variants = dual.get("variants", {}) if isinstance(dual.get("variants"), dict) else {}

    active = 0
    for variant in variants.values():
        if isinstance(variant, dict):
            last = variant.get("last", {}) if isinstance(variant.get("last"), dict) else {}
            active = max(active, int(last.get("active_pixels") or 0))
    dynamic_lags = set(BASE_LAGS)
    if active:
        dynamic_lags.update((-(active + 1), -active, -(active - 1), active - 1, active, active + 1))
    lags = sorted(dynamic_lags)

    rows: list[dict[str, Any]] = []
    cross_rows: list[dict[str, Any]] = []
    positional_rows: list[dict[str, Any]] = []

    baseline = complete.get("conditioner", {}) if isinstance(complete.get("conditioner"), dict) else {}
    baseline_bps = baseline.get("output_bps_until_complete") or baseline.get("output_bps_lifetime")

    for order, order_status_raw in variants.items():
        if not isinstance(order_status_raw, dict):
            continue
        order_status = order_status_raw
        token = order.replace("-", "_")
        c0_path = run / f"dual_weave_{token}_c0_raw_validation.bin"
        c1_path = run / f"dual_weave_{token}_c1_raw_validation.bin"
        if not c0_path.exists() or not c1_path.exists():
            continue
        c0 = unpack(c0_path)
        c1 = unpack(c1_path)
        order_cross: list[dict[str, Any]] = []
        for lag in lags:
            left, right = shifted(c0, c1, lag)
            row = {"order": order, "lag": lag, **correlation(left, right)}
            order_cross.append(row)
            cross_rows.append(row)
        finite_cross = [r for r in order_cross if isinstance(r.get("phi"), (int, float)) and math.isfinite(float(r["phi"]))]
        worst_cross = max(finite_cross, key=lambda r: abs(float(r["phi"])), default={})

        c0_summary = analysis_summary(run, f"dual_weave_{token}_c0_raw_validation")
        c1_summary = analysis_summary(run, f"dual_weave_{token}_c1_raw_validation")
        c0_vn_summary = analysis_summary(run, f"dual_weave_{token}_c0_vn")
        c1_vn_summary = analysis_summary(run, f"dual_weave_{token}_c1_vn")
        alignments = order_status.get("alignments", {}) if isinstance(order_status.get("alignments"), dict) else {}

        for alignment, alignment_status_raw in alignments.items():
            if not isinstance(alignment_status_raw, dict):
                continue
            alignment_status = alignment_status_raw
            alignment_token = alignment.replace("-", "_")
            input_stem = f"dual_weave_{token}_{alignment_token}_conditioner_input_validation"
            conditioned_stem = f"dual_weave_{token}_{alignment_token}_sha3_512"
            input_path = run / f"{input_stem}.bin"
            input_summary = analysis_summary(run, input_stem)
            input_lags = analysis_lags(run, input_stem)
            conditioned_summary = analysis_summary(run, conditioned_stem)
            conditioner = alignment_status.get("conditioner", {}) if isinstance(alignment_status.get("conditioner"), dict) else {}
            input_bits = int(conditioner.get("input_bits_per_block") or 2048)
            positional = block_positional_correlations(unpack(input_path), input_bits)
            for item in positional:
                positional_rows.append({"order": order, "alignment": alignment, **item})
            finite_pos = [r for r in positional if isinstance(r.get("phi"), (int, float)) and math.isfinite(float(r["phi"]))]
            worst_pos = max(finite_pos, key=lambda r: abs(float(r["phi"])), default={})
            conditioned_bps = conditioner.get("output_bps_until_complete") or conditioner.get("output_bps_lifetime")
            row: dict[str, Any] = {
                "order": order,
                "alignment": alignment,
                "delay_groups": alignment_status.get("delay_groups"),
                "group_pairs_fed": alignment_status.get("group_pairs_fed"),
                "discarded_initial_c1_groups": alignment_status.get("discarded_initial_c1_groups"),
                "pending_tail_c0_groups": alignment_status.get("pending_tail_c0_groups"),
                "active_pixels": active,
                "c0_raw_p1": c0_summary.get("p1"),
                "c1_raw_p1": c1_summary.get("p1"),
                "c0_raw_lag1_phi": lag1_phi(c0_summary),
                "c1_raw_lag1_phi": lag1_phi(c1_summary),
                "c0_vn_lag1_phi": lag1_phi(c0_vn_summary),
                "c1_vn_lag1_phi": lag1_phi(c1_vn_summary),
                "conditioner_input_lag1_phi": lag1_phi(input_summary),
                "conditioner_input_positional_worst_abs_phi": abs(float(worst_pos["phi"])) if worst_pos else None,
                "conditioner_input_positional_worst_lag": worst_pos.get("position_lag"),
                "same_group_cross_worst_abs_phi": abs(float(worst_cross["phi"])) if worst_cross else None,
                "same_group_cross_worst_lag": worst_cross.get("lag"),
                "conditioned_lag1_phi": lag1_phi(conditioned_summary),
                "conditioned_hmin_byte": conditioned_summary.get("byte_min_entropy_bits_per_byte"),
                "conditioned_chi_p": conditioned_summary.get("byte_chi_square_p_value"),
                "conditioned_bps_until_complete": conditioned_bps,
                "time_to_target_seconds": conditioner.get("time_to_target_seconds"),
                "conditioner_blocks": conditioner.get("blocks"),
                "throughput_ratio_vs_checkerboard": (
                    conditioned_bps / baseline_bps
                    if isinstance(conditioned_bps, (int, float)) and isinstance(baseline_bps, (int, float)) and baseline_bps
                    else None
                ),
                "health_latched": bool(order_status.get("latched") or alignment_status.get("latched")),
            }
            for lag in KEY_SERIAL_LAGS:
                row[f"input_phi_lag_{lag}"] = (input_lags.get(lag) or {}).get("phi")
            rows.append(row)

    report = {
        "run": run.name,
        "model": "complementary-temporal-checkerboard-v2",
        "pairing": complete.get("pairing"),
        "spatial_sampling_baseline": complete.get("spatial_sampling"),
        "baseline_conditioned_bps_until_complete": baseline_bps,
        "active_pixels": active,
        "results": rows,
        "same_group_cross_correlation": cross_rows,
        "conditioner_block_positional_correlation": positional_rows,
        "phase_health": dual.get("phase_health"),
        "active_clipping": complete.get("active_clipping"),
        "notes": [
            "same-group combines C0_g with C1_g; stagger-1 combines C0_g with C1_(g+1); stagger-2 uses C1_(g+2).",
            "Throughput uses the conditioner's own time-to-target, not the total runtime of parallel diagnostics.",
            "Serialized conditioner input is explicitly checked at lags 1022..1026 and 2047..2049.",
            "Position-wise C0/C1 correlation is measured inside each 2048-bit conditioner block for offsets -2..+2.",
            "This report is diagnostic and does not replace SP 800-90B non-IID estimation.",
        ],
    }
    def write_csv(path: Path, values: list[dict[str, Any]]) -> None:
        if not values:
            path.write_text("", encoding="utf-8")
            return
        fields: list[str] = []
        for value in values:
            for key in value:
                if key not in fields:
                    fields.append(key)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)

    write_csv(run / "dual_weave_comparison.csv", rows)
    write_csv(run / "dual_weave_cross_correlation.csv", cross_rows)
    write_csv(run / "dual_weave_positional_correlation.csv", positional_rows)

    healthy_rows = [row for row in rows if not row.get("health_latched")]
    ranked_rows = [
        row for row in healthy_rows
        if isinstance(row.get("conditioner_input_positional_worst_abs_phi"), (int, float))
    ]
    diagnostic_best = min(
        ranked_rows,
        key=lambda row: (
            float(row.get("conditioner_input_positional_worst_abs_phi") or math.inf),
            -float(row.get("throughput_ratio_vs_checkerboard") or 0.0),
        ),
        default=None,
    )
    report["diagnostic_best"] = diagnostic_best
    (run / "dual_weave_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    labels = [f"{row.get('order')} / {row.get('alignment')}" for row in rows]
    throughput_values = [row.get("throughput_ratio_vs_checkerboard") for row in rows]
    throughput_bps = [row.get("conditioned_bps_until_complete") for row in rows]

    positional_by_variant: dict[str, list[dict[str, Any]]] = {}
    for item in positional_rows:
        key = f"{item.get('order')} / {item.get('alignment')}"
        positional_by_variant.setdefault(key, []).append(item)

    key_lags = list(KEY_SERIAL_LAGS)
    plot_specs: list[dict[str, Any]] = [
        {
            "id": "throughput-chart",
            "data": [{
                "type": "bar",
                "x": labels,
                "y": throughput_values,
                "text": [fmt_rate(value) for value in throughput_bps],
                "textposition": "auto",
                "hovertemplate": "%{x}<br>%{y:.3f}× baseline<br>%{text}<extra></extra>",
            }],
            "layout": {
                "yaxis": {"title": "Wielokrotność checkerboard", "rangemode": "tozero"},
                "xaxis": {"title": "Wariant", "tickangle": -20},
                "shapes": [{"type": "line", "x0": -0.5, "x1": max(len(labels)-0.5, 0.5), "y0": 2, "y1": 2, "line": {"dash": "dot", "width": 1}}],
            },
        },
        {
            "id": "positional-chart",
            "data": [
                {
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": label,
                    "x": [item.get("position_lag") for item in sorted(values, key=lambda r: int(r.get("position_lag") or 0))],
                    "y": [abs(float(item["phi"])) if isinstance(item.get("phi"), (int, float)) else None for item in sorted(values, key=lambda r: int(r.get("position_lag") or 0))],
                    "hovertemplate": f"{label}<br>offset=%{{x}}<br>|φ|=%{{y:.7f}}<extra></extra>",
                }
                for label, values in positional_by_variant.items()
            ],
            "layout": {
                "xaxis": {"title": "Offset pozycyjny C0 → C1", "dtick": 1},
                "yaxis": {"title": "|φ|", "rangemode": "tozero"},
            },
        },
        {
            "id": "boundary-lags-chart",
            "data": [
                {
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": f"{row.get('order')} / {row.get('alignment')}",
                    "x": key_lags,
                    "y": [
                        abs(float(row[f"input_phi_lag_{lag}"]))
                        if isinstance(row.get(f"input_phi_lag_{lag}"), (int, float)) else None
                        for lag in key_lags
                    ],
                    "hovertemplate": f"{row.get('order')} / {row.get('alignment')}<br>lag=%{{x}}<br>|φ|=%{{y:.7f}}<extra></extra>",
                }
                for row in rows
            ],
            "layout": {
                "xaxis": {"title": "Lag serializowanego wejścia", "type": "category"},
                "yaxis": {"title": "|φ|", "rangemode": "tozero"},
            },
        },
        {
            "id": "cross-small-chart",
            "data": [
                {
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": order,
                    "x": [int(item["lag"]) for item in values if -8 <= int(item.get("lag") or 0) <= 8],
                    "y": [item.get("phi") for item in values if -8 <= int(item.get("lag") or 0) <= 8],
                    "hovertemplate": f"{order}<br>lag=%{{x}}<br>φ=%{{y:.7f}}<extra></extra>",
                }
                for order, values in {
                    order: [item for item in cross_rows if item.get("order") == order]
                    for order in sorted({str(item.get("order")) for item in cross_rows})
                }.items()
            ],
            "layout": {
                "xaxis": {"title": "Lag C0 ↔ C1", "dtick": 1},
                "yaxis": {"title": "φ", "zeroline": True},
            },
        },
    ]

    best_cards: list[str] = []
    if diagnostic_best:
        best_cards.extend([
            metric_card(
                "Najlepszy diagnostycznie",
                f"{diagnostic_best.get('order')} / {diagnostic_best.get('alignment')}",
                "ranking: najmniejsza korelacja pozycyjna, następnie przepustowość",
                "good",
            ),
            metric_card(
                "Najgorsze |φ| pozycyjne",
                ui_fmt(diagnostic_best.get("conditioner_input_positional_worst_abs_phi"), 7),
                f"offset {ui_fmt(diagnostic_best.get('conditioner_input_positional_worst_lag'))}",
            ),
            metric_card(
                "Przepustowość",
                fmt_rate(diagnostic_best.get("conditioned_bps_until_complete")),
                f"{ui_fmt(diagnostic_best.get('throughput_ratio_vs_checkerboard'), 4)}× checkerboard",
            ),
            metric_card(
                "SHA3-512 Hmin bajtu",
                ui_fmt(diagnostic_best.get("conditioned_hmin_byte"), 7),
                f"χ² p={ui_fmt(diagnostic_best.get('conditioned_chi_p'), 6)}",
            ),
        ])
    else:
        best_cards.append(metric_card("Wynik", "Brak kompletnego zdrowego wariantu", "sprawdź latch i pliki analizy", "bad"))

    clip = report.get("active_clipping") if isinstance(report.get("active_clipping"), dict) else {}
    clip_scopes = clip.get("scopes", {}) if isinstance(clip.get("scopes"), dict) else {}
    clipping_cards = []
    for scope in ("full", "even", "odd"):
        value = clip_scopes.get(scope, {}) if isinstance(clip_scopes.get(scope), dict) else {}
        clipping_cards.append(metric_card(
            f"Clipping {scope}",
            fmt_percent(value.get("rate"), 4),
            f"limit {fmt_percent(clip.get('limit'), 4)} · failures {ui_fmt(value.get('failures'))}",
            "bad" if value.get("latched") else "good",
        ))

    key_headers = [
        "Wariant", "Alignment", "Przepustowość", "× baseline", "Czas do celu",
        "Najgorsze |φ| pozycyjne", "Offset", "|φ| lag 1023", "|φ| lag 1024",
        "|φ| lag 1025", "SHA3 |φ| lag-1", "Hmin bajtu", "χ² p", "Health",
    ]
    key_table = table_html(
        key_headers,
        [
            [
                row.get("order"), row.get("alignment"), fmt_rate(row.get("conditioned_bps_until_complete")),
                ui_fmt(row.get("throughput_ratio_vs_checkerboard"), 5),
                f"{ui_fmt(row.get('time_to_target_seconds'), 6)} s",
                ui_fmt(row.get("conditioner_input_positional_worst_abs_phi"), 7),
                row.get("conditioner_input_positional_worst_lag"),
                ui_fmt(abs(float(row["input_phi_lag_1023"])) if isinstance(row.get("input_phi_lag_1023"), (int, float)) else None, 7),
                ui_fmt(abs(float(row["input_phi_lag_1024"])) if isinstance(row.get("input_phi_lag_1024"), (int, float)) else None, 7),
                ui_fmt(abs(float(row["input_phi_lag_1025"])) if isinstance(row.get("input_phi_lag_1025"), (int, float)) else None, 7),
                ui_fmt(abs(float(row["conditioned_lag1_phi"])) if isinstance(row.get("conditioned_lag1_phi"), (int, float)) else None, 7),
                ui_fmt(row.get("conditioned_hmin_byte"), 7), ui_fmt(row.get("conditioned_chi_p"), 6),
                "LATCH" if row.get("health_latched") else "OK",
            ]
            for row in rows
        ],
        compact=True,
    )

    detailed_columns = [
        "order", "alignment", "delay_groups", "group_pairs_fed", "discarded_initial_c1_groups",
        "pending_tail_c0_groups", "active_pixels", "c0_raw_p1", "c1_raw_p1",
        "c0_raw_lag1_phi", "c1_raw_lag1_phi", "c0_vn_lag1_phi", "c1_vn_lag1_phi",
        "conditioner_input_lag1_phi", "same_group_cross_worst_abs_phi", "same_group_cross_worst_lag",
        "conditioner_blocks", "time_to_target_seconds", "health_latched",
    ]
    detail_table = table_html(
        detailed_columns,
        [[ui_fmt(row.get(column), 8) for column in detailed_columns] for row in rows],
        compact=True,
    )
    position_table = table_html(
        ["Order", "Alignment", "Offset", "φ", "Mutual information", "Pary"],
        [[item.get("order"), item.get("alignment"), item.get("position_lag"), ui_fmt(item.get("phi"), 8), ui_fmt(item.get("mutual_information_bits"), 8), item.get("pairs")] for item in positional_rows],
        compact=True,
    )

    verdict = (
        f"Najlepszym wariantem diagnostycznym w tym przebiegu jest "
        f"{diagnostic_best.get('order')} / {diagnostic_best.get('alignment')}. "
        "To ranking porównawczy, nie formalny werdykt entropii."
        if diagnostic_best else
        "Nie wybrano wariantu: brak kompletnego, niezatrasniętego zestawu metryk."
    )
    stagger_diagram = ""
    if (run / "dual_weave_stagger2_checkerboard.svg").is_file():
        stagger_diagram = (
            '<section><h2>Jak dokładnie działa row-major / stagger-2</h2>'
            '<p>Implementacja buduje komplementarne strumienie '
            '<code>C0_g = A_g[EVEN] + B_g[ODD]</code> oraz '
            '<code>C1_g = B_g[EVEN] + A_g[ODD]</code>. Wariant stagger-2 '
            'kolejkuje C0 przez dwie grupy i podaje do conditionera '
            '<code>C0_g || C1_(g+2)</code>. Nie jest to przeplatanie pojedynczych bitów.</p>'
            '<img class="pipeline-diagram" src="dual_weave_stagger2_checkerboard.svg" '
            'alt="Dual weave row-major stagger-2 checkerboard">'
            '<p><a href="binary_geometry_report.html">Geometria 2D plików BIN</a></p></section>'
        )
    body = (
        f'<div class="callout {"good" if diagnostic_best else "bad"}"><strong>Wniosek:</strong> {esc(verdict)}</div>'
        + metrics_grid(best_cards)
        + stagger_diagram
        + '<div class="chart-grid">'
        + chart_div("throughput-chart", "Rzeczywista przepustowość", "Czas jest liczony do osiągnięcia celu przez dany conditioner, a nie do końca całego smoke.", 360)
        + chart_div("positional-chart", "Korelacja C0 ↔ C1 w bloku SHA3", "Najważniejszy wykres dla porównania same-group i stagger. Mniejsza wartość |φ| jest lepsza.", 360)
        + '</div><div class="chart-grid">'
        + chart_div("boundary-lags-chart", "Korelacja przy granicach bloków", "Kontrola lagów 1022–1026 oraz 2047–2049 w serializowanym wejściu conditionera.", 360)
        + chart_div("cross-small-chart", "Źródłowa korelacja C0 ↔ C1", "Pokazuje pik przy małych przesunięciach przed zastosowaniem alignmentu i SHA3.", 360)
        + '</div>'
        + '<section><h2>Najważniejsze metryki</h2>' + key_table + '</section>'
        + '<section><h2>Clipping fail-closed</h2>' + metrics_grid(clipping_cards) + '</section>'
        + '<details><summary>Pełna tabela diagnostyczna</summary>' + detail_table + '</details>'
        + '<details><summary>Wszystkie korelacje pozycyjne</summary>' + position_table + '</details>'
        + '<section><h2>Jak czytać wynik</h2><p>Dobry kandydat powinien jednocześnie zachować około 2× przepustowości checkerboardu, obniżyć korelację pozycyjną C0↔C1 względem same-group, nie tworzyć pików przy 1023–1025 i 2047–2049, mieć prawidłowe wyjście SHA3 oraz zero latchy RCT/APT i clippingu. Raport pozostaje diagnostyczny i nie zastępuje SP 800-90B non-IID.</p></section>'
    )
    navigation = (
        '<a href="run_report.html">Raport główny</a>'
        '<a href="dual_weave_report.json">JSON</a>'
        '<a href="dual_weave_comparison.csv">Porównanie CSV</a>'
        '<a href="dual_weave_positional_correlation.csv">Pozycje CSV</a>'
        + ('<a href="binary_geometry_report.html">Geometria 2D</a>' if stagger_diagram else '')
    )
    doc = html_page(
        title="Dual weave: same-group kontra stagger",
        subtitle=run.name,
        navigation=navigation,
        body=body,
        plot_specs=plot_specs,
        extra_css=".pipeline-diagram{display:block;width:100%;max-height:720px;object-fit:contain;background:#0d1117;border-radius:10px}",
    )
    (run / "dual_weave_report.html").write_text(doc, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
