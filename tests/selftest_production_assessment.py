#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

from app.qualification.qualification_production_assessment import build_cases

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_case(root: Path, index: int, ident: str, stage: str, **parameters: object) -> None:
    run = root / "runs" / ident
    run.mkdir(parents=True)
    state = {
        "index": index, "id": ident, "stage": stage, "label": ident,
        "status": "complete", "exit_code": 0, "run_dir": f"runs/{ident}",
        "log": f"logs/{ident}.log", "parameters": {key.upper(): value for key, value in parameters.items()},
    }
    write(root / "cases" / f"{index:03d}-{ident}.json", state)
    config = {
        "sample_mode": parameters.get("sample_mode", "xor"),
        "lsb_bits": parameters.get("lsb_bits", 1),
        "pairing_mode": parameters.get("pairing_mode", "disjoint"),
        "pair_lag_frames": parameters.get("pair_lag_frames", 4),
        "spatial_mask_pattern": parameters.get("spatial_mask_pattern", "full"),
        "spatial_sampling": parameters.get("spatial_sampling", "full"),
        "serialization_order": parameters.get("serialization_order", "row-major"),
        "conditioner_input_bits": parameters.get("conditioner_input_bits", 2048),
        "entropy_credit_bits_per_pixel": parameters.get("entropy_credit_bits_per_pixel", .5),
    }
    write(run / "runner_config.json", config)
    write(run / "output_complete.json", {
        "status": "complete", "active_pixels": 1000,
        "rates": {"masked_bps_lifetime": 800000.0 + index * 1000, "raw_change_bps_lifetime": 900000.0},
        "conditioner": {"output_bps_until_complete": 200000.0 + index * 1000, "time_to_target_seconds": 2.5, "written_bytes": 1024},
        "health": {"latched": False, "rct_failures": 0, "apt_failures": 0},
    })
    bits = int(parameters.get("lsb_bits", 1))
    write(run / "lsb_bitplane_summary.json", {
        "symbol_min_entropy_bits_per_symbol": .9 * bits,
        "symbol_min_entropy_bits_per_input_bit": .9,
        "max_abs_cross_plane_phi": .01 if bits > 1 else 0.0,
        "max_cross_plane_mutual_information_bits": .001,
    })
    write(run / "analysis_conditioned" / "summary.json", {
        "p1": .5001, "lag1": {"phi": .0002}, "byte_min_entropy_bits_per_byte": 7.94,
        "byte_chi_square_p_value": .55,
    })
    (run / "run_report.html").write_text("ok", encoding="utf-8")
    (root / "logs" / f"{ident}.log").parent.mkdir(exist_ok=True)
    (root / "logs" / f"{ident}.log").write_text("ok", encoding="utf-8")


def main() -> int:
    for level in ("quick", "full", "exhaustive"):
        cases = build_cases(level)
        assert cases
        assert len({case["id"] for case in cases}) == len(cases)
        assert max(int(case["parameters"]["LSB_BITS"]) for case in cases) <= 4
        assert {case["stage"] for case in cases} == {
            "lsb-mode-width", "temporal-pairing", "spatial-serialization",
            "entropy-credit", "conditioner", "dual-weave", "reproducibility",
        }
    assert len(build_cases("quick")) < len(build_cases("full")) < len(build_cases("exhaustive"))

    with tempfile.TemporaryDirectory() as temporary:
        campaign = Path(temporary) / "assessment"
        (campaign / "cases").mkdir(parents=True)
        (campaign / "logs").mkdir()
        write(campaign / "assessment_config.json", {
            "level": "full", "case_count": 7,
            "coverage": {"lsb": "xor/direct/delta × 1..4", "conditioner": "sweep"},
        })
        definitions = [
            ("xor-lsb1", "lsb-mode-width", {"sample_mode": "xor", "lsb_bits": 1}),
            ("xor-lsb2-disjoint-k2", "temporal-pairing", {"sample_mode": "xor", "lsb_bits": 2, "pair_lag_frames": 2}),
            ("checker-even", "spatial-serialization", {"sample_mode": "xor", "lsb_bits": 2, "spatial_mask_pattern": "checkerboard-even"}),
            ("xor-lsb1-credit-0p25", "entropy-credit", {"sample_mode": "xor", "lsb_bits": 1, "entropy_credit_bits_per_pixel": .25, "conditioner_input_bits": 2048}),
            ("sha3-input-4096", "conditioner", {"sample_mode": "xor", "lsb_bits": 2, "conditioner_input_bits": 4096}),
            ("dual-weave-k4", "dual-weave", {"sample_mode": "xor", "lsb_bits": 1, "pair_lag_frames": 4}),
            ("xor-lsb1-repeat-1", "reproducibility", {"sample_mode": "xor", "lsb_bits": 1}),
        ]
        for index, (ident, stage, params) in enumerate(definitions, 1):
            make_case(campaign, index, ident, stage, **params)
        dual = campaign / "runs" / "dual-weave-k4"
        write(dual / "dual_weave_report.json", {"results": [{
            "order": "row-major", "alignment": "stagger-2",
            "throughput_ratio_vs_checkerboard": 1.9,
            "conditioned_bps_until_complete": 300000.0,
            "conditioner_input_positional_worst_abs_phi": .002,
            "conditioned_lag1_phi": .0001,
            "conditioned_hmin_byte": 7.95,
            "health_latched": False,
        }]})
        subprocess.run(["python3", "-m", "app.reporting.summarize_production_assessment", str(campaign)], check=True, stdout=subprocess.DEVNULL)
        summary = json.loads((campaign / "production_assessment_summary.json").read_text(encoding="utf-8"))
        assert summary["schema"] == "camera-entropy-production-assessment-v1"
        assert summary["status"]["total"] == 7
        assert len(summary["dual_weave_variants"]) == 1
        report = (campaign / "production_assessment_report.html").read_text(encoding="utf-8")
        for token in (
            "kompleksowa ocena produkcyjna", "rate-unit-select", "production-assessment-data",
            "Tryb próbkowania i szerokość LSB", "Dokładne parametry", "Wszystkie przypadki",
            "kB/s", "MiB/s", "MB/s",
        ):
            assert token in report, token
        assert "/static/vendor/plotly" not in report
        assert "Plotly.newPlot" in report or "window.Plotly" in report
        assert len(report.encode("utf-8")) > 4_000_000
        assert (campaign / "production_assessment_cases.csv").is_file()

    print("production assessment self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
