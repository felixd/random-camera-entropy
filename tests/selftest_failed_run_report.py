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
        run = Path(temporary) / "delta-lsb3"
        run.mkdir()
        write(
            run / "runner_config.json",
            {
                "sample_mode": "delta",
                "lsb_bits": 3,
                "conditioner_input_bits": 6144,
                "von_neumann_stage": False,
            },
        )
        write(
            run / "run_failed.json",
            {
                "status": "failed",
                "app_version": "test",
                "reason": "RCT:full: RCT: run=41, cutoff=41, symbol=0",
                "state": "HEALTH_FAILED",
                "health": {
                    "latched": True,
                    "rct_failures": 1,
                    "apt_failures": 0,
                    "sample_width_bits": 3,
                    "assessed_min_entropy_bits_per_symbol": 0.5,
                },
                "active_clipping": {"latched": False, "failures": 0},
                "shadow": {"active_retention": 0.99, "jaccard": 0.98},
                "conditioner": {"written_bytes": 512, "compression_ratio": 12.0},
            },
        )
        write(
            run / "runner_summary.json",
            {
                "status": "failed",
                "analyses": {
                    "conditioned": {
                        "total_bytes": 512,
                        "p1": 0.5,
                        "lag1": {"phi": 0.001},
                        "byte_min_entropy_bits_per_byte": 7.9,
                        "byte_chi_square": 255.0,
                        "byte_chi_square_p_value": 0.5,
                    }
                },
            },
        )
        (run / "health_events.csv").write_text("test\n", encoding="utf-8")
        subprocess.run(
            ["python3", "-m", "app.reporting.generate_run_report", str(run)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        report = (run / "run_report.html").read_text(encoding="utf-8")
        assert "FAILED" in report
        assert "Przyczyna zatrzymania" in report
        assert "RCT:full" in report
        assert "3-bit symbols" in report
        assert "run_failed.json" in report

    print("failed run report self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
