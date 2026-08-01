#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate adjacent-byte 2D/3D diagnostics for Camera Entropy binary streams.

The two-dimensional image is the joint distribution of adjacent byte pairs
``(B_n, B_(n+1))``.  It is a diagnostic visualization, not an entropy estimate
by itself.  Numerical joint entropy, conditional entropy, pair min-entropy and
mutual information are reported next to the image.  The 3D views show the same
pair density as a surface and sampled consecutive byte triples as a point cloud.
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from report_ui import chart_div, esc, fmt, fmt_bytes, html_page, metric_card, metrics_grid, table_html
from unicode_image_text import UnicodeTextCanvas

REPORT_NAME = "binary_geometry_report.html"
SUMMARY_NAME = "binary_geometry_summary.json"
CSV_NAME = "binary_geometry_metrics.csv"
DIAGRAM_NAME = "dual_weave_stagger2_checkerboard.svg"
SCHEMA = "camera-entropy-binary-geometry-v4"
PEARSON_RESIDUAL_CLIP = 8.0


@dataclass(frozen=True)
class SegmentSample:
    data: np.ndarray
    offset: int


def safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "stream"


def entropy_from_counts(counts: np.ndarray) -> float:
    total = int(counts.sum())
    if total <= 0:
        return 0.0
    nonzero = counts[counts > 0].astype(np.float64)
    probabilities = nonzero / float(total)
    return float(-np.sum(probabilities * np.log2(probabilities)))


def min_entropy_from_counts(counts: np.ndarray) -> float:
    total = int(counts.sum())
    maximum = int(counts.max(initial=0))
    if total <= 0 or maximum <= 0:
        return 0.0
    return float(-math.log2(maximum / float(total)))


def serial_correlation(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 2 or y.size != x.size:
        return 0.0
    xf = x.astype(np.float64)
    yf = y.astype(np.float64)
    x_std = float(xf.std())
    y_std = float(yf.std())
    if x_std == 0.0 or y_std == 0.0:
        return 0.0
    return float(np.mean((xf - xf.mean()) * (yf - yf.mean())) / (x_std * y_std))


def evenly_spaced_segments(path: Path, max_bytes: int, segment_count: int = 16) -> list[SegmentSample]:
    size = path.stat().st_size
    if size <= 0:
        return []
    max_bytes = max(4096, int(max_bytes))
    if size <= max_bytes:
        return [SegmentSample(np.frombuffer(path.read_bytes(), dtype=np.uint8).copy(), 0)]

    segment_count = max(1, min(int(segment_count), max_bytes // 4096))
    segment_length = max(4096, max_bytes // segment_count)
    segment_length = min(segment_length, size)
    last_offset = max(0, size - segment_length)
    offsets = sorted({int(value) for value in np.linspace(0, last_offset, num=segment_count, dtype=np.int64)})
    samples: list[SegmentSample] = []
    with path.open("rb") as stream:
        for offset in offsets:
            stream.seek(offset)
            raw = stream.read(segment_length)
            if raw:
                samples.append(SegmentSample(np.frombuffer(raw, dtype=np.uint8).copy(), offset))
    return samples


def pair_counts(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return np.zeros((256, 256), dtype=np.uint64)
    indices = x.astype(np.uint32) * 256 + y.astype(np.uint32)
    return np.bincount(indices, minlength=256 * 256).reshape(256, 256).astype(np.uint64)


def independence_diagnostics(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, float | int]]:
    """Compare observed pair counts with the independent-marginals model.

    ``matrix[x, y]`` stores observed transitions ``B_n=x -> B_(n+1)=y``.
    Expected counts are the outer product of both marginals divided by N.
    Pearson residuals retain the sign of the deviation, while the log2 ratio
    gives an effect-size view with Jeffreys-style 0.5-count smoothing.
    """
    observed = matrix.astype(np.float64)
    total = float(observed.sum())
    if total <= 0:
        zeros = np.zeros_like(observed, dtype=np.float64)
        return zeros, zeros, zeros, {
            "transition_independence_chi_square": 0.0,
            "transition_independence_degrees_of_freedom": 0,
            "transition_chi_square_per_dof": 0.0,
            "transition_cramers_v": 0.0,
            "pearson_residual_rms": 0.0,
            "pearson_residual_abs_p95": 0.0,
            "pearson_residual_abs_p99": 0.0,
            "pearson_residual_abs_max": 0.0,
            "log2_enrichment_abs_p99": 0.0,
        }

    x_marginal = observed.sum(axis=1)
    y_marginal = observed.sum(axis=0)
    expected = np.outer(x_marginal, y_marginal) / total
    valid = expected > 0.0
    residual = np.zeros_like(expected)
    residual[valid] = (observed[valid] - expected[valid]) / np.sqrt(expected[valid])
    log2_enrichment = np.log2((observed + 0.5) / (expected + 0.5))

    residual_values = residual[valid]
    abs_residual = np.abs(residual_values)
    active_x = int(np.count_nonzero(x_marginal))
    active_y = int(np.count_nonzero(y_marginal))
    degrees_of_freedom = max(0, (active_x - 1) * (active_y - 1))
    chi_square = float(np.sum(residual_values * residual_values))
    min_dimension = min(active_x - 1, active_y - 1)
    cramers_v = math.sqrt(chi_square / (total * min_dimension)) if total > 0 and min_dimension > 0 else 0.0
    diagnostics: dict[str, float | int] = {
        "transition_independence_chi_square": chi_square,
        "transition_independence_degrees_of_freedom": degrees_of_freedom,
        "transition_chi_square_per_dof": chi_square / degrees_of_freedom if degrees_of_freedom else 0.0,
        "transition_cramers_v": float(cramers_v),
        "pearson_residual_rms": float(np.sqrt(np.mean(residual_values * residual_values))) if residual_values.size else 0.0,
        "pearson_residual_abs_p95": float(np.percentile(abs_residual, 95.0)) if abs_residual.size else 0.0,
        "pearson_residual_abs_p99": float(np.percentile(abs_residual, 99.0)) if abs_residual.size else 0.0,
        "pearson_residual_abs_max": float(abs_residual.max(initial=0.0)),
        "log2_enrichment_abs_p99": float(np.percentile(np.abs(log2_enrichment[valid]), 99.0)) if residual_values.size else 0.0,
    }
    return expected, residual, log2_enrichment, diagnostics


def downsample_pair_matrix(matrix: np.ndarray, bins: int = 32) -> np.ndarray:
    if 256 % bins:
        raise ValueError("surface bins must divide 256")
    factor = 256 // bins
    return matrix.reshape(bins, factor, bins, factor).sum(axis=(1, 3))


def deterministic_triples(samples: list[SegmentSample], max_points: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    available = sum(max(0, sample.data.size - 2) for sample in samples)
    if available <= 0:
        empty = np.empty(0, dtype=np.uint8)
        return empty, empty, empty
    stride = max(1, int(math.ceil(available / max(1, max_points))))
    triples: list[np.ndarray] = []
    phase = 0
    for sample in samples:
        data = sample.data
        if data.size < 3:
            continue
        start = (-phase) % stride
        indices = np.arange(start, data.size - 2, stride, dtype=np.int64)
        phase = (phase + data.size - 2) % stride
        if indices.size:
            triples.append(np.stack((data[indices], data[indices + 1], data[indices + 2]), axis=1))
    if not triples:
        empty = np.empty(0, dtype=np.uint8)
        return empty, empty, empty
    result = np.concatenate(triples, axis=0)[:max_points]
    return result[:, 0], result[:, 1], result[:, 2]


def shuffled_mutual_information(x: np.ndarray, y: np.ndarray, limit: int = 2_000_000) -> float:
    """Estimate finite-sample MI floor after destroying serial dependence."""
    if x.size < 2:
        return 0.0
    if x.size > limit:
        stride = int(math.ceil(x.size / limit))
        x = x[::stride]
        y = y[::stride]
    shuffled = y.copy()
    np.random.default_rng(0x5354414747455232).shuffle(shuffled)
    joint = pair_counts(x, shuffled)
    return max(
        0.0,
        entropy_from_counts(np.bincount(x, minlength=256))
        + entropy_from_counts(np.bincount(shuffled, minlength=256))
        - entropy_from_counts(joint.reshape(-1)),
    )


def analyze_file(path: Path, max_bytes: int, max_scatter_points: int) -> tuple[dict[str, Any], dict[str, np.ndarray], dict[str, list[int]]]:
    samples = evenly_spaced_segments(path, max_bytes=max_bytes)
    if not samples:
        raise ValueError("empty file")

    x_parts = [sample.data[:-1] for sample in samples if sample.data.size >= 2]
    y_parts = [sample.data[1:] for sample in samples if sample.data.size >= 2]
    if not x_parts:
        raise ValueError("file is too short for adjacent-byte analysis")
    x = np.concatenate(x_parts)
    y = np.concatenate(y_parts)
    joint = pair_counts(x, y)
    x_counts = np.bincount(x, minlength=256).astype(np.uint64)
    y_counts = np.bincount(y, minlength=256).astype(np.uint64)

    h_x = entropy_from_counts(x_counts)
    h_y = entropy_from_counts(y_counts)
    h_xy = entropy_from_counts(joint.reshape(-1))
    mutual_information = max(0.0, h_x + h_y - h_xy)
    shuffle_floor = shuffled_mutual_information(x, y)
    expected_pairs, pearson_residuals, log2_enrichment, independence_metrics = independence_diagnostics(joint)

    all_bytes = np.concatenate([sample.data for sample in samples])
    byte_counts = np.bincount(all_bytes, minlength=256).astype(np.uint64)
    expected = all_bytes.size / 256.0
    byte_chi_square = float(np.sum((byte_counts - expected) ** 2 / expected)) if expected > 0 else 0.0

    tx, ty, tz = deterministic_triples(samples, max_points=max_scatter_points)
    if tx.size:
        voxel_index = (
            (tx >> 4).astype(np.uint16) * 256
            + (ty >> 4).astype(np.uint16) * 16
            + (tz >> 4).astype(np.uint16)
        )
        voxel_counts = np.bincount(voxel_index, minlength=16**3)
        occupied_voxels = int(np.count_nonzero(voxel_counts))
        maximum_voxel_share = float(voxel_counts.max(initial=0) / tx.size)
    else:
        occupied_voxels = 0
        maximum_voxel_share = 0.0

    occupied_pairs = int(np.count_nonzero(joint))
    metrics: dict[str, Any] = {
        "file": path.name,
        "file_bytes": path.stat().st_size,
        "sampled_bytes": int(all_bytes.size),
        "sample_segments": len(samples),
        "sample_offsets": [sample.offset for sample in samples],
        "byte_entropy_bits": entropy_from_counts(byte_counts),
        "byte_min_entropy_bits": min_entropy_from_counts(byte_counts),
        "byte_chi_square": byte_chi_square,
        "pair_count": int(x.size),
        "pair_occupied_bins": occupied_pairs,
        "pair_occupied_ratio": occupied_pairs / float(256 * 256),
        "pair_entropy_bits": h_xy,
        "pair_entropy_ratio": h_xy / 16.0,
        "pair_min_entropy_bits": min_entropy_from_counts(joint.reshape(-1)),
        "x_entropy_bits": h_x,
        "y_entropy_bits": h_y,
        "conditional_entropy_y_given_x_bits": max(0.0, h_xy - h_x),
        "conditional_entropy_ratio": max(0.0, h_xy - h_x) / 8.0,
        "mutual_information_bits": mutual_information,
        "shuffle_baseline_mutual_information_bits": shuffle_floor,
        "excess_mutual_information_bits": max(0.0, mutual_information - shuffle_floor),
        "byte_serial_correlation": serial_correlation(x, y),
        **independence_metrics,
        "triplet_scatter_points": int(tx.size),
        "triplet_occupied_voxels_16": occupied_voxels,
        "triplet_occupied_ratio_16": occupied_voxels / float(16**3),
        "triplet_max_voxel_share_16": maximum_voxel_share,
    }
    scatter = {
        "x": tx.astype(int).tolist(),
        "y": ty.astype(int).tolist(),
        "z": tz.astype(int).tolist(),
    }
    matrix_data = {
        "counts": joint,
        "expected": expected_pairs,
        "pearson_residuals": pearson_residuals,
        "log2_enrichment": log2_enrichment,
        "x_counts": x_counts,
        "y_counts": y_counts,
        "byte_counts": byte_counts,
    }
    return metrics, matrix_data, scatter


def _short_filename(value: str, limit: int = 56) -> str:
    if len(value) <= limit:
        return value
    keep = max(12, (limit - 3) // 2)
    return value[:keep] + "..." + value[-keep:]


def _draw_marginals(
    canvas: np.ndarray, x_counts: np.ndarray, y_counts: np.ndarray,
    x0: int, y0: int, size: int,
) -> None:
    """Draw log-count marginal histograms above and to the left of the heatmap."""
    top_height = 58
    left_width = 70
    top_values = np.log10(1.0 + x_counts.astype(np.float64))
    left_values = np.log10(1.0 + y_counts.astype(np.float64))
    top_max = float(top_values.max(initial=0.0)) or 1.0
    left_max = float(left_values.max(initial=0.0)) or 1.0
    top_points: list[tuple[int, int]] = []
    for value in range(256):
        px = int(round(x0 + value * (size - 1) / 255.0))
        py = int(round(y0 - 8 - top_height * top_values[value] / top_max))
        top_points.append((px, py))
    cv2.polylines(canvas, [np.asarray(top_points, dtype=np.int32)], False, (65, 105, 225), 1, cv2.LINE_AA)
    for value in range(256):
        py = int(round(y0 + (255 - value) * (size - 1) / 255.0))
        length = int(round(left_width * left_values[value] / left_max))
        cv2.line(canvas, (x0 - 10, py), (x0 - 10 - length, py), (65, 105, 225), 1, cv2.LINE_AA)


def _diverging_bgr(values: np.ndarray, limit: float) -> np.ndarray:
    normalized = np.clip(values / float(limit), -1.0, 1.0)
    result = np.empty(values.shape + (3,), dtype=np.float64)
    neutral = np.array([245.0, 245.0, 245.0])
    negative = np.array([185.0, 90.0, 35.0])  # blue in BGR
    positive = np.array([45.0, 55.0, 220.0])  # red in BGR
    neg_mask = normalized < 0
    neg_weight = (-normalized[neg_mask])[:, None]
    pos_weight = normalized[~neg_mask][:, None]
    result[neg_mask] = neutral * (1.0 - neg_weight) + negative * neg_weight
    result[~neg_mask] = neutral * (1.0 - pos_weight) + positive * pos_weight
    return np.clip(result, 0, 255).astype(np.uint8)


def _write_transition_panel(
    values: np.ndarray, output: Path, filename: str, *, mode: str,
    x_counts: np.ndarray, y_counts: np.ndarray, residual_clip: float = PEARSON_RESIDUAL_CLIP,
    observed_scale_max: float | None = None,
) -> None:
    """Render an observed-count or Pearson-residual transition panel."""
    heat_size = 768
    x0, y0 = 180, 170
    canvas = np.full((1010, 1120, 3), 245, dtype=np.uint8)

    if mode == "observed":
        transformed = np.log10(1.0 + values.astype(np.float64))
        display = np.flipud(transformed.T)
        maximum = float(observed_scale_max) if observed_scale_max is not None else float(display.max(initial=0.0))
        normalized = np.zeros_like(display, dtype=np.uint8)
        if maximum > 0:
            normalized = np.clip(display / maximum * 255.0, 0, 255).astype(np.uint8)
        image = cv2.applyColorMap(normalized, cv2.COLORMAP_TURBO)
        subtitle = "Zaobserwowane liczności przejść; kolor = log₁₀(1 + liczba par)"
        bar_values = (maximum, maximum / 2.0, 0.0)
        bar_label = "log₁₀(1 + liczba par)"
    elif mode == "residual":
        display = np.flipud(values.astype(np.float64).T)
        image = _diverging_bgr(display, residual_clip)
        subtitle = f"Reszta Pearsona (O−E)/√E; stała skala ±{residual_clip:g}"
        bar_values = (residual_clip, 0.0, -residual_clip)
        bar_label = "Reszta Pearsona"
    else:
        raise ValueError(f"unknown heatmap mode: {mode}")

    image = cv2.resize(image, (heat_size, heat_size), interpolation=cv2.INTER_NEAREST)
    canvas[y0:y0 + heat_size, x0:x0 + heat_size] = image
    cv2.rectangle(canvas, (x0 - 1, y0 - 1), (x0 + heat_size, y0 + heat_size), (30, 30, 30), 2)
    _draw_marginals(canvas, x_counts, y_counts, x0, y0, heat_size)

    bar_x, bar_y, bar_w, bar_h = 990, y0, 28, heat_size
    if mode == "observed":
        gradient = np.linspace(255, 0, bar_h, dtype=np.uint8).reshape(-1, 1)
        colorbar = cv2.applyColorMap(np.repeat(gradient, bar_w, axis=1), cv2.COLORMAP_TURBO)
    else:
        vals = np.linspace(residual_clip, -residual_clip, bar_h, dtype=np.float64).reshape(-1, 1)
        colorbar = _diverging_bgr(np.repeat(vals, bar_w, axis=1), residual_clip)
    canvas[bar_y:bar_y + bar_h, bar_x:bar_x + bar_w] = colorbar
    cv2.rectangle(canvas, (bar_x - 1, bar_y - 1), (bar_x + bar_w, bar_y + bar_h), (30, 30, 30), 1)

    text = UnicodeTextCanvas(canvas)
    text.text((28, 18), f"Heatmapa przejść bajtowych — {_short_filename(filename)}", size=25, bold=True)
    text.text((28, 55), subtitle, size=16, color_bgr=(65, 65, 65))
    text.text((x0, y0 - 86), "Margines Bₙ (logarytm liczności)", size=13, color_bgr=(70, 70, 70))
    text.text((x0 - 104, y0 - 28), "Margines Bₙ₊₁", size=11, color_bgr=(70, 70, 70))
    text.text((x0 + heat_size / 2, 974), "Bieżący bajt Bₙ", size=21, bold=True, anchor="mt")
    text.rotated_text((34, y0 + heat_size / 2), "Następny bajt Bₙ₊₁", size=21, bold=True)
    for value, px in ((0, x0), (64, x0 + 192), (128, x0 + 384), (192, x0 + 576), (255, x0 + 740)):
        text.text((px, y0 + heat_size + 9), value, size=14, color_bgr=(35, 35, 35), anchor="mt")
    for value, py in ((255, y0), (192, y0 + 192), (128, y0 + 384), (64, y0 + 576), (0, y0 + 756)):
        text.text((x0 - 90, py), value, size=14, color_bgr=(35, 35, 35), anchor="rm")
    text.text((bar_x + 38, bar_y), f"{bar_values[0]:.3g}", size=13, color_bgr=(35, 35, 35), anchor="lm")
    text.text((bar_x + 38, bar_y + bar_h / 2), f"{bar_values[1]:.3g}", size=13, color_bgr=(35, 35, 35), anchor="lm")
    text.text((bar_x + 38, bar_y + bar_h), f"{bar_values[2]:.3g}", size=13, color_bgr=(35, 35, 35), anchor="lm")
    text.rotated_text((1080, y0 + bar_h / 2), bar_label, size=14)
    canvas = text.finish()

    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise RuntimeError(f"cannot write heatmap: {output}")


def write_observed_heatmap_png(
    matrix: np.ndarray, output: Path, filename: str, *, scale_max: float | None = None,
) -> None:
    _write_transition_panel(
        matrix, output, filename, mode="observed",
        x_counts=matrix.sum(axis=1), y_counts=matrix.sum(axis=0), observed_scale_max=scale_max,
    )


def write_residual_heatmap_png(matrix: np.ndarray, residuals: np.ndarray, output: Path, filename: str) -> None:
    _write_transition_panel(
        residuals, output, filename, mode="residual",
        x_counts=matrix.sum(axis=1), y_counts=matrix.sum(axis=0),
    )


def write_heatmap_png(matrix: np.ndarray, output: Path, title: str) -> None:
    """Backward-compatible alias for the observed transition panel."""
    write_observed_heatmap_png(matrix, output, title)


def write_byte_histogram_png(
    counts: np.ndarray,
    output: Path,
    filename: str,
    *,
    delta_scale_max: float | None = None,
) -> None:
    """Write a standalone 256-bin byte histogram centred around the uniform 1/256 baseline."""
    total = int(counts.sum())
    frequencies = counts.astype(np.float64) / float(total) if total else np.zeros(256, dtype=np.float64)
    uniform = 1.0 / 256.0
    delta_pp = 100.0 * (frequencies - uniform)
    max_abs = float(np.max(np.abs(delta_pp), initial=0.0)) if delta_pp.size else 0.0
    if delta_scale_max is not None:
        max_abs = max(max_abs, float(delta_scale_max))
    max_abs = max(max_abs, 0.004) * 1.08
    width, height = 1500, 760
    left, right, top, bottom = 150, 45, 100, 95
    plot_w, plot_h = width - left - right, height - top - bottom
    canvas = np.full((height, width, 3), 246, dtype=np.uint8)
    tick_labels: list[tuple[float, int]] = []
    for tick in range(7):
        value = -max_abs + (2.0 * max_abs * tick / 6.0)
        py = top + plot_h - int(round(plot_h * (value + max_abs) / (2.0 * max_abs)))
        line_color = (170, 185, 205) if abs(value) < 1e-12 else (215, 215, 215)
        thickness = 2 if abs(value) < 1e-12 else 1
        cv2.line(canvas, (left, py), (left + plot_w, py), line_color, thickness, cv2.LINE_AA)
        tick_labels.append((value, py))
    zero_y = top + plot_h - int(round(plot_h * (0.0 + max_abs) / (2.0 * max_abs)))
    points = []
    for value in range(256):
        px = left + int(round(value * plot_w / 255.0))
        py = top + plot_h - int(round(plot_h * (delta_pp[value] + max_abs) / (2.0 * max_abs)))
        points.append((px, py))
    cv2.polylines(canvas, [np.asarray(points, dtype=np.int32)], False, (215, 115, 45), 2, cv2.LINE_AA)
    for value in range(0, 256, 8):
        cv2.circle(canvas, points[value], 2, (215, 115, 45), -1, cv2.LINE_AA)
    cv2.rectangle(canvas, (left, top), (left + plot_w, top + plot_h), (35, 35, 35), 1)

    text = UnicodeTextCanvas(canvas)
    text.text((28, 17), f"Histogram odchyleń częstotliwości bajtów — {_short_filename(filename)}", size=27, bold=True)
    text.text(
        (28, 56),
        f"Pełne bajty: {total:,}; zero oznacza rozkład jednostajny 1/256 = {100.0/256.0:.6f}%",
        size=16,
        color_bgr=(65, 65, 65),
    )
    for value, py in tick_labels:
        text.text((left - 12, py), f"{value:+.4f} p.p.", size=13, color_bgr=(55, 55, 55), anchor="rm")
    for value in (0, 32, 64, 96, 128, 160, 192, 224, 255):
        px = left + int(round(value * plot_w / 255.0))
        text.text((px, top + plot_h + 10), value, size=14, color_bgr=(45, 45, 45), anchor="mt")
    text.text((left + plot_w / 2, height - 38), "Wartość bajtu", size=19, bold=True, anchor="mm")
    text.rotated_text((30, top + plot_h / 2), "Odchylenie od rozkładu jednostajnego [p.p.]", size=18, bold=True)
    canvas = text.finish()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise RuntimeError(f"cannot write byte histogram: {output}")



def write_surface_fallback_png(z_values: np.ndarray, output: Path, filename: str) -> None:
    """Render a static isometric wireframe used when Plotly/WebGL is unavailable."""
    z = np.asarray(z_values, dtype=np.float64)
    if z.ndim != 2:
        raise ValueError("surface fallback expects a 2D matrix")
    height, width = 760, 1080
    canvas = np.full((height, width, 3), 246, dtype=np.uint8)
    origin_x, origin_y = 535.0, 590.0
    step_x, step_y, z_scale = 15.0, 4.5, 160.0
    maximum = float(z.max(initial=0.0)) or 1.0

    def project(ix: float, iy: float, value: float) -> tuple[int, int]:
        px = origin_x + (ix - iy) * step_x
        py = origin_y - (ix + iy) * step_y - value / maximum * z_scale
        return int(round(px)), int(round(py))

    # Back-to-front wireframe; color follows mean local height.
    for iy in range(z.shape[1] - 1, -1, -1):
        points = np.asarray([project(ix, iy, z[ix, iy]) for ix in range(z.shape[0])], dtype=np.int32)
        mean = float(z[:, iy].mean()) / maximum
        color = tuple(int(v) for v in cv2.applyColorMap(np.array([[int(255 * mean)]], dtype=np.uint8), cv2.COLORMAP_TURBO)[0, 0])
        cv2.polylines(canvas, [points], False, color, 1, cv2.LINE_AA)
    for ix in range(z.shape[0]):
        points = np.asarray([project(ix, iy, z[ix, iy]) for iy in range(z.shape[1])], dtype=np.int32)
        mean = float(z[ix, :].mean()) / maximum
        color = tuple(int(v) for v in cv2.applyColorMap(np.array([[int(255 * mean)]], dtype=np.uint8), cv2.COLORMAP_TURBO)[0, 0])
        cv2.polylines(canvas, [points], False, color, 1, cv2.LINE_AA)

    base = [project(0, 0, 0), project(z.shape[0] - 1, 0, 0), project(z.shape[0] - 1, z.shape[1] - 1, 0), project(0, z.shape[1] - 1, 0)]
    cv2.polylines(canvas, [np.asarray(base + [base[0]], dtype=np.int32)], False, (45, 45, 45), 2, cv2.LINE_AA)
    text = UnicodeTextCanvas(canvas)
    text.text((28, 18), f"Bryła 3D rozkładu par — {_short_filename(filename)}", size=26, bold=True)
    text.text((28, 56), "Statyczna projekcja izometryczna; wysokość = log₁₀(1 + liczba par)", size=16, color_bgr=(65, 65, 65))
    text.text((795, 635), "Bieżący bajt Bₙ", size=17, bold=True, anchor="mm")
    text.text((270, 635), "Następny bajt Bₙ₊₁", size=17, bold=True, anchor="mm")
    text.rotated_text((86, 385), "Gęstość przejść", size=17, bold=True)
    canvas = text.finish()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise RuntimeError(f"cannot write static surface: {output}")


def write_scatter_fallback_png(scatter: dict[str, list[int]], output: Path, filename: str) -> None:
    """Render a static isometric projection of consecutive byte triples."""
    x = np.asarray(scatter.get("x", []), dtype=np.float64)
    y = np.asarray(scatter.get("y", []), dtype=np.float64)
    z = np.asarray(scatter.get("z", []), dtype=np.float64)
    height, width = 760, 1080
    canvas = np.full((height, width, 3), 246, dtype=np.uint8)
    if x.size:
        # Isometric projection of the 0..255 cube. Draw far points first.
        depth_order = np.argsort(x + y)
        x, y, z = x[depth_order], y[depth_order], z[depth_order]
        u = 535.0 + (x - y) * 1.35
        v = 690.0 - (x + y) * 0.55 - z * 1.20
        colors = cv2.applyColorMap(np.clip(z, 0, 255).astype(np.uint8).reshape(-1, 1), cv2.COLORMAP_TURBO).reshape(-1, 3)
        for px, py, color in zip(u.astype(int), v.astype(int), colors):
            if 0 <= px < width and 90 <= py < height - 70:
                cv2.circle(canvas, (int(px), int(py)), 1, tuple(int(value) for value in color), -1, cv2.LINE_AA)

    # Projected cube outline.
    def project(cx: float, cy: float, cz: float) -> tuple[int, int]:
        return int(round(535.0 + (cx - cy) * 1.35)), int(round(690.0 - (cx + cy) * 0.55 - cz * 1.20))
    corners = {(a, b, c): project(a, b, c) for a in (0, 255) for b in (0, 255) for c in (0, 255)}
    for a in (0, 255):
        for b in (0, 255):
            cv2.line(canvas, corners[(a, b, 0)], corners[(a, b, 255)], (90, 90, 90), 1, cv2.LINE_AA)
    for b in (0, 255):
        for c in (0, 255):
            cv2.line(canvas, corners[(0, b, c)], corners[(255, b, c)], (90, 90, 90), 1, cv2.LINE_AA)
    for a in (0, 255):
        for c in (0, 255):
            cv2.line(canvas, corners[(a, 0, c)], corners[(a, 255, c)], (90, 90, 90), 1, cv2.LINE_AA)

    text = UnicodeTextCanvas(canvas)
    text.text((28, 18), f"Chmura trójek kolejnych bajtów — {_short_filename(filename)}", size=26, bold=True)
    text.text((28, 56), "Statyczna projekcja izometryczna punktów (Bₙ, Bₙ₊₁, Bₙ₊₂)", size=16, color_bgr=(65, 65, 65))
    text.text((805, 650), "Bₙ", size=18, bold=True, anchor="mm")
    text.text((275, 650), "Bₙ₊₁", size=18, bold=True, anchor="mm")
    text.rotated_text((84, 390), "Bₙ₊₂", size=18, bold=True)
    text.text((28, 724), f"Punkty: {x.size:,}", size=11, color_bgr=(100, 100, 100))
    canvas = text.finish()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise RuntimeError(f"cannot write static scatter: {output}")


def _checker_cells(x: int, y: int, cell: int, rows: int, cols: int, even_text: str, odd_text: str) -> str:
    cells: list[str] = []
    for row in range(rows):
        for col in range(cols):
            even = ((row + col) & 1) == 0
            px = x + col * cell
            py = y + row * cell
            fill = "#2f81f7" if even else "#f0883e"
            label = even_text if even else odd_text
            cells.append(f'<rect x="{px}" y="{py}" width="{cell}" height="{cell}" fill="{fill}" stroke="#0d1117"/>')
            cells.append(f'<text x="{px + cell / 2}" y="{py + cell * .68}" text-anchor="middle" class="cell">{esc(label)}</text>')
    return "".join(cells)


def write_dual_weave_svg(output: Path) -> None:
    """Generate the exact checkerboard/row-major/stagger-2 data-flow diagram."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900">
<style>
.bg{{fill:#0d1117}}.panel{{fill:#161b22;stroke:#30363d;stroke-width:2}}.title{{fill:#f0f6fc;font:700 32px system-ui}}
.h{{fill:#f0f6fc;font:700 23px system-ui}}.t{{fill:#c9d1d9;font:18px system-ui}}.small{{fill:#8b949e;font:15px system-ui}}
.cell{{fill:#fff;font:700 12px monospace}}.arrow{{stroke:#c9d1d9;stroke-width:4;fill:none;marker-end:url(#arrow)}}
.box{{fill:#21262d;stroke:#58a6ff;stroke-width:2}}.green{{fill:#238636;stroke:#3fb950;stroke-width:2}}
</style>
<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#c9d1d9"/></marker></defs>
<rect class="bg" width="1600" height="900"/>
<text x="50" y="58" class="title">Dual weave row-major / stagger-2 + checkerboard EVEN/ODD</text>
<text x="50" y="91" class="t">Dokładny układ zaimplementowany w pipeline: dwie rozłączne mapy temporalne A i B, komplementarne C0/C1 oraz opóźnienie o dwie grupy.</text>
<rect x="35" y="120" width="730" height="345" rx="14" class="panel"/>
<text x="60" y="160" class="h">1. Checkerboard i komplementarne C0/C1</text>
<text x="75" y="202" class="t">C0_g = A_g[EVEN] + B_g[ODD]</text>
{_checker_cells(75, 225, 42, 5, 8, "A", "B")}
<text x="425" y="202" class="t">C1_g = B_g[EVEN] + A_g[ODD]</text>
{_checker_cells(425, 225, 42, 5, 8, "B", "A")}
<text x="75" y="450" class="small">Niebieskie pola: EVEN, (row + col) mod 2 = 0</text>
<text x="425" y="450" class="small">Pomarańczowe pola: ODD, (row + col) mod 2 = 1</text>
<rect x="795" y="120" width="770" height="345" rx="14" class="panel"/>
<text x="820" y="160" class="h">2. Serializacja row-major</text>
<text x="830" y="207" class="t">1. Aktywne piksele w wierszu 0: lewo → prawo</text>
<text x="830" y="247" class="t">2. Aktywne piksele w wierszu 1: lewo → prawo</text>
<text x="830" y="287" class="t">3. Następne wiersze w tej samej kolejności</text>
<path d="M840 338 H1480" class="arrow"/>
<text x="830" y="382" class="small">C0 i C1 są osobnymi strumieniami. Nie przeplatamy pojedynczych bitów C0,C1,C0,C1…</text>
<text x="830" y="416" class="small">RCT/APT pozostają osobne dla A-even, A-odd, B-even, B-odd oraz dla C0 i C1.</text>
<rect x="35" y="495" width="1530" height="350" rx="14" class="panel"/>
<text x="60" y="535" class="h">3. Stagger-2: stary C0 łączymy z bieżącym C1 po dwóch grupach</text>
<line x1="120" y1="625" x2="1450" y2="625" class="arrow"/>
<g><rect x="135" y="575" width="245" height="100" rx="10" class="box"/><text x="258" y="608" text-anchor="middle" class="t">grupa g</text><text x="258" y="643" text-anchor="middle" class="small">C0_g → kolejka</text></g>
<g><rect x="520" y="575" width="245" height="100" rx="10" class="box"/><text x="642" y="608" text-anchor="middle" class="t">grupa g+1</text><text x="642" y="643" text-anchor="middle" class="small">C0_g czeka</text></g>
<g><rect x="905" y="558" width="330" height="134" rx="10" class="box"/><text x="1070" y="594" text-anchor="middle" class="t">grupa g+2</text><text x="1070" y="630" text-anchor="middle" class="small">bieżące C1_(g+2)</text><text x="1070" y="660" text-anchor="middle" class="small">+ zakolejkowane C0_g</text></g>
<path d="M258 680 C258 745 630 755 900 695" class="arrow"/>
<rect x="105" y="752" width="710" height="58" rx="9" class="box"/><text x="460" y="789" text-anchor="middle" class="t">C0_g [1024 bity]  ||  C1_(g+2) [1024 bity]</text>
<path d="M835 781 H1010" class="arrow"/>
<rect x="1030" y="752" width="245" height="58" rx="9" fill="#6e40c9" stroke="#bc8cff" stroke-width="2"/><text x="1152" y="789" text-anchor="middle" class="t">SHA3-512</text>
<path d="M1295 781 H1390" class="arrow"/>
<rect x="1410" y="752" width="125" height="58" rx="9" class="green"/><text x="1472" y="789" text-anchor="middle" class="t">512 bit</text>
<text x="60" y="870" class="small">Pierwsze dwa bieżące C1 nie mają jeszcze starszego C0 i są pomijane; końcowe zakolejkowane C0 również nie są używane ponownie. Conditioner zawsze pobiera równe połówki.</text>
</svg>'''
    output.write_text(svg, encoding="utf-8")


def file_priority(path: Path) -> tuple[int, str]:
    name = path.name.lower()
    rules = (
        ("dual_weave_row_major_stagger_2_conditioner_input", 0),
        ("dual_weave_row_major_stagger_2_sha3", 1),
        ("dual_weave_row_major_c0_raw", 2),
        ("dual_weave_row_major_c1_raw", 3),
        ("dual_weave_row_major_c0_vn", 4),
        ("dual_weave_row_major_c1_vn", 5),
        ("camera_entropy_sha3", 6),
        ("y_direct_lsb_common_mask_validation", 7),
        ("y_temporal_raw_validation", 8),
        ("y_temporal_masked_validation", 9),
        ("y_temporal_vn", 10),
        ("checkerboard_even", 11),
        ("validation", 12),
        ("vn", 13),
    )
    for token, priority in rules:
        if token in name:
            return priority, name
    return 50, name


PIPELINE_STAGE_RULES: tuple[tuple[str, int, str], ...] = (
    ("y_direct_lsb_common_mask_validation", 0, "Direct LSB"),
    ("y_temporal_masked_validation", 1, "Temporal difference + active mask"),
    ("y_temporal_vn", 2, "Von Neumann"),
    ("camera_entropy_sha3", 3, "SHA3-512"),
)


def pipeline_stage(path: Path | str) -> tuple[int, str] | None:
    name = path.name.lower() if isinstance(path, Path) else str(path).lower()
    for token, rank, label in PIPELINE_STAGE_RULES:
        if token in name:
            return rank, label
    return None


def select_bin_files(run_dir: Path, patterns: list[str], max_files: int, all_bin: bool) -> list[Path]:
    candidates = [path for path in run_dir.rglob("*.bin") if path.is_file() and path.stat().st_size >= 3]
    candidates.sort(key=file_priority)
    if patterns:
        selected = []
        for path in candidates:
            relative = str(path.relative_to(run_dir))
            if any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns):
                selected.append(path)
        return selected[:max_files]
    if all_bin:
        return candidates[:max_files]

    # Keep the four principal pipeline stages in every default geometry report
    # when their files are present, then fill remaining slots by dual-weave priority.
    selected: list[Path] = []
    for token, _rank, _label in PIPELINE_STAGE_RULES:
        match = next((path for path in candidates if token in path.name.lower()), None)
        if match is not None and match not in selected:
            selected.append(match)
            if len(selected) >= max_files:
                return selected
    for path in candidates:
        if path in selected or file_priority(path)[0] >= 50:
            continue
        selected.append(path)
        if len(selected) >= max_files:
            break
    return selected

def _plot_specs(rows: list[dict[str, Any]], plot_data: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for row in rows:
        slug = str(row["slug"])
        surface = plot_data[slug]["surface"]
        scatter = plot_data[slug]["scatter"]
        histogram = plot_data[slug]["histogram"]
        histogram_range = max(0.004, max(abs(float(v)) for v in histogram["delta_pp"])) * 1.12
        specs.append({
            "id": f"histogram-{slug}",
            "data": [
                {
                    "type": "scatter",
                    "mode": "lines",
                    "x": histogram["x"],
                    "y": histogram["delta_pp"],
                    "name": "Odchylenie od 1/256",
                    "hovertemplate": "bajt=%{x}<br>Δ względem 1/256=%{y:+.7f} p.p.<br>częstotliwość=%{customdata:.7f}%<extra></extra>",
                    "customdata": histogram["frequency_percent"],
                    "line": {"color": "#d77332", "width": 1.8},
                    "fill": "tozeroy",
                    "fillcolor": "rgba(215,115,50,0.14)",
                },
            ],
            "layout": {
                "margin": {"l": 70, "r": 20, "t": 15, "b": 55},
                "xaxis": {"title": "Wartość bajtu", "range": [-0.5, 255.5], "dtick": 32},
                "yaxis": {"title": "Odchylenie od 1/256 [p.p.]", "range": [-histogram_range, histogram_range], "zeroline": True, "zerolinewidth": 1.2, "zerolinecolor": "#9fb2c7"},
                "showlegend": True,
                "shapes": [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": 0, "y1": 0, "line": {"dash": "dash", "width": 1, "color": "#9fb2c7"}}],
            },
        })
        specs.append({
            "id": f"surface-{slug}",
            "requiresWebGL": True,
            "staticUnderCsp": True,
            "staticReason": "Statyczna projekcja jest używana przy restrykcyjnej CSP bez unsafe-eval.",
            "fallbackImage": row.get("surface_fallback"),
            "data": [{
                "type": "surface",
                "x": surface["axis"],
                "y": surface["axis"],
                "z": surface["z"],
                "colorscale": "Turbo",
                "showscale": True,
                "hovertemplate": "B_n=%{x}<br>B_(n+1)=%{y}<br>log10(1+count)=%{z:.4f}<extra></extra>",
            }],
            "layout": {
                "margin": {"l": 0, "r": 0, "t": 15, "b": 0},
                "scene": {
                    "xaxis": {"title": "B_n", "range": [0, 255]},
                    "yaxis": {"title": "B_(n+1)", "range": [0, 255]},
                    "zaxis": {"title": "log10(1 + count)"},
                    "aspectmode": "cube",
                },
            },
        })
        specs.append({
            "id": f"scatter-{slug}",
            "requiresWebGL": True,
            "staticUnderCsp": True,
            "staticReason": "Statyczna projekcja jest używana przy restrykcyjnej CSP bez unsafe-eval.",
            "fallbackImage": row.get("scatter_fallback"),
            "data": [{
                "type": "scatter3d",
                "mode": "markers",
                "x": scatter["x"],
                "y": scatter["y"],
                "z": scatter["z"],
                "marker": {
                    "size": 2,
                    "opacity": 0.42,
                    "color": scatter["z"],
                    "colorscale": "Turbo",
                    "showscale": False,
                },
                "hovertemplate": "B_n=%{x}<br>B_(n+1)=%{y}<br>B_(n+2)=%{z}<extra></extra>",
            }],
            "layout": {
                "margin": {"l": 0, "r": 0, "t": 15, "b": 0},
                "scene": {
                    "xaxis": {"title": "B_n", "range": [0, 255]},
                    "yaxis": {"title": "B_(n+1)", "range": [0, 255]},
                    "zaxis": {"title": "B_(n+2)", "range": [0, 255]},
                    "aspectmode": "cube",
                },
            },
        })
    return specs


def build_report(run_dir: Path, rows: list[dict[str, Any]], plot_data: dict[str, Any]) -> str:
    summary_table = table_html(
        ["Plik", "Próbka", "H(X)", "H(Y)", "H(X,Y)", "H(Y|X)", "MI excess", "Cramer V", "|r| p99", "corr lag-1"],
        [[
            row["file"], fmt_bytes(row["sampled_bytes"]), fmt(row["x_entropy_bits"], 8),
            fmt(row["y_entropy_bits"], 8), fmt(row["pair_entropy_bits"], 8),
            fmt(row["conditional_entropy_y_given_x_bits"], 8), fmt(row["excess_mutual_information_bits"], 8),
            fmt(row["transition_cramers_v"], 8), fmt(row["pearson_residual_abs_p99"], 8),
            fmt(row["byte_serial_correlation"], 8),
        ] for row in rows],
        compact=True,
    )

    sections: list[str] = []
    for row in rows:
        slug = str(row["slug"])
        cards = metrics_grid([
            metric_card("H(X)", f'{row["x_entropy_bits"]:.7f} / 8 bit', "entropia histogramu bieżącego bajtu"),
            metric_card("H(Y)", f'{row["y_entropy_bits"]:.7f} / 8 bit', "entropia histogramu następnego bajtu"),
            metric_card("H(X,Y)", f'{row["pair_entropy_bits"]:.7f} / 16 bit', f'{100.0 * row["pair_entropy_ratio"]:.4f}% maksimum'),
            metric_card("H(Y|X)", f'{row["conditional_entropy_y_given_x_bits"]:.7f} / 8 bit', "niepewność następnego bajtu po poznaniu poprzedniego"),
            metric_card("MI ponad shuffle", f'{row["excess_mutual_information_bits"]:.8f} bit', f'całkowite MI {row["mutual_information_bits"]:.8f}'),
            metric_card("Cramer V", f'{row["transition_cramers_v"]:.8f}', "znormalizowany efekt zależności w tabeli 256×256"),
            metric_card("|residual| p99", f'{row["pearson_residual_abs_p99"]:.5f}', f'maksimum {row["pearson_residual_abs_max"]:.5f}'),
            metric_card("Korelacja bajtowa", f'{row["byte_serial_correlation"]:.8f}', "Pearson B_n ↔ B_(n+1)"),
        ])
        sections.append(
            f'<section><h2>{esc(row["file"])}</h2><p class="muted">Próbka: {esc(fmt_bytes(row["sampled_bytes"]))} z {esc(fmt_bytes(row["file_bytes"]))}; '
            f'{int(row["sample_segments"])} równomiernie rozłożonych segmentów.</p>{cards}'
            '<div class="histogram-report-grid">'
            f'<figure><h3>Histogram odchyleń częstotliwości bajtów</h3><a href="{esc(row["byte_histogram"])}"><img class="geometry-heatmap" src="{esc(row["byte_histogram"])}" alt="Histogram bajtów {esc(row["file"])}"></a>'
            '<figcaption>256 dyskretnych wartości bajtów; poziom zero odpowiada rozkładowi jednostajnemu 1/256. Dodatnie fragmenty linii oznaczają nadmiar, ujemne niedobór względem oczekiwania.</figcaption></figure>'
            + chart_div(f"histogram-{slug}", "Interaktywny histogram Plotly", "Najedź na słupek, aby odczytać odchylenie w punktach procentowych i dokładną częstotliwość.", 430)
            + '</div><div class="heatmap-grid">'
            '<figure><h3>Zaobserwowane przejścia</h3>'
            f'<a href="{esc(row["observed_heatmap"])}"><img class="geometry-heatmap" src="{esc(row["observed_heatmap"])}" alt="Zaobserwowane przejścia bajtowe {esc(row["file"])}"></a>'
            '<figcaption>Surowe liczności par. Pasy mogą pochodzić zarówno z biasu histogramów marginalnych, jak i z zależności sekwencyjnej.</figcaption></figure>'
            '<figure><h3>Odchylenie od niezależności</h3>'
            f'<a href="{esc(row["residual_heatmap"])}"><img class="geometry-heatmap" src="{esc(row["residual_heatmap"])}" alt="Reszty Pearsona przejść bajtowych {esc(row["file"])}"></a>'
            f'<figcaption>Reszta Pearsona r=(O-E)/sqrt(E), wspólna skala ±{PEARSON_RESIDUAL_CLIP:g}. Zero oznacza model niezależnych marginesów; czerwone pary są nadreprezentowane, niebieskie niedoreprezentowane.</figcaption></figure>'
            '</div><div class="chart-grid">'
            + chart_div(f"surface-{slug}", "Bryła 3D rozkładu par", "Statyczna projekcja izometryczna przy restrykcyjnej CSP; wysokość to log₁₀(1 + liczba par).", 520)
            + chart_div(f"scatter-{slug}", "Chmura trójek bajtów", "Statyczna projekcja kolejnych trójek (Bₙ, Bₙ₊₁, Bₙ₊₂) bez wymagania WebGL.", 520)
            + '</div>'
            f'<div class="geometry-controls"><a href="{esc(row["surface_fallback"])}">bryła 3D PNG</a> '
            f'<a href="{esc(row["scatter_fallback"])}">chmura 3D PNG</a> '
            f'<a href="{esc(row["pair_counts_npz"])}">macierz NPZ</a></div></section>'
        )

    pipeline_rows = sorted(
        [row for row in rows if isinstance(row.get("pipeline_stage_rank"), int)],
        key=lambda row: int(row["pipeline_stage_rank"]),
    )
    pipeline_comparison = ""
    if pipeline_rows:
        pipeline_cards = "".join(
            '<figure class="pipeline-stage-card">'
            f'<h3>{esc(row.get("pipeline_stage_label"))}</h3><p class="muted"><code>{esc(row.get("file"))}</code></p>'
            '<div class="pipeline-stage-images">'
            f'<a href="{esc(row.get("byte_histogram"))}"><img src="{esc(row.get("byte_histogram"))}" alt="Histogram {esc(row.get("pipeline_stage_label"))}"><span>histogram bajtów, wspólna skala</span></a>'
            f'<a href="{esc(row.get("observed_heatmap"))}"><img src="{esc(row.get("observed_heatmap"))}" alt="Liczności {esc(row.get("pipeline_stage_label"))}"><span>liczności, wspólna skala</span></a>'
            f'<a href="{esc(row.get("residual_heatmap"))}"><img src="{esc(row.get("residual_heatmap"))}" alt="Reszty {esc(row.get("pipeline_stage_label"))}"><span>vs niezależność, skala ±{PEARSON_RESIDUAL_CLIP:g}</span></a>'
            '</div>'
            f'<p class="pipeline-stage-metrics">H(Y|X)={float(row.get("conditional_entropy_y_given_x_bits", 0.0)):.6f} bit · MI excess={float(row.get("excess_mutual_information_bits", 0.0)):.6g} bit · Cramer V={float(row.get("transition_cramers_v", 0.0)):.6g}</p>'
            '</figure>'
            for row in pipeline_rows
        )
        pipeline_comparison = (
            '<section><h2>Porównanie etapów pipeline w jednej skali</h2>'
            '<p class="muted">Liczności używają wspólnego maksimum log10(1+count) dla całego raportu; reszty Pearsona zawsze używają stałej skali symetrycznej. Dzięki temu zanik biasu i struktur można porównywać bez zmiany skali pomiędzy etapami.</p>'
            f'<div class="pipeline-stage-grid">{pipeline_cards}</div></section>'
        )

    body = (
        '<section><h2>Jak zbudowany jest dual weave stagger-2</h2>'
        f'<img class="pipeline-diagram" src="{DIAGRAM_NAME}" alt="Checkerboard row-major stagger-2"></section>'
        '<div class="callout warn"><strong>Interpretacja:</strong> pierwszy obraz pokazuje surowy rozkład łączny par bajtów, a drugi usuwa wpływ samych histogramów marginalnych przez porównanie z E(x,y)=N_x N_y/N. '
        'Reszta Pearsona r=(O-E)/sqrt(E) pokazuje znak i siłę lokalnego odchylenia. H(X,Y), H(Y|X), MI, Cramer V i shuffle-baseline pozostają diagnostyką, nie zastępują SP 800-90B non-IID.</div>'
        + pipeline_comparison
        + '<section><h2>Porównanie strumieni</h2>' + summary_table + '</section>'
        + "".join(sections)
    )
    navigation = (
        '<a href="run_report.html">Raport główny</a>'
        '<a href="dual_weave_report.html">Dual weave</a>'
        f'<a href="{SUMMARY_NAME}">JSON</a>'
        f'<a href="{CSV_NAME}">CSV</a>'
    )
    extra_css = """
.pipeline-diagram{display:block;width:100%;max-height:720px;object-fit:contain;background:#0d1117;border-radius:10px}
.pipeline-stage-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.pipeline-stage-card{margin:0;border:1px solid var(--border);border-radius:10px;padding:12px;background:var(--panel-2)}.pipeline-stage-card h3{margin:0 0 4px}.pipeline-stage-card p{margin:4px 0}.pipeline-stage-images{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:9px}.pipeline-stage-images img{display:block;width:100%;border:1px solid var(--border);border-radius:8px;background:#f5f5f5}.pipeline-stage-images span{display:block;text-align:center;color:var(--muted);font-size:.72rem;margin-top:4px}.pipeline-stage-metrics{font-size:.78rem;color:var(--muted)}
.histogram-report-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;margin:14px 0 18px}.histogram-report-grid figure{margin:0}.histogram-report-grid figcaption{color:var(--muted);font-size:.84rem;line-height:1.45;margin-top:7px}
.heatmap-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:14px 0 18px}.heatmap-grid figure{margin:0}.heatmap-grid figcaption{color:var(--muted);font-size:.84rem;line-height:1.45;margin-top:7px}
.geometry-heatmap{display:block;width:100%;margin:10px auto 0;border:1px solid var(--border);border-radius:10px;background:#f5f5f5}
.geometry-controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px}
.geometry-controls button{background:#238636;color:#fff;border:1px solid #3fb950;border-radius:7px;padding:8px 12px;cursor:pointer}
@media(max-width:1050px){.heatmap-grid,.histogram-report-grid,.pipeline-stage-grid{grid-template-columns:1fr}}
@media(max-width:650px){.pipeline-stage-images{grid-template-columns:1fr}}
"""
    extra_js = ""

    return html_page(
        title="Geometria 2D/3D strumieni BIN",
        subtitle=run_dir.name,
        navigation=navigation,
        body=body,
        plot_specs=_plot_specs(rows, plot_data),
        extra_css=extra_css,
        extra_js=extra_js,
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "file", "file_bytes", "sampled_bytes", "sample_segments", "byte_entropy_bits", "byte_min_entropy_bits",
        "byte_chi_square", "pair_count", "pair_occupied_bins", "pair_occupied_ratio", "x_entropy_bits",
        "y_entropy_bits", "pair_entropy_bits", "pair_entropy_ratio", "pair_min_entropy_bits",
        "conditional_entropy_y_given_x_bits", "conditional_entropy_ratio",
        "mutual_information_bits", "shuffle_baseline_mutual_information_bits", "excess_mutual_information_bits",
        "byte_serial_correlation", "transition_independence_chi_square", "transition_independence_degrees_of_freedom",
        "transition_chi_square_per_dof", "transition_cramers_v", "pearson_residual_rms",
        "pearson_residual_abs_p95", "pearson_residual_abs_p99", "pearson_residual_abs_max",
        "log2_enrichment_abs_p99", "triplet_scatter_points", "triplet_occupied_voxels_16",
        "triplet_occupied_ratio_16", "triplet_max_voxel_share_16", "heatmap", "observed_heatmap",
        "residual_heatmap", "byte_histogram", "surface_fallback", "scatter_fallback", "pair_counts_npz", "pipeline_stage_rank", "pipeline_stage_label",
        "observed_log10_scale_max",
    ]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--include", action="append", default=[], help="Glob relative to run directory; repeatable")
    parser.add_argument("--all-bin", action="store_true", help="Analyze all .bin candidates, still limited by --max-files")
    parser.add_argument("--max-files", type=int, default=8)
    parser.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024, help="Maximum sampled bytes per file")
    parser.add_argument("--max-scatter-points", type=int, default=15_000)
    parser.add_argument("--diagram-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        raise SystemExit(f"run directory does not exist: {run_dir}")
    if args.max_files < 1 or args.max_bytes < 4096 or args.max_scatter_points < 100:
        raise SystemExit("invalid analysis limits")

    write_dual_weave_svg(run_dir / DIAGRAM_NAME)
    if args.diagram_only:
        print(json.dumps({"status": "ok", "diagram": str(run_dir / DIAGRAM_NAME)}, ensure_ascii=False))
        return 0

    selected = select_bin_files(run_dir, args.include, args.max_files, args.all_bin)
    if not selected:
        print(json.dumps({"status": "skipped", "reason": "no matching .bin files", "run_dir": str(run_dir)}, ensure_ascii=False))
        return 0

    rows: list[dict[str, Any]] = []
    plot_data: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    pending: list[tuple[Path, dict[str, Any], dict[str, np.ndarray], dict[str, list[int]]]] = []
    for path in selected:
        try:
            metrics, matrices, scatter = analyze_file(path, args.max_bytes, args.max_scatter_points)
            stage = pipeline_stage(path)
            if stage is not None:
                metrics["pipeline_stage_rank"], metrics["pipeline_stage_label"] = stage
            pending.append((path, metrics, matrices, scatter))
        except Exception as exc:  # keep other streams reportable
            failures.append({"file": str(path), "error": f"{type(exc).__name__}: {exc}"})

    if not pending:
        raise SystemExit("all binary geometry analyses failed: " + json.dumps(failures, ensure_ascii=False))

    observed_log10_scale_max = max(
        float(np.log10(1.0 + matrices["counts"].max(initial=0)))
        for _path, _metrics, matrices, _scatter in pending
    )
    histogram_delta_scale_max = max(
        float(np.max(np.abs(100.0 * (matrices["byte_counts"].astype(np.float64) / max(1.0, float(matrices["byte_counts"].sum())) - (1.0 / 256.0))), initial=0.0))
        for _path, _metrics, matrices, _scatter in pending
    )
    for path, metrics, matrices, scatter in pending:
        matrix = matrices["counts"]
        slug = safe_slug(path.stem)
        observed_name = f"byte_pairs_2d_{slug}.png"
        residual_name = f"byte_pairs_residual_{slug}.png"
        histogram_name = f"byte_histogram_{slug}.png"
        surface_fallback_name = f"byte_pairs_surface_3d_{slug}.png"
        scatter_fallback_name = f"byte_triplets_scatter_3d_{slug}.png"
        counts_name = f"byte_pairs_counts_{slug}.npz"
        write_observed_heatmap_png(
            matrix, run_dir / observed_name, path.name, scale_max=observed_log10_scale_max,
        )
        write_residual_heatmap_png(matrix, matrices["pearson_residuals"], run_dir / residual_name, path.name)
        write_byte_histogram_png(
            matrices["byte_counts"], run_dir / histogram_name, path.name,
            delta_scale_max=histogram_delta_scale_max,
        )
        np.savez_compressed(run_dir / counts_name, **matrices)
        surface = downsample_pair_matrix(matrix, bins=32)
        surface_z = np.log10(1.0 + surface.T.astype(np.float64))
        write_surface_fallback_png(surface_z.T, run_dir / surface_fallback_name, path.name)
        write_scatter_fallback_png(scatter, run_dir / scatter_fallback_name, path.name)
        axis = [int(round((index + 0.5) * 8 - 0.5)) for index in range(32)]
        metrics.update({
            "slug": slug, "heatmap": observed_name, "observed_heatmap": observed_name,
            "residual_heatmap": residual_name, "byte_histogram": histogram_name,
            "surface_fallback": surface_fallback_name, "scatter_fallback": scatter_fallback_name,
            "pair_counts_npz": counts_name,
            "observed_log10_scale_max": observed_log10_scale_max,
            "histogram_delta_scale_max": histogram_delta_scale_max,
        })
        rows.append(metrics)
        # Surface matrix is indexed [x, y], while Plotly z rows correspond to y.
        byte_counts = matrices["byte_counts"].astype(np.float64)
        byte_total = float(byte_counts.sum())
        plot_data[slug] = {
            "histogram": {
                "x": list(range(256)),
                "frequency_percent": (100.0 * byte_counts / byte_total).tolist() if byte_total else [0.0] * 256,
                "delta_pp": (100.0 * byte_counts / byte_total - (100.0 / 256.0)).tolist() if byte_total else [(-100.0 / 256.0)] * 256,
            },
            "surface": {"axis": axis, "z": surface_z.tolist()},
            "scatter": scatter,
        }

    if not rows:
        raise SystemExit("all binary geometry analyses failed: " + json.dumps(failures, ensure_ascii=False))

    summary = {
        "schema": SCHEMA,
        "run_dir": str(run_dir),
        "definition": "joint distribution of adjacent byte pairs with X=B_n (current byte) and Y=B_(n+1) (next byte)",
        "independence_model": "E(x,y)=count_X(x)*count_Y(y)/N; residual=(O-E)/sqrt(E)",
        "pearson_residual_display_clip": PEARSON_RESIDUAL_CLIP,
        "observed_log10_display_max": observed_log10_scale_max,
        "histogram_delta_display_max": histogram_delta_scale_max,
        "sample_strategy": "up to 16 evenly spaced segments per file; pairs never cross segment boundaries",
        "max_bytes_per_file": args.max_bytes,
        "max_scatter_points_per_file": args.max_scatter_points,
        "files": rows,
        "failures": failures,
        "caveat": "Diagnostic visualization and plug-in histogram metrics; not a replacement for SP 800-90B non-IID assessment.",
    }
    (run_dir / SUMMARY_NAME).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(run_dir / CSV_NAME, rows)
    (run_dir / REPORT_NAME).write_text(build_report(run_dir, rows, plot_data), encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "report": str(run_dir / REPORT_NAME),
        "files_analyzed": len(rows),
        "failures": failures,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
