#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent


def literal_assignment(path: Path, name: str):
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"missing assignment {name} in {path}")


def main() -> int:
    profiles = literal_assignment(ROOT / "control_server.py", "ALLOWED_PROFILES")
    assert isinstance(profiles, dict)
    assert profiles["lsb-campaign"] == "smoke_lsb_profiles.sh"
    assert profiles["global-all"] == "qualification_global_all_profiles.sh"

    global_text = (ROOT / "qualification_global_all_profiles.sh").read_text(encoding="utf-8")
    global_steps = dict(re.findall(r'^\s*"([^|]+)\|([^"|]+)"\s*$', global_text, re.MULTILINE))
    expected = {name: script for name, script in profiles.items() if name != "global-all"}
    assert global_steps == expected, (global_steps.keys(), expected.keys())
    assert len(global_steps) == 22

    for script in profiles.values():
        path = ROOT / script
        assert path.is_file(), path
        assert path.stat().st_mode & 0o111, f"not executable: {path}"

    lsb_text = (ROOT / "smoke_lsb_profiles.sh").read_text(encoding="utf-8")
    assert "for mode in xor direct delta" in lsb_text
    assert "for bits in {1..8}" in lsb_text
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
