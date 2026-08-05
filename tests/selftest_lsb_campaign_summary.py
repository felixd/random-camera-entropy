#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        campaign = Path(temporary) / "campaign"
        state = campaign / "lsb_profiles"
        state.mkdir(parents=True)
        for name, bits, masked_bps, sha3_bps, hmin in (
            ("xor-lsb1", 1, 1000.0, 100.0, 0.8),
            ("xor-lsb2", 2, 1800.0, 150.0, 1.4),
        ):
            run = campaign / name
            run.mkdir()
            write(
                state / f"{name}.json",
                {
                    "profile": name,
                    "status": "complete",
                    "sample_mode": "xor",
                    "lsb_bits": bits,
                    "entropy_credit_bits_per_pixel": 0.5,
                    "minimum_conditioner_input_bits": 1024,
                    "conditioner_input_bits": 2048,
                    "exit_code": 0,
                },
            )
            write(
                run / "runner_config.json",
                {"sample_mode": "xor", "lsb_bits": bits},
            )
            write(
                run / "output_complete.json",
                {
                    "status": "complete",
                    "active_pixels": 200,
                    "rates": {
                        "raw_change_bps_lifetime": masked_bps * 1.1,
                        "masked_bps_lifetime": masked_bps,
                        "raw_change_bps_10s": masked_bps * 9.9,
                        "masked_bps_10s": masked_bps * 9.0,
                    },
                    "conditioner": {
                        "output_bps_until_complete": sha3_bps,
                        "time_to_target_seconds": 12.0,
                        "written_bytes": 1024,
                        "input_bits_consumed": 16384,
                    },
                },
            )
            write(
                run / "lsb_bitplane_summary.json",
                {
                    "symbol_min_entropy_bits_per_symbol": hmin,
                    "symbol_min_entropy_bits_per_input_bit": hmin / bits,
                    "max_abs_cross_plane_phi": 0.01,
                    "max_cross_plane_mutual_information_bits": 0.001,
                },
            )
            write(run / "analysis_conditioned" / "summary.json", {"total_bytes": 1024})
            (run / "run_report.html").write_text("ok", encoding="utf-8")
            (run / "lsb_bitplane_report.html").write_text("ok", encoding="utf-8")

        failed_name = "delta-lsb3"
        failed_run = campaign / failed_name
        failed_run.mkdir()
        write(
            state / f"{failed_name}.json",
            {
                "profile": failed_name,
                "status": "failed",
                "sample_mode": "delta",
                "lsb_bits": 3,
                "entropy_credit_bits_per_pixel": 0.25,
                "exit_code": 1,
            },
        )
        write(failed_run / "runner_config.json", {"sample_mode": "delta", "lsb_bits": 3})
        write(
            failed_run / "run_failed.json",
            {
                "status": "failed",
                "reason": "RCT:full: RCT: run=41, cutoff=41, symbol=0",
                "state": "HEALTH_FAILED",
                "rates": {
                    "raw_change_bps_lifetime": 3000.0,
                    "masked_bps_lifetime": 2400.0,
                },
                "conditioner": {
                    "output_bps_until_complete": 200.0,
                    "written_bytes": 512,
                    "input_bits_consumed": 8192,
                },
                "health": {
                    "sample_domain": "symbol",
                    "sample_width_bits": 3,
                    "rct_failures": 1,
                    "apt_failures": 0,
                    "rct_cutoff": 41,
                    "apt_cutoff": 410,
                },
            },
        )
        (failed_run / "run_report.html").write_text("failed report", encoding="utf-8")
        (campaign / f"{failed_name}.profile.log").write_text("health failure", encoding="utf-8")

        subprocess.run(
            ["python3", "-m", "app.reporting.summarize_lsb_campaign", str(campaign)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        summary = json.loads((campaign / "lsb_campaign_summary.json").read_text(encoding="utf-8"))
        assert summary["schema"] == "camera-entropy-lsb-campaign-v4"
        assert summary["total_profiles"] == 3
        assert summary["complete"] == 2
        assert summary["failed"] == 1
        rows = {row["profile"]: row for row in summary["profiles"]}
        assert rows["xor-lsb1"]["masked_input_bps"] == 1000.0
        assert rows["xor-lsb1"]["masked_rate_basis"] == "masked_bps_lifetime"
        assert rows["xor-lsb1"]["pixel_symbols_per_second"] == 1000.0
        assert rows["xor-lsb1"]["empirical_symbol_hmin_bps"] == 800.0
        assert rows["xor-lsb2"]["pixel_symbols_per_second"] == 900.0
        assert rows["xor-lsb2"]["empirical_symbol_hmin_bps"] == 1260.0
        assert rows["xor-lsb2"]["masked_throughput_vs_xor_lsb1"] == 1.8
        assert rows["xor-lsb2"]["conditioned_throughput_vs_xor_lsb1"] == 1.5
        assert rows["delta-lsb3"]["failure_reason"].startswith("RCT:full")
        assert rows["delta-lsb3"]["health_sample_domain"] == "symbol"
        assert rows["delta-lsb3"]["report"] == "delta-lsb3/run_report.html"
        assert rows["delta-lsb3"]["profile_log"] == "delta-lsb3.profile.log"
        report = (campaign / "lsb_campaign_report.html").read_text(encoding="utf-8")
        assert "Przepustowość wszystkich etapów" in report
        assert "rate-unit-select" in report
        assert "Parametry każdego testu" in report
        assert "conditioned_output_bps" in report
        assert "RCT:full" in report
        assert "delta-lsb3.profile.log" in report
        assert (campaign / "lsb_campaign_summary.csv").is_file()

    print("LSB campaign summary self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
