#!/usr/bin/env python3
from __future__ import annotations
import json
import tempfile
from pathlib import Path
import numpy as np
from app.reporting import analyze_binary_geometry as geometry


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        rng = np.random.default_rng(12345)
        rng.integers(0, 256, 200_000, dtype=np.uint8).tofile(root / "camera_entropy_sha3_512.bin")
        assert geometry.main([str(root), "--max-files", "2", "--max-bytes", "65536"]) == 0
        report = (root / geometry.REPORT_NAME).read_text(encoding="utf-8")
        summary = json.loads((root / geometry.SUMMARY_NAME).read_text(encoding="utf-8"))
        assert "Geometria 2D" in report
        assert "surface" not in report.lower()
        assert "scatter" not in report.lower()
        assert summary["volumetric_plots"] is False
        assert not list(root.glob("*surface*"))
        assert not list(root.glob("*scatter*"))
    print("2D geometry self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
