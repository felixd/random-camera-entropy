#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import sys
import tempfile
import time
import types
from pathlib import Path

import cv2
import numpy as np

# The build environment may intentionally omit Flask.  The diagnostics classes
# do not need a web server, so provide a tiny import-only shim for this self-test.
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
from unicode_image_text import font_path, unicode_font


def wait_for_heatmap(diagnostics: server.LiveByteDiagnostics, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with diagnostics.lock:
            rendering = diagnostics.heatmap_rendering
            ready = diagnostics.heatmap_bytes is not None
        if ready and not rendering:
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for live heatmap")


def main() -> int:

    dashboard = server.INDEX_HTML
    assert "scattergl" not in dashboard
    assert "type:'scatter'" in dashboard
    assert "cameraEntropyHistogramMode" in dashboard
    assert "Rozdzielone — osobna skala" in dashboard
    assert "Nakładane linie — wspólna skala" in dashboard
    assert "type:'bar'" not in dashboard
    assert "directHistogramPanel" in dashboard
    assert "safePlotlyReact" in dashboard
    assert "Wykres live używa renderera SVG" in dashboard
    assert "saveHistogramMode(histogramMode)" in dashboard
    control_source = Path(server.__file__).with_name("control_server.py").read_text(encoding="utf-8")
    assert "script-src 'self' 'unsafe-inline'" in control_source
    assert "script-src 'self' 'unsafe-inline' 'unsafe-eval'" not in control_source
    # The deployment should expose a Unicode-capable system font.  A custom
    # path can be supplied with CAMERA_ENTROPY_FONT when necessary.
    assert font_path(False) is not None
    assert unicode_font(18).getbbox("Zażółć gęślą jaźń") is not None

    expected = np.array([0, 1, 1, 255, 128, 64, 32, 16], dtype=np.uint8)
    bits = np.unpackbits(expected, bitorder="big")
    accumulator = server.ByteStageAccumulator("stage", "Stage", 0, "main")
    # Deliberately split in the middle of bytes: packing must remain continuous.
    for chunk in (bits[:3], bits[3:13], bits[13:41], bits[41:]):
        accumulator.observe_bits(chunk)
    assert accumulator.total_bytes == expected.size
    assert accumulator.pending_bits.size == 0
    assert accumulator.counts[0] == 1
    assert accumulator.counts[1] == 2
    assert accumulator.counts[255] == 1
    assert accumulator.transitions[0, 1] == 1
    assert accumulator.transitions[1, 1] == 1
    assert accumulator.transitions[1, 255] == 1
    assert accumulator.transitions.sum() == expected.size - 1

    # Byte boundaries between calls must also create a transition.
    second = server.ByteStageAccumulator("bytes", "Bytes", 0, "main")
    second.observe_bytes(bytes([7, 8]))
    second.observe_bytes(bytes([9, 10]))
    assert second.transitions[8, 9] == 1
    assert second.transitions.sum() == 3

    logger = logging.getLogger("live-byte-diagnostics-selftest")
    logger.handlers[:] = [logging.NullHandler()]
    with tempfile.TemporaryDirectory() as raw:
        output = Path(raw)
        diagnostics = server.LiveByteDiagnostics(
            enabled=True,
            output_dir=output,
            heatmap_interval_seconds=1.0,
            heatmap_max_stages=3,
            heatmap_min_bytes=256,
            logger=logger,
        )
        rng = np.random.default_rng(20260801)
        diagnostics.observe_bytes("dual", "Dual", 0, rng.integers(0, 256, 4096, dtype=np.uint8), "dual")
        diagnostics.observe_bytes("main0", "Direct", 0, rng.integers(0, 256, 4096, dtype=np.uint8), "main")
        diagnostics.observe_bytes("main1", "SHA3", 40, rng.integers(0, 256, 4096, dtype=np.uint8), "main")
        snap = diagnostics.snapshot(include_counts=True)
        assert [row["key"] for row in snap["stages"]][:2] == ["main0", "main1"]
        assert len(snap["stages"][0]["counts"]) == 256
        assert abs(sum(snap["stages"][0]["frequencies"]) - 1.0) < 1e-12
        diagnostics.maybe_render_heatmap(time.monotonic(), force=True)
        wait_for_heatmap(diagnostics)
        assert diagnostics.heatmap_path.is_file()
        image = cv2.imread(str(diagnostics.heatmap_path), cv2.IMREAD_COLOR)
        assert image is not None and image.shape[0] > 500 and image.shape[1] >= 1200
        assert diagnostics.snapshot(False)["heatmap"]["sequence"] == 1

        primary_outputs: list[bytes] = []
        primary = server.Sha3ConditionerWriter(
            output / "primary-output.bin", True, 2048, 64,
            byte_observer=lambda value: primary_outputs.append(bytes(value)),
        )
        primary.write_bits(np.zeros(2048, dtype=np.uint8))
        primary.close()
        assert len(primary_outputs) == 1 and len(primary_outputs[0]) == 64
        assert (output / "primary-output.bin").read_bytes() == primary_outputs[0]

        observed_inputs: list[np.ndarray] = []
        observed_outputs: list[bytes] = []
        validation = server.ContinuousBitWriter(output / "dual-input.bin", True, 0)
        dual = server.DualBalancedSha3ConditionerWriter(
            output / "dual-output.bin",
            True,
            2048,
            64,
            validation,
            alignment="stagger-2",
            alignment_delay_groups=2,
            input_bit_observer=lambda value: observed_inputs.append(value.copy()),
            output_byte_observer=lambda value: observed_outputs.append(bytes(value)),
        )
        c0 = np.zeros(1024, dtype=np.uint8)
        c1 = np.ones(1024, dtype=np.uint8)
        dual.write_streams(c0, c1)
        dual.close()
        validation.close()
        assert len(observed_inputs) == 1
        assert np.array_equal(observed_inputs[0][:1024], c0)
        assert np.array_equal(observed_inputs[0][1024:], c1)
        assert len(observed_outputs) == 1 and len(observed_outputs[0]) == 64

        class AlignmentArgs:
            validation_output_bytes = 4096
            conditioner = "sha3-512"
            write_conditioned_output = True
            conditioner_input_bits = 2048
            conditioned_output_bytes = 64

        alignment_dir = output / "alignment"
        alignment_dir.mkdir()
        alignment = server.DualWeaveAlignmentVariant(
            "row-major", "stagger-2", alignment_dir, AlignmentArgs()
        )
        zeros = np.zeros(1024, dtype=np.uint8)
        ones = np.ones(1024, dtype=np.uint8)
        alternating = np.tile(np.array([0, 1], dtype=np.uint8), 512)
        alignment.consume(zeros, alternating)
        alignment.consume(alternating, zeros)
        status = alignment.consume(ones, ones)
        alignment.close()
        assert alignment.conditioner_input_path.read_bytes() == bytes(128) + bytes([0xFF]) * 128
        assert status["discarded_initial_c1_groups"] == 2
        assert status["group_pairs_fed"] == 1
        assert status["pending_tail_c0_groups"] == 2

    print("live byte diagnostics self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
