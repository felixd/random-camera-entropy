#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a 2D-only byte geometry report for binary pipeline outputs.

The report contains byte histograms, adjacent-byte transition heatmaps and
Pearson-residual heatmaps.  Volumetric surfaces and triplet scatter plots are
intentionally not generated.
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import html
import json
import math
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np

APP_VERSION = "2026.08.04.camera-entropy-binary-geometry.7.12.0"
REPORT_NAME = "binary_geometry_report.html"
SUMMARY_NAME = "binary_geometry_summary.json"
CSV_NAME = "binary_geometry_metrics.csv"
DIAGRAM_NAME = "dual_weave_geometry.svg"
PEARSON_CLIP = 8.0
PIPELINE_STAGE_RULES = (
    ("direct_lsb", 0, "Direct LSB"),
    ("temporal_raw", 10, "Temporal difference"),
    ("temporal_masked", 20, "Temporal difference + active mask"),
    ("temporal_vn", 30, "Von Neumann"),
    ("sha3_512", 40, "SHA3-512"),
)


def safe_slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.") or "stream"


def fmt_bytes(value: int) -> str:
    number = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(number) < 1024.0 or unit == "TiB":
            return f"{number:.2f} {unit}"
        number /= 1024.0
    return f"{number:.2f} TiB"


def entropy_from_counts(counts: np.ndarray) -> float:
    total = float(counts.sum())
    if total <= 0:
        return 0.0
    probabilities = counts[counts > 0].astype(np.float64) / total
    return float(-np.sum(probabilities * np.log2(probabilities)))


def min_entropy_from_counts(counts: np.ndarray) -> float:
    total = float(counts.sum())
    maximum = float(counts.max(initial=0))
    return -math.log2(maximum / total) if total > 0 and maximum > 0 else 0.0


def pair_counts(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Count oriented byte pairs; matrix[x, y] means B_n=x, B_(n+1)=y."""
    left = np.asarray(x, dtype=np.uint8).reshape(-1)
    right = np.asarray(y, dtype=np.uint8).reshape(-1)
    if left.shape != right.shape:
        raise ValueError("x and y must have the same shape")
    indices = left.astype(np.uint32) * 256 + right.astype(np.uint32)
    return np.bincount(indices, minlength=65536).reshape(256, 256).astype(np.uint64)


def independence_diagnostics(
    counts: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """Return expected counts, Pearson residuals, log2 enrichment and metrics."""
    observed = np.asarray(counts, dtype=np.uint64)
    if observed.shape != (256, 256):
        raise ValueError("counts must have shape (256, 256)")
    total = int(observed.sum())
    x_counts = observed.sum(axis=1).astype(np.float64)
    y_counts = observed.sum(axis=0).astype(np.float64)
    expected = np.outer(x_counts, y_counts) / max(1.0, float(total))
    residuals = np.zeros_like(expected)
    valid = expected > 0
    residuals[valid] = (
        observed.astype(np.float64)[valid] - expected[valid]
    ) / np.sqrt(expected[valid])
    # Jeffreys-style 0.5 smoothing keeps empty bins finite and inspectable.
    log2_enrichment = np.log2(
        (observed.astype(np.float64) + 0.5) / (expected + 0.5)
    )
    chi_square = float(np.sum(np.square(residuals[valid])))
    occupied_rows = int(np.count_nonzero(x_counts))
    occupied_cols = int(np.count_nonzero(y_counts))
    dof = max(1, (occupied_rows - 1) * (occupied_cols - 1))
    denominator = max(
        1.0,
        float(total)
        * min(max(1, occupied_rows - 1), max(1, occupied_cols - 1)),
    )
    cramer_v = math.sqrt(chi_square / denominator) if total else 0.0
    abs_residual = np.abs(residuals[valid]) if np.any(valid) else np.array([0.0])
    diagnostics: dict[str, Any] = {
        "transition_independence_chi_square": chi_square,
        "transition_independence_degrees_of_freedom": dof,
        "transition_chi_square_per_dof": chi_square / dof,
        "transition_cramers_v": cramer_v,
        "pearson_residual_rms": float(np.sqrt(np.mean(np.square(residuals[valid])))) if np.any(valid) else 0.0,
        "pearson_residual_abs_p95": float(np.percentile(abs_residual, 95)),
        "pearson_residual_abs_p99": float(np.percentile(abs_residual, 99)),
        "pearson_residual_abs_max": float(abs_residual.max(initial=0.0)),
    }
    return expected, residuals, log2_enrichment, diagnostics


def read_even_sample(path: Path, max_bytes: int, segments: int = 32) -> np.ndarray:
    size = path.stat().st_size
    if size <= max_bytes:
        return np.fromfile(path, dtype=np.uint8)
    segment_count = max(1, min(segments, max_bytes // 4096))
    chunk_size = max(4096, max_bytes // segment_count)
    chunk_size = min(chunk_size, size)
    offsets = np.linspace(0, max(0, size - chunk_size), segment_count, dtype=np.int64)
    chunks: list[np.ndarray] = []
    with path.open("rb") as stream:
        for offset in offsets:
            stream.seek(int(offset))
            chunks.append(np.frombuffer(stream.read(chunk_size), dtype=np.uint8).copy())
    data = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.uint8)
    return data[:max_bytes]


def analyze_bytes(data: np.ndarray) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    byte_counts = np.bincount(data, minlength=256).astype(np.uint64)
    transition_counts = (
        pair_counts(data[:-1], data[1:])
        if data.size >= 2
        else np.zeros((256, 256), dtype=np.uint64)
    )
    pair_total = int(transition_counts.sum())
    row_counts = transition_counts.sum(axis=1).astype(np.float64)
    col_counts = transition_counts.sum(axis=0).astype(np.float64)
    expected, residual, log2_enrichment, independence = independence_diagnostics(transition_counts)
    x_entropy = entropy_from_counts(row_counts)
    y_entropy = entropy_from_counts(col_counts)
    pair_entropy = entropy_from_counts(transition_counts)
    mutual_information = max(0.0, x_entropy + y_entropy - pair_entropy)
    serial = 0.0
    if data.size > 2:
        x = data[:-1].astype(np.float64)
        y = data[1:].astype(np.float64)
        x -= x.mean()
        y -= y.mean()
        denominator = float(np.sqrt(np.dot(x, x) * np.dot(y, y)))
        serial = float(np.dot(x, y) / denominator) if denominator > 0 else 0.0
    metrics: dict[str, Any] = {
        "sampled_bytes": int(data.size),
        "byte_entropy_bits": entropy_from_counts(byte_counts),
        "byte_min_entropy_bits": min_entropy_from_counts(byte_counts),
        "pair_count": pair_total,
        "pair_occupied_bins": int(np.count_nonzero(transition_counts)),
        "pair_occupied_ratio": float(np.count_nonzero(transition_counts) / 65536.0),
        "x_entropy_bits": x_entropy,
        "y_entropy_bits": y_entropy,
        "pair_entropy_bits": pair_entropy,
        "pair_entropy_ratio": pair_entropy / 16.0,
        "conditional_entropy_y_given_x_bits": max(0.0, pair_entropy - x_entropy),
        "mutual_information_bits": mutual_information,
        "excess_mutual_information_bits": mutual_information,
        "byte_serial_correlation": serial,
        **independence,
    }
    return metrics, {
        "byte_counts": byte_counts,
        "pair_counts": transition_counts,
        "expected": expected,
        "residual": residual,
        "log2_enrichment": log2_enrichment,
        "x_counts": row_counts.astype(np.uint64),
        "y_counts": col_counts.astype(np.uint64),
    }


def normalize_image(values: np.ndarray, *, symmetric: bool = False, clip: float | None = None) -> np.ndarray:
    source = values.astype(np.float64)
    if symmetric:
        bound = clip if clip is not None else float(np.max(np.abs(source), initial=1.0))
        bound = max(bound, 1e-12)
        normalized = np.clip((source + bound) / (2.0 * bound), 0.0, 1.0)
    else:
        maximum = float(source.max(initial=0.0))
        normalized = source / maximum if maximum > 0 else np.zeros_like(source)
    return np.rint(normalized * 255.0).astype(np.uint8)


def write_transition_png(path: Path, pair_counts: np.ndarray) -> None:
    image = normalize_image(np.log10(1.0 + pair_counts.astype(np.float64)))
    colored = cv2.applyColorMap(image, cv2.COLORMAP_TURBO)
    cv2.imwrite(str(path), cv2.resize(colored, (768, 768), interpolation=cv2.INTER_NEAREST))


def write_residual_png(path: Path, residual: np.ndarray) -> None:
    scaled = np.clip(residual.astype(np.float64) / PEARSON_CLIP, -1.0, 1.0)
    magnitude = np.abs(scaled)
    neutral = np.rint(245.0 * (1.0 - magnitude)).astype(np.uint8)
    colored = np.empty((*scaled.shape, 3), dtype=np.uint8)
    colored[..., 0] = np.where(scaled < 0, 255, neutral)  # blue for deficits
    colored[..., 1] = neutral
    colored[..., 2] = np.where(scaled > 0, 255, neutral)  # red for excesses
    cv2.imwrite(str(path), cv2.resize(colored, (768, 768), interpolation=cv2.INTER_NEAREST))


def write_histogram_png(path: Path, counts: np.ndarray) -> None:
    width, height = 1024, 420
    canvas = np.full((height, width, 3), 248, dtype=np.uint8)
    total = max(1.0, float(counts.sum()))
    delta = 100.0 * (counts.astype(np.float64) / total - 1.0 / 256.0)
    bound = max(0.004, float(np.max(np.abs(delta), initial=0.0))) * 1.1
    zero_y = height // 2
    cv2.line(canvas, (44, zero_y), (width - 18, zero_y), (100, 100, 100), 1)
    points = []
    for index, value in enumerate(delta):
        x = 44 + int(index * (width - 64) / 255)
        y = zero_y - int((value / bound) * (height * 0.42))
        points.append((x, y))
    cv2.polylines(canvas, [np.asarray(points, dtype=np.int32)], False, (50, 95, 210), 2, cv2.LINE_AA)
    cv2.putText(canvas, f"delta from 1/256 [percentage points], range +/-{bound:.6f}", (44, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 30), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), canvas)


def pipeline_stage(path: Path) -> tuple[int, str] | None:
    name = path.name.lower()
    for token, rank, label in PIPELINE_STAGE_RULES:
        if token in name:
            return rank, label
    return None


def select_files(run_dir: Path, includes: list[str], max_files: int, all_bin: bool) -> list[Path]:
    candidates = sorted((p for p in run_dir.rglob("*.bin") if p.is_file() and p.stat().st_size >= 2), key=lambda p: (pipeline_stage(p) or (99, ""))[0])
    if includes:
        candidates = [p for p in candidates if any(fnmatch.fnmatch(str(p.relative_to(run_dir)), pattern) or fnmatch.fnmatch(p.name, pattern) for pattern in includes)]
    if not all_bin and not includes:
        preferred: list[Path] = []
        for token, _rank, _label in PIPELINE_STAGE_RULES:
            match = next((p for p in candidates if token in p.name.lower()), None)
            if match is not None and match not in preferred:
                preferred.append(match)
        preferred.extend(p for p in candidates if p not in preferred)
        candidates = preferred
    return candidates[:max_files]


def select_bin_files(
    run_dir: Path,
    includes: list[str],
    max_files: int,
    all_bin: bool,
) -> list[Path]:
    """Compatibility alias retained for existing self-tests and tooling."""
    return select_files(run_dir, includes, max_files, all_bin)


def write_dual_weave_svg(path: Path) -> None:
    path.write_text("""<svg xmlns="http://www.w3.org/2000/svg" width="980" height="250" viewBox="0 0 980 250">
<style>text{font-family:system-ui,sans-serif;fill:#eaf3fb}.box{fill:#152230;stroke:#55b5ff;stroke-width:2}.arrow{stroke:#94a3b5;stroke-width:3;marker-end:url(#a)}.small{font-size:14px;fill:#a9bac9}.title{font-size:19px;font-weight:700}</style><defs><marker id="a" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#94a3b5"/></marker></defs><rect width="980" height="250" fill="#0b0f14"/><rect class="box" x="30" y="75" width="170" height="90" rx="10"/><text class="title" x="55" y="112">Y frames</text><text class="small" x="55" y="138">pair lag k</text><line class="arrow" x1="200" y1="120" x2="285" y2="120"/><rect class="box" x="285" y="75" width="190" height="90" rx="10"/><text class="title" x="310" y="108">Frozen mask</text><text class="small" x="310" y="135">LSB0 calibration</text><line class="arrow" x1="475" y1="120" x2="560" y2="120"/><rect class="box" x="560" y="55" width="190" height="130" rx="10"/><text class="title" x="585" y="93">Bit planes</text><text class="small" x="585" y="120">1..4 LSB</text><text class="small" x="585" y="143">LSB-first</text><line class="arrow" x1="750" y1="120" x2="825" y2="120"/><rect class="box" x="825" y="75" width="125" height="90" rx="10"/><text class="title" x="845" y="110">VN /</text><text class="title" x="845" y="137">SHA3</text></svg>""", encoding="utf-8")


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def build_report(run_dir: Path, rows: list[dict[str, Any]]) -> str:
    table_rows = "".join(
        f"<tr><td><code>{esc(row['file'])}</code></td><td>{esc(fmt_bytes(row['sampled_bytes']))}</td><td>{row['byte_entropy_bits']:.7f}</td><td>{row['byte_min_entropy_bits']:.7f}</td><td>{row['mutual_information_bits']:.8f}</td><td>{row['transition_cramers_v']:.8f}</td><td>{row['byte_serial_correlation']:.8f}</td></tr>"
        for row in rows
    )
    sections = []
    for row in rows:
        sections.append(f"""
<section><h2>{esc(row['file'])}</h2><p class="muted">Próbka: {esc(fmt_bytes(row['sampled_bytes']))} z {esc(fmt_bytes(row['file_bytes']))}</p>
<div class="metrics"><div><b>H(byte)</b><span>{row['byte_entropy_bits']:.8f} / 8</span></div><div><b>Hmin(byte)</b><span>{row['byte_min_entropy_bits']:.8f}</span></div><div><b>MI(Bn;Bn+1)</b><span>{row['mutual_information_bits']:.9f}</span></div><div><b>Cramer V</b><span>{row['transition_cramers_v']:.9f}</span></div><div><b>|residual| p99</b><span>{row['pearson_residual_abs_p99']:.5f}</span></div><div><b>corr lag-1</b><span>{row['byte_serial_correlation']:.9f}</span></div></div>
<div class="plots"><figure><h3>Histogram bajtów</h3><a href="{esc(row['byte_histogram'])}"><img src="{esc(row['byte_histogram'])}"></a><figcaption>Odchylenie od rozkładu jednostajnego 1/256.</figcaption></figure><figure><h3>Przejścia Bn → Bn+1</h3><a href="{esc(row['observed_heatmap'])}"><img src="{esc(row['observed_heatmap'])}"></a><figcaption>log10(1 + liczność) dla macierzy 256×256.</figcaption></figure><figure><h3>Reszty Pearsona</h3><a href="{esc(row['residual_heatmap'])}"><img src="{esc(row['residual_heatmap'])}"></a><figcaption>(O-E)/sqrt(E), skala obcięta do ±{PEARSON_CLIP:g}.</figcaption></figure></div>
<p><a href="{esc(row['pair_counts_npz'])}">Pobierz macierz NPZ</a></p></section>""")
    return f"""<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Geometria 2D strumieni BIN</title><style>
:root{{color-scheme:dark;--bg:#0b0f14;--panel:#131a22;--border:#2a3441;--text:#edf4fb;--muted:#94a3b5;--accent:#55b5ff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,sans-serif}}main{{max-width:1500px;margin:auto;padding:22px}}section{{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;margin:16px 0}}h1,h2,h3{{margin-top:0}}a{{color:var(--accent)}}.muted,figcaption{{color:var(--muted)}}table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid var(--border);text-align:left}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}}.metrics div{{background:#0e141b;border:1px solid var(--border);border-radius:8px;padding:10px}}.metrics span{{display:block;color:var(--muted);margin-top:4px}}.plots{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}figure{{margin:0}}img{{width:100%;border:1px solid var(--border);border-radius:9px;background:white}}@media(max-width:900px){{.plots,.metrics{{grid-template-columns:1fr}}}}</style></head><body><main><h1>Geometria 2D strumieni BIN</h1><p class="muted">{esc(run_dir.name)} · {esc(APP_VERSION)}. Raport celowo nie generuje wykresów wolumetrycznych ani chmur trójek.</p><section><h2>Podsumowanie</h2><table><thead><tr><th>Plik</th><th>Próbka</th><th>H</th><th>Hmin</th><th>MI</th><th>Cramer V</th><th>corr</th></tr></thead><tbody>{table_rows}</tbody></table></section>{''.join(sections)}</main></body></html>"""


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row if key != "slug"})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--all-bin", action="store_true")
    parser.add_argument("--max-files", type=int, default=8)
    parser.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--diagram-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = args.run_dir.expanduser().resolve()
    if not run_dir.is_dir():
        raise SystemExit(f"run directory does not exist: {run_dir}")
    if args.max_files < 1 or args.max_bytes < 4096:
        raise SystemExit("invalid analysis limits")
    write_dual_weave_svg(run_dir / DIAGRAM_NAME)
    if args.diagram_only:
        print(json.dumps({"status": "ok", "diagram": str(run_dir / DIAGRAM_NAME)}, ensure_ascii=False))
        return 0
    selected = select_files(run_dir, args.include, args.max_files, args.all_bin)
    if not selected:
        print(json.dumps({"status": "skipped", "reason": "no matching .bin files"}, ensure_ascii=False))
        return 0
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for path in selected:
        try:
            data = read_even_sample(path, args.max_bytes)
            metrics, matrices = analyze_bytes(data)
            slug = safe_slug(path.stem)
            observed_name = f"byte_pairs_2d_{slug}.png"
            residual_name = f"byte_pairs_residual_{slug}.png"
            histogram_name = f"byte_histogram_{slug}.png"
            counts_name = f"byte_pairs_counts_{slug}.npz"
            write_transition_png(run_dir / observed_name, matrices["pair_counts"])
            write_residual_png(run_dir / residual_name, matrices["residual"])
            write_histogram_png(run_dir / histogram_name, matrices["byte_counts"])
            np.savez_compressed(
                run_dir / counts_name,
                counts=matrices["pair_counts"],
                pair_counts=matrices["pair_counts"],
                expected=matrices["expected"],
                pearson_residuals=matrices["residual"],
                pearson_residual=matrices["residual"],
                log2_enrichment=matrices["log2_enrichment"],
                x_counts=matrices["x_counts"],
                y_counts=matrices["y_counts"],
                byte_counts=matrices["byte_counts"],
            )
            stage = pipeline_stage(path)
            row = {
                "file": str(path.relative_to(run_dir)),
                "file_bytes": path.stat().st_size,
                "slug": slug,
                "observed_heatmap": observed_name,
                "residual_heatmap": residual_name,
                "byte_histogram": histogram_name,
                "pair_counts_npz": counts_name,
                "pipeline_stage_rank": stage[0] if stage else None,
                "pipeline_stage_label": stage[1] if stage else None,
                "observed_log10_scale_local_max": float(
                    np.log10(1.0 + matrices["pair_counts"].astype(np.float64)).max(initial=0.0)
                ),
                **metrics,
            }
            rows.append(row)
        except Exception as exc:
            failures.append({"file": str(path), "error": f"{type(exc).__name__}: {exc}"})
    if not rows:
        raise SystemExit("all 2D binary geometry analyses failed: " + json.dumps(failures, ensure_ascii=False))
    shared_scale = max(float(row.get("observed_log10_scale_local_max", 0.0)) for row in rows)
    for row in rows:
        row["observed_log10_scale_max"] = shared_scale
    (run_dir / REPORT_NAME).write_text(build_report(run_dir, rows), encoding="utf-8")
    write_csv(run_dir / CSV_NAME, rows)
    summary = {
        "schema": "camera-entropy-binary-geometry-v5-2d-only",
        "tool_version": APP_VERSION,
        "run_directory": str(run_dir),
        "report": REPORT_NAME,
        "csv": CSV_NAME,
        "diagram": DIAGRAM_NAME,
        "plots": ["byte_histogram", "byte_transition_heatmap", "pearson_residual_heatmap"],
        "volumetric_plots": False,
        "rows": rows,
        "files": rows,
        "failures": failures,
    }
    (run_dir / SUMMARY_NAME).write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps({"status": "ok", "files": len(rows), "report": str(run_dir / REPORT_NAME)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
