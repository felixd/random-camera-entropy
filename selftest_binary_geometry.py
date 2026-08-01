#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np

import analyze_binary_geometry as geometry


def main() -> int:
    # Orientation invariant: matrix[x, y] means B_n=x and B_(n+1)=y.
    x = np.array([1, 1, 2], dtype=np.uint8)
    y = np.array([250, 250, 3], dtype=np.uint8)
    counts = geometry.pair_counts(x, y)
    assert counts[1, 250] == 2
    assert counts[2, 3] == 1
    assert counts.sum() == 3

    # Exact independence model: observed == outer marginals / N.
    independent = np.zeros((256, 256), dtype=np.uint64)
    independent[:2, :2] = np.array([[40, 20], [20, 10]], dtype=np.uint64)
    expected, residuals, enrichment, diag = geometry.independence_diagnostics(independent)
    assert np.allclose(expected[:2, :2], independent[:2, :2])
    assert np.allclose(residuals, 0.0)
    assert diag["transition_independence_chi_square"] == 0.0
    assert diag["transition_cramers_v"] == 0.0
    assert enrichment.shape == (256, 256)

    with tempfile.TemporaryDirectory() as raw:
        run = Path(raw) / "run"
        run.mkdir()
        rng = np.random.default_rng(123456)
        random_data = rng.integers(0, 256, size=1_000_000, dtype=np.uint8)
        dependent_data = np.tile(np.arange(256, dtype=np.uint8), 4096)
        random_name = "camera_entropy_sha3_512.bin"
        dependent_name = "y_direct_lsb_common_mask_validation.bin"
        (run / random_name).write_bytes(random_data.tobytes())
        (run / dependent_name).write_bytes(dependent_data.tobytes())

        selection = Path(raw) / "selection"
        selection.mkdir()
        for name in (
            "y_direct_lsb_common_mask_validation.bin",
            "y_temporal_masked_validation.bin",
            "y_temporal_vn.bin",
            "camera_entropy_sha3_512.bin",
            "dual_weave_row_major_stagger_2_conditioner_input_validation.bin",
        ):
            (selection / name).write_bytes(b"\x00\x01\x02\x03")
        selected = geometry.select_bin_files(selection, [], 4, False)
        assert [geometry.pipeline_stage(path)[1] for path in selected] == [
            "Direct LSB", "Temporal difference + active mask", "Von Neumann", "SHA3-512",
        ]

        result = geometry.main([
            str(run), "--all-bin", "--max-files", "4", "--max-bytes", "1048576",
            "--max-scatter-points", "4000",
        ])
        assert result == 0
        summary = json.loads((run / geometry.SUMMARY_NAME).read_text(encoding="utf-8"))
        rows = {row["file"]: row for row in summary["files"]}
        random_row = rows[random_name]
        dependent_row = rows[dependent_name]
        assert random_row["byte_entropy_bits"] > 7.99
        assert random_row["conditional_entropy_y_given_x_bits"] > 7.9
        assert dependent_row["excess_mutual_information_bits"] > random_row["excess_mutual_information_bits"] + 4.0
        assert dependent_row["conditional_entropy_y_given_x_bits"] < 0.1
        assert dependent_row["transition_cramers_v"] > random_row["transition_cramers_v"] + 0.5
        assert dependent_row["pearson_residual_abs_p99"] > random_row["pearson_residual_abs_p99"]
        assert dependent_row["pipeline_stage_label"] == "Direct LSB"
        assert random_row["pipeline_stage_label"] == "SHA3-512"
        assert dependent_row["observed_log10_scale_max"] == random_row["observed_log10_scale_max"]
        assert (run / geometry.REPORT_NAME).is_file()
        assert (run / geometry.DIAGRAM_NAME).is_file()
        assert list(run.glob("byte_pairs_2d_*.png"))
        assert list(run.glob("byte_pairs_residual_*.png"))
        assert list(run.glob("byte_histogram_*.png"))
        assert list(run.glob("byte_pairs_surface_3d_*.png"))
        assert list(run.glob("byte_triplets_scatter_3d_*.png"))
        npz_paths = list(run.glob("byte_pairs_counts_*.npz"))
        assert npz_paths
        with np.load(npz_paths[0]) as archive:
            assert {"counts", "expected", "pearson_residuals", "log2_enrichment", "x_counts", "y_counts", "byte_counts"} <= set(archive.files)
        report = (run / geometry.REPORT_NAME).read_text(encoding="utf-8")
        assert '"type":"surface"' in report
        assert '"type":"scatter3d"' in report
        assert '"requiresWebGL":true' in report
        assert '"staticUnderCsp":true' in report
        assert '"fallbackImage":"byte_pairs_surface_3d_' in report
        assert '"fallbackImage":"byte_triplets_scatter_3d_' in report
        assert "plot-static-fallback" in report
        assert "Zaobserwowane przejścia" in report
        assert "Odchylenie od niezależności" in report
        assert "Reszta Pearsona" in report
        assert "Porównanie etapów pipeline w jednej skali" in report
        assert "Histogram odchyleń częstotliwości bajtów" in report
        assert "Odchylenie od 1/256" in report
        diagram = (run / geometry.DIAGRAM_NAME).read_text(encoding="utf-8")
        assert "C0_g [1024 bity]" in diagram
        assert "C1_(g+2) [1024 bity]" in diagram

    print("binary geometry self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
