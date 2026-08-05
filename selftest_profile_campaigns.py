#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent



def main() -> int:
    sys.path.insert(0, str(ROOT))
    from profile_catalog import ALLOWED_PROFILES as profiles

    assert isinstance(profiles, dict)
    assert profiles["lsb-campaign"] == "smoke_lsb_profiles.sh"
    assert profiles["global-all"] == "qualification_global_all_profiles.sh"

    global_text = (ROOT / "qualification_global_all_profiles.sh").read_text(encoding="utf-8")
    global_steps = dict(re.findall(r'^\s*"([^|]+)\|([^"|]+)"\s*$', global_text, re.MULTILINE))
    # The final preproduction profile intentionally consumes an entire dataset and
    # is a separate release gate.  It must not be hidden inside the historical
    # global campaign, which remains a bounded collection of smoke/qualification runs.
    global_exclusions = {"global-all", "final-preproduction", "production-safe"}
    expected = {name: script for name, script in profiles.items() if name not in global_exclusions}
    assert global_steps == expected, (global_steps.keys(), expected.keys())
    assert len(global_steps) == 23
    assert profiles["final-preproduction"] == "qualification_final_preproduction.py"
    assert profiles["production-safe"] == "run_production.sh"

    for script in profiles.values():
        path = ROOT / script
        assert path.is_file(), path
        assert path.stat().st_mode & 0o111, f"not executable: {path}"

    lsb_text = (ROOT / "smoke_lsb_profiles.sh").read_text(encoding="utf-8")
    assert "for mode in xor direct delta" in lsb_text
    assert "for bits in {1..4}" in lsb_text
    assert "profiles+=(\"$name|$mode|$bits|$credit\")" in lsb_text

    control = (ROOT / "control_server.py").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "control.html").read_text(encoding="utf-8")
    for token in ("sample_mode", "lsb_bits", "entropy_credit_bits_per_pixel"):
        assert token in control
        assert token in template

    report_sources = [
        ROOT / "analyze_binary_geometry.py",
        ROOT / "generate_run_report.py",
        ROOT / "analyze_dual_weave.py",
        ROOT / "run_one.sh",
    ]
    forbidden = ("2D/3D", "surface_3d", "scatter_3d", "max-scatter-points")
    for path in report_sources:
        value = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in value, f"{token} remains in {path.name}"

    for script in ("run_one.sh", "smoke_lsb_profiles.sh", "qualification_global_all_profiles.sh"):
        subprocess.run(["bash", "-n", str(ROOT / script)], check=True)

    print("profile/global campaign self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
