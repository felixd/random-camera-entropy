#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


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
                        "raw_change_bps_10s": masked_bps * 1.1,
                        "masked_bps_10s": masked_bps,
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

        subprocess.run(
            ["python3", str(ROOT / "summarize_lsb_campaign.py"), str(campaign)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        summary = json.loads((campaign / "lsb_campaign_summary.json").read_text(encoding="utf-8"))
        assert summary["schema"] == "camera-entropy-lsb-campaign-v2"
        assert summary["total_profiles"] == 2
        rows = {row["profile"]: row for row in summary["profiles"]}
        assert rows["xor-lsb1"]["pixel_symbols_per_second"] == 1000.0
        assert rows["xor-lsb1"]["empirical_symbol_hmin_bps"] == 800.0
        assert rows["xor-lsb2"]["pixel_symbols_per_second"] == 900.0
        assert rows["xor-lsb2"]["empirical_symbol_hmin_bps"] == 1260.0
        assert rows["xor-lsb2"]["masked_throughput_vs_xor_lsb1"] == 1.8
        assert rows["xor-lsb2"]["conditioned_throughput_vs_xor_lsb1"] == 1.5
        report = (campaign / "lsb_campaign_report.html").read_text(encoding="utf-8")
        assert "Empirical Hmin bit/s" in report
        assert (campaign / "lsb_campaign_summary.csv").is_file()

    print("LSB campaign summary self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
