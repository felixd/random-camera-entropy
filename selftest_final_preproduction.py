#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from qualification_final_preproduction import cases, render_report


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_case(root: Path, index: int, case: dict) -> None:
    ident = str(case["id"])
    run = root / "runs" / ident
    state = {
        **case,
        "index": index,
        "stage": "final-preproduction",
        "status": "complete",
        "exit_code": 0,
        "run_dir": f"runs/{ident}",
        "log": f"logs/{ident}.log",
    }
    write(root / "cases" / f"{index:02d}-{ident}.json", state)
    params = case["parameters"]
    write(run / "runner_config.json", {
        "sample_mode": params["SAMPLE_MODE"],
        "lsb_bits": params["LSB_BITS"],
        "pairing_mode": params["PAIRING_MODE"],
        "pair_lag_frames": params["PAIR_LAG_FRAMES"],
        "spatial_mask_pattern": params["SPATIAL_MASK_PATTERN"],
        "spatial_sampling": params["SPATIAL_SAMPLING"],
        "serialization_order": params["SERIALIZATION_ORDER"],
        "conditioner_input_bits": params["CONDITIONER_INPUT_BITS"],
        "entropy_credit_bits_per_pixel": params["ENTROPY_CREDIT_BITS_PER_PIXEL"],
    })
    write(run / "output_complete.json", {
        "status": "complete",
        "completion_reason": "dataset-exhausted",
        "targets_complete": True,
        "active_pixels": 880000,
        "rates": {"masked_bps_lifetime": 8_000_000.0, "raw_change_bps_lifetime": 9_000_000.0},
        "conditioner": {"output_bps_until_complete": 4_000_000.0, "time_to_target_seconds": 12.0},
        "health": {"latched": False, "rct_failures": 0, "apt_failures": 0},
    })
    write(run / "lsb_bitplane_summary.json", {
        "symbol_min_entropy_bits_per_symbol": 0.995 * int(params["LSB_BITS"]),
        "symbol_min_entropy_bits_per_input_bit": 0.995,
        "max_abs_cross_plane_phi": 0.01 if int(params["LSB_BITS"]) > 1 else 0.0,
        "max_cross_plane_mutual_information_bits": 0.001,
    })
    write(run / "stream_lsb_summary.json", {
        "schema": "camera-entropy-stream-lsb-statistics-v1",
        "aggregate": {
            "total_symbols": 100_000_000,
            "pair_updates": 4096,
            "symbol_min_entropy_bits_per_symbol": 0.995 * int(params["LSB_BITS"]),
            "symbol_min_entropy_bits_per_input_bit": 0.995,
            "max_abs_cross_plane_phi": 0.01 if int(params["LSB_BITS"]) > 1 else 0.0,
            "max_cross_plane_mutual_information_bits": 0.001,
        },
        "window_count": 4,
        "worst_window_hmin_per_input_bit": 0.991,
        "max_window_abs_bit_bias": 0.001,
        "max_window_abs_bit_lag1_phi": 0.002,
        "windows": [
            {"index": value, "symbol_min_entropy_bits_per_input_bit": 0.991 + value * 0.001}
            for value in range(4)
        ],
    })
    write(run / "analysis_conditioned" / "summary.json", {
        "p1": 0.50005,
        "lag1": {"phi": 0.0001},
        "byte_min_entropy_bits_per_byte": 7.95,
        "byte_chi_square_p_value": 0.55,
    })
    (run / "run_report.html").parent.mkdir(parents=True, exist_ok=True)
    (run / "run_report.html").write_text("ok", encoding="utf-8")
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "logs" / f"{ident}.log").write_text("ok", encoding="utf-8")


def main() -> int:
    definitions = cases()
    assert len(definitions) == 5
    assert definitions[0]["id"] == "xor1-safe-k4-2048"
    assert all(int(case["parameters"]["LSB_BITS"]) <= 2 for case in definitions)

    with tempfile.TemporaryDirectory() as temporary:
        campaign = Path(temporary) / "final"
        for sub in ("cases", "runs", "logs"):
            (campaign / sub).mkdir(parents=True, exist_ok=True)
        for index, case in enumerate(definitions, 1):
            make_case(campaign, index, case)
        config = {
            "dataset": {"frame_count": 200000, "bytes_written": 184_320_000_000},
            "verification": {"verified": True, "closed_chunks": 170},
            "cases": definitions,
        }
        summary = render_report(campaign, config)
        assert summary["decision"]["production_ready"] is True
        assert summary["decision"]["production_candidate"] == "xor1-safe-k4-2048"
        assert summary["decision"]["optimisation_candidate"] == "xor1-fast-k4-1024"
        report = (campaign / "final_preproduction_report.html").read_text(encoding="utf-8")
        for token in (
            "ostateczna kwalifikacja przedprodukcyjna",
            "final-preproduction-data",
            "Dokładne parametry każdego testu",
            "rate-unit-select",
            "kB/s",
            "MiB/s",
            "MB/s",
        ):
            assert token in report, token
        assert "/static/vendor/plotly" not in report
        assert len(report.encode("utf-8")) > 4_000_000
        stored = json.loads((campaign / "final_preproduction_summary.json").read_text(encoding="utf-8"))
        assert stored["schema"] == "camera-entropy-final-preproduction-summary-v1"
        assert len(stored["cases"]) == 5

    print("final preproduction self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
