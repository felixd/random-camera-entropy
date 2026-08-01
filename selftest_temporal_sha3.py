#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import io
import logging
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

try:
    import flask  # noqa: F401
except ModuleNotFoundError:
    module = types.ModuleType("flask")
    module.Flask = object
    module.Response = object
    module.abort = lambda *args, **kwargs: None
    module.render_template_string = lambda *args, **kwargs: ""
    module.send_from_directory = lambda *args, **kwargs: None
    sys.modules["flask"] = module

import camera_entropy_server as server


def main() -> int:
    root = Path(__file__).resolve().parent
    profile = (root / "smoke_temporal_sha3.sh").read_text(encoding="utf-8")
    runner = (root / "run_one.sh").read_text(encoding="utf-8")
    control = (root / "control_server.py").read_text(encoding="utf-8")
    control_html = (root / "templates" / "control.html").read_text(encoding="utf-8")
    worker_source = (root / "camera_entropy_server.py").read_text(encoding="utf-8")

    assert 'SPATIAL_SAMPLING="full"' in profile
    assert "DUAL_WEAVE_COMPARISON=0" in profile
    assert "SPATIAL_COMPARISON=0" in profile
    assert "ENABLE_VON_NEUMANN=0" in profile
    assert "DIAGNOSTIC_VN_BYTES=0" in profile
    assert 'CONDITIONER_INPUT_BITS="${CONDITIONER_INPUT_BITS:-65536}"' in profile
    assert 'WEB_IMAGES="${WEB_IMAGES:-0}"' in profile
    assert 'MASK_SNAPSHOT_IMAGES="${MASK_SNAPSHOT_IMAGES:-0}"' in profile

    assert 'WEB_IMAGES="${WEB_IMAGES:-0}"' in runner
    assert 'MASK_SNAPSHOT_IMAGES="${MASK_SNAPSHOT_IMAGES:-0}"' in runner
    assert '"$mask_snapshot_images_flag" "$vn_stage_flag"' in runner
    assert '"temporal-sha3": "smoke_temporal_sha3.sh"' in control
    assert 'payload.get("web_images", False)' in control
    assert 'payload.get("mask_snapshot_images", False)' in control
    assert '{% for p in profiles %}' in control_html
    assert 'value="{{ p.id }}"' in control_html
    assert 'id="web_images" name="web_images" type="checkbox"' in control_html
    assert 'id="mask_snapshot_images" name="mask_snapshot_images" type="checkbox"' in control_html
    assert "if self.args.mask_snapshot_images:" in worker_source
    assert "if self.args.von_neumann_stage and" in worker_source


    # Drift metrics must remain available when periodic mask PNGs are disabled.
    service = object.__new__(server.Service)
    active = np.array([[True, False], [True, True]], dtype=bool)
    shadow = np.array([[True, True], [True, False]], dtype=bool)
    comparison = server.MaskComparison(
        active_pixels=3, shadow_pixels=3, overlap_pixels=2, union_pixels=4,
        active_retention=2 / 3, shadow_retention=2 / 3, jaccard=0.5,
        disagreement_rate=0.5, updates=7, bad_streak=0, bad=False,
    )
    service.shadow = types.SimpleNamespace(mask=shadow, active_mask=active, comparison=comparison, updates=7)
    service.args = types.SimpleNamespace(mask_snapshot_interval_seconds=1.0, mask_snapshot_images=False, exposure_value=7000)
    service.last_mask_snapshot_monotonic = 0.0
    service.last_mask_snapshot_utc = None
    service.mask_snapshot_sequence = 0
    service.last_mask_snapshot_sha256 = ""
    service.mask_snapshot_dir = Path(tempfile.mkdtemp()) / "mask_snapshots"
    service.mask_drift_history = []
    buffer = io.StringIO()
    fieldnames = [
        "timestamp_utc", "frame_id", "shadow_updates", "sequence", "sha256",
        "image_changed", "base_name", "active_pixels", "shadow_pixels",
        "active_retention", "jaccard", "disagreement_rate", "bad_streak", "result",
    ]
    service.mask_snapshot_writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    service.mask_snapshot_csv = types.SimpleNamespace(flush=lambda: None)
    original_imwrite = server.cv2.imwrite
    server.cv2.imwrite = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("PNG write attempted"))
    try:
        server.Service.archive_mask_snapshot(service, "2026-08-01T11:00:00Z", 123)
    finally:
        server.cv2.imwrite = original_imwrite
    assert len(service.mask_drift_history) == 1
    assert service.mask_drift_history[0]["jaccard"] == 0.5
    assert not service.mask_snapshot_dir.exists()

    with tempfile.TemporaryDirectory() as raw:
        output = Path(raw) / "sha3.bin"
        observer: list[bytes] = []
        writer = server.Sha3ConditionerWriter(
            output,
            True,
            65536,
            128,
            byte_observer=lambda block: observer.append(bytes(block)),
        )
        first = np.tile(np.array([0, 1], dtype=np.uint8), 32768)
        second = np.tile(np.array([1, 0, 0, 1], dtype=np.uint8), 16384)
        writer.write_bits(first)
        writer.write_bits(second)
        writer.close()
        assert len(observer) == 2
        assert output.stat().st_size == 128
        assert observer[0] != observer[1]

        repeated = server.Sha3ConditionerWriter(Path(raw) / "repeat.bin", True, 65536, 128)
        repeated.write_bits(np.concatenate((first, first)))
        repeated.close()
        assert repeated.latched
        assert repeated.repeated_block_failures == 1
        assert repeated.written_bytes == 64

    print("temporal SHA3 profile self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
