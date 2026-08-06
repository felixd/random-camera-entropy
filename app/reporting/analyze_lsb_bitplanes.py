#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analyze individual LSB planes and same-pixel cross-plane dependence.

Input is the continuously packed pre-conditioner validation stream produced in
``pixel-major-lsb-first`` order.  The report is diagnostic: marginal and symbol
min-entropy estimates from one dataset are not an SP 800-90B entropy claim.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.reporting.report_ui import chart_div, esc, fmt, html_page, metric_card, metrics_grid, table_html

APP_VERSION = "2026.08.06.camera-entropy-lsb-analysis.8.0.5"
BIT_ORDER = "pixel-major-lsb-first"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def binary_entropy(p1: float) -> float:
    if p1 <= 0.0 or p1 >= 1.0:
        return 0.0
    return -(p1 * math.log2(p1) + (1.0 - p1) * math.log2(1.0 - p1))


def binary_min_entropy(p1: float) -> float:
    return -math.log2(max(p1, 1.0 - p1)) if 0.0 <= p1 <= 1.0 else 0.0


def phi_binary(x: np.ndarray, y: np.ndarray) -> float | None:
    left = np.asarray(x, dtype=np.uint8).reshape(-1)
    right = np.asarray(y, dtype=np.uint8).reshape(-1)
    if left.size != right.size or left.size < 2:
        return None
    n11 = int(np.count_nonzero((left == 1) & (right == 1)))
    n10 = int(np.count_nonzero((left == 1) & (right == 0)))
    n01 = int(np.count_nonzero((left == 0) & (right == 1)))
    n00 = int(left.size - n11 - n10 - n01)
    denominator = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    return (n11 * n00 - n10 * n01) / denominator if denominator else None


def mutual_information_binary(x: np.ndarray, y: np.ndarray) -> float:
    left = np.asarray(x, dtype=np.uint8).reshape(-1)
    right = np.asarray(y, dtype=np.uint8).reshape(-1)
    if left.size != right.size or left.size == 0:
        return 0.0
    counts = np.bincount(left.astype(np.uint8) * 2 + right.astype(np.uint8), minlength=4).reshape(2, 2).astype(np.float64)
    probabilities = counts / float(left.size)
    px = probabilities.sum(axis=1)
    py = probabilities.sum(axis=0)
    result = 0.0
    for i in range(2):
        for j in range(2):
            if probabilities[i, j] > 0 and px[i] > 0 and py[j] > 0:
                result += float(probabilities[i, j] * math.log2(probabilities[i, j] / (px[i] * py[j])))
    return result


def entropy_counts(counts: np.ndarray) -> tuple[float, float]:
    total = int(counts.sum())
    if total <= 0:
        return 0.0, 0.0
    nonzero = counts[counts > 0].astype(np.float64)
    probabilities = nonzero / total
    shannon = float(-np.sum(probabilities * np.log2(probabilities)))
    minimum = -math.log2(float(counts.max()) / total)
    return shannon, minimum


def write_matrix_png(path: Path, matrix: np.ndarray, title: str, unit: str) -> None:
    values = np.asarray(matrix, dtype=np.float64)
    size = max(1, values.shape[0])
    cell = max(70, min(150, 700 // size))
    margin = 100
    canvas = np.full((margin + size * cell + 40, margin + size * cell + 40, 3), 248, dtype=np.uint8)
    finite = values[np.isfinite(values)]
    bound = max(1e-12, float(np.max(np.abs(finite))) if finite.size else 1.0)
    normalized = np.clip((values / bound + 1.0) * 127.5, 0, 255).astype(np.uint8)
    heat = cv2.applyColorMap(normalized, cv2.COLORMAP_TURBO)
    heat = cv2.resize(heat, (size * cell, size * cell), interpolation=cv2.INTER_NEAREST)
    canvas[margin:margin + size * cell, margin:margin + size * cell] = heat
    cv2.putText(canvas, title, (20, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (25, 25, 25), 1, cv2.LINE_AA)
    for index in range(size):
        label = f"b{index}"
        cv2.putText(canvas, label, (margin + index * cell + cell // 3, margin - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (25, 25, 25), 1, cv2.LINE_AA)
        cv2.putText(canvas, label, (26, margin + index * cell + cell // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (25, 25, 25), 1, cv2.LINE_AA)
        for column in range(size):
            value = values[index, column]
            text = "—" if not math.isfinite(float(value)) else f"{float(value):.4g}"
            cv2.putText(canvas, text, (margin + column * cell + 8, margin + index * cell + cell // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (10, 10, 10), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"range +/-{bound:.6g} {unit}", (20, canvas.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (35, 35, 35), 1, cv2.LINE_AA)
    if not cv2.imwrite(str(path), canvas):
        raise OSError(f"cannot write {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--lsb-bits", type=int)
    parser.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.run_dir.expanduser().resolve()
    config = read_json(root / "runner_config.json")
    lsb_bits = int(args.lsb_bits if args.lsb_bits is not None else config.get("lsb_bits", 1))
    if not 1 <= lsb_bits <= 4:
        raise SystemExit("lsb-bits must be in 1..4")
    bit_order = str(config.get("bit_order", BIT_ORDER))
    if bit_order != BIT_ORDER:
        raise SystemExit(f"unsupported bit order: {bit_order}")
    source = (args.input or (root / "y_temporal_masked_validation.bin")).expanduser()
    if not source.is_absolute():
        source = root / source
    if not source.is_file() or source.stat().st_size == 0:
        print(json.dumps({"status": "skipped", "reason": f"missing input: {source}"}, ensure_ascii=False))
        return 0
    max_bytes = max(1, int(args.max_bytes))
    with source.open("rb") as stream:
        raw = stream.read(max_bytes)
    packed = np.frombuffer(raw, dtype=np.uint8)
    unpacked = np.unpackbits(packed, bitorder="big")
    usable = (unpacked.size // lsb_bits) * lsb_bits
    samples = unpacked[:usable].reshape(-1, lsb_bits)
    if samples.shape[0] < 2:
        print(json.dumps({"status": "skipped", "reason": "too few complete pixel symbols"}, ensure_ascii=False))
        return 0

    rows: list[dict[str, Any]] = []
    for plane in range(lsb_bits):
        values = samples[:, plane]
        ones = int(values.sum())
        p1 = ones / values.size
        rows.append({
            "bit_plane": plane,
            "samples": int(values.size),
            "ones": ones,
            "p1": p1,
            "shannon_entropy_bits_per_bit": binary_entropy(p1),
            "marginal_min_entropy_bits_per_bit": binary_min_entropy(p1),
            "lag1_phi": phi_binary(values[:-1], values[1:]),
            "lag1_mutual_information_bits": mutual_information_binary(values[:-1], values[1:]),
        })

    phi = np.full((lsb_bits, lsb_bits), np.nan, dtype=np.float64)
    mi = np.zeros((lsb_bits, lsb_bits), dtype=np.float64)
    for left in range(lsb_bits):
        for right in range(lsb_bits):
            if left == right:
                phi[left, right] = 1.0
                mi[left, right] = binary_entropy(float(samples[:, left].mean()))
            else:
                value = phi_binary(samples[:, left], samples[:, right])
                phi[left, right] = float(value) if value is not None else np.nan
                mi[left, right] = mutual_information_binary(samples[:, left], samples[:, right])

    weights = np.left_shift(np.uint16(1), np.arange(lsb_bits, dtype=np.uint16))
    symbols = np.sum(samples.astype(np.uint16) * weights[None, :], axis=1)
    counts = np.bincount(symbols.astype(np.int64), minlength=1 << lsb_bits).astype(np.uint64)
    symbol_shannon, symbol_minimum = entropy_counts(counts)
    off_diagonal = ~np.eye(lsb_bits, dtype=bool)
    finite_phi = np.abs(phi[off_diagonal & np.isfinite(phi)])
    summary = {
        "schema": "camera-entropy-lsb-bitplane-analysis-v1",
        "app_version": APP_VERSION,
        "status": "complete",
        "input": str(source.relative_to(root) if source.is_relative_to(root) else source),
        "input_bytes_read": len(raw),
        "bit_order": bit_order,
        "lsb_bits": lsb_bits,
        "complete_pixel_symbols": int(samples.shape[0]),
        "trailing_bits_discarded": int(unpacked.size - usable),
        "sample_mode": config.get("sample_mode", "xor"),
        "entropy_credit_bits_per_pixel": config.get("entropy_credit_bits_per_pixel"),
        "planes": rows,
        "symbol_alphabet_size": int(1 << lsb_bits),
        "symbol_shannon_entropy_bits_per_symbol": symbol_shannon,
        "symbol_min_entropy_bits_per_symbol": symbol_minimum,
        "symbol_min_entropy_bits_per_input_bit": symbol_minimum / lsb_bits,
        "max_abs_cross_plane_phi": float(finite_phi.max()) if finite_phi.size else 0.0,
        "max_cross_plane_mutual_information_bits": float(mi[off_diagonal].max()) if lsb_bits > 1 else 0.0,
        "cross_plane_phi": [[None if not math.isfinite(float(v)) else float(v) for v in row] for row in phi],
        "cross_plane_mutual_information_bits": mi.tolist(),
        "warning": "Diagnostic empirical estimates are not an SP 800-90B entropy claim.",
    }
    (root / "lsb_bitplane_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (root / "lsb_bitplane_metrics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    np.savez_compressed(root / "lsb_cross_plane_matrices.npz", phi=phi, mutual_information_bits=mi, symbol_counts=counts)
    write_matrix_png(root / "lsb_cross_plane_phi.png", phi, "Same-pixel cross-plane phi", "phi")
    write_matrix_png(root / "lsb_cross_plane_mi.png", mi, "Same-pixel cross-plane mutual information", "bit")

    plane_labels = [f"b{row['bit_plane']}" for row in rows]
    plot_specs = [
        {
            "id": "plane-entropy",
            "data": [
                {"type": "bar", "name": "P(1)", "x": plane_labels, "y": [row["p1"] for row in rows]},
                {"type": "bar", "name": "Shannon/bit", "x": plane_labels, "y": [row["shannon_entropy_bits_per_bit"] for row in rows]},
                {"type": "bar", "name": "Hmin/bit", "x": plane_labels, "y": [row["marginal_min_entropy_bits_per_bit"] for row in rows]},
            ],
            "layout": {"barmode": "group", "yaxis": {"title": "wartość", "rangemode": "tozero"}},
        },
        {
            "id": "plane-temporal",
            "data": [
                {"type": "bar", "name": "|phi lag-1|", "x": plane_labels, "y": [abs(float(row["lag1_phi"])) if row["lag1_phi"] is not None else None for row in rows]},
                {"type": "bar", "name": "MI lag-1 [bit]", "x": plane_labels, "y": [row["lag1_mutual_information_bits"] for row in rows]},
            ],
            "layout": {"barmode": "group", "yaxis": {"title": "zależność", "rangemode": "tozero"}},
        },
        {
            "id": "symbol-distribution",
            "data": [{"type": "bar", "name": "liczba", "x": list(range(1 << lsb_bits)), "y": counts.tolist()}],
            "layout": {"xaxis": {"title": f"symbol {lsb_bits}-bitowy", "dtick": 1}, "yaxis": {"title": "liczba wystąpień", "rangemode": "tozero"}},
        },
    ]
    plane_table = table_html(
        ["Bit", "Próbki", "P(1)", "Shannon/bit", "Hmin marginalna/bit", "lag-1 phi", "lag-1 MI"],
        [[
            f"b{row['bit_plane']}", row["samples"], fmt(row["p1"], 9),
            fmt(row["shannon_entropy_bits_per_bit"], 9), fmt(row["marginal_min_entropy_bits_per_bit"], 9),
            fmt(row["lag1_phi"], 9), fmt(row["lag1_mutual_information_bits"], 9),
        ] for row in rows], compact=True,
    )
    parameter_table = table_html(
        ["Parametr", "Wartość"],
        [[key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value] for key, value in sorted(config.items()) if key not in {"command_line", "rtsp_url"}],
        compact=True,
    )
    cards = metrics_grid([
        metric_card("Tryb", config.get("sample_mode", "xor"), f"{lsb_bits} LSB · {BIT_ORDER}"),
        metric_card("Symbole", int(samples.shape[0]), f"alphabet {1 << lsb_bits}"),
        metric_card("Hmin / symbol", fmt(symbol_minimum, 9), f"{fmt(symbol_minimum / lsb_bits, 9)} / input bit"),
        metric_card("max cross-plane |phi|", fmt(summary["max_abs_cross_plane_phi"], 8), f"MI {fmt(summary['max_cross_plane_mutual_information_bits'], 8)} bit"),
    ])
    body = (
        '<div class="callout warn"><strong>Diagnostyka:</strong> empiryczne estymaty z jednego datasetu nie są deklaracją min-entropii SP 800-90B.</div>'
        + cards
        + '<div class="chart-grid">'
        + chart_div("plane-entropy", "Każda płaszczyzna — bias i entropia", "Dane z tabeli przedstawione na wspólnej skali.", 390)
        + chart_div("plane-temporal", "Każda płaszczyzna — zależność temporalna", "Wartości bezwzględne phi lag-1 i mutual information.", 390)
        + '</div>'
        + chart_div("symbol-distribution", "Rozkład symboli źródłowych", "Pełny alfabet do 16 symboli dla maksymalnie 4 LSB.", 360)
        + '<section><h2>Każdy bit osobno</h2>' + plane_table + '</section>'
        + f'<section><h2>Cały symbol {lsb_bits}-bitowy</h2><p>Shannon: <b>{symbol_shannon:.9f}</b> bit/symbol · Hmin marginalna: <b>{symbol_minimum:.9f}</b> bit/symbol · Hmin / pobrany bit: <b>{symbol_minimum / lsb_bits:.9f}</b>.</p></section>'
        + '<section><h2>Zależności pomiędzy bitami tego samego piksela</h2><div class="chart-grid"><figure><img src="lsb_cross_plane_phi.png" alt="Macierz phi" style="max-width:100%;background:white;border-radius:8px"><figcaption>Współczynnik phi.</figcaption></figure><figure><img src="lsb_cross_plane_mi.png" alt="Macierz mutual information" style="max-width:100%;background:white;border-radius:8px"><figcaption>Informacja wzajemna w bitach.</figcaption></figure></div></section>'
        + '<details><summary>Dokładne parametry przebiegu</summary>' + parameter_table + '</details>'
        + '<section><h2>Dane</h2><p><a href="lsb_cross_plane_matrices.npz">Macierze NPZ</a> · <a href="lsb_bitplane_summary.json">JSON</a> · <a href="lsb_bitplane_metrics.csv">CSV</a></p></section>'
    )
    document = html_page(
        title="Analiza bitów LSB",
        subtitle=f"{root.name} · {config.get('sample_mode', 'xor')} · {lsb_bits} LSB · {samples.shape[0]} symboli",
        navigation='<a href="run_report.html">Raport główny</a><a href="lsb_bitplane_summary.json">JSON</a><a href="lsb_bitplane_metrics.csv">CSV</a>',
        body=body, plot_specs=plot_specs,
    )
    (root / "lsb_bitplane_report.html").write_text(document, encoding="utf-8")
    print(json.dumps({"status": "ok", "report": str(root / "lsb_bitplane_report.html"), "lsb_bits": lsb_bits}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
