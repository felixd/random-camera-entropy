#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np


def main() -> int:
    script = Path(__file__).with_name("analyze_lsb_bitplanes.py")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = {
            "sample_mode": "direct",
            "lsb_bits": 3,
            "bit_order": "pixel-major-lsb-first",
            "entropy_credit_bits_per_pixel": 0.5,
        }
        (root / "runner_config.json").write_text(json.dumps(config), encoding="utf-8")
        symbols = np.tile(np.arange(8, dtype=np.uint8), 1024)
        bits = ((symbols[:, None] >> np.arange(3, dtype=np.uint8)[None, :]) & 1).astype(np.uint8).reshape(-1)
        (root / "y_temporal_masked_validation.bin").write_bytes(np.packbits(bits, bitorder="big").tobytes())
        subprocess.run(["python3", str(script), str(root), "--max-bytes", "1048576"], check=True, capture_output=True, text=True)
        summary = json.loads((root / "lsb_bitplane_summary.json").read_text())
        assert summary["lsb_bits"] == 3
        assert summary["complete_pixel_symbols"] == symbols.size
        assert abs(summary["symbol_shannon_entropy_bits_per_symbol"] - 3.0) < 1e-12
        assert abs(summary["symbol_min_entropy_bits_per_symbol"] - 3.0) < 1e-12
        assert len(summary["planes"]) == 3
        assert (root / "lsb_bitplane_report.html").is_file()
        assert (root / "lsb_cross_plane_phi.png").is_file()
    print("LSB bit-plane analysis self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
