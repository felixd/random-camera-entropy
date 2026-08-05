#!/usr/bin/env python3
from __future__ import annotations

from types import SimpleNamespace

from generate_run_report import build_pipeline_label
from profile_catalog import PROFILE_DEFINITIONS
from spatial_sampling import effective_spatial_selection


def main() -> int:
    assert build_pipeline_label({
        "sample_mode": "xor", "lsb_bits": 1, "von_neumann_stage": 0,
        "von_neumann_passes": 0, "conditioner": "sha3-512",
    }) == "Temporal XOR / 1 LSB → mask → symbol RCT/APT → SHA3-512"
    assert "VN ×2" in build_pipeline_label({
        "sample_mode": "xor", "lsb_bits": 1, "von_neumann_stage": 1,
        "von_neumann_passes": 2, "conditioner": "sha3-512",
    })

    modern = effective_spatial_selection(SimpleNamespace(
        spatial_mask_pattern="full", spatial_sampling="checkerboard-even",
    ))
    assert modern["effective"] == "full"
    legacy = effective_spatial_selection(SimpleNamespace(
        spatial_mask_pattern="legacy", spatial_sampling="checkerboard",
    ))
    assert legacy["effective"] == "checkerboard-even"

    preset = PROFILE_DEFINITIONS["production-safe"]["preset"]
    expected = {
        "sample_mode": "xor", "lsb_bits": 1, "pairing_mode": "disjoint",
        "pair_lag_frames": 4, "spatial_mask_pattern": "full",
        "spatial_sampling": "full", "entropy_credit_bits_per_pixel": 0.5,
        "von_neumann_passes": 0, "conditioner": "sha3-512",
        "conditioner_input_bits": 2048, "diagnostic_vn_mib": 0,
        "validation_mib": 0, "web_images": False,
        "mask_snapshot_images": False, "live_byte_diagnostics": False,
    }
    for key, value in expected.items():
        assert preset[key] == value, (key, preset.get(key), value)

    print("production profile/reporting self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
