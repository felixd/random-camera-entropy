#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Distributed direct-Y temporal entropy source candidate with two masks.

Noise-source path:
    strict uncompressed YUYV -> direct Y bytes -> temporal LSB XOR
    -> frozen ACTIVE mask -> configured spatial sampler (checkerboard by default)
    -> continuous RCT/APT health tests -> parallel diagnostic Von Neumann output
    and a SHA3-512 conditioning candidate.

A second SHADOW mask is continuously updated in the background. It never
selects output samples and never replaces the active mask. It is used only to
measure source drift. In normal production mode persistent drift stops output.
Explicit benchmark-only flags may continue collection while retaining failure
markers so equal-size datasets can be compared; those files are diagnostic,
not production-approved entropy output.

Important:
- A UVC camera exposes data after its internal ISP, not raw sensor ADC samples.
- This implementation is a validation candidate, not a certified TRNG.
- The configured min-entropy claim must come from an external SP 800-90B
  assessment. Statistical PASS results do not establish that claim.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import re
import shutil
import signal
import shlex
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
from flask import Flask, Response, abort, render_template_string, send_from_directory

from app.sources.frame_sources import FrameSource, create_frame_source, redact_url
from app.sources.control_values import control_integer
from app.core.spatial_sampling import (
    align_previous_frame,
    build_pattern_set,
    effective_spatial_selection,
    SpatialSerializer,
    spatial_xor_lsb,
)
# CAMERA_ENTROPY_SPATIAL_V7_7
from app.core.entropy_bitplanes import (
    BIT_ORDER,
    SAMPLE_MODES,
    minimum_conditioner_input_bits,
    sample_label,
    serialize_sample_symbols,
    serialize_samples,
    serialize_symbol_bits,
)
from app.paths import STATIC_ROOT
from app.reporting.unicode_image_text import UnicodeTextCanvas, font_description
from app.core.masking import FrozenPixelCalibrator, MaskComparison, ShadowPixelMonitor
from app.core.entropy_extractors import repeated_von_neumann, von_neumann_split
from app.core.stream_statistics import StreamingBitplaneStatistics

APP_VERSION = "2026.08.06.camera-entropy-distributed.8.0.5"
TARGET_VID = "041e"
TARGET_PID = "4097"
EXPECTED_FOURCC = "YUYV"

FRAME_HEADER = [
    "timestamp_utc", "frame_id", "state", "pair_sequence", "pairing_mode",
    "pair_lag_frames", "pair_buffer_frames", "previous_frame_id",
    "pair_delta_seconds", "pair_buffer_fill", "width", "height", "camera_fps",
    "calibration_pairs", "calibration_target",
    "active_pixels", "active_rate", "shadow_pixels", "shadow_rate",
    "mask_overlap_pixels", "mask_union_pixels", "active_retention",
    "shadow_retention", "mask_jaccard", "mask_disagreement_rate",
    "shadow_updates", "shadow_grace_remaining", "drift_bad_streak",
    "drift_latched", "raw_change_bits", "raw_change_ones", "raw_change_p1",
    "masked_bits", "masked_ones", "masked_p1", "vn_input_bits",
    "vn_output_bits", "vn_efficiency", "frame_interval_s",
    "raw_change_bps", "masked_bps", "vn_output_bps", "vn_output_bps_ema",
    "vn_output_bps_1s", "vn_output_bps_10s", "vn_output_bps_60s",
    "vn_output_bps_lifetime", "packed_bytes_per_second_10s",
    "projected_mib_per_day_10s", "output_offset_bytes", "output_bytes",
    "rct_failures", "apt_failures", "health_latched",
    "active_clip_pixels", "active_clip_rate", "clip_bad_streak", "clip_latched",
    "control_checks", "control_mismatches", "control_latched", "processing_ms",
]

HEALTH_HEADER = [
    "timestamp_utc", "frame_id", "test", "result", "details",
    "sample_domain", "sample_width_bits", "alphabet_size",
    "rct_cutoff", "apt_window", "apt_cutoff",
    "assessed_min_entropy_bits_per_symbol", "assessed_min_entropy", "alpha",
]

MASK_DRIFT_HEADER = [
    "timestamp_utc", "frame_id", "active_pixels", "shadow_pixels",
    "overlap_pixels", "union_pixels", "active_retention", "shadow_retention",
    "jaccard", "disagreement_rate", "shadow_updates", "grace_remaining",
    "bad_streak", "result", "latched", "details",
]

OUTPUT_INDEX_HEADER = [
    "timestamp_utc", "frame_id", "pair_sequence", "pairing_mode",
    "pair_lag_frames", "previous_frame_id", "pair_delta_seconds",
    "offset_bytes", "length_bytes", "valid_bits",
    "source_input_bits", "pending_bits_before", "pending_bits_after",
    "mask_epoch_id", "active_mask_sha256", "state",
]

PIXEL_CORRELATION_HEADER = [
    "timestamp_utc", "frame_id", "sample_index", "stage", "direction",
    "distance", "n00", "n01", "n10", "n11", "pairs", "same_rate",
    "change_rate", "p1_first", "p1_second", "phi", "mutual_information_bits",
]

MASK_SNAPSHOT_HEADER = [
    "timestamp_utc", "frame_id", "shadow_updates", "sequence", "sha256",
    "image_changed", "base_name", "active_pixels", "shadow_pixels",
    "active_retention", "jaccard", "disagreement_rate", "bad_streak", "result",
]

SPATIAL_VARIANT_HEADER = [
    "timestamp_utc", "frame_id", "pair_sequence", "variant", "selected_bits",
    "selected_ones", "selected_p1", "vn_output_bits", "vn_efficiency",
    "bytes_written_this_pair", "total_written_bytes", "target_bytes", "complete",
    "rct_failures", "apt_failures", "health_latched",
]

DUAL_WEAVE_METRICS_HEADER = [
    "timestamp_utc", "group_sequence", "pair_a_sequence", "pair_b_sequence",
    "frame_a_id", "frame_b_id", "order", "active_pixels", "c0_bits", "c1_bits",
    "c0_ones", "c1_ones", "c0_p1", "c1_p1", "c0_vn_bits", "c1_vn_bits",
    "c0_vn_bytes", "c1_vn_bytes", "alignment_status_json", "phase_health_latched",
    "c0_health_latched", "c1_health_latched", "complete",
]

SPATIAL_SAMPLING_CHOICES = (
    "full", "checkerboard", "checkerboard-even", "checkerboard-odd", "grid2x2"
)
SPATIAL_COMPARISON_VARIANTS = (
    "full", "checkerboard-even", "checkerboard-odd", "grid2x2"
)


def canonical_spatial_sampling(name: str) -> str:
    # Backwards-compatible wrapper; the implementation lives in spatial_sampling.py.
    return "checkerboard-even" if name == "checkerboard" else name

def entropy_pipeline_name(args: argparse.Namespace) -> str:
    mode = {"xor": "temporal-xor", "direct": "direct-y", "delta": "temporal-delta"}[args.sample_mode]
    tail = (
        "mask-symbol-health-sha3"
        if not args.von_neumann_stage
        else f"mask-symbol-health-vn{args.von_neumann_passes}-and-sha3"
    )
    return f"{mode}-lsb{args.lsb_bits}-{tail}"


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{time.time_ns() % 1_000_000_000:09d}Z"


def epoch_timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def json_safe(value: Any) -> Any:
    """Convert status data to strict RFC 8259 JSON-compatible values.

    Python's default JSON encoder emits NaN/Infinity tokens. They are accepted by
    some decoders but rejected by browser JSON.parse(), which broke live updates
    during calibration while correlation metrics were not available yet.
    """
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def strict_json_response(payload: Any, status: int = 200) -> Response:
    body = json.dumps(
        json_safe(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return Response(
        body,
        status=status,
        content_type="application/json; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def decode_fourcc(value: float) -> str:
    number = int(value)
    return "".join(chr((number >> (8 * i)) & 0xFF) for i in range(4)).rstrip("\x00 ")


def binomial_tail_ge(n: int, p: float, k: int) -> float:
    """Return P[X >= k], adequate for threshold calculation at n=1024."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    logs = [
        math.lgamma(n + 1)
        - math.lgamma(x + 1)
        - math.lgamma(n - x + 1)
        + x * math.log(p)
        + (n - x) * math.log1p(-p)
        for x in range(k, n + 1)
    ]
    maximum = max(logs)
    return math.exp(maximum) * sum(math.exp(value - maximum) for value in logs)


def compute_health_cutoffs(h_min: float, alpha: float, apt_window: int) -> tuple[int, int]:
    """Derive SP 800-90B RCT/APT thresholds for one source sample.

    ``h_min`` is expressed in bits per *source sample symbol*.  The APT cutoff
    is for the reference symbol selected as the first sample of each window,
    as specified by SP 800-90B section 4.4.2.  Binary sources additionally test
    the complementary value, which the recommendation explicitly permits.
    """
    if not math.isfinite(h_min) or h_min <= 0:
        raise ValueError("assessed min-entropy must be positive and finite")
    if not (0 < alpha < 1):
        raise ValueError("health alpha must be in (0, 1)")
    if apt_window < 2:
        raise ValueError("APT window must be >= 2")
    rct_cutoff = 1 + math.ceil(-math.log2(alpha) / h_min)
    maximum_symbol_probability = min(1.0, 2.0 ** (-h_min))
    apt_cutoff = apt_window
    for cutoff in range(1, apt_window + 1):
        if binomial_tail_ge(apt_window, maximum_symbol_probability, cutoff) <= alpha:
            apt_cutoff = cutoff
            break
    return rct_cutoff, apt_cutoff


class ContinuousHealthTests:
    """RCT and non-overlapping-window APT for discrete uint8 symbols.

    For one-LSB profiles this is the historical binary test.  For k-LSB
    profiles, the tested sample alphabet is ``0..2**k-1``.  Testing the
    pixel-major bit serialization would create deterministic cross-plane runs
    and false RCT failures, because adjacent bits would not be independent
    samples from one binary source.
    """

    def __init__(
        self,
        h_min: float,
        alpha: float,
        apt_window: int,
        *,
        alphabet_size: int = 2,
        sample_width_bits: int = 1,
    ) -> None:
        if not 2 <= int(alphabet_size) <= 256:
            raise ValueError("health alphabet_size must be in 2..256")
        if not 1 <= int(sample_width_bits) <= 4:
            raise ValueError("health sample_width_bits must be in 1..4")
        maximum_entropy = math.log2(int(alphabet_size))
        if h_min > maximum_entropy:
            raise ValueError(
                f"assessed min-entropy {h_min} exceeds alphabet capacity {maximum_entropy}"
            )
        self.h_min = float(h_min)
        self.alpha = float(alpha)
        self.apt_window = int(apt_window)
        self.alphabet_size = int(alphabet_size)
        self.sample_width_bits = int(sample_width_bits)
        self.rct_cutoff, self.apt_cutoff = compute_health_cutoffs(
            self.h_min, self.alpha, self.apt_window
        )
        self.last: Optional[int] = None
        self.run = 0
        self.apt_buffer = np.empty(self.apt_window, dtype=np.uint8)
        self.apt_position = 0
        self.rct_failures = 0
        self.apt_failures = 0
        self.latched = False
        self.last_failure = ""

    def consume(self, samples: np.ndarray) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        if self.latched:
            return events
        values_array = np.asarray(samples, dtype=np.uint8).reshape(-1)
        if values_array.size == 0:
            return events
        if int(values_array.max()) >= self.alphabet_size:
            raise ValueError("health sample outside configured alphabet")

        changes = np.flatnonzero(values_array[1:] != values_array[:-1]) + 1
        boundaries = np.concatenate(([0], changes, [values_array.size]))
        lengths = np.diff(boundaries).astype(np.int64, copy=False)
        run_values = values_array[boundaries[:-1]]

        if self.last is not None and run_values.size and int(run_values[0]) == self.last:
            lengths[0] += self.run

        if lengths.size:
            index = int(np.argmax(lengths))
            if int(lengths[index]) >= self.rct_cutoff:
                self.rct_failures += 1
                self.latched = True
                self.last_failure = (
                    f"RCT: run={int(lengths[index])}, cutoff={self.rct_cutoff}, "
                    f"symbol={int(run_values[index])}"
                )
                events.append(("RCT", self.last_failure))
                return events
            self.last = int(run_values[-1])
            self.run = int(lengths[-1])

        position = 0
        while position < values_array.size and not self.latched:
            take = min(
                self.apt_window - self.apt_position, values_array.size - position
            )
            self.apt_buffer[self.apt_position : self.apt_position + take] = (
                values_array[position : position + take]
            )
            self.apt_position += take
            position += take
            if self.apt_position == self.apt_window:
                reference_symbol = int(self.apt_buffer[0])
                reference_count = int(np.count_nonzero(self.apt_buffer == reference_symbol))
                tested_symbol = reference_symbol
                tested_count = reference_count
                # SP 800-90B permits the binary extension that also checks the
                # complementary value in the same window.  For non-binary
                # alphabets only the first (reference) symbol is counted.
                if self.alphabet_size == 2:
                    complement_count = self.apt_window - reference_count
                    if complement_count > tested_count:
                        tested_symbol = 1 - reference_symbol
                        tested_count = complement_count
                if tested_count >= self.apt_cutoff:
                    self.apt_failures += 1
                    self.latched = True
                    self.last_failure = (
                        f"APT: reference_symbol={reference_symbol}, "
                        f"tested_symbol={tested_symbol}, count={tested_count}, "
                        f"cutoff={self.apt_cutoff}, W={self.apt_window}"
                    )
                    events.append(("APT", self.last_failure))
                    return events
                self.apt_position = 0
        return events

    def status(self) -> dict[str, Any]:
        return {
            "latched": self.latched,
            "last_failure": self.last_failure,
            "rct_failures": self.rct_failures,
            "apt_failures": self.apt_failures,
            "standard": "NIST SP 800-90B section 4.4",
            "sample_domain": "symbol",
            "sample_width_bits": self.sample_width_bits,
            "alphabet_size": self.alphabet_size,
            "apt_mode": (
                "reference-symbol-and-binary-complement"
                if self.alphabet_size == 2
                else "reference-symbol"
            ),
            "assessed_min_entropy_bits_per_symbol": self.h_min,
            "rct_cutoff": self.rct_cutoff,
            "apt_cutoff": self.apt_cutoff,
            "apt_window": self.apt_window,
        }


def parse_positive_int_list(value: str) -> tuple[int, ...]:
    result = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    if not result or any(item <= 0 for item in result):
        raise argparse.ArgumentTypeError("expected a comma-separated list of positive integers")
    return tuple(result)


def correlation_from_counts(counts: np.ndarray) -> dict[str, float | int]:
    n00, n01, n10, n11 = (int(value) for value in counts)
    total = n00 + n01 + n10 + n11
    first_ones = n10 + n11
    second_ones = n01 + n11
    p1_first = first_ones / total if total else math.nan
    p1_second = second_ones / total if total else math.nan
    denominator = math.sqrt(
        (n10 + n11) * (n00 + n01) * (n01 + n11) * (n00 + n10)
    )
    phi = (n11 * n00 - n10 * n01) / denominator if denominator else math.nan
    mutual_information = 0.0
    if total:
        table = ((n00, n01), (n10, n11))
        row = (n00 + n01, n10 + n11)
        col = (n00 + n10, n01 + n11)
        for i in range(2):
            for j in range(2):
                count = table[i][j]
                if count:
                    probability = count / total
                    mutual_information += probability * math.log2(
                        count * total / (row[i] * col[j])
                    )
    return {
        "n00": n00,
        "n01": n01,
        "n10": n10,
        "n11": n11,
        "pairs": total,
        "same_rate": (n00 + n11) / total if total else math.nan,
        "change_rate": (n01 + n10) / total if total else math.nan,
        "p1_first": p1_first,
        "p1_second": p1_second,
        "phi": phi,
        "mutual_information_bits": mutual_information if total else math.nan,
    }


class SpatialCorrelationAccumulator:
    """Accumulate exact binary pair counts for spatial offsets without saving frames."""

    DIRECTIONS = ("horizontal", "vertical", "diagonal")

    def __init__(self, distances: tuple[int, ...]) -> None:
        self.distances = distances
        self.counts: dict[tuple[str, str, int], np.ndarray] = {
            (stage, direction, distance): np.zeros(4, dtype=np.int64)
            for stage in ("raw", "masked")
            for direction in self.DIRECTIONS
            for distance in distances
        }
        self.samples = 0
        self.last_frame_id = 0
        self.last_timestamp = ""

    @staticmethod
    def pair_views(
        bits: np.ndarray,
        mask: Optional[np.ndarray],
        direction: str,
        distance: int,
    ) -> tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
        if direction == "horizontal":
            first, second = bits[:, :-distance], bits[:, distance:]
            valid = None if mask is None else mask[:, :-distance] & mask[:, distance:]
        elif direction == "vertical":
            first, second = bits[:-distance, :], bits[distance:, :]
            valid = None if mask is None else mask[:-distance, :] & mask[distance:, :]
        elif direction == "diagonal":
            first, second = bits[:-distance, :-distance], bits[distance:, distance:]
            valid = None if mask is None else mask[:-distance, :-distance] & mask[distance:, distance:]
        else:
            raise ValueError(f"unsupported direction: {direction}")
        return first, second, valid

    @staticmethod
    def count_pairs(first: np.ndarray, second: np.ndarray, valid: Optional[np.ndarray]) -> np.ndarray:
        if valid is not None:
            if not np.any(valid):
                return np.zeros(4, dtype=np.int64)
            a = first[valid]
            b = second[valid]
        else:
            a = first.reshape(-1)
            b = second.reshape(-1)
        codes = (a.astype(np.uint8, copy=False) << 1) | b.astype(np.uint8, copy=False)
        return np.bincount(codes, minlength=4).astype(np.int64)

    def update(
        self,
        bits: np.ndarray,
        effective_mask: np.ndarray,
        timestamp: str,
        frame_id: int,
        writer: csv.DictWriter,
    ) -> None:
        self.samples += 1
        self.last_frame_id = frame_id
        self.last_timestamp = timestamp
        for stage, mask in (("raw", None), ("masked", effective_mask)):
            for direction in self.DIRECTIONS:
                for distance in self.distances:
                    if distance >= bits.shape[0] and direction != "horizontal":
                        continue
                    if distance >= bits.shape[1] and direction != "vertical":
                        continue
                    first, second, valid = self.pair_views(bits, mask, direction, distance)
                    partial = self.count_pairs(first, second, valid)
                    key = (stage, direction, distance)
                    self.counts[key] += partial
                    metrics = correlation_from_counts(self.counts[key])
                    writer.writerow({
                        "timestamp_utc": timestamp,
                        "frame_id": frame_id,
                        "sample_index": self.samples,
                        "stage": stage,
                        "direction": direction,
                        "distance": distance,
                        **metrics,
                    })

    def rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for (stage, direction, distance), counts in sorted(self.counts.items()):
            rows.append({
                "stage": stage,
                "direction": direction,
                "distance": distance,
                **correlation_from_counts(counts),
            })
        return rows

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "samples": self.samples,
            "last_frame_id": self.last_frame_id,
            "distances": list(self.distances),
            "raw": {},
            "masked": {},
        }
        for stage in ("raw", "masked"):
            stage_rows = [row for row in self.rows() if row["stage"] == stage]
            finite = [row for row in stage_rows if math.isfinite(float(row["phi"]))]
            worst = max(finite, key=lambda row: abs(float(row["phi"])), default=None)
            result[stage] = {
                "max_abs_phi": abs(float(worst["phi"])) if worst else None,
                "worst_direction": worst["direction"] if worst else None,
                "worst_distance": worst["distance"] if worst else None,
                "lag1": {
                    direction: next(
                        (row for row in stage_rows if row["direction"] == direction and row["distance"] == 1),
                        {},
                    )
                    for direction in self.DIRECTIONS
                },
            }
        return result


class ContinuousBitWriter:
    """Pack bits continuously across frame boundaries without padding each frame."""

    def __init__(self, path: Path, enabled: bool, max_bytes: int) -> None:
        self.path = path
        self.enabled = enabled
        self.max_bytes = int(max_bytes)
        if self.max_bytes < 0:
            raise ValueError("max output bytes cannot be negative")
        if self.enabled and self.max_bytes and path.exists() and path.stat().st_size:
            raise RuntimeError(
                f"Refusing limited run with non-empty output file: {path}"
            )
        self.file = path.open("ab", buffering=0) if enabled else None
        self.pending = np.empty(0, dtype=np.uint8)
        self.written_bytes = self.file.tell() if self.file is not None else 0
        self.accepted_bits = 0
        self.completed = bool(self.max_bytes and self.written_bytes >= self.max_bytes)

    def close(self) -> None:
        if self.file is not None:
            self.file.flush()
            self.file.close()
            self.file = None

    def write_bits(self, bits: np.ndarray) -> tuple[int, int, int, bool]:
        """Return accepted input bits, bytes written, pending bits, completed."""
        if bits.size == 0 or self.completed:
            return 0, 0, int(self.pending.size), self.completed

        accepted = bits.astype(np.uint8, copy=False)
        if self.max_bytes:
            remaining_output_bits = (self.max_bytes - self.written_bytes) * 8 - int(self.pending.size)
            if remaining_output_bits <= 0:
                self.completed = True
                return 0, 0, int(self.pending.size), True
            if accepted.size > remaining_output_bits:
                accepted = accepted[:remaining_output_bits]

        accepted_count = int(accepted.size)
        self.accepted_bits += accepted_count
        combined = accepted if self.pending.size == 0 else np.concatenate((self.pending, accepted))
        full_bit_count = (combined.size // 8) * 8
        bytes_written = 0
        if full_bit_count:
            packed = np.packbits(combined[:full_bit_count], bitorder="big").tobytes()
            if self.max_bytes:
                remaining = self.max_bytes - self.written_bytes
                packed = packed[:remaining]
            if self.file is not None and packed:
                self.file.write(packed)
            bytes_written = len(packed)
            self.written_bytes += bytes_written
        self.pending = combined[full_bit_count:].copy()

        if self.max_bytes and self.written_bytes >= self.max_bytes:
            self.completed = True
            # At an exact byte target no pending bits should remain.
            self.pending = np.empty(0, dtype=np.uint8)
        return accepted_count, bytes_written, int(self.pending.size), self.completed


class ByteStageAccumulator:
    """Incremental byte histogram and adjacent-byte transition accumulator.

    Bit-oriented pipeline stages are packed continuously across frame/group
    boundaries, exactly like the binary writers, so the live diagnostics do not
    introduce per-frame padding artefacts.
    """

    def __init__(self, key: str, label: str, rank: int, group: str) -> None:
        self.key = key
        self.label = label
        self.rank = int(rank)
        self.group = group
        self.counts = np.zeros(256, dtype=np.uint64)
        self.transitions = np.zeros((256, 256), dtype=np.uint64)
        self.pending_bits = np.empty(0, dtype=np.uint8)
        self.previous_byte: Optional[int] = None
        self.total_bits_received = 0
        self.total_bytes = 0
        self.transition_pairs = 0
        self.updated_utc: Optional[str] = None

    def observe_bits(self, bits: np.ndarray) -> None:
        if bits.size == 0:
            return
        incoming = bits.astype(np.uint8, copy=False).reshape(-1)
        self.total_bits_received += int(incoming.size)
        combined = incoming if self.pending_bits.size == 0 else np.concatenate((self.pending_bits, incoming))
        full_bits = (combined.size // 8) * 8
        if full_bits:
            packed = np.packbits(combined[:full_bits], bitorder="big")
            self.observe_bytes(packed, count_received_bits=False)
        self.pending_bits = combined[full_bits:].copy()

    def observe_bytes(
        self, values: bytes | bytearray | memoryview | np.ndarray, *, count_received_bits: bool = True
    ) -> None:
        if isinstance(values, np.ndarray):
            data = values.astype(np.uint8, copy=False).reshape(-1)
        else:
            data = np.frombuffer(values, dtype=np.uint8)
        if data.size == 0:
            return
        if count_received_bits:
            self.total_bits_received += int(data.size) * 8
        self.counts += np.bincount(data, minlength=256).astype(np.uint64)
        if self.previous_byte is not None:
            self.transitions[self.previous_byte, int(data[0])] += 1
            self.transition_pairs += 1
        if data.size > 1:
            indices = data[:-1].astype(np.uint32) * 256 + data[1:].astype(np.uint32)
            self.transitions.reshape(-1)[:] += np.bincount(indices, minlength=256 * 256).astype(np.uint64)
            self.transition_pairs += int(data.size - 1)
        self.previous_byte = int(data[-1])
        self.total_bytes += int(data.size)
        self.updated_utc = utc_timestamp()

    def snapshot(self, include_counts: bool = True) -> dict[str, Any]:
        total = int(self.total_bytes)
        if total:
            probabilities = self.counts.astype(np.float64) / float(total)
            nonzero = probabilities[probabilities > 0.0]
            entropy = float(-np.sum(nonzero * np.log2(nonzero)))
            maximum = int(self.counts.max(initial=0))
            min_entropy = float(-math.log2(maximum / float(total))) if maximum else 0.0
            expected = total / 256.0
            chi_square = float(np.sum((self.counts.astype(np.float64) - expected) ** 2 / expected))
            mean = float(np.dot(np.arange(256, dtype=np.float64), self.counts.astype(np.float64)) / total)
        else:
            probabilities = np.zeros(256, dtype=np.float64)
            entropy = min_entropy = chi_square = mean = 0.0
        result: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "rank": self.rank,
            "group": self.group,
            "total_bits_received": int(self.total_bits_received),
            "pending_bits": int(self.pending_bits.size),
            "total_bytes": total,
            "transition_pairs": int(self.transition_pairs),
            "byte_entropy_bits": entropy,
            "byte_min_entropy_bits": min_entropy,
            "byte_chi_square": chi_square,
            "byte_mean": mean,
            "uniform_frequency": 1.0 / 256.0,
            "updated_utc": self.updated_utc,
        }
        if include_counts:
            result["counts"] = self.counts.astype(int).tolist()
            result["frequencies"] = probabilities.tolist()
        return result


class LiveByteDiagnostics:
    """Thread-safe live byte distributions and periodic heatmap snapshots."""

    def __init__(
        self,
        enabled: bool,
        output_dir: Path,
        heatmap_interval_seconds: float,
        heatmap_max_stages: int,
        heatmap_min_bytes: int,
        logger: logging.Logger,
    ) -> None:
        self.enabled = bool(enabled)
        self.output_dir = output_dir
        self.heatmap_interval_seconds = float(heatmap_interval_seconds)
        self.heatmap_max_stages = int(heatmap_max_stages)
        self.heatmap_min_bytes = int(heatmap_min_bytes)
        self.logger = logger
        self.lock = threading.RLock()
        self.stages: dict[str, ByteStageAccumulator] = {}
        self.heatmap_path = output_dir / "live_byte_heatmaps.png"
        self.heatmap_bytes: Optional[bytes] = None
        self.heatmap_sequence = 0
        self.heatmap_last_utc: Optional[str] = None
        self.heatmap_last_monotonic = 0.0
        self.heatmap_rendering = False

    def register(self, key: str, label: str, rank: int, group: str = "main") -> ByteStageAccumulator:
        with self.lock:
            stage = self.stages.get(key)
            if stage is None:
                stage = ByteStageAccumulator(key, label, rank, group)
                self.stages[key] = stage
            return stage

    def observe_bits(self, key: str, label: str, rank: int, bits: np.ndarray, group: str = "main") -> None:
        if not self.enabled or bits.size == 0:
            return
        with self.lock:
            self.register(key, label, rank, group).observe_bits(bits)

    def observe_bytes(
        self,
        key: str,
        label: str,
        rank: int,
        values: bytes | bytearray | memoryview | np.ndarray,
        group: str = "main",
    ) -> None:
        if not self.enabled:
            return
        with self.lock:
            self.register(key, label, rank, group).observe_bytes(values)

    def snapshot(self, include_counts: bool = True) -> dict[str, Any]:
        with self.lock:
            rows = [stage.snapshot(include_counts=include_counts) for stage in self.stages.values()]
            rows.sort(
                key=lambda row: (
                    0 if str(row["group"]) == "main" else 1,
                    str(row["group"]),
                    int(row["rank"]),
                    str(row["label"]),
                )
            )
            return {
                "enabled": self.enabled,
                "stages": rows,
                "heatmap": {
                    "file": self.heatmap_path.name if self.heatmap_bytes is not None else None,
                    "sequence": self.heatmap_sequence,
                    "last_generated_utc": self.heatmap_last_utc,
                    "interval_seconds": self.heatmap_interval_seconds,
                    "max_stages": self.heatmap_max_stages,
                    "minimum_bytes_per_stage": self.heatmap_min_bytes,
                    "normalization": "pairs_per_million",
                    "transform": "log10(1+pairs_per_million)",
                    "rendering": self.heatmap_rendering,
                    "font": font_description(),
                },
            }

    def image(self) -> Optional[bytes]:
        with self.lock:
            return self.heatmap_bytes

    def maybe_render_heatmap(self, now_monotonic: float, force: bool = False) -> None:
        if not self.enabled or self.heatmap_interval_seconds <= 0.0:
            return
        with self.lock:
            if self.heatmap_rendering:
                return
            if not force and now_monotonic - self.heatmap_last_monotonic < self.heatmap_interval_seconds:
                return
            candidates = [
                stage for stage in sorted(
                    self.stages.values(),
                    key=lambda item: (
                        0 if item.group == "main" else 1, item.group, item.rank, item.label
                    ),
                )
                if stage.total_bytes >= self.heatmap_min_bytes and stage.transition_pairs > 0
            ][: self.heatmap_max_stages]
            if not candidates:
                return
            snapshots = [
                {
                    "label": stage.label,
                    "group": stage.group,
                    "total_bytes": stage.total_bytes,
                    "transition_pairs": stage.transition_pairs,
                    "transitions": stage.transitions.copy(),
                }
                for stage in candidates
            ]
            self.heatmap_rendering = True
            self.heatmap_last_monotonic = now_monotonic
        threading.Thread(
            target=self._render_heatmap_worker,
            args=(snapshots,),
            name="live-byte-heatmap",
            daemon=True,
        ).start()

    def _render_heatmap_worker(self, snapshots: list[dict[str, Any]]) -> None:
        try:
            image = self._render_heatmap_overview(snapshots)
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                raise RuntimeError("cv2.imencode failed")
            payload = encoded.tobytes()
            temporary = self.heatmap_path.with_suffix(".png.tmp")
            temporary.write_bytes(payload)
            os.replace(temporary, self.heatmap_path)
            with self.lock:
                self.heatmap_bytes = payload
                self.heatmap_sequence += 1
                self.heatmap_last_utc = utc_timestamp()
        except Exception:
            self.logger.exception("cannot render live byte heatmap")
        finally:
            with self.lock:
                self.heatmap_rendering = False

    @staticmethod
    def _render_heatmap_overview(snapshots: list[dict[str, Any]]) -> np.ndarray:
        columns = 2
        panel_width = 620
        panel_height = 560
        rows = int(math.ceil(len(snapshots) / columns))
        canvas = np.full((78 + rows * panel_height, columns * panel_width, 3), 246, dtype=np.uint8)
        transformed = [
            np.log10(
                1.0
                + item["transitions"].astype(np.float64)
                * (1_000_000.0 / max(1, int(item["transition_pairs"])))
            )
            for item in snapshots
        ]
        common_max = max(float(values.max(initial=0.0)) for values in transformed) or 1.0

        # One shared legend makes cross-stage color comparisons explicit.
        legend_x0, legend_y0, legend_w, legend_h = canvas.shape[1] - 310, 17, 270, 18
        legend_values = np.tile(np.arange(256, dtype=np.uint8), (legend_h, 1))
        legend_values = cv2.resize(legend_values, (legend_w, legend_h), interpolation=cv2.INTER_LINEAR)
        canvas[legend_y0:legend_y0 + legend_h, legend_x0:legend_x0 + legend_w] = cv2.applyColorMap(legend_values, cv2.COLORMAP_TURBO)
        cv2.rectangle(canvas, (legend_x0 - 1, legend_y0 - 1), (legend_x0 + legend_w, legend_y0 + legend_h), (35, 35, 35), 1)

        panel_text: list[dict[str, Any]] = []
        for index, (item, values) in enumerate(zip(snapshots, transformed)):
            row, col = divmod(index, columns)
            ox = col * panel_width
            oy = 78 + row * panel_height
            heat = np.flipud(values.T)
            normalized = np.clip(heat / common_max * 255.0, 0, 255).astype(np.uint8)
            colored = cv2.applyColorMap(normalized, cv2.COLORMAP_TURBO)
            size = 450
            colored = cv2.resize(colored, (size, size), interpolation=cv2.INTER_NEAREST)
            x0, y0 = ox + 105, oy + 62
            canvas[y0:y0 + size, x0:x0 + size] = colored
            cv2.rectangle(canvas, (x0 - 1, y0 - 1), (x0 + size, y0 + size), (35, 35, 35), 1)
            title = str(item["label"])
            if len(title) > 58:
                title = title[:55] + "..."
            panel_text.append({"ox": ox, "oy": oy, "x0": x0, "y0": y0, "size": size, "title": title, "bytes": int(item["total_bytes"])})

        # Draw all labels in one Pillow pass.  OpenCV Hershey fonts cannot
        # represent Polish characters such as ą, ć, ę, ł, ń, ó, ś, ź and ż.
        text = UnicodeTextCanvas(canvas)
        text.text((24, 12), "Heatmapy przejść sąsiednich bajtów — podgląd na żywo", size=27, bold=True)
        text.text(
            (24, 48),
            "X = bieżący bajt Bₙ, Y = następny bajt Bₙ₊₁; wspólna skala znormalizowanej gęstości",
            size=14,
            color_bgr=(70, 70, 70),
        )
        text.text((legend_x0, legend_y0 + legend_h + 5), "0", size=11, color_bgr=(55, 55, 55))
        text.text((legend_x0 + legend_w, legend_y0 + legend_h + 5), f"{common_max:.3f}", size=11, color_bgr=(55, 55, 55), anchor="rt")
        text.text((legend_x0 + legend_w / 2, legend_y0 + legend_h + 5), "log₁₀(1 + pary na milion)", size=11, color_bgr=(55, 55, 55), anchor="mt")
        for item in panel_text:
            ox, oy, x0, y0, size = item["ox"], item["oy"], item["x0"], item["y0"], item["size"]
            text.text((ox + 18, oy + 8), item["title"], size=19, bold=True)
            text.text((ox + 18, oy + 37), f'{item["bytes"]:,} bajtów', size=13, color_bgr=(75, 75, 75))
            text.text((x0 + size / 2, y0 + size + 30), "Bieżący bajt Bₙ", size=15, anchor="mt")
            text.rotated_text((ox + 55, y0 + size / 2), "Następny bajt Bₙ₊₁", size=15)
            for value, px in ((0, x0), (64, x0 + 113), (128, x0 + 226), (192, x0 + 339), (255, x0 + 432)):
                text.text((px, y0 + size + 5), value, size=11, color_bgr=(50, 50, 50), anchor="mt")
            for value, py in ((255, y0), (192, y0 + 113), (128, y0 + 226), (64, y0 + 339), (0, y0 + 444)):
                text.text((x0 - 18, py), value, size=11, color_bgr=(50, 50, 50), anchor="rm")
        return text.finish()



class Sha3ConditionerWriter:
    """Continuously condition binary samples with SHA3-512.

    The configured input block is hashed independently and produces one 512-bit
    digest. Health tests are performed before this component. A repeated
    consecutive digest latches a fail-closed diagnostic because its probability
    under normal operation is negligible.
    """

    EMPTY_SHA3_512 = (
        "a69f73cca23a9ac5c8b567dc185a756e97c982164fe25859e0d1dcc1475c80a6"
        "15b2123af1f5f94c11e3e9402c3ac558f500199d95b6d3e301758586281dcd26"
    )

    def __init__(
        self, path: Path, enabled: bool, input_bits: int, max_bytes: int,
        *, byte_observer: Optional[Callable[[bytes], None]] = None,
    ) -> None:
        if input_bits < 512 or input_bits % 8:
            raise ValueError("conditioner input bits must be a multiple of 8 and >= 512")
        if max_bytes < 0:
            raise ValueError("conditioned output bytes cannot be negative")
        if hashlib.sha3_512(b"").hexdigest() != self.EMPTY_SHA3_512:
            raise RuntimeError("SHA3-512 known-answer self-test failed")
        self.path = path
        self.enabled = bool(enabled)
        self.byte_observer = byte_observer
        self.input_bits = int(input_bits)
        self.input_bytes = self.input_bits // 8
        self.output_bits_per_block = 512
        self.output_bytes_per_block = 64
        self.max_bytes = int(max_bytes)
        if self.enabled and self.max_bytes and path.exists() and path.stat().st_size:
            raise RuntimeError(f"Refusing limited run with non-empty conditioned file: {path}")
        self.file = path.open("ab", buffering=0) if self.enabled else None
        self.written_bytes = self.file.tell() if self.file is not None else 0
        self.pending = np.empty(0, dtype=np.uint8)
        self.input_bits_consumed = 0
        self.blocks = 0
        self.previous_digest: Optional[bytes] = None
        self.repeated_block_failures = 0
        self.latched = False
        self.last_failure = ""
        self.completed = bool(self.max_bytes and self.written_bytes >= self.max_bytes)
        self.production_started_monotonic: Optional[float] = None
        self.conditioner_completed_monotonic: Optional[float] = None
        self.production_started_utc: Optional[str] = None
        self.conditioner_completed_utc: Optional[str] = None

    def close(self) -> None:
        if self.file is not None:
            self.file.flush()
            self.file.close()
            self.file = None

    def _mark_started(self) -> None:
        if self.production_started_monotonic is None:
            self.production_started_monotonic = time.monotonic()
            self.production_started_utc = utc_timestamp()

    def _mark_completed(self) -> None:
        if self.conditioner_completed_monotonic is None:
            self.conditioner_completed_monotonic = time.monotonic()
            self.conditioner_completed_utc = utc_timestamp()

    def write_bits(self, bits: np.ndarray) -> dict[str, Any]:
        if bits.size == 0 or self.completed or self.latched:
            return self.status()
        incoming = bits.astype(np.uint8, copy=False)
        self._mark_started()
        self.pending = incoming.copy() if self.pending.size == 0 else np.concatenate((self.pending, incoming))
        while self.pending.size >= self.input_bits and not self.completed and not self.latched:
            block_bits = self.pending[: self.input_bits]
            self.pending = self.pending[self.input_bits :].copy()
            block = np.packbits(block_bits, bitorder="big").tobytes()
            digest = hashlib.sha3_512(block).digest()
            if self.previous_digest is not None and digest == self.previous_digest:
                self.repeated_block_failures += 1
                self.latched = True
                self.last_failure = f"repeated SHA3-512 output block at block {self.blocks + 1}"
                break
            self.previous_digest = digest
            self.blocks += 1
            self.input_bits_consumed += self.input_bits
            if self.max_bytes:
                remaining = self.max_bytes - self.written_bytes
                digest = digest[:remaining]
            if self.file is not None and digest:
                self.file.write(digest)
            if digest and self.byte_observer is not None:
                self.byte_observer(digest)
            self.written_bytes += len(digest)
            if self.max_bytes and self.written_bytes >= self.max_bytes:
                self.completed = True
                self._mark_completed()
        return self.status()

    def status(self) -> dict[str, Any]:
        endpoint = self.conditioner_completed_monotonic or time.monotonic()
        elapsed = (
            max(0.0, endpoint - self.production_started_monotonic)
            if self.production_started_monotonic is not None else 0.0
        )
        return {
            "enabled": self.enabled,
            "algorithm": "SHA3-512",
            "input_bits_per_block": self.input_bits,
            "output_bits_per_block": self.output_bits_per_block,
            "compression_ratio": self.input_bits / self.output_bits_per_block,
            "blocks": self.blocks,
            "input_bits_consumed": self.input_bits_consumed,
            "pending_input_bits": int(self.pending.size),
            "written_bytes": self.written_bytes,
            "target_bytes": self.max_bytes,
            "complete": self.completed,
            "latched": self.latched,
            "repeated_block_failures": self.repeated_block_failures,
            "last_failure": self.last_failure,
            "production_started_monotonic": self.production_started_monotonic,
            "conditioner_completed_monotonic": self.conditioner_completed_monotonic,
            "production_started_utc": self.production_started_utc,
            "conditioner_completed_utc": self.conditioner_completed_utc,
            "time_to_target_seconds": elapsed if self.completed and self.production_started_monotonic is not None else None,
            "output_bps_until_complete": self.written_bytes * 8.0 / elapsed if elapsed > 0 else 0.0,
        }


class DualBalancedSha3ConditionerWriter:
    """SHA3-512 conditioner consuming equal bit counts from C0 and C1."""

    def __init__(
        self,
        path: Path,
        enabled: bool,
        input_bits: int,
        max_bytes: int,
        input_validation_writer: ContinuousBitWriter,
        *,
        alignment: str,
        alignment_delay_groups: int,
        input_bit_observer: Optional[Callable[[np.ndarray], None]] = None,
        output_byte_observer: Optional[Callable[[bytes], None]] = None,
    ) -> None:
        if input_bits < 512 or input_bits % 16:
            raise ValueError("dual weave conditioner input bits must be divisible by 16 and >= 512")
        if max_bytes < 0:
            raise ValueError("conditioned output bytes cannot be negative")
        if hashlib.sha3_512(b"").hexdigest() != Sha3ConditionerWriter.EMPTY_SHA3_512:
            raise RuntimeError("SHA3-512 known-answer self-test failed")
        self.path = path
        self.enabled = bool(enabled)
        self.input_bits = int(input_bits)
        self.half_bits = self.input_bits // 2
        self.max_bytes = int(max_bytes)
        self.alignment = alignment
        self.alignment_delay_groups = int(alignment_delay_groups)
        self.domain = f"camera-entropy-dual-weave-v2:{alignment}".encode("ascii") + b"\x00"
        if self.enabled and self.max_bytes and path.exists() and path.stat().st_size:
            raise RuntimeError(f"Refusing limited run with non-empty dual conditioned file: {path}")
        self.file = path.open("ab", buffering=0) if self.enabled else None
        self.written_bytes = self.file.tell() if self.file is not None else 0
        self.pending_c0 = np.empty(0, dtype=np.uint8)
        self.pending_c1 = np.empty(0, dtype=np.uint8)
        self.input_validation_writer = input_validation_writer
        self.input_bit_observer = input_bit_observer
        self.output_byte_observer = output_byte_observer
        self.input_bits_consumed = 0
        self.c0_bits_consumed = 0
        self.c1_bits_consumed = 0
        self.blocks = 0
        self.previous_digest: Optional[bytes] = None
        self.repeated_block_failures = 0
        self.latched = False
        self.last_failure = ""
        self.completed = bool(self.max_bytes and self.written_bytes >= self.max_bytes)
        self.production_started_monotonic: Optional[float] = None
        self.conditioner_completed_monotonic: Optional[float] = None
        self.production_started_utc: Optional[str] = None
        self.conditioner_completed_utc: Optional[str] = None

    def close(self) -> None:
        if self.file is not None:
            self.file.flush()
            self.file.close()
            self.file = None

    def _mark_started(self) -> None:
        if self.production_started_monotonic is None:
            self.production_started_monotonic = time.monotonic()
            self.production_started_utc = utc_timestamp()

    def _mark_completed(self) -> None:
        if self.conditioner_completed_monotonic is None:
            self.conditioner_completed_monotonic = time.monotonic()
            self.conditioner_completed_utc = utc_timestamp()

    def mark_input_started(self) -> None:
        """Start per-conditioner timing when its first source group becomes available."""
        self._mark_started()

    def write_streams(self, c0: np.ndarray, c1: np.ndarray) -> dict[str, Any]:
        if self.completed or self.latched:
            return self.status()
        if c0.size:
            incoming = c0.astype(np.uint8, copy=False)
            self.pending_c0 = incoming.copy() if self.pending_c0.size == 0 else np.concatenate((self.pending_c0, incoming))
        if c1.size:
            incoming = c1.astype(np.uint8, copy=False)
            self.pending_c1 = incoming.copy() if self.pending_c1.size == 0 else np.concatenate((self.pending_c1, incoming))
        while (
            self.pending_c0.size >= self.half_bits
            and self.pending_c1.size >= self.half_bits
            and not self.completed
            and not self.latched
        ):
            c0_block = self.pending_c0[: self.half_bits]
            c1_block = self.pending_c1[: self.half_bits]
            self.pending_c0 = self.pending_c0[self.half_bits :].copy()
            self.pending_c1 = self.pending_c1[self.half_bits :].copy()
            entropy_bits = np.concatenate((c0_block, c1_block))
            self.input_validation_writer.write_bits(entropy_bits)
            if self.input_bit_observer is not None:
                self.input_bit_observer(entropy_bits)
            packed = np.packbits(entropy_bits, bitorder="big").tobytes()
            counter = (self.blocks + 1).to_bytes(8, "big")
            digest = hashlib.sha3_512(self.domain + counter + packed).digest()
            if self.previous_digest is not None and digest == self.previous_digest:
                self.repeated_block_failures += 1
                self.latched = True
                self.last_failure = f"repeated dual-weave SHA3-512 output block at block {self.blocks + 1}"
                break
            self.previous_digest = digest
            self.blocks += 1
            self.input_bits_consumed += self.input_bits
            self.c0_bits_consumed += self.half_bits
            self.c1_bits_consumed += self.half_bits
            if self.max_bytes:
                digest = digest[: self.max_bytes - self.written_bytes]
            if self.file is not None and digest:
                self.file.write(digest)
            if digest and self.output_byte_observer is not None:
                self.output_byte_observer(digest)
            self.written_bytes += len(digest)
            if self.max_bytes and self.written_bytes >= self.max_bytes:
                self.completed = True
                self._mark_completed()
        return self.status()

    def status(self) -> dict[str, Any]:
        endpoint = self.conditioner_completed_monotonic or time.monotonic()
        elapsed = (
            max(0.0, endpoint - self.production_started_monotonic)
            if self.production_started_monotonic is not None else 0.0
        )
        return {
            "enabled": self.enabled,
            "algorithm": "SHA3-512",
            "mode": "balanced-dual-input",
            "alignment": self.alignment,
            "alignment_delay_groups": self.alignment_delay_groups,
            "domain_separator": self.domain.rstrip(b"\x00").decode("ascii"),
            "input_bits_per_block": self.input_bits,
            "input_bits_per_stream": self.half_bits,
            "output_bits_per_block": 512,
            "compression_ratio": self.input_bits / 512,
            "blocks": self.blocks,
            "input_bits_consumed": self.input_bits_consumed,
            "c0_bits_consumed": self.c0_bits_consumed,
            "c1_bits_consumed": self.c1_bits_consumed,
            "pending_c0_bits": int(self.pending_c0.size),
            "pending_c1_bits": int(self.pending_c1.size),
            "written_bytes": self.written_bytes,
            "target_bytes": self.max_bytes,
            "complete": self.completed,
            "latched": self.latched,
            "repeated_block_failures": self.repeated_block_failures,
            "last_failure": self.last_failure,
            "production_started_monotonic": self.production_started_monotonic,
            "conditioner_completed_monotonic": self.conditioner_completed_monotonic,
            "production_started_utc": self.production_started_utc,
            "conditioner_completed_utc": self.conditioner_completed_utc,
            "time_to_target_seconds": elapsed if self.completed and self.production_started_monotonic is not None else None,
            "output_bps_until_complete": self.written_bytes * 8.0 / elapsed if elapsed > 0 else 0.0,
        }


class DualWeaveAlignmentVariant:
    ALIGNMENT_DELAYS = {"same-group": 0, "stagger-1": 1, "stagger-2": 2}

    def __init__(
        self, order_name: str, alignment: str, output_dir: Path, args: argparse.Namespace,
        diagnostics: Optional[LiveByteDiagnostics] = None,
    ) -> None:
        if alignment not in self.ALIGNMENT_DELAYS:
            raise ValueError(f"unsupported dual-weave alignment: {alignment}")
        self.order_name = order_name
        self.alignment = alignment
        self.delay_groups = self.ALIGNMENT_DELAYS[alignment]
        order_token = order_name.replace("-", "_")
        alignment_token = alignment.replace("-", "_")
        validation_enabled = args.validation_output_bytes > 0
        self.conditioner_input_path = output_dir / f"dual_weave_{order_token}_{alignment_token}_conditioner_input_validation.bin"
        self.conditioned_path = output_dir / f"dual_weave_{order_token}_{alignment_token}_sha3_512.bin"
        self.conditioner_input_writer = ContinuousBitWriter(
            self.conditioner_input_path, validation_enabled, args.validation_output_bytes
        )
        input_stage_key = f"dual_{order_token}_{alignment_token}_conditioner_input"
        output_stage_key = f"dual_{order_token}_{alignment_token}_sha3"
        self.conditioner = DualBalancedSha3ConditionerWriter(
            self.conditioned_path,
            args.conditioner == "sha3-512" and args.write_conditioned_output,
            args.conditioner_input_bits,
            args.conditioned_output_bytes,
            self.conditioner_input_writer,
            alignment=alignment,
            alignment_delay_groups=self.delay_groups,
            input_bit_observer=(
                (lambda bits, key=input_stage_key, label=f"Dual {order_name} {alignment} — SHA3 input":
                    diagnostics.observe_bits(key, label, 40 + self.delay_groups * 2, bits, "dual"))
                if diagnostics is not None else None
            ),
            output_byte_observer=(
                (lambda data, key=output_stage_key, label=f"Dual {order_name} {alignment} — SHA3-512":
                    diagnostics.observe_bytes(key, label, 41 + self.delay_groups * 2, data, "dual"))
                if diagnostics is not None else None
            ),
        )
        self.pending_c0_groups: deque[np.ndarray] = deque()
        self.groups_seen = 0
        self.group_pairs_fed = 0
        self.discarded_initial_c1_groups = 0

    def consume(self, c0: np.ndarray, c1: np.ndarray) -> dict[str, Any]:
        self.groups_seen += 1
        if self.conditioner.completed or self.conditioner.latched:
            return self.status()
        self.conditioner.mark_input_started()
        if self.delay_groups == 0:
            feed_c0 = c0
        else:
            self.pending_c0_groups.append(c0.copy())
            if len(self.pending_c0_groups) <= self.delay_groups:
                self.discarded_initial_c1_groups += 1
                return self.status()
            feed_c0 = self.pending_c0_groups.popleft()
        self.group_pairs_fed += 1
        self.conditioner.write_streams(feed_c0, c1)
        return self.status()

    def close(self) -> None:
        self.conditioner_input_writer.close()
        self.conditioner.close()

    def status(self) -> dict[str, Any]:
        return {
            "alignment": self.alignment,
            "delay_groups": self.delay_groups,
            "groups_seen": self.groups_seen,
            "group_pairs_fed": self.group_pairs_fed,
            "discarded_initial_c1_groups": self.discarded_initial_c1_groups,
            "pending_tail_c0_groups": len(self.pending_c0_groups),
            "conditioner_input_file": self.conditioner_input_path.name,
            "conditioned_file": self.conditioned_path.name,
            "validation_input_bytes": self.conditioner_input_writer.written_bytes,
            "conditioner": self.conditioner.status(),
            "complete": self.conditioner.completed,
            "latched": self.conditioner.latched,
        }

    def files(self) -> dict[str, str]:
        order_token = self.order_name.replace("-", "_")
        alignment_token = self.alignment.replace("-", "_")
        return {
            f"dual_weave_{order_token}_{alignment_token}_conditioner_input": self.conditioner_input_path.name,
            f"dual_weave_{order_token}_{alignment_token}_conditioned": self.conditioned_path.name,
        }


class DualWeaveOrderVariant:
    """One spatial ordering of complementary C0/C1 temporal composites."""

    def __init__(
        self,
        name: str,
        alignments: tuple[str, ...],
        output_dir: Path,
        args: argparse.Namespace,
        diagnostics: Optional[LiveByteDiagnostics] = None,
    ) -> None:
        if name not in {"row-major", "serpentine"}:
            raise ValueError(f"unsupported dual-weave order: {name}")
        self.name = name
        self.alignments_requested = alignments
        self.diagnostics = diagnostics
        token = name.replace("-", "_")
        validation_enabled = args.validation_output_bytes > 0
        self.c0_vn_path = output_dir / f"dual_weave_{token}_c0_vn.bin"
        self.c1_vn_path = output_dir / f"dual_weave_{token}_c1_vn.bin"
        self.c0_raw_path = output_dir / f"dual_weave_{token}_c0_raw_validation.bin"
        self.c1_raw_path = output_dir / f"dual_weave_{token}_c1_raw_validation.bin"
        self.c0_vn_writer = ContinuousBitWriter(self.c0_vn_path, args.write_output, args.max_output_bytes)
        self.c1_vn_writer = ContinuousBitWriter(self.c1_vn_path, args.write_output, args.max_output_bytes)
        self.c0_raw_writer = ContinuousBitWriter(self.c0_raw_path, validation_enabled, args.validation_output_bytes)
        self.c1_raw_writer = ContinuousBitWriter(self.c1_raw_path, validation_enabled, args.validation_output_bytes)
        self.alignment_variants = {
            alignment: DualWeaveAlignmentVariant(name, alignment, output_dir, args, diagnostics)
            for alignment in alignments
        }
        self.health = {
            "c0": ContinuousHealthTests(
                min(args.assessed_min_entropy, 1.0), args.health_alpha, args.apt_window
            ),
            "c1": ContinuousHealthTests(
                min(args.assessed_min_entropy, 1.0), args.health_alpha, args.apt_window
            ),
        }
        self.groups = 0
        self.selected_bits = 0
        self.last: dict[str, Any] = {}
        self.index_cache_key: tuple[tuple[int, int], str] | None = None
        self.order_indices = np.empty(0, dtype=np.int64)
        self.order_even = np.empty(0, dtype=bool)

    def _ordered_active_indices(self, active_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        key = (active_mask.shape, hashlib.sha256(np.packbits(active_mask.reshape(-1), bitorder="big").tobytes()).hexdigest())
        if self.index_cache_key == key:
            return self.order_indices, self.order_even
        height, width = active_mask.shape
        if self.name == "row-major":
            order = np.arange(height * width, dtype=np.int64)
        else:
            grid = np.arange(height * width, dtype=np.int64).reshape(height, width)
            grid[1::2] = grid[1::2, ::-1]
            order = grid.reshape(-1)
        active_flat = active_mask.reshape(-1)
        indices = order[active_flat[order]]
        rows = indices // width
        cols = indices - rows * width
        even = ((rows + cols) & 1) == 0
        self.index_cache_key = key
        self.order_indices = indices
        self.order_even = even
        return indices, even

    def consume(
        self,
        change_a: np.ndarray,
        change_b: np.ndarray,
        active_mask: np.ndarray,
        health_callback: Any,
        timestamp: str,
        frame_id: int,
    ) -> tuple[dict[str, Any], Optional[str]]:
        indices, even = self._ordered_active_indices(active_mask)
        flat_a = change_a.reshape(-1)[indices]
        flat_b = change_b.reshape(-1)[indices]
        c0 = np.where(even, flat_a, flat_b).astype(np.uint8, copy=False)
        c1 = np.where(even, flat_b, flat_a).astype(np.uint8, copy=False)
        if self.diagnostics is not None:
            token = self.name.replace("-", "_")
            self.diagnostics.observe_bits(f"dual_{token}_c0_raw", f"Dual {self.name} — C0 raw", 30, c0, "dual")
            self.diagnostics.observe_bits(f"dual_{token}_c1_raw", f"Dual {self.name} — C1 raw", 31, c1, "dual")
        failure: Optional[str] = None
        for stream_name, bits in (("c0", c0), ("c1", c1)):
            health = self.health[stream_name]
            for test, details in health.consume(bits):
                health_callback(timestamp, frame_id, f"{test}:dual-weave:{self.name}:{stream_name}", details)
                failure = f"{test}:dual-weave:{self.name}:{stream_name}: {details}"
        self.c0_raw_writer.write_bits(c0)
        self.c1_raw_writer.write_bits(c1)
        c0_vn = von_neumann_split(c0)
        c1_vn = von_neumann_split(c1)
        if self.diagnostics is not None:
            token = self.name.replace("-", "_")
            self.diagnostics.observe_bits(f"dual_{token}_c0_vn", f"Dual {self.name} — C0 Von Neumann", 34, c0_vn, "dual")
            self.diagnostics.observe_bits(f"dual_{token}_c1_vn", f"Dual {self.name} — C1 Von Neumann", 35, c1_vn, "dual")
        c0_before = self.c0_vn_writer.written_bytes
        c1_before = self.c1_vn_writer.written_bytes
        if not self.health["c0"].latched:
            self.c0_vn_writer.write_bits(c0_vn)
        if not self.health["c1"].latched:
            self.c1_vn_writer.write_bits(c1_vn)
        alignment_status: dict[str, Any] = {}
        if not self.health["c0"].latched and not self.health["c1"].latched:
            for alignment, variant in self.alignment_variants.items():
                before = variant.conditioner.written_bytes
                status = variant.consume(c0, c1)
                alignment_status[alignment] = {
                    "conditioned_bytes": variant.conditioner.written_bytes - before,
                    "blocks": variant.conditioner.blocks,
                    "complete": variant.conditioner.completed,
                    "latched": variant.conditioner.latched,
                }
                if status.get("latched"):
                    details = str(status.get("conditioner", {}).get("last_failure") or "dual weave conditioner failure")
                    health_callback(timestamp, frame_id, f"CONDITIONER:dual-weave:{self.name}:{alignment}", details)
                    failure = f"CONDITIONER:dual-weave:{self.name}:{alignment}: {details}"
        self.groups += 1
        self.selected_bits += int(c0.size + c1.size)
        result = {
            "order": self.name,
            "active_pixels": int(indices.size),
            "c0_bits": int(c0.size),
            "c1_bits": int(c1.size),
            "c0_ones": int(c0.sum()),
            "c1_ones": int(c1.sum()),
            "c0_p1": float(c0.mean()) if c0.size else 0.0,
            "c1_p1": float(c1.mean()) if c1.size else 0.0,
            "c0_vn_bits": int(c0_vn.size),
            "c1_vn_bits": int(c1_vn.size),
            "c0_vn_bytes": self.c0_vn_writer.written_bytes - c0_before,
            "c1_vn_bytes": self.c1_vn_writer.written_bytes - c1_before,
            "alignment_status_json": json.dumps(alignment_status, sort_keys=True, separators=(",", ":")),
            "phase_health_latched": False,
            "c0_health_latched": self.health["c0"].latched,
            "c1_health_latched": self.health["c1"].latched,
            "complete": self.complete(),
        }
        self.last = result
        return result, failure

    def complete(self) -> bool:
        targets: list[bool] = []
        if self.c0_vn_writer.max_bytes:
            targets.append(self.c0_vn_writer.completed and self.c1_vn_writer.completed)
        enabled_alignments = [v for v in self.alignment_variants.values() if v.conditioner.enabled and v.conditioner.max_bytes]
        if enabled_alignments:
            targets.append(all(v.conditioner.completed for v in enabled_alignments))
        return bool(targets) and all(targets)

    def any_latched(self) -> bool:
        return (
            self.health["c0"].latched
            or self.health["c1"].latched
            or any(v.conditioner.latched for v in self.alignment_variants.values())
        )

    def close(self) -> None:
        self.c0_vn_writer.close(); self.c1_vn_writer.close()
        self.c0_raw_writer.close(); self.c1_raw_writer.close()
        for variant in self.alignment_variants.values():
            variant.close()

    def status(self, production_seconds: float) -> dict[str, Any]:
        return {
            "order": self.name,
            "groups": self.groups,
            "selected_bits": self.selected_bits,
            "input_bps_lifetime": self.selected_bits / production_seconds if production_seconds > 0 else 0.0,
            "c0_vn": {
                "file": self.c0_vn_path.name,
                "written_bytes": self.c0_vn_writer.written_bytes,
                "accepted_bits": self.c0_vn_writer.accepted_bits,
                "complete": self.c0_vn_writer.completed,
                "health": self._health_status("c0"),
            },
            "c1_vn": {
                "file": self.c1_vn_path.name,
                "written_bytes": self.c1_vn_writer.written_bytes,
                "accepted_bits": self.c1_vn_writer.accepted_bits,
                "complete": self.c1_vn_writer.completed,
                "health": self._health_status("c1"),
            },
            "alignments": {
                name: variant.status()
                for name, variant in self.alignment_variants.items()
            },
            "validation": {
                "c0_raw_bytes": self.c0_raw_writer.written_bytes,
                "c1_raw_bytes": self.c1_raw_writer.written_bytes,
            },
            "last": self.last,
            "complete": self.complete(),
            "latched": self.any_latched(),
        }

    def _health_status(self, name: str) -> dict[str, Any]:
        h = self.health[name]
        return {"latched": h.latched, "rct_failures": h.rct_failures, "apt_failures": h.apt_failures, "last_failure": h.last_failure}

    def files(self) -> dict[str, str]:
        token = self.name.replace("-", "_")
        result = {
            f"dual_weave_{token}_c0_vn": self.c0_vn_path.name,
            f"dual_weave_{token}_c1_vn": self.c1_vn_path.name,
            f"dual_weave_{token}_c0_raw": self.c0_raw_path.name,
            f"dual_weave_{token}_c1_raw": self.c1_raw_path.name,
        }
        for variant in self.alignment_variants.values():
            result.update(variant.files())
        return result


class DualWeaveExperiment:
    """Pair consecutive disjoint temporal maps and weave complementary phases."""

    PHASE_NAMES = ("a_even", "a_odd", "b_even", "b_odd")

    def __init__(
        self,
        enabled: bool,
        orders: tuple[str, ...],
        alignments: tuple[str, ...],
        output_dir: Path,
        args: argparse.Namespace,
        diagnostics: Optional[LiveByteDiagnostics] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.orders = tuple(orders)
        self.alignments = tuple(alignments)
        self.args = args
        self.diagnostics = diagnostics
        self.pending: Optional[dict[str, Any]] = None
        self.group_sequence = 0
        self.skipped_groups = 0
        self.phase_health: dict[str, ContinuousHealthTests] = {}
        self.phase_validation_paths: dict[str, Path] = {}
        self.phase_validation_writers: dict[str, ContinuousBitWriter] = {}
        self.variants: dict[str, DualWeaveOrderVariant] = {}
        self.metrics_path = output_dir / "dual_weave_metrics.csv"
        self.summary_path = output_dir / "dual_weave_summary.json"
        self.metrics_file: Optional[Any] = None
        self.metrics_writer: Optional[csv.DictWriter] = None
        validation_enabled = args.validation_output_bytes > 0
        if not self.enabled:
            return
        for phase in self.PHASE_NAMES:
            path = output_dir / f"dual_weave_phase_{phase}_validation.bin"
            self.phase_validation_paths[phase] = path
            self.phase_validation_writers[phase] = ContinuousBitWriter(path, validation_enabled, args.validation_output_bytes)
            self.phase_health[phase] = ContinuousHealthTests(
                min(args.assessed_min_entropy, 1.0), args.health_alpha, args.apt_window
            )
        self.variants = {
            name: DualWeaveOrderVariant(name, self.alignments, output_dir, args, diagnostics)
            for name in self.orders
        }
        self.metrics_file = self.metrics_path.open("w", newline="", encoding="utf-8")
        self.metrics_writer = csv.DictWriter(self.metrics_file, fieldnames=DUAL_WEAVE_METRICS_HEADER)
        self.metrics_writer.writeheader(); self.metrics_file.flush()

    @staticmethod
    def _serpentine_active_indices(active_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        height, width = active_mask.shape
        grid = np.arange(height * width, dtype=np.int64).reshape(height, width)
        grid[1::2] = grid[1::2, ::-1]
        order = grid.reshape(-1)
        indices = order[active_mask.reshape(-1)[order]]
        rows = indices // width
        cols = indices - rows * width
        return indices, (((rows + cols) & 1) == 0)

    def consume(
        self,
        change: np.ndarray,
        active_mask: np.ndarray,
        valid: bool,
        timestamp: str,
        frame_id: int,
        pair_sequence: int,
        health_callback: Any,
    ) -> Optional[str]:
        if not self.enabled:
            return None
        item = {
            "change": change.copy(),
            "valid": bool(valid),
            "timestamp": timestamp,
            "frame_id": int(frame_id),
            "pair_sequence": int(pair_sequence),
        }
        if self.pending is None:
            self.pending = item
            return None
        first = self.pending
        self.pending = None
        self.group_sequence += 1
        if not first["valid"] or not item["valid"]:
            self.skipped_groups += 1
            return None
        change_a = first["change"]
        change_b = item["change"]
        indices, even = self._serpentine_active_indices(active_mask)
        flat_a = change_a.reshape(-1)[indices]
        flat_b = change_b.reshape(-1)[indices]
        phase_bits = {
            "a_even": flat_a[even], "a_odd": flat_a[~even],
            "b_even": flat_b[even], "b_odd": flat_b[~even],
        }
        failure: Optional[str] = None
        phase_latched = False
        for phase, bits in phase_bits.items():
            self.phase_validation_writers[phase].write_bits(bits)
            h = self.phase_health[phase]
            for test, details in h.consume(bits):
                health_callback(timestamp, frame_id, f"{test}:dual-weave-phase:{phase}", details)
                failure = f"{test}:dual-weave-phase:{phase}: {details}"
            phase_latched = phase_latched or h.latched
        for name, variant in self.variants.items():
            result, variant_failure = variant.consume(change_a, change_b, active_mask, health_callback, timestamp, frame_id)
            if variant_failure:
                failure = variant_failure
            result["phase_health_latched"] = phase_latched
            if self.metrics_writer is not None:
                self.metrics_writer.writerow({
                    "timestamp_utc": timestamp,
                    "group_sequence": self.group_sequence,
                    "pair_a_sequence": first["pair_sequence"],
                    "pair_b_sequence": item["pair_sequence"],
                    "frame_a_id": first["frame_id"],
                    "frame_b_id": item["frame_id"],
                    **result,
                })
        if self.metrics_file is not None:
            self.metrics_file.flush()
        return failure

    def any_latched(self) -> bool:
        return any(h.latched for h in self.phase_health.values()) or any(
            v.any_latched() for v in self.variants.values()
        )

    def all_complete(self) -> bool:
        return self.enabled and bool(self.variants) and all(v.complete() for v in self.variants.values())

    def close(self) -> None:
        if not self.enabled:
            return
        for writer in self.phase_validation_writers.values(): writer.close()
        for variant in self.variants.values(): variant.close()
        if self.metrics_file is not None:
            self.metrics_file.flush(); self.metrics_file.close(); self.metrics_file = None

    def status(self, production_seconds: float) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        return {
            "enabled": True,
            "model": "complementary-temporal-checkerboard-v2",
            "orders": list(self.orders),
            "alignments": list(self.alignments),
            "group_sequence": self.group_sequence,
            "pending_pair": None if self.pending is None else {
                "pair_sequence": self.pending["pair_sequence"], "frame_id": self.pending["frame_id"], "valid": self.pending["valid"]
            },
            "skipped_groups": self.skipped_groups,
            "phase_health": {
                name: {"latched": h.latched, "rct_failures": h.rct_failures, "apt_failures": h.apt_failures, "last_failure": h.last_failure}
                for name, h in self.phase_health.items()
            },
            "variants": {name: variant.status(production_seconds) for name, variant in self.variants.items()},
            "complete": self.all_complete(),
            "latched": self.any_latched(),
        }

    def write_summary(self, production_seconds: float) -> None:
        if not self.enabled:
            return
        self.summary_path.write_text(json.dumps(json_safe(self.status(production_seconds)), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")

    def files(self) -> dict[str, str]:
        if not self.enabled:
            return {}
        result = {"dual_weave_metrics": self.metrics_path.name, "dual_weave_summary": self.summary_path.name}
        for phase, path in self.phase_validation_paths.items(): result[f"dual_weave_phase_{phase}"] = path.name
        for variant in self.variants.values(): result.update(variant.files())
        return result


def extract_yuyv(frame: np.ndarray, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    array = np.asarray(frame, dtype=np.uint8)
    if array.ndim == 3 and array.shape == (height, width, 2):
        yuyv = array
    elif array.ndim == 2 and array.shape == (height, width * 2):
        yuyv = array.reshape(height, width, 2)
    else:
        flat = array.reshape(-1)
        expected = width * height * 2
        if flat.size != expected:
            raise RuntimeError(
                f"Unexpected raw frame shape={array.shape}, bytes={flat.size}, "
                f"expected={expected}; RGB conversion may still be active"
            )
        yuyv = flat.reshape(height, width, 2)
    return yuyv[:, :, 0].copy(), yuyv


def make_preview(yuyv: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(yuyv, cv2.COLOR_YUV2BGR_YUY2)


def find_camera(vid: str, pid: str) -> str:
    for path in sorted(Path("/dev").glob("video*")):
        try:
            result = subprocess.run(
                ["udevadm", "info", "--query=property", f"--name={path}"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            continue
        properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if (
            properties.get("ID_VENDOR_ID", "").lower() == vid.lower()
            and properties.get("ID_MODEL_ID", "").lower() == pid.lower()
        ):
            return str(path)
    raise RuntimeError(f"Camera USB {vid}:{pid} not found")


def v4l2_set(device: str, control: str, value: int, logger: logging.Logger) -> None:
    result = subprocess.run(
        ["v4l2-ctl", "-d", device, "--set-ctrl", f"{control}={value}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("Cannot set %s=%s: %s", control, value, result.stderr.strip())


def v4l2_get(device: str, control: str, logger: logging.Logger) -> Optional[int]:
    try:
        result = subprocess.run(
            ["v4l2-ctl", "-d", device, "--get-ctrl", control],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("Cannot read V4L2 control %s: %s", control, exc)
        return None
    if result.returncode != 0:
        logger.warning("Cannot read %s: %s", control, result.stderr.strip())
        return None
    text = result.stdout.strip()
    match = re.search(r":\s*(-?\d+)\b", text)
    if match:
        return int(match.group(1))
    logger.warning("Unexpected v4l2-ctl output for %s: %r", control, text)
    return None


def v4l2_control_snapshot(device: str, logger: logging.Logger) -> dict[str, Optional[int]]:
    return {
        "auto_exposure": v4l2_get(device, "auto_exposure", logger),
        "exposure_time_absolute": v4l2_get(device, "exposure_time_absolute", logger),
    }


def configure_camera(device: str, args: argparse.Namespace, logger: logging.Logger) -> None:
    if args.manual_exposure:
        v4l2_set(device, "auto_exposure", 1, logger)
        if args.exposure_value is not None:
            v4l2_set(device, "exposure_time_absolute", args.exposure_value, logger)


def open_camera(
    device: str,
    args: argparse.Namespace,
    logger: logging.Logger,
) -> tuple[cv2.VideoCapture, str, int, int, float]:
    capture = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open {device}")
    capture.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*EXPECTED_FOURCC))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    capture.set(cv2.CAP_PROP_FPS, args.camera_fps)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    ok, frame = capture.read()
    if not ok or frame is None:
        capture.release()
        raise RuntimeError("Camera opened but returned no frame")

    fourcc = decode_fourcc(capture.get(cv2.CAP_PROP_FOURCC))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    logger.info(
        "Actual mode %s %dx%d @ %.3f, first shape=%s bytes=%d",
        fourcc,
        width,
        height,
        fps,
        frame.shape,
        frame.nbytes,
    )
    if args.strict_mode and (
        fourcc != EXPECTED_FOURCC
        or width != args.width
        or height != args.height
        or abs(fps - args.camera_fps) > 0.2
    ):
        capture.release()
        raise RuntimeError(f"Camera rejected strict mode; actual={fourcc} {width}x{height}@{fps}")
    extract_yuyv(frame, width, height)
    return capture, fourcc, width, height, fps


@dataclass
class LastFrame:
    timestamp_utc: str
    frame_id: int
    state: str
    pair_sequence: int
    pairing_mode: str
    pair_lag_frames: int
    pair_buffer_frames: int
    previous_frame_id: int
    pair_delta_seconds: float
    pair_buffer_fill: int
    raw_change_bits: int
    raw_change_ones: int
    raw_change_p1: float
    masked_bits: int
    masked_ones: int
    masked_p1: float
    vn_output_bits: int
    vn_efficiency: float
    output_bytes: int
    frame_interval_s: float
    raw_change_bps: float
    masked_bps: float
    vn_output_bps: float
    vn_output_bps_ema: float
    vn_output_bps_1s: float
    vn_output_bps_10s: float
    vn_output_bps_60s: float
    vn_output_bps_lifetime: float
    packed_bytes_per_second_10s: float
    projected_mib_per_day_10s: float
    processing_ms: float


@dataclass
class CapturedYFrame:
    frame_id: int
    timestamp_monotonic: float
    y: np.ndarray
    yuyv: Optional[np.ndarray] = None


class FramePairScheduler:
    """Create either overlapping lag-k pairs or k disjoint pairs per 2k-frame block."""

    def __init__(self, mode: str, lag_frames: int) -> None:
        if mode not in {"sliding", "disjoint"}:
            raise ValueError(f"unsupported pairing mode: {mode}")
        if lag_frames < 1:
            raise ValueError("lag_frames must be positive")
        self.mode = mode
        self.lag_frames = int(lag_frames)
        self.sliding_buffer: deque[CapturedYFrame] = deque(maxlen=self.lag_frames)
        self.disjoint_first_half: list[CapturedYFrame] = []
        self.disjoint_second_index = 0

    @property
    def conceptual_buffer_frames(self) -> int:
        return self.lag_frames + 1 if self.mode == "sliding" else 2 * self.lag_frames

    @property
    def buffered_frames(self) -> int:
        if self.mode == "sliding":
            return len(self.sliding_buffer)
        return len(self.disjoint_first_half) + self.disjoint_second_index

    @property
    def phase(self) -> str:
        if self.mode == "sliding":
            return "PRIMING" if len(self.sliding_buffer) < self.lag_frames else "PAIRING"
        if len(self.disjoint_first_half) < self.lag_frames:
            return "FIRST_HALF"
        return "SECOND_HALF"

    def reset(self) -> None:
        self.sliding_buffer.clear()
        self.disjoint_first_half.clear()
        self.disjoint_second_index = 0

    @staticmethod
    def _stored(frame: CapturedYFrame) -> CapturedYFrame:
        # Only Y and metadata are needed for the older side of a pair.
        return CapturedYFrame(
            frame_id=frame.frame_id,
            timestamp_monotonic=frame.timestamp_monotonic,
            y=frame.y,
            yuyv=None,
        )

    def push(self, frame: CapturedYFrame) -> Optional[tuple[CapturedYFrame, CapturedYFrame]]:
        if self.mode == "sliding":
            if len(self.sliding_buffer) < self.lag_frames:
                self.sliding_buffer.append(self._stored(frame))
                return None
            previous = self.sliding_buffer[0]
            self.sliding_buffer.append(self._stored(frame))
            return previous, frame

        if len(self.disjoint_first_half) < self.lag_frames:
            self.disjoint_first_half.append(self._stored(frame))
            return None

        previous = self.disjoint_first_half[self.disjoint_second_index]
        self.disjoint_second_index += 1
        if self.disjoint_second_index >= self.lag_frames:
            self.disjoint_first_half.clear()
            self.disjoint_second_index = 0
        return previous, frame


class Service:
    def __init__(self, args: argparse.Namespace, logger: logging.Logger) -> None:
        self.args = args
        self.logger = logger
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="camera-source", daemon=True)
        self.source: Optional[FrameSource] = None
        self.source_manifest: dict[str, Any] = {}
        self.source_connection_generation = 0
        self.source_reconnect_count = 0
        self.source_reconnect_events: list[dict[str, Any]] = []
        self.device = ""
        self.error: Optional[str] = None
        self.fourcc = ""
        self.width = 0
        self.height = 0
        self.fps = 0.0
        self.measured_fps = 0.0
        self.last_read = 0.0
        self.pair_scheduler = FramePairScheduler(args.pairing_mode, args.pair_lag_frames)
        self.pair_sequence = 0
        self.pair_delta_count = 0
        self.pair_delta_sum = 0.0
        self.pair_delta_min = math.inf
        self.pair_delta_max = 0.0
        self.calibrator: Optional[FrozenPixelCalibrator] = None
        self.shadow: Optional[ShadowPixelMonitor] = None
        self.health = ContinuousHealthTests(
            args.assessed_min_entropy,
            args.health_alpha,
            args.apt_window,
            alphabet_size=1 << args.lsb_bits,
            sample_width_bits=args.lsb_bits,
        )
        self.last: Optional[LastFrame] = None
        self.started_monotonic = time.monotonic()
        self.production_started_monotonic: Optional[float] = None
        self.last_processed_monotonic: Optional[float] = None
        self.warmup_started_monotonic: Optional[float] = None
        self.warmup_deadline_monotonic: Optional[float] = None
        self.source_warmup_seconds: Optional[float] = None
        self.source_warmup_observed_monotonic: Optional[float] = None
        self.warmup_complete = args.thermal_warmup_seconds <= 0.0
        self.exit_requested = False
        self.output_limit_reached = False
        self.camera_controls_after_configure: dict[str, Optional[int]] = {}
        self.camera_controls_after_open_initial: dict[str, Optional[int]] = {}
        self.camera_controls_after_open: dict[str, Optional[int]] = {}
        self.camera_controls_final: dict[str, Optional[int]] = {}
        # Each entry: (interval_end, interval_seconds, raw_bits, masked_bits, vn_bits, packed_bytes).
        self.rate_samples: deque[tuple[float, float, int, int, int, int]] = deque()
        self.vn_output_bps_ema = 0.0
        self.rate_ema_seconds = 10.0
        self.rate_history_seconds = 65.0
        self.total_raw_input_bits = 0
        self.total_masked_input_bits = 0
        self.total_output_bits = 0
        self.total_packed_bytes = 0
        self.total_written_bytes = 0
        self.active_mask_sha256 = ""
        self.mask_epoch_id = ""
        self.active_mask_epoch_path: Optional[Path] = None
        self.production_pair_count = 0
        self.spatial_correlation = SpatialCorrelationAccumulator(args.correlation_distances)
        self.last_mask_snapshot_monotonic = 0.0
        self.last_mask_snapshot_sha256 = ""
        self.last_mask_snapshot_utc: Optional[str] = None
        self.mask_snapshot_sequence = 0
        self.mask_drift_history: deque[dict[str, Any]] = deque(
            maxlen=args.mask_drift_history_points
        )
        self.shadow_bad_seen = False
        # Fail-closed clipping is evaluated on the actual production sampling
        # mask (for example checkerboard-even), not on pixels that are never
        # consumed by the production stream. Full-mask clipping remains a
        # diagnostic metric.
        self.active_clip_pixels = 0
        self.active_clip_rate = 0.0
        self.full_active_clip_pixels = 0
        self.full_active_clip_rate = 0.0
        self.production_active_pixels = 0
        self.full_active_pixels = 0
        self.clip_bad_streak = 0
        self.clip_failures = 0
        self.clip_latched = False
        self.clip_enforced_scopes = ("full", "even", "odd") if args.dual_weave_comparison else ("production",)
        self.clip_scopes: dict[str, dict[str, Any]] = {
            name: {"active_pixels": 0, "clipped_pixels": 0, "rate": 0.0, "bad_streak": 0, "failures": 0, "latched": False}
            for name in ("production", "full", "even", "odd")
        }
        self.last_control_check_monotonic = 0.0
        self.control_checks = 0
        self.control_mismatches = 0
        self.control_bad_streak = 0
        self.control_latched = False
        self.camera_controls_periodic: dict[str, Optional[int]] = {}
        self.last_conditioned_bytes_this_pair = 0

        self.preview_jpg: Optional[bytes] = None
        self.y_png: Optional[bytes] = None
        self.change_png: Optional[bytes] = None
        self.active_mask_png: Optional[bytes] = None
        self.shadow_mask_png: Optional[bytes] = None
        self.mask_difference_png: Optional[bytes] = None
        self.active_overlay_png: Optional[bytes] = None
        self.shadow_overlay_png: Optional[bytes] = None

        self.output_dir = Path(args.output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stream_lsb_summary_path = self.output_dir / "stream_lsb_summary.json"
        self.stream_lsb_statistics = StreamingBitplaneStatistics(
            args.lsb_bits, args.stream_stats_window_pairs
        )
        self.byte_diagnostics = LiveByteDiagnostics(
            args.live_byte_diagnostics,
            self.output_dir,
            args.live_heatmap_interval_seconds,
            args.live_heatmap_max_stages,
            args.live_heatmap_min_bytes,
            logger,
        )
        diagnostic_stages = [
            ("direct_lsb", "Direct LSB — aktywna maska", 0),
            ("temporal_raw", "Temporal XOR — pełna mapa", 10),
            ("temporal_masked", "Temporal XOR — aktywna maska", 20),
        ]
        if args.von_neumann_stage:
            diagnostic_stages.append((
                "von_neumann",
                f"Von Neumann ×{args.von_neumann_passes} — wyjście",
                30,
            ))
        diagnostic_stages.append(("sha3_512", "SHA3-512 — wyjście", 40))
        for key, label, rank in diagnostic_stages:
            self.byte_diagnostics.register(key, label, rank, "main")
        self.bin_path = self.output_dir / "y_temporal_vn.bin"
        self.index_path = self.output_dir / "y_temporal_vn_index.csv"
        self.frame_path = self.output_dir / "frame_metrics.csv"
        self.health_path = self.output_dir / "health_events.csv"
        self.drift_path = self.output_dir / "mask_drift.csv"
        self.active_mask_path = self.output_dir / "active_mask.png"
        self.shadow_mask_path = self.output_dir / "shadow_mask.png"
        self.mask_difference_path = self.output_dir / "mask_difference.png"
        self.active_report_path = self.output_dir / "active_mask_report.json"
        self.shadow_report_path = self.output_dir / "shadow_mask_report.json"
        self.run_manifest_path = self.output_dir / "run_manifest.json"
        self.output_complete_path = self.output_dir / "output_complete.json"
        self.run_failed_path = self.output_dir / "run_failed.json"
        self.pixel_correlation_path = self.output_dir / "pixel_correlation.csv"
        self.pixel_correlation_summary_path = self.output_dir / "pixel_correlation_summary.json"
        self.mask_snapshot_dir = self.output_dir / "mask_snapshots"
        self.mask_snapshot_index_path = self.output_dir / "mask_snapshots.csv"
        self.direct_validation_path = self.output_dir / "y_direct_lsb_common_mask_validation.bin"
        self.raw_temporal_validation_path = self.output_dir / "y_temporal_raw_validation.bin"
        self.masked_temporal_validation_path = self.output_dir / "y_temporal_masked_validation.bin"
        self.spatial_variant_metrics_path = self.output_dir / "spatial_variant_metrics.csv"
        self.production_mask_path = self.output_dir / "production_mask.png"
        self.conditioned_path = self.output_dir / "camera_entropy_sha3_512.bin"

        self.bit_writer = ContinuousBitWriter(
            self.bin_path, args.write_output, args.max_output_bytes
        )
        self.conditioner = Sha3ConditionerWriter(
            self.conditioned_path,
            args.conditioner == "sha3-512" and args.write_conditioned_output,
            args.conditioner_input_bits,
            args.conditioned_output_bytes,
            byte_observer=lambda data: self.byte_diagnostics.observe_bytes(
                "sha3_512", "SHA3-512 — wyjście", 40, data, "main"
            ),
        )
        self.dual_weave = DualWeaveExperiment(
            args.dual_weave_comparison,
            args.dual_weave_orders,
            args.dual_weave_alignments,
            self.output_dir,
            args,
            self.byte_diagnostics,
        )
        self.spatial_comparison_enabled = bool(args.spatial_comparison)
        self.spatial_serializer = SpatialSerializer(
            args.serialization_order,
            args.serialization_tile_width,
            args.serialization_tile_height,
        )
        self.spatial_pattern_cache: dict[str, np.ndarray] = {}
        self.spatial_variant_paths: dict[str, Path] = {"full": self.bin_path}
        self.spatial_variant_writers: dict[str, ContinuousBitWriter] = {"full": self.bit_writer}
        self.spatial_variant_health: dict[str, ContinuousHealthTests] = {"full": self.health}
        self.spatial_variant_validation_paths: dict[str, Path] = {
            "full": self.masked_temporal_validation_path
        }
        self.spatial_variant_validation_writers: dict[str, ContinuousBitWriter] = {}
        self.spatial_variant_correlations: dict[str, SpatialCorrelationAccumulator] = {
            "full": self.spatial_correlation
        }
        self.spatial_variant_correlation_paths: dict[str, Path] = {
            "full": self.pixel_correlation_path
        }
        self.spatial_variant_correlation_summary_paths: dict[str, Path] = {
            "full": self.pixel_correlation_summary_path
        }
        self.spatial_variant_correlation_files: dict[str, Any] = {}
        self.spatial_variant_correlation_writers: dict[str, csv.DictWriter] = {}
        self.spatial_variant_last: dict[str, dict[str, Any]] = {}
        validation_enabled = args.validation_output_bytes > 0
        self.direct_validation_writer = ContinuousBitWriter(
            self.direct_validation_path, validation_enabled, args.validation_output_bytes
        )
        self.raw_temporal_validation_writer = ContinuousBitWriter(
            self.raw_temporal_validation_path, validation_enabled, args.validation_output_bytes
        )
        self.masked_temporal_validation_writer = ContinuousBitWriter(
            self.masked_temporal_validation_path, validation_enabled, args.validation_output_bytes
        )
        self.spatial_variant_validation_writers["full"] = self.masked_temporal_validation_writer

        if self.spatial_comparison_enabled:
            for variant in SPATIAL_COMPARISON_VARIANTS[1:]:
                file_variant = variant.replace("-", "_")
                output_path = self.output_dir / f"y_temporal_vn_{file_variant}.bin"
                validation_path = self.output_dir / f"y_temporal_masked_{file_variant}_validation.bin"
                correlation_path = self.output_dir / f"pixel_correlation_{file_variant}.csv"
                correlation_summary_path = self.output_dir / f"pixel_correlation_{file_variant}_summary.json"
                self.spatial_variant_paths[variant] = output_path
                self.spatial_variant_writers[variant] = ContinuousBitWriter(
                    output_path, args.write_output, args.max_output_bytes
                )
                self.spatial_variant_health[variant] = ContinuousHealthTests(
                    args.assessed_min_entropy,
                    args.health_alpha,
                    args.apt_window,
                    alphabet_size=1 << args.lsb_bits,
                    sample_width_bits=args.lsb_bits,
                )
                self.spatial_variant_validation_paths[variant] = validation_path
                self.spatial_variant_validation_writers[variant] = ContinuousBitWriter(
                    validation_path, validation_enabled, args.validation_output_bytes
                )
                self.spatial_variant_correlations[variant] = SpatialCorrelationAccumulator(
                    args.correlation_distances
                )
                self.spatial_variant_correlation_paths[variant] = correlation_path
                self.spatial_variant_correlation_summary_paths[variant] = correlation_summary_path

        self.index_csv, self.index_writer = self.open_csv(self.index_path, OUTPUT_INDEX_HEADER)
        self.frame_csv, self.frame_writer = self.open_csv(self.frame_path, FRAME_HEADER)
        self.health_csv, self.health_writer = self.open_csv(self.health_path, HEALTH_HEADER)
        self.drift_csv, self.drift_writer = self.open_csv(self.drift_path, MASK_DRIFT_HEADER)
        self.pixel_correlation_csv, self.pixel_correlation_writer = self.open_csv(
            self.pixel_correlation_path, PIXEL_CORRELATION_HEADER
        )
        self.spatial_variant_correlation_files["full"] = self.pixel_correlation_csv
        self.spatial_variant_correlation_writers["full"] = self.pixel_correlation_writer
        if self.spatial_comparison_enabled:
            for variant in SPATIAL_COMPARISON_VARIANTS[1:]:
                corr_file, corr_writer = self.open_csv(
                    self.spatial_variant_correlation_paths[variant], PIXEL_CORRELATION_HEADER
                )
                self.spatial_variant_correlation_files[variant] = corr_file
                self.spatial_variant_correlation_writers[variant] = corr_writer
        self.spatial_variant_metrics_csv, self.spatial_variant_metrics_writer = self.open_csv(
            self.spatial_variant_metrics_path, SPATIAL_VARIANT_HEADER
        )
        self.mask_snapshot_csv, self.mask_snapshot_writer = self.open_csv(
            self.mask_snapshot_index_path, MASK_SNAPSHOT_HEADER
        )

    @staticmethod
    def open_csv(path: Path, header: list[str]) -> tuple[Any, csv.DictWriter]:
        if path.exists() and path.stat().st_size > 0:
            with path.open("r", newline="", encoding="utf-8") as existing:
                current_header = next(csv.reader(existing), [])
            if current_header != header:
                backup = path.with_name(
                    f"{path.stem}.schema-mismatch-{epoch_timestamp()}{path.suffix}"
                )
                path.replace(backup)
        file = path.open("a", newline="", encoding="utf-8")
        writer = csv.DictWriter(file, fieldnames=header)
        if path.stat().st_size == 0:
            writer.writeheader()
            file.flush()
        return file, writer

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        if self.source is not None:
            self.source.close()
        closed: set[int] = set()
        for writer in self.spatial_variant_writers.values():
            if id(writer) not in closed:
                writer.close()
                closed.add(id(writer))
        self.conditioner.close()
        production_seconds = (
            max(0.0, time.monotonic() - self.production_started_monotonic)
            if self.production_started_monotonic is not None else 0.0
        )
        self.dual_weave.write_summary(production_seconds)
        self.dual_weave.close()
        self.direct_validation_writer.close()
        self.raw_temporal_validation_writer.close()
        for writer in self.spatial_variant_validation_writers.values():
            if id(writer) not in closed:
                writer.close()
                closed.add(id(writer))
        self.write_correlation_summary()
        # Capture one final diagnostic image after all writers have flushed.
        # Finish an in-flight periodic render first, then take a fresh snapshot
        # and wait briefly so finite benchmarks retain their true final state.
        heatmap_deadline = time.monotonic() + 10.0
        while time.monotonic() < heatmap_deadline:
            with self.byte_diagnostics.lock:
                if not self.byte_diagnostics.heatmap_rendering:
                    break
            time.sleep(0.05)
        self.byte_diagnostics.maybe_render_heatmap(time.monotonic(), force=True)
        heatmap_deadline = time.monotonic() + 10.0
        while time.monotonic() < heatmap_deadline:
            with self.byte_diagnostics.lock:
                if not self.byte_diagnostics.heatmap_rendering:
                    break
            time.sleep(0.05)
        files_to_close = [
            self.index_csv,
            self.frame_csv,
            self.health_csv,
            self.drift_csv,
            self.mask_snapshot_csv,
            self.spatial_variant_metrics_csv,
            *self.spatial_variant_correlation_files.values(),
        ]
        for file in files_to_close:
            if file:
                try:
                    file.flush()
                    file.close()
                except Exception:
                    pass

    def spatial_selection_status(self) -> dict[str, object]:
        return effective_spatial_selection(self.args)

    def build_spatial_patterns(self, shape: tuple[int, int]) -> dict[str, np.ndarray]:
        if self.spatial_pattern_cache and next(iter(self.spatial_pattern_cache.values())).shape == shape:
            return self.spatial_pattern_cache
        self.spatial_pattern_cache = build_pattern_set(shape, self.args)
        return self.spatial_pattern_cache
    def spatial_variant_masks(self, effective_active: np.ndarray) -> dict[str, np.ndarray]:
        patterns = self.build_spatial_patterns(effective_active.shape)
        if self.spatial_comparison_enabled:
            return {name: effective_active & patterns[name] for name in SPATIAL_COMPARISON_VARIANTS}
        production = self.production_mask(effective_active)
        assert production is not None
        return {"full": production}
    def production_mask(self, active_mask: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if active_mask is None:
            return None
        patterns = self.build_spatial_patterns(active_mask.shape)
        if self.args.spatial_mask_pattern == "legacy":
            selected = canonical_spatial_sampling(self.args.spatial_sampling)
            pattern = patterns[selected]
        else:
            pattern = patterns["configured"]
        return active_mask & pattern
    def update_active_clipping(
        self,
        active_mask: np.ndarray,
        current_ok: np.ndarray,
        timestamp: str,
        frame_id: int,
    ) -> tuple[bool, Optional[str]]:
        patterns = self.build_spatial_patterns(active_mask.shape)
        production = self.production_mask(active_mask)
        if production is None:
            production = active_mask
        scope_masks = {
            "production": production,
            "full": active_mask,
            "even": active_mask & patterns["checkerboard-even"],
            "odd": active_mask & patterns["checkerboard-odd"],
        }
        pair_bad = False
        failure_reason: Optional[str] = None
        for name, mask in scope_masks.items():
            state = self.clip_scopes[name]
            active_pixels = int(np.count_nonzero(mask))
            clipped_pixels = int(np.count_nonzero(mask & ~current_ok))
            rate = clipped_pixels / active_pixels if active_pixels else 1.0
            state["active_pixels"] = active_pixels
            state["clipped_pixels"] = clipped_pixels
            state["rate"] = rate
            if name not in self.clip_enforced_scopes:
                continue
            bad = rate > self.args.max_active_clip_rate
            pair_bad = pair_bad or bad
            if bad:
                state["bad_streak"] = int(state["bad_streak"]) + 1
                state["failures"] = int(state["failures"]) + 1
                details = (
                    f"scope={name} clipped pixels={clipped_pixels}/{active_pixels} "
                    f"rate={rate:.9f} limit={self.args.max_active_clip_rate:.9f}"
                )
                self.write_health_event(timestamp, frame_id, f"ACTIVE_CLIP_RATE:{name}", details)
                self.logger.error("Active clipping health event: %s", details)
                if int(state["bad_streak"]) >= self.args.clip_fail_consecutive:
                    state["latched"] = True
                    failure_reason = f"ACTIVE_CLIP_RATE:{name}: {details}"
            else:
                state["bad_streak"] = 0

        production_state = self.clip_scopes["production"]
        full_state = self.clip_scopes["full"]
        self.production_active_pixels = int(production_state["active_pixels"])
        self.active_clip_pixels = int(production_state["clipped_pixels"])
        self.active_clip_rate = float(production_state["rate"])
        self.full_active_pixels = int(full_state["active_pixels"])
        self.full_active_clip_pixels = int(full_state["clipped_pixels"])
        self.full_active_clip_rate = float(full_state["rate"])
        self.clip_bad_streak = max(int(self.clip_scopes[name]["bad_streak"]) for name in self.clip_enforced_scopes)
        self.clip_failures = sum(int(self.clip_scopes[name]["failures"]) for name in self.clip_enforced_scopes)
        self.clip_latched = any(bool(self.clip_scopes[name]["latched"]) for name in self.clip_enforced_scopes)
        return pair_bad, failure_reason

    def active_clipping_status(self) -> dict[str, Any]:
        return {
            "scope": "multi-scope" if self.args.dual_weave_comparison else "production_sampling_mask",
            "sampling": self.spatial_selection_status()["effective"],
            "selection": self.spatial_selection_status(),
            "enforced_scopes": list(self.clip_enforced_scopes),
            "production_pixels": self.production_active_pixels,
            "pixels": self.active_clip_pixels,
            "rate": self.active_clip_rate,
            "full_active_pixels": self.full_active_pixels,
            "full_pixels": self.full_active_clip_pixels,
            "full_rate": self.full_active_clip_rate,
            "limit": self.args.max_active_clip_rate,
            "bad_streak": self.clip_bad_streak,
            "failures": self.clip_failures,
            "latched": self.clip_latched,
            "dynamic_filter": self.args.dynamic_clip_filter,
            "scopes": {
                name: {
                    "active_pixels": int(state["active_pixels"]),
                    "clipped_pixels": int(state["clipped_pixels"]),
                    "rate": float(state["rate"]),
                    "bad_streak": int(state["bad_streak"]),
                    "failures": int(state["failures"]),
                    "latched": bool(state["latched"]),
                    "enforced": name in self.clip_enforced_scopes,
                }
                for name, state in self.clip_scopes.items()
            },
        }

    def all_output_writers_complete(self) -> bool:
        targets: list[bool] = []
        if self.args.max_output_bytes:
            targets.append(all(writer.completed for writer in self.spatial_variant_writers.values()))
        if self.conditioner.enabled and self.args.conditioned_output_bytes:
            targets.append(self.conditioner.completed)
        if self.dual_weave.enabled:
            targets.append(self.dual_weave.all_complete())
        return bool(targets) and all(targets)

    def spatial_variants_status(self) -> dict[str, Any]:
        production_seconds = (
            max(0.0, time.monotonic() - self.production_started_monotonic)
            if self.production_started_monotonic is not None
            else 0.0
        )
        result: dict[str, Any] = {}
        for name, writer in self.spatial_variant_writers.items():
            health = self.spatial_variant_health[name]
            result[name] = {
                "output_file": self.spatial_variant_paths[name].name,
                "validation_file": self.spatial_variant_validation_paths[name].name,
                "target_bytes": self.args.max_output_bytes,
                "written_bytes": writer.written_bytes,
                "accepted_bits": writer.accepted_bits,
                "pending_bits": int(writer.pending.size),
                "complete": writer.completed,
                "output_bps_lifetime": writer.accepted_bits / production_seconds if production_seconds > 0 else 0.0,
                "health": health.status(),
                "pixel_correlation": self.spatial_variant_correlations[name].summary(),
                "last": self.spatial_variant_last.get(name),
            }
        return result

    def state(self) -> str:
        if self.error:
            return "ERROR"
        if self.output_limit_reached and self.args.exit_on_output_limit:
            return "OUTPUT_COMPLETE"
        if self.health.latched:
            return "HEALTH_FAILED"
        if self.conditioner.latched:
            return "CONDITIONER_FAILED"
        if self.dual_weave.any_latched():
            return "DUAL_WEAVE_FAILED"
        if self.clip_latched:
            return "CLIP_HEALTH_FAILED"
        if self.control_latched:
            return "CAMERA_CONTROL_FAILED"
        if self.shadow and self.shadow.latched:
            return "MASK_DRIFT_FAILED"
        if not self.warmup_complete:
            return "WARMING_UP"
        if not self.calibrator or not self.calibrator.ready:
            return "CALIBRATING"
        return "RUNNING"

    @staticmethod
    def normalize_source_warmup_seconds(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return max(0.0, parsed)

    def source_warmup_from_manifest(self) -> Optional[float]:
        manifest = self.source_manifest or {}
        hello = manifest.get("hello") or {}
        for value in (
            manifest.get("source_warmup_seconds"),
            hello.get("source_warmup_seconds") if isinstance(hello, dict) else None,
            hello.get("agent_uptime_seconds") if isinstance(hello, dict) else None,
        ):
            parsed = self.normalize_source_warmup_seconds(value)
            if parsed is not None:
                return parsed
        return None

    def record_source_warmup(self, value: Any, observed_now: float) -> Optional[float]:
        parsed = self.normalize_source_warmup_seconds(value)
        if parsed is None:
            return None
        self.source_warmup_seconds = parsed
        self.source_warmup_observed_monotonic = observed_now
        required = max(0.0, float(self.args.thermal_warmup_seconds))
        self.warmup_deadline_monotonic = observed_now + max(0.0, required - parsed)
        return parsed

    def current_source_warmup_seconds(self, now: Optional[float] = None) -> Optional[float]:
        if self.source_warmup_seconds is None:
            return None
        current = time.monotonic() if now is None else now
        elapsed = 0.0
        if self.source_warmup_observed_monotonic is not None:
            elapsed = max(0.0, current - self.source_warmup_observed_monotonic)
        return self.source_warmup_seconds + elapsed

    def begin_warmup_epoch(self, started_now: float) -> None:
        self.warmup_started_monotonic = started_now
        self.source_warmup_seconds = None
        self.source_warmup_observed_monotonic = None
        required = max(0.0, float(self.args.thermal_warmup_seconds))
        source_age = self.source_warmup_from_manifest()
        if source_age is not None:
            self.record_source_warmup(source_age, started_now)
            # Reflect credited source age in progress reporting without allowing
            # the hello message alone to bypass validation of the first frame.
            self.warmup_started_monotonic = started_now - min(required, source_age)
        else:
            self.warmup_deadline_monotonic = started_now + required
        self.warmup_complete = required <= 0.0

    def log_warmup_basis(self, context: str) -> None:
        source_age = self.current_source_warmup_seconds()
        if source_age is None:
            self.logger.info(
                "%s warm-up uses worker-local timer: required %.3f seconds",
                context,
                self.args.thermal_warmup_seconds,
            )
            return
        self.logger.info(
            "%s warm-up credits remote source age %.3f seconds: required %.3f, remaining %.3f seconds",
            context,
            source_age,
            self.args.thermal_warmup_seconds,
            self.warmup_remaining_seconds(),
        )

    def warmup_remaining_seconds(self) -> float:
        if self.warmup_complete or self.warmup_deadline_monotonic is None:
            return 0.0
        return max(0.0, self.warmup_deadline_monotonic - time.monotonic())

    def request_process_exit(self, reason: str) -> None:
        if self.exit_requested:
            return
        self.exit_requested = True
        self.logger.info("Requesting process exit: %s", reason)

        def terminate() -> None:
            time.sleep(0.2)
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=terminate, name="exit-request", daemon=True).start()

    def serializable_arguments(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in vars(self.args).items():
            if key.startswith("_"):
                continue
            if key == "rtsp_url" and value:
                result[key] = redact_url(str(value))
            else:
                result[key] = str(value) if isinstance(value, Path) else value
        return result

    def pairing_status(self) -> dict[str, Any]:
        average = self.pair_delta_sum / self.pair_delta_count if self.pair_delta_count else 0.0
        minimum = self.pair_delta_min if self.pair_delta_count else 0.0
        return {
            "mode": self.args.pairing_mode,
            "lag_frames": self.args.pair_lag_frames,
            "conceptual_buffer_frames": self.pair_scheduler.conceptual_buffer_frames,
            "buffered_frames": self.pair_scheduler.buffered_frames,
            "phase": self.pair_scheduler.phase,
            "pair_sequence": self.pair_sequence,
            "pair_delta_seconds_mean": average,
            "pair_delta_seconds_min": minimum,
            "pair_delta_seconds_max": self.pair_delta_max,
        }

    def write_run_manifest(self) -> None:
        manifest = {
            "app_version": APP_VERSION,
            "created_utc": utc_timestamp(),
            "command_line": self.args._command_line,
            "arguments": self.serializable_arguments(),
            "device": self.device,
            "source": self.source_manifest,
            "source_reconnects": {
                "count": self.source_reconnect_count,
                "events": self.source_reconnect_events,
                "allowed_during_production": self.args.allow_source_reconnect_during_production,
            },
            "requested_exposure": self.args.exposure_value,
            "controls_after_configure": self.camera_controls_after_configure,
            "controls_after_open_initial": self.camera_controls_after_open_initial,
            "controls_after_open": self.camera_controls_after_open,
            "mode": {
                "fourcc": self.fourcc,
                "width": self.width,
                "height": self.height,
                "reported_fps": self.fps,
            },
            "pairing": self.pairing_status(),
            "diagnostics": {
                "validation_output_bytes_each": self.args.validation_output_bytes,
                "correlation_distances": list(self.args.correlation_distances),
                "correlation_every": self.args.correlation_every,
                "mask_snapshot_interval_seconds": self.args.mask_snapshot_interval_seconds,
                "mask_snapshot_images": self.args.mask_snapshot_images,
                "web_images": self.args.web_images,
                "von_neumann_stage": self.args.von_neumann_stage,
                "von_neumann_passes": self.args.von_neumann_passes,
                "stream_stats_window_pairs": self.args.stream_stats_window_pairs,
                "pipeline": entropy_pipeline_name(self.args),
                "sample_mode": self.args.sample_mode,
                "lsb_bits": self.args.lsb_bits,
                "bit_order": BIT_ORDER,
                "entropy_credit_bits_per_pixel": self.args.entropy_credit_bits_per_pixel,
                "minimum_conditioner_input_bits": self.args.minimum_conditioner_input_bits,
                "mask_calibration_source": "temporal-xor-lsb0",
                "spatial_sampling": self.spatial_selection_status()["effective"],
                "spatial_sampling_legacy": self.args.spatial_sampling,
                "spatial_selection": self.spatial_selection_status(),
                "spatial_mask_pattern": self.args.spatial_mask_pattern,
                "spatial_step": [self.args.spatial_step_x, self.args.spatial_step_y],
                "spatial_phase": [self.args.spatial_phase_x, self.args.spatial_phase_y],
                "spatial_block": [self.args.spatial_block_width, self.args.spatial_block_height],
                "temporal_spatial_offset": [
                    self.args.temporal_spatial_offset_x,
                    self.args.temporal_spatial_offset_y,
                ],
                "serialization_order": self.args.serialization_order,
                "serialization_tile": [
                    self.args.serialization_tile_width,
                    self.args.serialization_tile_height,
                ],
                "spatial_comparison": self.spatial_comparison_enabled,
                "spatial_variants": list(self.spatial_variant_writers),
                "conditioner": self.conditioner.status(),
                "dual_weave": self.dual_weave.status(0.0),
            },
        }
        self.run_manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def write_stream_lsb_summary(self) -> dict[str, Any]:
        summary = self.stream_lsb_statistics.snapshot(finalize_partial_window=True)
        summary.update({
            "app_version": APP_VERSION,
            "generated_utc": utc_timestamp(),
            "sample_mode": self.args.sample_mode,
            "pairing_mode": self.args.pairing_mode,
            "pair_lag_frames": self.args.pair_lag_frames,
            "spatial_mask_pattern": self.args.spatial_mask_pattern,
            "spatial_sampling": self.spatial_selection_status()["effective"],
            "spatial_sampling_legacy": self.args.spatial_sampling,
            "spatial_selection": self.spatial_selection_status(),
            "serialization_order": self.args.serialization_order,
        })
        self.stream_lsb_summary_path.write_text(
            json.dumps(json_safe(summary), indent=2, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        return summary

    def write_output_complete_report(self, reason: str = "output-targets-reached") -> None:
        # A run directory must have exactly one terminal marker.
        self.run_failed_path.unlink(missing_ok=True)
        stream_lsb = self.write_stream_lsb_summary()
        self.camera_controls_final = (
            self.source.control_snapshot()
            if self.source is not None and self.source.supports_controls
            else dict(self.camera_controls_periodic)
        )
        report = {
            "status": "complete",
            "completion_reason": reason,
            "targets_complete": self.all_output_writers_complete(),
            "timestamp_utc": utc_timestamp(),
            "app_version": APP_VERSION,
            "device": self.device,
            "source": self.source_manifest,
            "source_transport": {
                "connection_generation": self.source_connection_generation,
                "reconnect_count": self.source_reconnect_count,
                "events": self.source_reconnect_events,
                "last_reconnect_reason": str(getattr(self.source, "last_reconnect_reason", "")) if self.source else "",
                "frame_timeout_seconds": self.args.source_frame_timeout_seconds,
                "reconnect_attempts": self.args.source_reconnect_attempts,
                "allow_reconnect_during_production": self.args.allow_source_reconnect_during_production,
            },
            "requested_exposure": self.args.exposure_value,
            "controls_after_configure": self.camera_controls_after_configure,
            "controls_after_open_initial": self.camera_controls_after_open_initial,
            "controls_after_open": self.camera_controls_after_open,
            "controls_final": self.camera_controls_final,
            "target_output_bytes": self.args.max_output_bytes,
            "written_output_bytes": self.bit_writer.written_bytes,
            "accepted_output_bits": self.bit_writer.accepted_bits,
            "pending_bits": int(self.bit_writer.pending.size),
            "spatial_sampling": self.spatial_selection_status()["effective"],
            "spatial_sampling_legacy": self.args.spatial_sampling,
            "spatial_selection": self.spatial_selection_status(),
            "spatial_mask_pattern": self.args.spatial_mask_pattern,
            "spatial_step": [self.args.spatial_step_x, self.args.spatial_step_y],
            "spatial_phase": [self.args.spatial_phase_x, self.args.spatial_phase_y],
            "spatial_block": [self.args.spatial_block_width, self.args.spatial_block_height],
            "temporal_spatial_offset": [
                self.args.temporal_spatial_offset_x,
                self.args.temporal_spatial_offset_y,
            ],
            "serialization_order": self.args.serialization_order,
            "serialization_tile": [
                self.args.serialization_tile_width,
                self.args.serialization_tile_height,
            ],
            "pipeline": entropy_pipeline_name(self.args),
            "sample_mode": self.args.sample_mode,
            "lsb_bits": self.args.lsb_bits,
            "bit_order": BIT_ORDER,
            "entropy_credit_bits_per_pixel": self.args.entropy_credit_bits_per_pixel,
            "minimum_conditioner_input_bits": self.args.minimum_conditioner_input_bits,
            "mask_calibration_source": "temporal-xor-lsb0",
            "von_neumann_stage": self.args.von_neumann_stage,
            "von_neumann_passes": self.args.von_neumann_passes,
            "web_images": self.args.web_images,
            "mask_snapshot_images": self.args.mask_snapshot_images,
            "conditioner": self.conditioner.status(),
            "dual_weave": self.dual_weave.status(
                max(0.0, time.monotonic() - self.production_started_monotonic)
                if self.production_started_monotonic is not None else 0.0
            ),
            "active_clipping": self.active_clipping_status(),
            "camera_control_monitor": {
                "checks": self.control_checks,
                "mismatches": self.control_mismatches,
                "latched": self.control_latched,
                "last_snapshot": self.camera_controls_periodic,
            },
            "validation_output": {
                "target_bytes_each": self.args.validation_output_bytes,
                "direct_lsb_bytes": self.direct_validation_writer.written_bytes,
                "raw_temporal_bytes": self.raw_temporal_validation_writer.written_bytes,
                "masked_temporal_bytes": self.masked_temporal_validation_writer.written_bytes,
            },
            "pixel_correlation": self.spatial_correlation.summary(),
            "spatial_comparison": self.spatial_comparison_enabled,
            "spatial_variants": self.spatial_variants_status(),
            "rates": self.rates(),
            "mode": {
                "fourcc": self.fourcc,
                "width": self.width,
                "height": self.height,
                "reported_fps": self.fps,
                "measured_fps": self.measured_fps,
            },
            "pairing": self.pairing_status(),
            "mask_epoch_id": self.mask_epoch_id,
            "active_mask_sha256": self.active_mask_sha256,
            "active_pixels": int(self.calibrator.mask.sum())
            if self.calibrator and self.calibrator.mask is not None
            else 0,
            "health": self.health.status(),
            "stream_lsb_statistics": {
                "file": self.stream_lsb_summary_path.name,
                "aggregate_hmin_per_input_bit": stream_lsb.get("aggregate", {}).get("symbol_min_entropy_bits_per_input_bit"),
                "worst_window_hmin_per_input_bit": stream_lsb.get("worst_window_hmin_per_input_bit"),
                "window_count": stream_lsb.get("window_count"),
            },
            "shadow": (asdict(self.shadow.comparison) | {"bad_seen": self.shadow_bad_seen}) if self.shadow else {"bad_seen": self.shadow_bad_seen},
        }
        self.output_complete_path.write_text(
            json.dumps(json_safe(report), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )

    def write_failure_report(self, reason: str) -> None:
        # Fail-closed takes precedence, including when a target was reached in
        # the same pair. Never leave a contradictory completion marker behind.
        self.output_complete_path.unlink(missing_ok=True)
        stream_lsb = self.write_stream_lsb_summary()
        self.camera_controls_final = (
            self.source.control_snapshot()
            if self.source is not None and self.source.supports_controls
            else dict(self.camera_controls_periodic)
        )
        report = {
            "status": "failed",
            "timestamp_utc": utc_timestamp(),
            "app_version": APP_VERSION,
            "reason": reason,
            "state": self.state(),
            "source": self.source_manifest,
            "source_transport": {
                "connection_generation": self.source_connection_generation,
                "reconnect_count": self.source_reconnect_count,
                "events": self.source_reconnect_events,
                "last_reconnect_reason": str(getattr(self.source, "last_reconnect_reason", "")) if self.source else "",
                "frame_timeout_seconds": self.args.source_frame_timeout_seconds,
                "reconnect_attempts": self.args.source_reconnect_attempts,
                "allow_reconnect_during_production": self.args.allow_source_reconnect_during_production,
            },
            "requested_exposure": self.args.exposure_value,
            "controls_after_configure": self.camera_controls_after_configure,
            "controls_after_open_initial": self.camera_controls_after_open_initial,
            "controls_after_open": self.camera_controls_after_open,
            "controls_final": self.camera_controls_final,
            "target_output_bytes": self.args.max_output_bytes,
            "written_output_bytes": self.bit_writer.written_bytes,
            "accepted_output_bits": self.bit_writer.accepted_bits,
            "spatial_sampling": self.spatial_selection_status()["effective"],
            "spatial_sampling_legacy": self.args.spatial_sampling,
            "spatial_selection": self.spatial_selection_status(),
            "spatial_mask_pattern": self.args.spatial_mask_pattern,
            "pipeline": entropy_pipeline_name(self.args),
            "sample_mode": self.args.sample_mode,
            "lsb_bits": self.args.lsb_bits,
            "bit_order": BIT_ORDER,
            "entropy_credit_bits_per_pixel": self.args.entropy_credit_bits_per_pixel,
            "minimum_conditioner_input_bits": self.args.minimum_conditioner_input_bits,
            "mask_calibration_source": "temporal-xor-lsb0",
            "von_neumann_stage": self.args.von_neumann_stage,
            "von_neumann_passes": self.args.von_neumann_passes,
            "web_images": self.args.web_images,
            "mask_snapshot_images": self.args.mask_snapshot_images,
            "conditioner": self.conditioner.status(),
            "dual_weave": self.dual_weave.status(
                max(0.0, time.monotonic() - self.production_started_monotonic)
                if self.production_started_monotonic is not None else 0.0
            ),
            "active_clipping": self.active_clipping_status(),
            "camera_control_monitor": {
                "checks": self.control_checks,
                "mismatches": self.control_mismatches,
                "bad_streak": self.control_bad_streak,
                "latched": self.control_latched,
                "last_snapshot": self.camera_controls_periodic,
            },
            "rates": self.rates(),
            "mode": {
                "fourcc": self.fourcc,
                "width": self.width,
                "height": self.height,
                "reported_fps": self.fps,
                "measured_fps": self.measured_fps,
            },
            "pairing": self.pairing_status(),
            "active_pixels": int(self.calibrator.mask.sum())
            if self.calibrator and self.calibrator.mask is not None
            else 0,
            "health": self.health.status(),
            "stream_lsb_statistics": {
                "file": self.stream_lsb_summary_path.name,
                "aggregate_hmin_per_input_bit": stream_lsb.get("aggregate", {}).get("symbol_min_entropy_bits_per_input_bit"),
                "worst_window_hmin_per_input_bit": stream_lsb.get("worst_window_hmin_per_input_bit"),
                "window_count": stream_lsb.get("window_count"),
            },
            "shadow": (asdict(self.shadow.comparison) | {"bad_seen": self.shadow_bad_seen}) if self.shadow else {"bad_seen": self.shadow_bad_seen},
        }
        self.run_failed_path.write_text(
            json.dumps(json_safe(report), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )

    def update_rate_counters(
        self,
        now: float,
        frame_interval_s: float,
        raw_bits: int,
        masked_bits: int,
        vn_bits: int,
        packed_bytes: int,
    ) -> None:
        with self.lock:
            interval = max(float(frame_interval_s), 1e-9)
            raw_count = int(raw_bits)
            masked_count = int(masked_bits)
            self.rate_samples.append(
                (now, interval, raw_count, masked_count, int(vn_bits), int(packed_bytes))
            )
            self.total_raw_input_bits += raw_count
            self.total_masked_input_bits += masked_count
            cutoff = now - self.rate_history_seconds
            while self.rate_samples and self.rate_samples[0][0] < cutoff:
                self.rate_samples.popleft()

            instantaneous = vn_bits / interval
            alpha = 1.0 - math.exp(-interval / self.rate_ema_seconds)
            if self.vn_output_bps_ema == 0.0:
                self.vn_output_bps_ema = instantaneous
            else:
                self.vn_output_bps_ema += alpha * (instantaneous - self.vn_output_bps_ema)

    def rate_over_window(self, window_seconds: float, field_index: int) -> float:
        if not self.rate_samples:
            return 0.0
        now = self.rate_samples[-1][0]
        window_start = now - window_seconds
        weighted_total = 0.0
        covered_seconds = 0.0
        for sample in self.rate_samples:
            end = sample[0]
            interval = sample[1]
            start = end - interval
            overlap = max(0.0, min(end, now) - max(start, window_start))
            if overlap <= 0.0:
                continue
            fraction = overlap / interval
            weighted_total += sample[field_index] * fraction
            covered_seconds += overlap
        return weighted_total / covered_seconds if covered_seconds > 0.0 else 0.0

    def rates(self) -> dict[str, Any]:
        with self.lock:
            now = time.monotonic()
            last = self.last
            current_interval = last.frame_interval_s if last else 0.0
            current_raw = last.raw_change_bps if last else 0.0
            current_masked = last.masked_bps if last else 0.0
            current_output = last.vn_output_bps if last else 0.0
            production_seconds = (
                max(0.0, now - self.production_started_monotonic)
                if self.production_started_monotonic is not None
                else 0.0
            )
            lifetime_bps = (
                self.total_output_bits / production_seconds if production_seconds > 0.0 else 0.0
            )
            raw_lifetime_bps = (
                self.total_raw_input_bits / production_seconds if production_seconds > 0.0 else 0.0
            )
            masked_lifetime_bps = (
                self.total_masked_input_bits / production_seconds if production_seconds > 0.0 else 0.0
            )
            output_1s = self.rate_over_window(1.0, 4)
            output_10s = self.rate_over_window(10.0, 4)
            output_60s = self.rate_over_window(60.0, 4)
            bytes_10s = self.rate_over_window(10.0, 5)
            return {
                "frame_interval_seconds": current_interval,
                "raw_change_bps_current": current_raw,
                "masked_bps_current": current_masked,
                "output_bps_current": current_output,
                "output_bps_ema_10s": self.vn_output_bps_ema,
                "raw_change_bps_10s": self.rate_over_window(10.0, 2),
                "masked_bps_10s": self.rate_over_window(10.0, 3),
                "raw_change_bps_lifetime": raw_lifetime_bps,
                "masked_bps_lifetime": masked_lifetime_bps,
                "output_bps_1s": output_1s,
                "output_bps_10s": output_10s,
                "output_bps_60s": output_60s,
                "output_bps_lifetime": lifetime_bps,
                "packed_bytes_per_second_10s": bytes_10s,
                "projected_mib_per_day_10s": (output_10s / 8.0) * 86400.0 / (1024.0 * 1024.0),
                "source_uptime_seconds": max(0.0, now - self.started_monotonic),
                "production_uptime_seconds": production_seconds,
                "total_raw_input_bits": self.total_raw_input_bits,
                "total_masked_input_bits": self.total_masked_input_bits,
                "total_output_bits": self.total_output_bits,
                "total_packed_bytes": self.total_packed_bytes,
                "total_written_bytes": self.total_written_bytes,
                "write_output": self.args.write_output,
            }

    def files(self) -> dict[str, Optional[str]]:
        result: dict[str, Optional[str]] = {
            "output_bin": self.bin_path.name if self.args.write_output else None,
            "conditioned_output": self.conditioned_path.name if self.conditioner.enabled else None,
            "live_byte_heatmaps": self.byte_diagnostics.heatmap_path.name if self.byte_diagnostics.heatmap_path.exists() else None,
            "production_mask": self.production_mask_path.name if self.production_mask_path.exists() else None,
            "spatial_variant_metrics": self.spatial_variant_metrics_path.name,
            "output_index": self.index_path.name,
            "frame_metrics": self.frame_path.name,
            "health_events": self.health_path.name,
            "mask_drift": self.drift_path.name,
            "pixel_correlation": self.pixel_correlation_path.name,
            "pixel_correlation_summary": self.pixel_correlation_summary_path.name if self.pixel_correlation_summary_path.exists() else None,
            "direct_lsb_validation": self.direct_validation_path.name if self.direct_validation_path.exists() else None,
            "raw_temporal_validation": self.raw_temporal_validation_path.name if self.raw_temporal_validation_path.exists() else None,
            "masked_temporal_validation": self.masked_temporal_validation_path.name if self.masked_temporal_validation_path.exists() else None,
            "mask_snapshots": self.mask_snapshot_dir.name if self.mask_snapshot_dir.exists() else None,
            "mask_snapshot_index": self.mask_snapshot_index_path.name,
            "active_mask": self.active_mask_path.name if self.active_mask_path.exists() else None,
            "active_mask_epoch": self.active_mask_epoch_path.name if self.active_mask_epoch_path and self.active_mask_epoch_path.exists() else None,
            "shadow_mask": self.shadow_mask_path.name if self.shadow_mask_path.exists() else None,
            "mask_difference": self.mask_difference_path.name if self.mask_difference_path.exists() else None,
            "active_mask_report": self.active_report_path.name if self.active_report_path.exists() else None,
            "shadow_mask_report": self.shadow_report_path.name if self.shadow_report_path.exists() else None,
            "run_manifest": self.run_manifest_path.name if self.run_manifest_path.exists() else None,
            "output_complete": self.output_complete_path.name if self.output_complete_path.exists() else None,
            "run_failed": self.run_failed_path.name if self.run_failed_path.exists() else None,
            "log": self.args.log_file.name,
        }
        result.update(self.dual_weave.files())
        if self.spatial_comparison_enabled:
            for name, path in self.spatial_variant_paths.items():
                if name == "full":
                    continue
                key = name.replace("-", "_")
                result[f"output_{key}"] = path.name
                validation = self.spatial_variant_validation_paths.get(name)
                if validation is not None:
                    result[f"masked_{key}_validation"] = validation.name
                mask_name = f"active_mask_{key}.png"
                if (self.output_dir / mask_name).exists():
                    result[f"active_mask_{key}"] = mask_name
                corr = self.spatial_variant_correlation_paths.get(name)
                if corr is not None:
                    result[f"pixel_correlation_{key}"] = corr.name
        return result

    def status(self) -> dict[str, Any]:
        with self.lock:
            shadow_report = asdict(self.shadow.comparison) if self.shadow else {}
            shadow_report["bad_seen"] = self.shadow_bad_seen
            production_seconds = (
                max(0.0, time.monotonic() - self.production_started_monotonic)
                if self.production_started_monotonic is not None else 0.0
            )
            return {
                "app_version": APP_VERSION,
                "device": self.device,
                "source": self.source_manifest,
                "source_transport": {
                    "connection_generation": self.source_connection_generation,
                    "reconnect_count": self.source_reconnect_count,
                    "events": self.source_reconnect_events[-20:],
                    "frame_timeout_seconds": self.args.source_frame_timeout_seconds,
                    "reconnect_attempts": self.args.source_reconnect_attempts,
                    "allow_reconnect_during_production": self.args.allow_source_reconnect_during_production,
                },
                "state": self.state(),
                "error": self.error,
                "mode": {
                    "fourcc": self.fourcc,
                    "width": self.width,
                    "height": self.height,
                    "reported_fps": self.fps,
                    "measured_fps": self.measured_fps,
                },
                "pairing": self.pairing_status(),
                "camera_controls": {
                    "requested_exposure": self.args.exposure_value,
                    "after_configure": self.camera_controls_after_configure,
                    "after_open_initial": self.camera_controls_after_open_initial,
                    "after_open": self.camera_controls_after_open,
                    "final": self.camera_controls_final,
                    "periodic": self.camera_controls_periodic,
                    "checks": self.control_checks,
                    "mismatches": self.control_mismatches,
                    "bad_streak": self.control_bad_streak,
                    "latched": self.control_latched,
                },
                "active_clipping": self.active_clipping_status(),
                "warmup": {
                    "configured_seconds": self.args.thermal_warmup_seconds,
                    "complete": self.warmup_complete,
                    "remaining_seconds": self.warmup_remaining_seconds(),
                    "source_age_seconds": self.current_source_warmup_seconds(),
                    "basis": "source-reported" if self.source_warmup_seconds is not None else "worker-local",
                },
                "output_limit": {
                    "target_bytes": self.args.max_output_bytes,
                    "written_bytes": self.bit_writer.written_bytes,
                    "remaining_bytes": max(0, self.args.max_output_bytes - self.bit_writer.written_bytes)
                    if self.args.max_output_bytes
                    else None,
                    "complete": self.output_limit_reached,
                    "pending_bits": int(self.bit_writer.pending.size),
                },
                "conditioner": self.conditioner.status() | {
                    "bytes_this_pair": self.last_conditioned_bytes_this_pair,
                    "output_bps_lifetime": (
                        self.conditioner.written_bytes * 8.0
                        / max(1e-12, (time.monotonic() - self.production_started_monotonic))
                        if self.production_started_monotonic is not None else 0.0
                    ),
                },
                "dual_weave": self.dual_weave.status(production_seconds),
                "spatial_comparison": {
                    "enabled": self.spatial_comparison_enabled,
                    "variants": self.spatial_variants_status(),
                },
                "validation_output": {
                    "target_bytes_each": self.args.validation_output_bytes,
                    "direct_lsb_bytes": self.direct_validation_writer.written_bytes,
                    "raw_temporal_bytes": self.raw_temporal_validation_writer.written_bytes,
                    "masked_temporal_bytes": self.masked_temporal_validation_writer.written_bytes,
                },
                "byte_diagnostics": self.byte_diagnostics.snapshot(include_counts=False),
                "pixel_correlation": self.spatial_correlation.summary(),
                "calibration": self.calibrator.report() if self.calibrator else {},
                "active_mask": {
                    "epoch_id": self.mask_epoch_id,
                    "sha256": self.active_mask_sha256,
                    "pixels": int(self.calibrator.mask.sum()) if self.calibrator and self.calibrator.mask is not None else 0,
                    "production_pixels": int(np.count_nonzero(self.production_mask(self.calibrator.mask)))
                    if self.calibrator and self.calibrator.mask is not None else 0,
                    "spatial_sampling": self.spatial_selection_status()["effective"],
                    "spatial_sampling_legacy": self.args.spatial_sampling,
                    "spatial_selection": self.spatial_selection_status(),
                    "spatial_mask_pattern": self.args.spatial_mask_pattern,
                    "serialization_order": self.args.serialization_order,
                    "offset": [
                        self.args.temporal_spatial_offset_x,
                        self.args.temporal_spatial_offset_y,
                    ],
                },
                "shadow_mask": shadow_report,
                "mask_snapshot": {
                    "sequence": self.mask_snapshot_sequence,
                    "last_timestamp_utc": self.last_mask_snapshot_utc,
                    "interval_seconds": self.args.mask_snapshot_interval_seconds,
                    "history_points": len(self.mask_drift_history),
                    "last_sha256": self.last_mask_snapshot_sha256,
                },
                "mask_drift_history": list(self.mask_drift_history),
                "health": self.health.status() | {
                    "alpha": self.args.health_alpha,
                },
                "last": asdict(self.last) if self.last else None,
                "total_output_bits": self.total_output_bits,
                "rates": self.rates(),
                "command_line": self.args._command_line,
                "startup_parameters": self.args._startup_parameters,
                "files": self.files(),
                "settings": {
                    "mask_p1": [self.args.mask_p1_min, self.args.mask_p1_max],
                    "mask_transition": [
                        self.args.mask_transition_min,
                        self.args.mask_transition_max,
                    ],
                    "mask_clip_max": self.args.mask_clip_max,
                    "clip": [self.args.clip_low, self.args.clip_high],
                    "shadow_half_life_pairs": self.args.shadow_half_life_pairs,
                    "shadow_update_every": self.args.shadow_update_every,
                    "shadow_grace_pairs": self.args.shadow_grace_pairs,
                    "shadow_min_active_retention": self.args.shadow_min_active_retention,
                    "shadow_min_jaccard": self.args.shadow_min_jaccard,
                    "shadow_fail_consecutive": self.args.shadow_fail_consecutive,
                    "shadow_stop_on_drift": self.args.shadow_stop_on_drift,
                    "write_output": self.args.write_output,
                    "web_images": self.args.web_images,
                    "mask_snapshot_images": self.args.mask_snapshot_images,
                    "von_neumann_stage": self.args.von_neumann_stage,
            "von_neumann_passes": self.args.von_neumann_passes,
                    "pairing_mode": self.args.pairing_mode,
                    "pair_lag_frames": self.args.pair_lag_frames,
                    "spatial_sampling": self.spatial_selection_status()["effective"],
                    "spatial_sampling_legacy": self.args.spatial_sampling,
                    "spatial_selection": self.spatial_selection_status(),
                    "spatial_mask_pattern": self.args.spatial_mask_pattern,
                    "spatial_step_x": self.args.spatial_step_x,
                    "spatial_step_y": self.args.spatial_step_y,
                    "spatial_phase_x": self.args.spatial_phase_x,
                    "spatial_phase_y": self.args.spatial_phase_y,
                    "spatial_block_width": self.args.spatial_block_width,
                    "spatial_block_height": self.args.spatial_block_height,
                    "temporal_spatial_offset_x": self.args.temporal_spatial_offset_x,
                    "temporal_spatial_offset_y": self.args.temporal_spatial_offset_y,
                    "serialization_order": self.args.serialization_order,
                    "serialization_tile_width": self.args.serialization_tile_width,
                    "serialization_tile_height": self.args.serialization_tile_height,
                    "conditioner": self.args.conditioner,
                    "conditioner_input_bits": self.args.conditioner_input_bits,
                    "conditioned_output_bytes": self.args.conditioned_output_bytes,
                    "dynamic_clip_filter": self.args.dynamic_clip_filter,
                    "max_active_clip_rate": self.args.max_active_clip_rate,
                    "clip_fail_consecutive": self.args.clip_fail_consecutive,
                    "control_check_seconds": self.args.control_check_seconds,
                    "control_fail_consecutive": self.args.control_fail_consecutive,
                    "pair_buffer_frames": self.pair_scheduler.conceptual_buffer_frames,
                    "thermal_warmup_seconds": self.args.thermal_warmup_seconds,
                    "max_output_bytes": self.args.max_output_bytes,
                    "validation_output_bytes": self.args.validation_output_bytes,
                    "correlation_distances": list(self.args.correlation_distances),
                    "correlation_every": self.args.correlation_every,
                    "mask_snapshot_interval_seconds": self.args.mask_snapshot_interval_seconds,
                    "mask_drift_history_points": self.args.mask_drift_history_points,
                    "live_byte_diagnostics": self.args.live_byte_diagnostics,
                    "live_heatmap_interval_seconds": self.args.live_heatmap_interval_seconds,
                    "live_heatmap_max_stages": self.args.live_heatmap_max_stages,
                    "live_heatmap_min_bytes": self.args.live_heatmap_min_bytes,
                    "exit_on_output_limit": self.args.exit_on_output_limit,
                    "exit_on_failure": self.args.exit_on_failure,
                    "continue_output_after_health_failure": self.args.continue_output_after_health_failure,
                    "spatial_comparison": self.spatial_comparison_enabled,
                    "rate_ema_seconds": self.rate_ema_seconds,
                    "rate_history_seconds": self.rate_history_seconds,
                },
            }

    def verify_camera_controls(self, frame_id: int, now_monotonic: float) -> None:
        if self.source is None or not self.source.supports_controls:
            return
        interval = self.args.control_check_seconds
        if interval <= 0:
            return
        if now_monotonic - self.last_control_check_monotonic < interval:
            return
        self.last_control_check_monotonic = now_monotonic
        snapshot = self.source.control_snapshot()
        self.camera_controls_periodic = snapshot
        self.control_checks += 1
        problems: list[str] = []
        if self.args.manual_exposure:
            auto_exposure = control_integer(snapshot.get("auto_exposure"))
            exposure_absolute = control_integer(snapshot.get("exposure_time_absolute"))
            if auto_exposure != 1:
                problems.append(
                    f"auto_exposure={snapshot.get('auto_exposure')!r} "
                    f"normalized={auto_exposure!r} expected=1"
                )
            if (
                self.args.exposure_value is not None
                and exposure_absolute != self.args.exposure_value
            ):
                problems.append(
                    f"exposure_time_absolute={snapshot.get('exposure_time_absolute')!r} "
                    f"normalized={exposure_absolute!r} expected={self.args.exposure_value}"
                )
        if problems:
            self.control_mismatches += 1
            self.control_bad_streak += 1
            details = "; ".join(problems)
            self.write_health_event(utc_timestamp(), frame_id, "CAMERA_CONTROL", details)
            self.logger.error("Source control mismatch: %s", details)
            if self.control_bad_streak >= self.args.control_fail_consecutive:
                self.control_latched = True
                if self.args.control_stop_on_mismatch:
                    raise RuntimeError(f"source controls changed: {details}")
        else:
            self.control_bad_streak = 0

    def handle_source_reconnect(self, new_generation: int, received_now: float) -> None:
        old_generation = self.source_connection_generation
        if new_generation == old_generation:
            return
        self.source_connection_generation = new_generation
        self.source_reconnect_count = int(getattr(self.source, "reconnect_count", self.source_reconnect_count))
        reason = str(getattr(self.source, "last_reconnect_reason", "transport reconnect"))
        event = {
            "timestamp_utc": utc_timestamp(),
            "old_generation": old_generation,
            "new_generation": new_generation,
            "reconnect_count": self.source_reconnect_count,
            "reason": reason,
            "production_started": self.production_started_monotonic is not None,
        }
        self.source_reconnect_events.append(event)
        self.source_manifest = self.source.manifest() if self.source is not None else self.source_manifest
        if self.source is not None and self.source.supports_controls:
            self.camera_controls_after_open = self.source.control_snapshot()
            self.camera_controls_periodic = dict(self.camera_controls_after_open)

        if self.production_started_monotonic is not None and not self.args.allow_source_reconnect_during_production:
            raise RuntimeError(
                "TLS-Y source reconnected after production started; fail-closed policy requires a new run"
            )

        # Before production, discard all temporal/calibration state and begin a
        # fresh warm-up epoch.  No entropy from two transport/capture sessions is
        # paired together and no partially learned mask survives the reconnect.
        self.pair_scheduler.reset()
        self.pair_sequence = 0
        self.pair_delta_count = 0
        self.pair_delta_sum = 0.0
        self.pair_delta_min = math.inf
        self.pair_delta_max = 0.0
        self.measured_fps = 0.0
        self.last_read = received_now
        self.last_processed_monotonic = None
        self.calibrator = FrozenPixelCalibrator(
            (self.height, self.width), self.args.calibration_pairs, self.args
        )
        self.shadow = None
        self.begin_warmup_epoch(received_now)
        self.log_warmup_basis("Reconnect")
        self.logger.warning(
            "Source reconnect generation %d -> %d before production; warm-up and calibration restarted (%s)",
            old_generation, new_generation, reason,
        )
        self.write_run_manifest()

    def run(self) -> None:
        try:
            self.source = create_frame_source(self.args, self.logger)
            self.source.open()
            self.source_manifest = self.source.manifest()
            if not bool(self.source_manifest.get("has_full_luma", True)):
                self.logger.warning(
                    "Source contains packed LSB only. Temporal/LSB processing is exact, but "
                    "full-luminance clipping diagnostics use neutral 128/129 reconstruction."
                )
            self.source_connection_generation = int(getattr(self.source, "connection_generation", 0))
            self.source_reconnect_count = int(getattr(self.source, "reconnect_count", 0))
            self.device = self.source.label
            self.fourcc = self.source.pixel_format
            self.width = self.source.width
            self.height = self.source.height
            self.fps = self.source.reported_fps
            initial_controls = self.source.control_snapshot()
            self.camera_controls_after_configure = dict(initial_controls)
            self.camera_controls_after_open_initial = dict(initial_controls)
            self.camera_controls_after_open = dict(initial_controls)
            self.camera_controls_periodic = dict(initial_controls)
            self.logger.info(
                "Source type=%s label=%s mode=%s %dx%d @ %.3f timestamp=%s",
                self.source.source_type, self.device, self.fourcc, self.width, self.height,
                self.fps, self.source.timestamp_basis,
            )
            self.write_run_manifest()
            self.calibrator = FrozenPixelCalibrator(
                (self.height, self.width), self.args.calibration_pairs, self.args
            )
            self.last_read = time.monotonic()
            self.begin_warmup_epoch(self.last_read)
            self.log_warmup_basis("Initial source")
            while not self.stop_event.is_set():
                source_frame = self.source.read()
                received_now = time.monotonic()
                frame_generation = int(
                    source_frame.metadata.get(
                        "source_connection_generation",
                        getattr(self.source, "connection_generation", self.source_connection_generation),
                    )
                )
                if frame_generation != self.source_connection_generation:
                    self.handle_source_reconnect(frame_generation, received_now)
                delta = received_now - self.last_read
                self.last_read = received_now
                if delta > 0:
                    instant = 1.0 / delta
                    self.measured_fps = (
                        instant
                        if not self.measured_fps
                        else 0.9 * self.measured_fps + 0.1 * instant
                    )
                self.verify_camera_controls(source_frame.frame_id, received_now)

                if not self.warmup_complete:
                    reported_warmup = source_frame.metadata.get("source_warmup_seconds")
                    if reported_warmup is not None:
                        observed_warmup = self.record_source_warmup(reported_warmup, received_now)
                        if observed_warmup is None:
                            raise RuntimeError(
                                f"invalid source_warmup_seconds metadata: {reported_warmup!r}"
                            )
                    else:
                        observed_warmup = self.current_source_warmup_seconds(received_now)

                    if observed_warmup is not None:
                        if observed_warmup < self.args.thermal_warmup_seconds:
                            continue
                    else:
                        if received_now < (self.warmup_deadline_monotonic or received_now):
                            continue
                        observed_warmup = self.args.thermal_warmup_seconds
                    self.warmup_complete = True
                    self.pair_scheduler.reset()
                    self.last_processed_monotonic = None
                    self.logger.info(
                        "Thermal warm-up complete; source age %.3f seconds, required %.3f seconds; calibration begins",
                        observed_warmup,
                        self.args.thermal_warmup_seconds,
                    )

                captured = CapturedYFrame(
                    frame_id=source_frame.frame_id,
                    timestamp_monotonic=source_frame.timestamp_monotonic,
                    y=source_frame.y,
                    yuyv=source_frame.yuyv,
                )
                pair = self.pair_scheduler.push(captured)
                if pair is not None:
                    self.process_pair(pair[0], pair[1])
        except EOFError as exc:
            if self.source is not None and self.source.source_type == "dataset-y":
                self.logger.info("Dataset exhausted normally: %s", exc)
                try:
                    self.write_correlation_summary()
                    self.write_output_complete_report("dataset-exhausted")
                except Exception:
                    self.logger.exception("cannot finalize dataset-exhausted report")
                    raise
                self.request_process_exit("dataset exhausted")
                return
            if self.stop_event.is_set():
                self.logger.info("source loop stopped during service shutdown: %s", exc)
                return
            self.error = f"{type(exc).__name__}: {exc}"
            self.logger.error("LIVE source closed unexpectedly", exc_info=True)
            try:
                self.write_failure_report(self.error)
            except Exception:
                self.logger.exception("cannot write failure report")
            if self.args.exit_on_failure:
                self.request_process_exit(self.error)
        except Exception as exc:
            if self.stop_event.is_set():
                self.logger.info("source loop stopped during service shutdown: %s", exc)
                return
            self.error = f"{type(exc).__name__}: {exc}"
            self.logger.exception("source stopped")
            try:
                self.write_failure_report(self.error)
            except Exception:
                self.logger.exception("cannot write failure report")
            if self.args.exit_on_failure:
                self.request_process_exit(self.error)

    def initialize_masks(self) -> None:
        assert self.calibrator
        assert self.calibrator.mask is not None
        assert self.calibrator.reason_code is not None
        assert self.calibrator.p1_rate is not None
        assert self.calibrator.transition_rate is not None
        assert self.calibrator.clip_rate is not None
        assert self.calibrator.previous_change is not None

        packed_mask = np.packbits(self.calibrator.mask.reshape(-1), bitorder="big").tobytes()
        self.active_mask_sha256 = hashlib.sha256(packed_mask).hexdigest()
        self.mask_epoch_id = f"{epoch_timestamp()}-{self.active_mask_sha256[:12]}"
        self.active_mask_epoch_path = self.output_dir / f"active_mask_{self.mask_epoch_id}.png"

        production_mask = self.production_mask(self.calibrator.mask)
        if self.args.mask_snapshot_images:
            active_image = self.calibrator.mask.astype(np.uint8) * 255
            cv2.imwrite(str(self.active_mask_path), active_image)
            cv2.imwrite(str(self.active_mask_epoch_path), active_image)
            if production_mask is not None:
                cv2.imwrite(str(self.production_mask_path), production_mask.astype(np.uint8) * 255)
            if self.spatial_comparison_enabled:
                patterns = self.build_spatial_patterns(self.calibrator.mask.shape)
                for variant in SPATIAL_COMPARISON_VARIANTS[1:]:
                    variant_mask = self.calibrator.mask & patterns[variant]
                    file_variant = variant.replace("-", "_")
                    cv2.imwrite(
                        str(self.output_dir / f"active_mask_{file_variant}.png"),
                        variant_mask.astype(np.uint8) * 255,
                    )

        reasons = self.calibrator.reason_code
        report = self.calibrator.report() | {
            "epoch_id": self.mask_epoch_id,
            "sha256": self.active_mask_sha256,
            "rejected_p1": int(np.count_nonzero(reasons & 1)),
            "rejected_transition": int(np.count_nonzero(reasons & 2)),
            "rejected_clipping": int(np.count_nonzero(reasons & 4)),
            "pairing": self.pairing_status(),
            "spatial_sampling": self.spatial_selection_status()["effective"],
            "spatial_sampling_legacy": self.args.spatial_sampling,
            "spatial_selection": self.spatial_selection_status(),
            "production_pixels": int(np.count_nonzero(production_mask)) if production_mask is not None else 0,
            "dynamic_clip_filter": self.args.dynamic_clip_filter,
            "max_active_clip_rate": self.args.max_active_clip_rate,
            "criteria": {
                "p1": [self.args.mask_p1_min, self.args.mask_p1_max],
                "transition": [
                    self.args.mask_transition_min,
                    self.args.mask_transition_max,
                ],
                "clip_max": self.args.mask_clip_max,
            },
        }
        self.active_report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        self.shadow = ShadowPixelMonitor(
            active_mask=self.calibrator.mask,
            initial_p1=self.calibrator.p1_rate,
            initial_transition=self.calibrator.transition_rate,
            initial_clip=self.calibrator.clip_rate,
            previous_change=self.calibrator.previous_change,
            args=self.args,
        )
        self.save_shadow_artifacts()
        self.logger.info(
            "Active mask frozen: epoch=%s sha256=%s pixels=%d",
            self.mask_epoch_id,
            self.active_mask_sha256,
            int(self.calibrator.mask.sum()),
        )

    def write_correlation_summary(self) -> None:
        for variant, accumulator in self.spatial_variant_correlations.items():
            report = {
                "app_version": APP_VERSION,
                "timestamp_utc": utc_timestamp(),
                "variant": variant,
                "samples": accumulator.samples,
                "distances": list(accumulator.distances),
                "summary": accumulator.summary(),
                "rows": accumulator.rows(),
            }
            self.spatial_variant_correlation_summary_paths[variant].write_text(
                json.dumps(json_safe(report), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
            )

    def archive_mask_snapshot(self, timestamp: str, frame_id: int) -> None:
        """Record periodic drift metrics and archive PNGs only when the mask changes."""
        if not self.shadow or self.args.mask_snapshot_interval_seconds <= 0.0:
            return
        now = time.monotonic()
        if now - self.last_mask_snapshot_monotonic < self.args.mask_snapshot_interval_seconds:
            return

        self.last_mask_snapshot_monotonic = now
        self.last_mask_snapshot_utc = timestamp
        self.mask_snapshot_sequence += 1

        shadow_bytes = np.packbits(self.shadow.mask.reshape(-1), bitorder="big").tobytes()
        digest = hashlib.sha256(shadow_bytes).hexdigest()
        image_changed = digest != self.last_mask_snapshot_sha256
        base = ""
        comparison = self.shadow.comparison

        if image_changed:
            self.last_mask_snapshot_sha256 = digest
            if self.args.mask_snapshot_images:
                self.mask_snapshot_dir.mkdir(parents=True, exist_ok=True)
                safe_time = re.sub(r"[^0-9A-Za-z]+", "", timestamp)[:24]
                base = f"{self.mask_snapshot_sequence:05d}_{safe_time}_frame_{frame_id}"
                shadow_path = self.mask_snapshot_dir / f"{base}_shadow.png"
                difference_path = self.mask_snapshot_dir / f"{base}_difference.png"
                metadata_path = self.mask_snapshot_dir / f"{base}_metadata.json"
                cv2.imwrite(str(shadow_path), self.shadow.mask.astype(np.uint8) * 255)
                cv2.imwrite(
                    str(difference_path),
                    self.build_mask_difference(self.shadow.active_mask, self.shadow.mask),
                )
                metadata = {
                    "tool_version": APP_VERSION,
                    "captured_utc": utc_timestamp(),
                    "timestamp_utc": timestamp,
                    "frame_id": frame_id,
                    "exposure": self.args.exposure_value,
                    "sequence": self.mask_snapshot_sequence,
                    "sha256": digest,
                    "image_changed": True,
                    "base": base,
                    "mask_metrics": asdict(comparison),
                }
                metadata_path.write_text(
                    json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
                )

        point = {
            "timestamp_utc": timestamp,
            "frame_id": int(frame_id),
            "sequence": int(self.mask_snapshot_sequence),
            "shadow_updates": int(self.shadow.updates),
            "active_pixels": int(comparison.active_pixels),
            "shadow_pixels": int(comparison.shadow_pixels),
            "active_retention": float(comparison.active_retention),
            "jaccard": float(comparison.jaccard),
            "disagreement_rate": float(comparison.disagreement_rate),
            "bad_streak": int(comparison.bad_streak),
            "result": ("GRACE" if comparison.grace_remaining else ("FAIL" if comparison.bad else "PASS")),
            "image_changed": bool(image_changed),
        }
        self.mask_drift_history.append(point)
        self.mask_snapshot_writer.writerow({
            "timestamp_utc": timestamp,
            "frame_id": frame_id,
            "shadow_updates": self.shadow.updates,
            "sequence": self.mask_snapshot_sequence,
            "sha256": digest,
            "image_changed": int(image_changed),
            "base_name": base,
            "active_pixels": comparison.active_pixels,
            "shadow_pixels": comparison.shadow_pixels,
            "active_retention": comparison.active_retention,
            "jaccard": comparison.jaccard,
            "disagreement_rate": comparison.disagreement_rate,
            "bad_streak": comparison.bad_streak,
            "result": ("GRACE" if comparison.grace_remaining else ("FAIL" if comparison.bad else "PASS")),
        })
        self.mask_snapshot_csv.flush()

    def save_shadow_artifacts(self, timestamp: Optional[str] = None, frame_id: int = 0) -> None:
        if not self.shadow:
            return
        active = self.shadow.active_mask
        shadow = self.shadow.mask
        if self.args.mask_snapshot_images:
            cv2.imwrite(str(self.shadow_mask_path), shadow.astype(np.uint8) * 255)
            difference = self.build_mask_difference(active, shadow)
            cv2.imwrite(str(self.mask_difference_path), difference)
        report = asdict(self.shadow.comparison) | {
            "active_epoch_id": self.mask_epoch_id,
            "active_mask_sha256": self.active_mask_sha256,
            "ewma_alpha": self.shadow.alpha,
            "half_life_pairs": self.args.shadow_half_life_pairs,
            "thresholds": {
                "minimum_active_retention": self.args.shadow_min_active_retention,
                "minimum_jaccard": self.args.shadow_min_jaccard,
                "consecutive_failures": self.args.shadow_fail_consecutive,
            },
        }
        self.shadow_report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        self.archive_mask_snapshot(timestamp or utc_timestamp(), frame_id)

    @staticmethod
    def build_mask_difference(active: np.ndarray, shadow: np.ndarray) -> np.ndarray:
        image = np.zeros((active.shape[0], active.shape[1], 3), dtype=np.uint8)
        both = active & shadow
        active_only = active & ~shadow
        shadow_only = ~active & shadow
        image[both] = (255, 255, 255)      # white: accepted by both
        image[active_only] = (0, 0, 255)   # red: active pixel now rejected by shadow
        image[shadow_only] = (255, 0, 0)   # blue: new candidate visible only in shadow
        return image

    def write_health_event(self, timestamp: str, frame_id: int, test: str, details: str) -> None:
        self.health_writer.writerow(
            {
                "timestamp_utc": timestamp,
                "frame_id": frame_id,
                "test": test,
                "result": "FAIL",
                "details": details,
                "sample_domain": self.health.status()["sample_domain"],
                "sample_width_bits": self.health.sample_width_bits,
                "alphabet_size": self.health.alphabet_size,
                "rct_cutoff": self.health.rct_cutoff,
                "apt_window": self.health.apt_window,
                "apt_cutoff": self.health.apt_cutoff,
                "assessed_min_entropy_bits_per_symbol": self.args.assessed_min_entropy,
                "assessed_min_entropy": self.args.assessed_min_entropy,
                "alpha": self.args.health_alpha,
            }
        )
        self.health_csv.flush()

    def write_drift_event(self, timestamp: str, frame_id: int, comparison: MaskComparison) -> None:
        result = "GRACE" if comparison.grace_remaining else ("FAIL" if comparison.bad else "PASS")
        self.drift_writer.writerow(
            {
                "timestamp_utc": timestamp,
                "frame_id": frame_id,
                "active_pixels": comparison.active_pixels,
                "shadow_pixels": comparison.shadow_pixels,
                "overlap_pixels": comparison.overlap_pixels,
                "union_pixels": comparison.union_pixels,
                "active_retention": comparison.active_retention,
                "shadow_retention": comparison.shadow_retention,
                "jaccard": comparison.jaccard,
                "disagreement_rate": comparison.disagreement_rate,
                "shadow_updates": comparison.updates,
                "grace_remaining": comparison.grace_remaining,
                "bad_streak": comparison.bad_streak,
                "result": result,
                "latched": comparison.latched,
                "details": comparison.details,
            }
        )
        self.drift_csv.flush()

    def process_pair(self, previous: CapturedYFrame, current: CapturedYFrame) -> None:
        started = time.perf_counter()
        now_monotonic = time.monotonic()
        frame_interval_s = (
            now_monotonic - self.last_processed_monotonic
            if self.last_processed_monotonic is not None
            else (1.0 / self.fps if self.fps > 0.0 else 0.0)
        )
        self.last_processed_monotonic = now_monotonic
        timestamp = utc_timestamp()
        frame_id = current.frame_id
        y = current.y
        yuyv = current.yuyv
        previous_y = previous.y
        pair_delta_seconds = max(0.0, current.timestamp_monotonic - previous.timestamp_monotonic)
        self.pair_sequence += 1
        self.pair_delta_count += 1
        self.pair_delta_sum += pair_delta_seconds
        self.pair_delta_min = min(self.pair_delta_min, pair_delta_seconds)
        self.pair_delta_max = max(self.pair_delta_max, pair_delta_seconds)

        aligned_previous_y, spatial_valid_mask = align_previous_frame(
            previous_y,
            dx=self.args.temporal_spatial_offset_x,
            dy=self.args.temporal_spatial_offset_y,
            invalid_fill=0,
        )
        change = spatial_xor_lsb(y, aligned_previous_y)
        assert self.calibrator
        was_ready = self.calibrator.ready
        self.calibrator.update(change, y, aligned_previous_y)
        just_frozen = not was_ready and self.calibrator.ready
        if just_frozen:
            self.initialize_masks()

        active_mask = self.calibrator.mask
        shadow_mask = self.shadow.mask if self.shadow else None
        comparison = self.shadow.comparison if self.shadow else MaskComparison()
        just_drift_latched = False

        if self.shadow and not just_frozen:
            previous_updates = self.shadow.updates
            comparison, just_drift_latched = self.shadow.update(change, y, aligned_previous_y)
            shadow_mask = self.shadow.mask
            if comparison.bad:
                self.shadow_bad_seen = True
            if self.shadow.updates != previous_updates and self.shadow.updates % self.args.shadow_update_every == 0:
                self.write_drift_event(timestamp, frame_id, comparison)
                self.save_shadow_artifacts(timestamp, frame_id)
            if just_drift_latched:
                self.write_health_event(timestamp, frame_id, "MASK_DRIFT", comparison.details)
                self.logger.error("Mask drift latched: %s", comparison.details)

        raw_bits = serialize_samples(
            y,
            aligned_previous_y,
            self.spatial_serializer,
            spatial_valid_mask,
            self.args.sample_mode,
            self.args.lsb_bits,
        )
        raw_ones = int(raw_bits.sum())
        masked = np.empty(0, dtype=np.uint8)
        von_neumann = np.empty(0, dtype=np.uint8)
        output_bytes_count = 0
        output_offset = self.bit_writer.written_bytes
        pending_bits_before = int(self.bit_writer.pending.size)
        pending_bits_after = pending_bits_before
        just_completed = False
        failure_reason: Optional[str] = None

        if just_drift_latched:
            failure_reason = f"MASK_DRIFT: {comparison.details}"

        if active_mask is not None and not just_frozen and self.production_started_monotonic is None:
            # The pair that freezes the active mask belongs to calibration and is discarded.
            self.production_started_monotonic = now_monotonic

        health_allows_output = (
            not self.health.latched or self.args.continue_output_after_health_failure
        )
        if (
            active_mask is not None
            and not just_frozen
            and health_allows_output
            and not (self.shadow and self.shadow.latched)
        ):
            current_ok = (
                (y > self.args.clip_low)
                & (y < self.args.clip_high)
                & (aligned_previous_y > self.args.clip_low)
                & (aligned_previous_y < self.args.clip_high)
                & spatial_valid_mask
            )
            clip_pair_bad, clip_failure = self.update_active_clipping(
                active_mask, current_ok, timestamp, frame_id
            )
            if clip_failure:
                failure_reason = clip_failure

            # Production uses a frozen sampling map. Dynamic per-pair filtering is
            # retained only as an explicit comparison/compatibility option.
            effective_active = active_mask & current_ok if self.args.dynamic_clip_filter else active_mask
            variant_masks = self.spatial_variant_masks(effective_active)
            variant_symbols = {
                name: serialize_sample_symbols(
                    y,
                    aligned_previous_y,
                    self.spatial_serializer,
                    mask,
                    self.args.sample_mode,
                    self.args.lsb_bits,
                )
                for name, mask in variant_masks.items()
            }
            variant_bits = {
                name: serialize_symbol_bits(symbols, self.args.lsb_bits)
                for name, symbols in variant_symbols.items()
            }
            masked = variant_bits["full"]
            primary_mask = variant_masks["full"]
            direct_masked = self.spatial_serializer.serialize(y & 1, primary_mask)

            # Raw temporal validation intentionally includes all post-calibration
            # pairs. Selected validation/correlation excludes pairs rejected by
            # the active-clipping health monitor, matching production output.
            self.raw_temporal_validation_writer.write_bits(raw_bits)
            self.byte_diagnostics.observe_bits(
                "temporal_raw", sample_label(self.args.sample_mode, self.args.lsb_bits) + " — pełna mapa", 10, raw_bits, "main"
            )
            if not clip_pair_bad and not self.clip_latched:
                self.direct_validation_writer.write_bits(direct_masked)
                self.byte_diagnostics.observe_bits(
                    "direct_lsb", "Direct LSB — aktywna maska", 0, direct_masked, "main"
                )
                self.byte_diagnostics.observe_bits(
                    "temporal_masked", sample_label(self.args.sample_mode, self.args.lsb_bits) + " — aktywna maska", 20, masked, "main"
                )
                for variant, bits_for_variant in variant_bits.items():
                    self.spatial_variant_validation_writers[variant].write_bits(bits_for_variant)

                self.production_pair_count += 1
                if self.production_pair_count % self.args.correlation_every == 0:
                    for variant, mask_for_variant in variant_masks.items():
                        self.spatial_variant_correlations[variant].update(
                            change,
                            mask_for_variant,
                            timestamp,
                            frame_id,
                            self.spatial_variant_correlation_writers[variant],
                        )
                        self.spatial_variant_correlation_files[variant].flush()

            dual_failure = self.dual_weave.consume(
                change,
                effective_active,
                not clip_pair_bad and not self.clip_latched,
                timestamp,
                frame_id,
                self.pair_sequence,
                self.write_health_event,
            )
            if dual_failure and not self.args.continue_output_after_health_failure:
                failure_reason = dual_failure

            for variant, bits_for_variant in variant_bits.items():
                symbols_for_variant = variant_symbols[variant]
                health = self.spatial_variant_health[variant]
                if clip_pair_bad or self.clip_latched:
                    continue
                for test, details in health.consume(symbols_for_variant):
                    self.write_health_event(
                        timestamp, frame_id, f"{test}:{variant}", details
                    )
                    self.logger.error(
                        "Health test latched for %s: %s", variant, details
                    )
                    if not self.args.continue_output_after_health_failure:
                        failure_reason = f"{test}:{variant}: {details}"

                if variant == "full" and not health.latched:
                    self.stream_lsb_statistics.observe(symbols_for_variant, frame_id, timestamp)

                if variant == "full" and self.conditioner.enabled and not health.latched:
                    conditioner_before = self.conditioner.written_bytes
                    conditioner_status = self.conditioner.write_bits(bits_for_variant)
                    self.last_conditioned_bytes_this_pair = (
                        self.conditioner.written_bytes - conditioner_before
                    )
                    if bool(conditioner_status.get("latched")):
                        details = str(conditioner_status.get("last_failure") or "conditioner failure")
                        self.write_health_event(timestamp, frame_id, "CONDITIONER", details)
                        self.logger.error("Conditioner latched: %s", details)
                        failure_reason = f"CONDITIONER: {details}"

                writer = self.spatial_variant_writers[variant]
                generated = np.empty(0, dtype=np.uint8)
                accepted = np.empty(0, dtype=np.uint8)
                bytes_this_pair = 0
                before_pending = int(writer.pending.size)
                after_pending = before_pending
                vn_pass_metrics: list[dict[str, float | int]] = []
                if self.args.von_neumann_stage and (not health.latched or self.args.continue_output_after_health_failure):
                    generated, vn_pass_metrics = repeated_von_neumann(
                        bits_for_variant, self.args.von_neumann_passes
                    )
                    if generated.size:
                        accepted_count, bytes_this_pair, after_pending, _ = writer.write_bits(generated)
                        accepted = generated[:accepted_count]
                        if variant == "full" and accepted.size:
                            self.byte_diagnostics.observe_bits(
                                "von_neumann", "Von Neumann — wyjście", 30, accepted, "main"
                            )

                selected_ones = int(bits_for_variant.sum()) if bits_for_variant.size else 0
                self.spatial_variant_last[variant] = {
                    "selected_symbols": int(symbols_for_variant.size),
                    "sample_width_bits": self.args.lsb_bits,
                    "selected_bits": int(bits_for_variant.size),
                    "selected_ones": selected_ones,
                    "selected_p1": selected_ones / bits_for_variant.size if bits_for_variant.size else 0.0,
                    "vn_output_bits": int(accepted.size),
                    "vn_efficiency": accepted.size / bits_for_variant.size if bits_for_variant.size else 0.0,
                    "vn_passes": self.args.von_neumann_passes,
                    "vn_pass_metrics": vn_pass_metrics if self.args.von_neumann_stage else [],
                    "bytes_written_this_pair": bytes_this_pair,
                    "written_bytes": writer.written_bytes,
                    "complete": writer.completed,
                }
                self.spatial_variant_metrics_writer.writerow({
                    "timestamp_utc": timestamp,
                    "frame_id": frame_id,
                    "pair_sequence": self.pair_sequence,
                    "variant": variant,
                    "selected_bits": int(bits_for_variant.size),
                    "selected_ones": selected_ones,
                    "selected_p1": selected_ones / bits_for_variant.size if bits_for_variant.size else 0.0,
                    "vn_output_bits": int(accepted.size),
                    "vn_efficiency": accepted.size / bits_for_variant.size if bits_for_variant.size else 0.0,
                    "bytes_written_this_pair": bytes_this_pair,
                    "total_written_bytes": writer.written_bytes,
                    "target_bytes": self.args.max_output_bytes,
                    "complete": int(writer.completed),
                    "rct_failures": health.rct_failures,
                    "apt_failures": health.apt_failures,
                    "health_latched": int(health.latched),
                })

                if variant == "full":
                    output_offset = max(0, writer.written_bytes - bytes_this_pair)
                    output_bytes_count = bytes_this_pair
                    pending_bits_after = after_pending
                    von_neumann = accepted
                    just_completed = writer.completed
                    if accepted.size:
                        self.index_writer.writerow({
                            "timestamp_utc": timestamp,
                            "frame_id": frame_id,
                            "pair_sequence": self.pair_sequence,
                            "pairing_mode": self.args.pairing_mode,
                            "pair_lag_frames": self.args.pair_lag_frames,
                            "previous_frame_id": previous.frame_id,
                            "pair_delta_seconds": pair_delta_seconds,
                            "offset_bytes": output_offset,
                            "length_bytes": output_bytes_count,
                            "valid_bits": int(accepted.size),
                            "source_input_bits": int(bits_for_variant.size),
                            "pending_bits_before": before_pending,
                            "pending_bits_after": after_pending,
                            "mask_epoch_id": self.mask_epoch_id,
                            "active_mask_sha256": self.active_mask_sha256,
                            "state": self.state(),
                        })
                        self.index_csv.flush()
            self.spatial_variant_metrics_csv.flush()

        self.total_output_bits = self.bit_writer.accepted_bits
        self.total_packed_bytes = self.bit_writer.written_bytes
        self.total_written_bytes = self.bit_writer.written_bytes if self.args.write_output else 0

        self.update_rate_counters(
            now_monotonic,
            frame_interval_s,
            int(raw_bits.size),
            int(masked.size),
            int(von_neumann.size),
            output_bytes_count,
        )
        rates = self.rates()
        masked_ones = int(masked.sum()) if masked.size else 0
        last = LastFrame(
            timestamp_utc=timestamp,
            frame_id=frame_id,
            state=self.state(),
            pair_sequence=self.pair_sequence,
            pairing_mode=self.args.pairing_mode,
            pair_lag_frames=self.args.pair_lag_frames,
            pair_buffer_frames=self.pair_scheduler.conceptual_buffer_frames,
            previous_frame_id=previous.frame_id,
            pair_delta_seconds=pair_delta_seconds,
            pair_buffer_fill=self.pair_scheduler.buffered_frames,
            raw_change_bits=int(raw_bits.size),
            raw_change_ones=raw_ones,
            raw_change_p1=raw_ones / raw_bits.size if raw_bits.size else 0.0,
            masked_bits=int(masked.size),
            masked_ones=masked_ones,
            masked_p1=masked_ones / masked.size if masked.size else 0.0,
            vn_output_bits=int(von_neumann.size),
            vn_efficiency=von_neumann.size / masked.size if masked.size else 0.0,
            output_bytes=output_bytes_count,
            frame_interval_s=frame_interval_s,
            raw_change_bps=int(raw_bits.size) / frame_interval_s if frame_interval_s > 0.0 else 0.0,
            masked_bps=int(masked.size) / frame_interval_s if frame_interval_s > 0.0 else 0.0,
            vn_output_bps=int(von_neumann.size) / frame_interval_s if frame_interval_s > 0.0 else 0.0,
            vn_output_bps_ema=self.vn_output_bps_ema,
            vn_output_bps_1s=rates["output_bps_1s"],
            vn_output_bps_10s=rates["output_bps_10s"],
            vn_output_bps_60s=rates["output_bps_60s"],
            vn_output_bps_lifetime=rates["output_bps_lifetime"],
            packed_bytes_per_second_10s=rates["packed_bytes_per_second_10s"],
            projected_mib_per_day_10s=rates["projected_mib_per_day_10s"],
            processing_ms=(time.perf_counter() - started) * 1000.0,
        )

        calibration = self.calibrator.report()
        comparison = self.shadow.comparison if self.shadow else MaskComparison()
        self.frame_writer.writerow(
            {
                "timestamp_utc": timestamp,
                "frame_id": frame_id,
                "state": last.state,
                "pair_sequence": last.pair_sequence,
                "pairing_mode": last.pairing_mode,
                "pair_lag_frames": last.pair_lag_frames,
                "pair_buffer_frames": last.pair_buffer_frames,
                "previous_frame_id": last.previous_frame_id,
                "pair_delta_seconds": last.pair_delta_seconds,
                "pair_buffer_fill": last.pair_buffer_fill,
                "width": self.width,
                "height": self.height,
                "camera_fps": self.measured_fps,
                "calibration_pairs": calibration["pairs"],
                "calibration_target": calibration["target"],
                "active_pixels": comparison.active_pixels or calibration["eligible_pixels"],
                "active_rate": (comparison.active_pixels or calibration["eligible_pixels"]) / (self.width * self.height),
                "shadow_pixels": comparison.shadow_pixels,
                "shadow_rate": comparison.shadow_pixels / (self.width * self.height),
                "mask_overlap_pixels": comparison.overlap_pixels,
                "mask_union_pixels": comparison.union_pixels,
                "active_retention": comparison.active_retention,
                "shadow_retention": comparison.shadow_retention,
                "mask_jaccard": comparison.jaccard,
                "mask_disagreement_rate": comparison.disagreement_rate,
                "shadow_updates": comparison.updates,
                "shadow_grace_remaining": comparison.grace_remaining,
                "drift_bad_streak": comparison.bad_streak,
                "drift_latched": comparison.latched,
                "raw_change_bits": last.raw_change_bits,
                "raw_change_ones": last.raw_change_ones,
                "raw_change_p1": last.raw_change_p1,
                "masked_bits": last.masked_bits,
                "masked_ones": last.masked_ones,
                "masked_p1": last.masked_p1,
                "vn_input_bits": last.masked_bits,
                "vn_output_bits": last.vn_output_bits,
                "vn_efficiency": last.vn_efficiency,
                "frame_interval_s": last.frame_interval_s,
                "raw_change_bps": last.raw_change_bps,
                "masked_bps": last.masked_bps,
                "vn_output_bps": last.vn_output_bps,
                "vn_output_bps_ema": last.vn_output_bps_ema,
                "vn_output_bps_1s": last.vn_output_bps_1s,
                "vn_output_bps_10s": last.vn_output_bps_10s,
                "vn_output_bps_60s": last.vn_output_bps_60s,
                "vn_output_bps_lifetime": last.vn_output_bps_lifetime,
                "packed_bytes_per_second_10s": last.packed_bytes_per_second_10s,
                "projected_mib_per_day_10s": last.projected_mib_per_day_10s,
                "output_offset_bytes": output_offset,
                "output_bytes": last.output_bytes,
                "rct_failures": self.health.rct_failures,
                "apt_failures": self.health.apt_failures,
                "health_latched": self.health.latched,
                "active_clip_pixels": self.active_clip_pixels,
                "active_clip_rate": self.active_clip_rate,
                "clip_bad_streak": self.clip_bad_streak,
                "clip_latched": self.clip_latched,
                "control_checks": self.control_checks,
                "control_mismatches": self.control_mismatches,
                "control_latched": self.control_latched,
                "processing_ms": last.processing_ms,
            }
        )
        self.frame_csv.flush()

        self.byte_diagnostics.maybe_render_heatmap(now_monotonic)
        self.publish_images(
            y, yuyv, change, self.production_mask(active_mask),
            self.production_mask(shadow_mask),
        )
        with self.lock:
            self.last = last

        # Fail closed takes precedence over a size target reached in the same
        # frame pair. A run must never be marked complete when a health/control
        # failure was latched concurrently with the final output block.
        if failure_reason and self.args.exit_on_failure:
            self.write_failure_report(failure_reason)
            self.request_process_exit(failure_reason)
            return

        if self.all_output_writers_complete() and not self.output_limit_reached:
            self.output_limit_reached = True
            targets = {name: writer.written_bytes for name, writer in self.spatial_variant_writers.items()}
            if self.args.exit_on_output_limit:
                self.write_correlation_summary()
                self.write_output_complete_report()
                self.logger.info(
                    "Output targets completed successfully; stopping live capture normally: spatial=%s conditioned=%s",
                    targets,
                    self.conditioner.written_bytes,
                )
                self.request_process_exit("output targets completed")
            else:
                self.logger.info(
                    "Output targets reached; continuing source processing for full-dataset statistics: spatial=%s conditioned=%s",
                    targets,
                    self.conditioner.written_bytes,
                )

    def publish_images(
        self,
        y: np.ndarray,
        yuyv: Optional[np.ndarray],
        change: np.ndarray,
        active_mask: Optional[np.ndarray],
        shadow_mask: Optional[np.ndarray],
    ) -> None:
        # Statistics-only mode: skip YUYV->BGR conversion and all JPEG/PNG
        # encoding. The entropy path and on-disk audit reports are unchanged.
        if not self.args.web_images:
            return

        preview = make_preview(yuyv) if yuyv is not None else cv2.cvtColor(y, cv2.COLOR_GRAY2BGR)
        _, preview_encoded = cv2.imencode(
            ".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), self.args.jpeg_quality]
        )
        _, y_encoded = cv2.imencode(".png", y)
        _, change_encoded = cv2.imencode(".png", (change * 255).astype(np.uint8))

        if active_mask is None:
            active_mask_image = np.zeros_like(y)
            active_overlay = np.dstack([change * 255] * 3).astype(np.uint8)
        else:
            active_mask_image = active_mask.astype(np.uint8) * 255
            active_overlay = self.make_overlay(change, active_mask)

        if shadow_mask is None:
            shadow_mask_image = np.zeros_like(y)
            shadow_overlay = np.dstack([change * 255] * 3).astype(np.uint8)
            difference = np.zeros((y.shape[0], y.shape[1], 3), dtype=np.uint8)
        else:
            shadow_mask_image = shadow_mask.astype(np.uint8) * 255
            shadow_overlay = self.make_overlay(change, shadow_mask)
            difference = self.build_mask_difference(active_mask, shadow_mask) if active_mask is not None else np.zeros((y.shape[0], y.shape[1], 3), dtype=np.uint8)

        _, active_mask_encoded = cv2.imencode(".png", active_mask_image)
        _, shadow_mask_encoded = cv2.imencode(".png", shadow_mask_image)
        _, difference_encoded = cv2.imencode(".png", difference)
        _, active_overlay_encoded = cv2.imencode(".png", active_overlay)
        _, shadow_overlay_encoded = cv2.imencode(".png", shadow_overlay)

        with self.lock:
            self.preview_jpg = preview_encoded.tobytes()
            self.y_png = y_encoded.tobytes()
            self.change_png = change_encoded.tobytes()
            self.active_mask_png = active_mask_encoded.tobytes()
            self.shadow_mask_png = shadow_mask_encoded.tobytes()
            self.mask_difference_png = difference_encoded.tobytes()
            self.active_overlay_png = active_overlay_encoded.tobytes()
            self.shadow_overlay_png = shadow_overlay_encoded.tobytes()

    @staticmethod
    def make_overlay(change: np.ndarray, mask: np.ndarray) -> np.ndarray:
        # Overlay is a two-dimensional diagnostic image.  It must preserve the
        # original pixel coordinates and therefore intentionally ignores the
        # output serialization order.
        overlay = np.zeros((change.shape[0], change.shape[1], 3), dtype=np.uint8)
        overlay[:] = (0, 0, 255)
        values = (change[mask] * 255).astype(np.uint8)
        overlay[mask] = np.repeat(values[:, None], 3, axis=1)
        return overlay


def _live_dashboard_html(web_images: bool) -> str:
    image_section = r'''
<div class="grid2">
<section class="panel"><h3>Bieżąca ramka Y / podgląd</h3><div class="image-frame"><img id="preview" data-zoom-title="Bieżąca ramka Y / podgląd" alt="Podgląd ramki YUYV"></div></section>
<section class="panel"><h3>Bezpośredni kanał Y</h3><div class="image-frame"><img id="y" data-zoom-title="Bezpośredni kanał Y" alt="Kanał luminancji Y"></div></section>
</div>
<section class="panel"><h3>Zmiana LSB wybranej pary ramek</h3><div class="image-frame"><img id="change" data-zoom-title="Temporalna zmiana LSB" alt="Mapa temporalnej zmiany LSB"></div></section>
<div class="grid3">
<section class="panel"><h3>Maska aktywna — zamrożona</h3><div class="image-frame"><img id="activeMask" data-zoom-title="Maska aktywna" alt="Aktywna maska pikseli"></div></section>
<section class="panel"><h3>Maska shadow — aktualna</h3><div class="image-frame"><img id="shadowMask" data-zoom-title="Maska shadow" alt="Dynamiczna maska shadow"></div></section>
<section class="panel"><h3>Różnica masek</h3><div class="image-frame"><img id="difference" data-zoom-title="Różnica maski aktywnej i shadow" alt="Różnica masek"></div><p class="hint">Biały: obie akceptują. Czerwony: aktywna akceptuje, shadow odrzuca. Niebieski: tylko shadow akceptuje.</p></section>
</div>
<div class="grid2">
<section class="panel"><h3>LSB po masce aktywnej</h3><div class="image-frame"><img id="activeOverlay" data-zoom-title="LSB po masce aktywnej" alt="LSB po masce aktywnej"></div></section>
<section class="panel"><h3>LSB po masce shadow — obserwacja</h3><div class="image-frame"><img id="shadowOverlay" data-zoom-title="LSB po masce shadow" alt="LSB po masce shadow"></div></section>
</div>
''' if web_images else r'''
<section class="panel"><h3>Tryb WWW</h3><p>Podglądy kamery i masek są wyłączone przez <code>--no-web-images</code>. Wykresy Plotly, histogramy bajtów, heatmapy okresowe, testy zdrowia i zapis BIN nadal działają.</p></section>
'''

    image_js = r'''
const magnifier=byId('floatingMagnifier'),zoomCanvas=byId('zoomCanvas'),zoomTitle=byId('zoomTitle'),zoomCoords=byId('zoomCoords'),zoomCtx=zoomCanvas.getContext('2d');
function moveMagnifier(ev){const img=ev.currentTarget;if(!img.complete||!img.naturalWidth||!img.naturalHeight)return;const rect=img.getBoundingClientRect(),rx=(ev.clientX-rect.left)/rect.width,ry=(ev.clientY-rect.top)/rect.height;if(rx<0||rx>1||ry<0||ry>1)return;const sourceW=Math.min(56,img.naturalWidth),sourceH=Math.min(34,img.naturalHeight),cx=rx*img.naturalWidth,cy=ry*img.naturalHeight,sx=Math.max(0,Math.min(img.naturalWidth-sourceW,cx-sourceW/2)),sy=Math.max(0,Math.min(img.naturalHeight-sourceH,cy-sourceH/2));zoomCtx.imageSmoothingEnabled=false;zoomCtx.clearRect(0,0,zoomCanvas.width,zoomCanvas.height);zoomCtx.drawImage(img,sx,sy,sourceW,sourceH,0,0,zoomCanvas.width,zoomCanvas.height);const px=zoomCanvas.width/sourceW,py=zoomCanvas.height/sourceH;zoomCtx.strokeStyle='#00ff88';zoomCtx.lineWidth=2;zoomCtx.strokeRect((cx-sx)*px-px/2,(cy-sy)*py-py/2,px,py);zoomTitle.textContent=img.dataset.zoomTitle||img.alt||'Powiększenie';zoomCoords.textContent=`x=${Math.floor(cx)}, y=${Math.floor(cy)} · obszar ${sourceW}×${sourceH} px`;magnifier.style.display='block';magnifier.setAttribute('aria-hidden','false');const mw=magnifier.offsetWidth,mh=magnifier.offsetHeight,pad=18;let left=ev.clientX+pad,top=ev.clientY+pad;if(left+mw>window.innerWidth-8)left=ev.clientX-mw-pad;if(top+mh>window.innerHeight-8)top=ev.clientY-mh-pad;magnifier.style.left=`${Math.max(8,left)}px`;magnifier.style.top=`${Math.max(8,top)}px`}
function hideMagnifier(){magnifier.style.display='none';magnifier.setAttribute('aria-hidden','true')}
document.querySelectorAll('.image-frame img').forEach(img=>{img.addEventListener('mousemove',moveMagnifier);img.addEventListener('mouseleave',hideMagnifier)});
const imageEndpoints=[['preview','/frame.jpg'],['y','/y.png'],['change','/lsb_change.png'],['activeMask','/mask_active.png'],['shadowMask','/mask_shadow.png'],['difference','/mask_difference.png'],['activeOverlay','/mask_active_overlay.png'],['shadowOverlay','/mask_shadow_overlay.png']];
function refreshImages(warmup){if(!warmup.complete)return;const q=Date.now();for(const [id,url] of imageEndpoints)byId(id).src=`${url}?t=${q}`}
''' if web_images else "function refreshImages(_warmup){}"

    magnifier = r'''<div id="floatingMagnifier" aria-hidden="true"><b id="zoomTitle">Powiększenie</b><canvas id="zoomCanvas" width="740" height="440"></canvas><div id="zoomCoords"></div></div>''' if web_images else ""

    html = r'''<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Y Temporal Entropy — live diagnostics</title>
<script src="/static/vendor/plotly-3.3.1.min.js"></script>
<style>
:root{color-scheme:dark;--bg:#101318;--panel:#191e26;--line:#303846;--text:#eef2f7;--muted:#aeb8c5;--good:#73d99d;--bad:#ff7474;--warn:#ffd166;--blue:#76a9ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}header{position:sticky;top:0;z-index:20;padding:14px 20px;border-bottom:1px solid var(--line);background:rgba(16,19,24,.96);display:flex;justify-content:space-between;align-items:flex-start;gap:18px}.header-main{min-width:0}.state-summary{min-width:min(360px,42vw);display:flex;flex-direction:column;align-items:flex-end;text-align:right;gap:2px}.state-summary #state{font-size:1rem;line-height:1.2}.state-detail{max-width:440px;color:var(--muted);font-size:.82rem;line-height:1.25;overflow-wrap:anywhere}.state-progress{width:min(320px,38vw);height:5px;margin-top:5px;overflow:hidden;border:1px solid var(--line);border-radius:999px;background:#0b0e13}.state-progress>i{display:block;width:0;height:100%;border-radius:inherit;background:currentColor;transition:width .35s ease}.state-progress.warn{color:var(--warn)}.state-progress.good{color:var(--good)}.state-progress.bad{color:var(--bad)}main{max-width:1760px;margin:auto;padding:16px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:13px;margin-bottom:14px}.grid2,.grid3{display:grid;gap:12px}.grid2{grid-template-columns:repeat(2,minmax(0,1fr))}.grid3{grid-template-columns:repeat(3,minmax(0,1fr))}.stats{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:8px}.card{border:1px solid var(--line);padding:9px;border-radius:8px;min-width:0}.card span{color:var(--muted);font-size:.8rem}.card b{display:block;overflow-wrap:anywhere}.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}a{color:#a9d1ff}.hint,.plot-info{color:var(--muted);font-size:.86rem}.api-error{display:none;margin-top:8px;padding:8px;border:1px solid var(--bad);border-radius:7px;color:var(--bad);white-space:pre-wrap}.plot{width:100%;height:390px;background:#101318;border:1px solid var(--line);border-radius:8px}.plot.large{height:470px}.histogram-toolbar{display:flex;justify-content:flex-end;align-items:center;gap:8px;margin:-4px 0 10px}.histogram-toolbar label{color:var(--muted);font-size:.84rem}.histogram-toolbar select{background:#101318;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:6px 9px}.histogram-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.histogram-grid .plot{height:330px}.histogram-grid .plot.wide{grid-column:1/-1;height:470px}.plot-error{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:24px;color:var(--bad);background:#101318}.plot-error small{margin-top:7px;color:var(--muted)}.image-frame{position:relative;background:#050608;border:1px solid var(--line);border-radius:8px;overflow:hidden;min-height:120px;cursor:crosshair}.image-frame img{display:block;width:100%;height:auto;image-rendering:pixelated;min-height:120px;object-fit:contain}.heatmap-frame{background:#f5f5f5;border:1px solid var(--line);border-radius:8px;overflow:auto}.heatmap-frame img{display:block;width:100%;height:auto;min-height:180px;object-fit:contain}table{width:100%;border-collapse:collapse;font-size:.88rem}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--muted)}code,pre{color:#d8e9ff}pre{white-space:pre-wrap;word-break:break-all;background:#101318;border:1px solid var(--line);padding:10px;border-radius:8px}.tag{border:1px solid var(--line);border-radius:999px;padding:2px 7px}#floatingMagnifier{position:fixed;z-index:9999;display:none;pointer-events:none;width:390px;padding:9px;border:1px solid #5b6879;border-radius:10px;background:rgba(9,12,17,.97);box-shadow:0 10px 32px rgba(0,0,0,.45)}#zoomCanvas{display:block;width:370px;height:220px;background:#000;border:1px solid var(--line);image-rendering:pixelated}
@media(max-width:1200px){.stats{grid-template-columns:repeat(3,1fr)}.grid3{grid-template-columns:repeat(2,1fr)}}@media(max-width:760px){header{padding:11px 13px;flex-direction:column;gap:8px}.state-summary{width:100%;min-width:0;align-items:flex-start;text-align:left}.state-detail{max-width:100%}.state-progress{width:100%}.grid2,.grid3,.histogram-grid{grid-template-columns:1fr}.stats{grid-template-columns:1fr 1fr}.plot{height:330px}.histogram-grid .plot.wide{height:390px}}
</style></head><body>
<header><div class="header-main"><b>Y Temporal Entropy — worker live</b><div id="device"></div><div id="apiError" class="api-error"></div></div><div id="stateSummary" class="state-summary" role="status" aria-live="polite"><b id="state">START</b><span id="stateDetail" class="state-detail">uruchamianie workera</span><div id="stateProgress" class="state-progress warn" role="progressbar" aria-label="Postęp bieżącego etapu" aria-valuemin="0" aria-valuemax="100" hidden><i id="stateProgressBar"></i></div></div></header><main>
__IMAGE_SECTION__
<section class="panel"><h3>Stan źródła i zgodność masek</h3><div class="stats" id="cards"></div></section>
<section class="panel"><h3>Prędkość generowania bitów</h3><div class="stats" id="rateCards"></div></section>
<section class="panel"><h3>Histogramy odchyleń częstotliwości bajtów — główny pipeline</h3><div class="histogram-toolbar"><label>Widok <select id="histogramMode"><option value="separate">Rozdzielone — osobna skala</option><option value="overlay">Nakładane linie — wspólna skala</option></select></label></div><div id="byteHistogramMain" class="histogram-grid"></div><div id="byteStageCards" class="stats"></div><p class="hint">Każda linia pokazuje odchylenie od rozkładu jednostajnego 1/256 = 0,390625%. Domyślnie każdy etap ma osobny wykres i własną symetryczną skalę. Wybrany tryb widoku jest zapisywany w pamięci przeglądarki.</p></section>
<section class="panel" id="directHistogramPanel" hidden><h3>Direct LSB — aktywna maska</h3><div id="byteHistogramDirect" class="histogram-grid"></div><p class="hint">Surowy Direct LSB jest zawsze wydzielony, ponieważ jego większe odchylenia nie powinny wpływać na skalę kolejnych etapów.</p></section>
<section class="panel" id="dualHistogramPanel" hidden><h3>Histogramy odchyleń częstotliwości bajtów — dual weave</h3><div id="byteHistogramDual" class="histogram-grid"></div><p class="hint">W trybie rozdzielonym każdy strumień C0/C1, VN, wejście conditionera i SHA3-512 ma własny wykres oraz autoskalę.</p></section>
<section class="panel"><h3>Okresowa heatmapa 2D przejść bajtowych</h3><div class="heatmap-frame"><img id="liveHeatmap" alt="Okresowa heatmapa przejść bajtowych"></div><div id="heatmapInfo" class="plot-info">Oczekiwanie na minimalną liczbę bajtów i pierwszy interwał generowania.</div><p class="hint">X = bieżący bajt, Y = następny bajt. Kolor oznacza log10(1 + liczba par na milion); wszystkie etapy mają wspólną skalę.</p></section>
<section class="panel"><h3>Dryft maski w czasie — Plotly</h3><div id="driftChart" class="plot"></div><div id="driftInfo" class="plot-info">Oczekiwanie na dane dryftu.</div></section>
<section class="panel"><h3>Parametry uruchomienia aplikacji</h3><pre id="commandLine"></pre><table id="parameters"><thead><tr><th>Opcja</th><th>Wartość</th><th>Domyślna</th><th>Źródło</th></tr></thead><tbody></tbody></table></section>
<section class="panel"><h3>Pliki</h3><div id="files"></div></section>
</main>__MAGNIFIER__
<script>
const byId=id=>document.getElementById(id),finite=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v)),f=(v,d=6)=>finite(v)?Number(v).toFixed(d):'n/a',n=v=>finite(v)?new Intl.NumberFormat('pl-PL').format(Number(v)):'n/a',esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),card=(a,b,c='')=>`<div class="card"><span>${esc(a)}</span><b>${esc(b)}</b><span>${esc(c)}</span></div>`;
const plotConfig={responsive:true,displaylogo:false,scrollZoom:true};
function plotError(element,error){console.error('Plotly render error',error);element.innerHTML=`<div class="plot-error"><b>Nie udało się wyświetlić wykresu.</b><br>${esc(error&&error.message?error.message:error||'nieznany błąd')}<br><small>Wykres live używa renderera SVG i nie wymaga WebGL.</small></div>`}
function safePlotlyReact(element,traces,layout){try{return Promise.resolve(Plotly.react(element,traces,layout,plotConfig)).catch(error=>plotError(element,error))}catch(error){plotError(element,error);return Promise.resolve()}}
function rate(v){v=Number(v);if(!Number.isFinite(v))return'n/a';if(v>=1e9)return`${(v/1e9).toFixed(3)} Gbit/s`;if(v>=1e6)return`${(v/1e6).toFixed(3)} Mbit/s`;if(v>=1e3)return`${(v/1e3).toFixed(3)} kbit/s`;return`${v.toFixed(2)} bit/s`}
function duration(v){v=Number(v);if(!Number.isFinite(v))return'n/a';v=Math.max(0,v);return`${Math.floor(v/3600)}h ${Math.floor((v%3600)/60)}m ${Math.floor(v%60)}s`}
function durationShort(v){v=Number(v);if(!Number.isFinite(v))return'n/a';v=Math.max(0,Math.ceil(v));const h=Math.floor(v/3600),m=Math.floor((v%3600)/60),s=Math.floor(v%60);return h?`${h}h ${m}m ${s}s`:(m?`${m}m ${s}s`:`${s}s`)}
function percent(v){v=Number(v);return Number.isFinite(v)?new Intl.NumberFormat('pl-PL',{minimumFractionDigits:0,maximumFractionDigits:1}).format(v):'n/a'}
function byteSize(v){v=Number(v);if(!Number.isFinite(v))return'n/a';const units=['B','KiB','MiB','GiB','TiB'];let i=0;while(Math.abs(v)>=1024&&i<units.length-1){v/=1024;i++}return`${new Intl.NumberFormat('pl-PL',{maximumFractionDigits:i?2:0}).format(v)} ${units[i]}`}
function stateHeaderModel(s){const state=String((s||{}).state||'UNKNOWN'),w=(s||{}).warmup||{},c=(s||{}).calibration||{},conditioner=(s||{}).conditioner||{},out=(s||{}).output_limit||{},rates=(s||{}).rates||{},health=(s||{}).health||{};let detail='',progress=null;if(state==='WARMING_UP'){const remaining=Math.max(0,Number(w.remaining_seconds)||0),total=Math.max(0,Number(w.configured_seconds)||0),sourceAge=Number(w.source_age_seconds),sourceDetail=Number.isFinite(sourceAge)?` · wiek źródła ${durationShort(Math.max(0,sourceAge))}`:'';detail=(total>0?`pozostało ${durationShort(remaining)} z ${durationShort(total)}`:`pozostało ${durationShort(remaining)}`)+sourceDetail;progress=total>0?(total-remaining)/total:null}else if(state==='CALIBRATING'){const current=Math.max(0,Number(c.pairs)||0),target=Math.max(0,Number(c.target)||0);detail=target>0?`para kalibracyjna ${n(current)} / ${n(target)} · ${percent(100*current/target)}%`:`zebrano ${n(current)} par ramek`;progress=target>0?current/target:null}else if(state==='RUNNING'||state==='OUTPUT_COMPLETE'){const conditionerTarget=Math.max(0,Number(conditioner.target_bytes)||0),useConditioner=conditionerTarget>0,target=useConditioner?conditionerTarget:Math.max(0,Number(out.target_bytes)||0),written=useConditioner?Math.max(0,Number(conditioner.written_bytes)||0):Math.max(0,Number(out.written_bytes)||0);if(target>0){detail=`${useConditioner?'SHA3':'dane'} ${byteSize(written)} / ${byteSize(target)} · ${percent(100*written/target)}%`;progress=written/target}else{detail=`produkcja ${durationShort(rates.production_uptime_seconds)} · ${rate(rates.output_bps_current)}`}}else if(state==='ERROR'||state==='API ERROR'){detail=String((s||{}).error||'błąd workera')}else if(state.endsWith('_FAILED')){detail=String(health.last_failure||(s||{}).error||'etap zatrzymany fail-closed')}else{detail=state==='START'?'uruchamianie workera':''}return{state,detail,progress:progress===null?null:Math.max(0,Math.min(1,Number(progress)||0))}}
function renderStateHeader(s){const model=stateHeaderModel(s),stateElement=byId('state'),detailElement=byId('stateDetail'),progressElement=byId('stateProgress'),bar=byId('stateProgressBar');const stateClass=['RUNNING','OUTPUT_COMPLETE'].includes(model.state)?'good':(['CALIBRATING','WARMING_UP'].includes(model.state)?'warn':'bad');stateElement.textContent=model.state;stateElement.className=stateClass;detailElement.textContent=model.detail;if(model.progress===null){progressElement.hidden=true;progressElement.removeAttribute('aria-valuenow');bar.style.width='0%'}else{const value=Math.round(model.progress*1000)/10;progressElement.hidden=false;progressElement.className=`state-progress ${stateClass}`;progressElement.setAttribute('aria-valuenow',String(value));bar.style.width=`${value}%`}}
async function fetchJson(url){const r=await fetch(url,{cache:'no-store'}),t=await r.text();if(!r.ok)throw new Error(`${url}: HTTP ${r.status}: ${t.slice(0,180)}`);return JSON.parse(t)}
function darkLayout(title,yTitle){return{title:{text:title,font:{size:14}},paper_bgcolor:'#101318',plot_bgcolor:'#101318',font:{color:'#eef2f7'},margin:{l:70,r:25,t:48,b:55},xaxis:{title:'Wartość bajtu',range:[-0.5,255.5],dtick:32,gridcolor:'#303846'},yaxis:{title:yTitle,gridcolor:'#303846'},legend:{orientation:'h',y:-0.22},hovermode:'x unified'}}
function centeredDeltaRange(values,minFloor=.004){const finiteValues=(values||[]).filter(v=>Number.isFinite(Number(v))).map(v=>Math.abs(Number(v)));const maxAbs=Math.max(minFloor,...finiteValues);const padded=maxAbs*1.12;return[-padded,padded]}
const histogramStorageKey='cameraEntropyHistogramMode';function loadHistogramMode(){try{return localStorage.getItem(histogramStorageKey)||'separate'}catch(_error){return'separate'}}function saveHistogramMode(value){try{localStorage.setItem(histogramStorageKey,value)}catch(_error){}}let histogramMode=loadHistogramMode(),lastByteDiagnostics=null;
function purgeHistogramContainer(element){for(const plot of element.querySelectorAll('.plot')){try{Plotly.purge(plot)}catch(_error){}}element.innerHTML=''}
function stageDelta(stage){const uniform=100/256;return(stage.frequencies||[]).map(v=>100*Number(v)-uniform)}
function histogramTrace(stage,color){return{type:'scatter',mode:'lines',name:`${stage.label} (${n(stage.total_bytes)} B)`,x:Array.from({length:256},(_,i)=>i),y:stageDelta(stage),line:{width:1.55,color},fill:'tozeroy',fillcolor:`${color}22`,customdata:(stage.frequencies||[]).map(v=>100*Number(v)),hovertemplate:'bajt=%{x}<br>Δ względem 1/256=%{y:+.6f} p.p.<br>częstotliwość=%{customdata:.6f}%<extra>%{fullData.name}</extra>'}}
function histogramLayout(title,values,showLegend){const layout=darkLayout(title,'Odchylenie od 1/256 [p.p.]');layout.yaxis={title:'Odchylenie od 1/256 [p.p.]',range:centeredDeltaRange(values),gridcolor:'#303846',zeroline:true,zerolinewidth:1.2,zerolinecolor:'#9fb2c7'};layout.shapes=[{type:'line',xref:'paper',x0:0,x1:1,y0:0,y1:0,line:{dash:'dash',width:1,color:'#9fb2c7'}}];layout.showlegend=showLegend;return layout}
function drawByteGroup(stages,elementId,title){const element=byId(elementId);if(!window.Plotly){element.textContent='Plotly nie został załadowany.';return}const filtered=stages.filter(s=>Number(s.total_bytes)>0),palette=['#76a9ff','#73d99d','#ffd166','#ff9f7f','#c792ea','#56cfe1','#f28482','#a3be8c','#b8c0ff','#90be6d'];purgeHistogramContainer(element);if(!filtered.length)return;if(histogramMode==='overlay'){const plot=document.createElement('div');plot.className='plot wide';element.appendChild(plot);const traces=filtered.map((s,i)=>{const trace=histogramTrace(s,palette[i%palette.length]);trace.fill='none';return trace});safePlotlyReact(plot,traces,histogramLayout(title,traces.flatMap(t=>t.y),true));return}filtered.forEach((stage,index)=>{const plot=document.createElement('div');plot.className='plot';element.appendChild(plot);const trace=histogramTrace(stage,palette[index%palette.length]);safePlotlyReact(plot,[trace],histogramLayout(stage.label,trace.y,false))})}
function setHistogramMode(value){histogramMode=value==='overlay'?'overlay':'separate';saveHistogramMode(histogramMode);const selector=byId('histogramMode');if(selector)selector.value=histogramMode;if(lastByteDiagnostics)renderByteDiagnostics(lastByteDiagnostics)}
let heatmapSequence=-1;
function renderByteDiagnostics(data){lastByteDiagnostics=data;const stages=(data.stages||[]).slice().sort((a,b)=>(a.rank-b.rank)||String(a.label).localeCompare(String(b.label)));const main=stages.filter(s=>s.group==='main'&&Number(s.total_bytes)>0),direct=main.filter(s=>s.key==='direct_lsb'),mainCore=main.filter(s=>s.key!=='direct_lsb'),dual=stages.filter(s=>s.group==='dual'&&Number(s.total_bytes)>0);drawByteGroup(mainCore,'byteHistogramMain','Odchylenia rozkładu bajtów po etapach czyszczenia');byId('directHistogramPanel').hidden=!direct.length;if(direct.length)drawByteGroup(direct,'byteHistogramDirect','Direct LSB — aktywna maska');byId('byteStageCards').innerHTML=main.map(s=>card(s.label,`${n(s.total_bytes)} B`,`H=${f(s.byte_entropy_bits,6)} · Hmin=${f(s.byte_min_entropy_bits,6)} · mean=${f(s.byte_mean,4)}`)).join('');byId('dualHistogramPanel').hidden=!dual.length;if(dual.length)drawByteGroup(dual,'byteHistogramDual','Dual weave — odchylenia rozkładów bajtów');const hm=data.heatmap||{};if(hm.file&&Number(hm.sequence)!==heatmapSequence){heatmapSequence=Number(hm.sequence);byId('liveHeatmap').src=`/live_byte_heatmaps.png?t=${Date.now()}`}byId('heatmapInfo').textContent=hm.file?`Sekwencja ${hm.sequence} · wygenerowano ${hm.last_generated_utc||'n/a'} · interwał ${hm.interval_seconds}s · maks. ${hm.max_stages} etapów`:`Oczekiwanie: minimum ${n(hm.minimum_bytes_per_stage)} B na etap · interwał ${hm.interval_seconds}s${hm.rendering?' · generowanie w toku':''}`}
function drawDrift(history,settings){const rows=(history||[]).slice(-720).filter(p=>Number.isFinite(Number(p.active_retention))&&Number.isFinite(Number(p.jaccard)));if(!window.Plotly)return;if(!rows.length){Plotly.purge(byId('driftChart'));byId('driftInfo').textContent='Oczekiwanie na pierwszy punkt dryftu.';return}const x=rows.map((p,i)=>p.timestamp_utc||i),ret=rows.map(p=>100*Number(p.active_retention)),jac=rows.map(p=>100*Number(p.jaccard)),rt=100*Number(settings.shadow_min_active_retention||.95),jt=100*Number(settings.shadow_min_jaccard||.90);const traces=[{type:'scatter',mode:'lines+markers',name:'Retencja aktywnej',x,y:ret,line:{width:2},marker:{size:4},hovertemplate:'%{x}<br>retencja=%{y:.5f}%<extra></extra>'},{type:'scatter',mode:'lines+markers',name:'Jaccard',x,y:jac,line:{width:2},marker:{size:4},hovertemplate:'%{x}<br>Jaccard=%{y:.5f}%<extra></extra>'}];const allValues=[...ret,...jac,rt,jt].filter(v=>Number.isFinite(Number(v))).map(Number);const low=Math.min(...allValues),high=Math.max(...allValues),span=Math.max(0.05,high-low),pad=Math.max(0.05,span*0.12);let y0=Math.max(0,low-pad),y1=Math.min(100,high+pad);if(y1-y0<0.6){const mid=(y0+y1)/2;y0=Math.max(0,mid-0.3);y1=Math.min(100,mid+0.3)}const layout=darkLayout('Retencja aktywnej maski i indeks Jaccarda','[%]');layout.xaxis={title:'Czas / zapis',gridcolor:'#303846'};layout.yaxis={title:'[%]',range:[y0,y1],gridcolor:'#303846'};layout.shapes=[{type:'line',xref:'paper',x0:0,x1:1,y0:rt,y1:rt,line:{dash:'dash',width:1}},{type:'line',xref:'paper',x0:0,x1:1,y0:jt,y1:jt,line:{dash:'dot',width:1}}];safePlotlyReact(byId('driftChart'),traces,layout);const last=rows[rows.length-1];byId('driftInfo').textContent=`Punkty: ${rows.length} · ostatni: ${last.timestamp_utc||'n/a'} · retencja ${f(100*last.active_retention,4)}% · Jaccard ${f(100*last.jaccard,4)}% · bad streak ${n(last.bad_streak)} · zakres osi ${f(y0,3)}…${f(y1,3)}%`}
__IMAGE_JS__
async function updateStats(){try{const s=await fetchJson('/api/stats'),l=s.last||{},c=s.calibration||{},h=s.health||{},m=s.shadow_mask||{},a=s.active_mask||{},r=s.rates||{},ctrl=s.camera_controls||{},out=s.output_limit||{},w=s.warmup||{},p=s.pairing||{},dw=s.dual_weave||{},ac=s.active_clipping||{},st=s.source_transport||{};byId('apiError').style.display='none';byId('device').textContent=`${s.device||''} · ${s.mode.fourcc||''} ${s.mode.width||0}×${s.mode.height||0} · ${s.app_version}`;renderStateHeader(s);byId('cards').innerHTML=card('Tryb parowania',`${p.mode||'n/a'} / k=${n(p.lag_frames)}`,`bufor ${n(p.buffered_frames)}/${n(p.conceptual_buffer_frames)} · ${p.phase||''}`)+card('Transport źródła',`gen ${n(st.connection_generation)} · reconnect ${n(st.reconnect_count)}`,`timeout ${n(st.frame_timeout_seconds)} s`)+card('Selekcja przestrzenna',`${(((s.settings||{}).spatial_selection||{}).label)||((s.settings||{}).spatial_sampling)||'n/a'}`)+card('Dual weave',dw.enabled?`${dw.group_sequence||0} grup · ${dw.complete?'COMPLETE':'RUNNING'}`:'wyłączony')+card('Clipping full',f((((ac.scopes||{}).full||{}).rate)),`limit ${f(ac.limit)}`)+card('Clipping even',f((((ac.scopes||{}).even||{}).rate)))+card('Clipping odd',f((((ac.scopes||{}).odd||{}).rate)))+card('Kalibracja',`${n(c.pairs)} / ${n(c.target)}`,c.ready?'zamrożona':'zbieranie')+card('Maska produkcyjna',n(a.production_pixels||m.active_pixels||a.pixels))+card('Shadow maska',n(m.shadow_pixels))+card('Retencja',f(m.active_retention))+card('Jaccard',f(m.jaccard))+card('Niezgodność',f(m.disagreement_rate))+card('P(1) RAW',f(l.raw_change_p1))+card('P(1) po masce',f(l.masked_p1))+card('Von Neumann',(s.settings||{}).von_neumann_stage?n(l.vn_output_bits):'wyłączony',(s.settings||{}).von_neumann_stage?`wydajność ${f(l.vn_efficiency)}`:'temporal SHA3')+card('SHA3',`${n((s.conditioner||{}).written_bytes)} B`,rate((s.conditioner||{}).output_bps_lifetime))+card('RCT/APT',h.latched?'FAIL':'OK',h.last_failure||'')+card('Ekspozycja',n((ctrl.after_open||{}).exposure_time_absolute))+card('Warm-up',w.complete?'zakończony':duration(w.remaining_seconds),Number.isFinite(Number(w.source_age_seconds))?`wiek źródła ${duration(w.source_age_seconds)} · ${w.basis||'source-reported'}`:'timer lokalny workera')+card('Cel danych',out.target_bytes?`${f(100*(out.written_bytes||0)/out.target_bytes,3)}%`:'bez limitu');byId('rateCards').innerHTML=card('Bieżąca',rate(r.output_bps_current))+card('EMA 10 s',rate(r.output_bps_ema_10s))+card('1 s',rate(r.output_bps_1s))+card('10 s',rate(r.output_bps_10s))+card('60 s',rate(r.output_bps_60s))+card('Średnia',rate(r.output_bps_lifetime),duration(r.production_uptime_seconds))+card('RAW 10 s',rate(r.raw_change_bps_10s))+card('Po masce 10 s',rate(r.masked_bps_10s))+card('Packed 10 s',`${f(r.packed_bytes_per_second_10s,2)} B/s`)+card('Zapisano',`${n(r.total_written_bytes)} B`);byId('commandLine').textContent=s.command_line||'';document.querySelector('#parameters tbody').innerHTML=(s.startup_parameters||[]).map(p=>`<tr><td><code>${esc(p.options)}</code><br><small>${esc(p.destination)}</small></td><td><code>${esc(p.value)}</code></td><td><code>${esc(p.default)}</code></td><td><span class="tag">${esc(p.source)}</span></td></tr>`).join('');byId('files').innerHTML=Object.entries(s.files||{}).filter(([,v])=>v).map(([k,v])=>`<a href="/download/${encodeURIComponent(v)}">${esc(k)}: ${esc(v)}</a>`).join(' · ');drawDrift(s.mask_drift_history||[],s.settings||{});refreshImages(w)}catch(error){console.error(error);renderStateHeader({state:'API ERROR',error:error.message});const box=byId('apiError');box.textContent=error.message;box.style.display='block'}}
async function updateBytes(){try{renderByteDiagnostics(await fetchJson('/api/byte-diagnostics'))}catch(error){console.error('byte diagnostics',error)}}
const histogramSelector=byId('histogramMode');histogramSelector.value=histogramMode;histogramSelector.addEventListener('change',event=>setHistogramMode(event.target.value));updateStats();updateBytes();setInterval(updateStats,1000);setInterval(updateBytes,2000);
</script></body></html>'''
    return (html.replace("__IMAGE_SECTION__", image_section)
                .replace("__IMAGE_JS__", image_js)
                .replace("__MAGNIFIER__", magnifier))


INDEX_HTML = _live_dashboard_html(True)
STATS_ONLY_HTML = _live_dashboard_html(False)

def create_app(service: Service) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_ROOT), static_url_path="/static")

    @app.after_request
    def force_utf8(response: Response) -> Response:
        if response.mimetype in {"text/html", "text/plain", "application/json"}:
            response.headers["Content-Type"] = f"{response.mimetype}; charset=utf-8"
        return response

    @app.get("/")
    def index() -> str:
        template = INDEX_HTML if service.args.web_images else STATS_ONLY_HTML
        return render_template_string(template)

    def image(attribute: str, mime: str) -> Response:
        if not service.args.web_images:
            abort(404, "Web image generation is disabled")
        with service.lock:
            data = getattr(service, attribute)
        if data is None:
            abort(503)
        return Response(data, mimetype=mime, headers={"Cache-Control": "no-store"})

    @app.get("/frame.jpg")
    def frame() -> Response:
        return image("preview_jpg", "image/jpeg")

    @app.get("/y.png")
    def y_image() -> Response:
        return image("y_png", "image/png")

    @app.get("/lsb_change.png")
    def change_image() -> Response:
        return image("change_png", "image/png")

    @app.get("/mask_active.png")
    @app.get("/mask.png")
    def active_mask_image() -> Response:
        return image("active_mask_png", "image/png")

    @app.get("/mask_shadow.png")
    def shadow_mask_image() -> Response:
        return image("shadow_mask_png", "image/png")

    @app.get("/mask_difference.png")
    def mask_difference_image() -> Response:
        return image("mask_difference_png", "image/png")

    @app.get("/mask_active_overlay.png")
    @app.get("/mask_overlay.png")
    def active_overlay_image() -> Response:
        return image("active_overlay_png", "image/png")

    @app.get("/mask_shadow_overlay.png")
    def shadow_overlay_image() -> Response:
        return image("shadow_overlay_png", "image/png")

    @app.get("/api/stats")
    @app.get("/api/status")
    def stats() -> Response:
        # /api/status is retained as a compatibility alias for runner scripts.
        return strict_json_response(service.status())

    @app.get("/api/mask-drift-history")
    def mask_drift_history() -> Response:
        status = service.status()
        return strict_json_response({
            "snapshot": status.get("mask_snapshot", {}),
            "history": status.get("mask_drift_history", []),
        })

    @app.get("/api/byte-diagnostics")
    def byte_diagnostics() -> Response:
        return strict_json_response(service.byte_diagnostics.snapshot(include_counts=True))

    @app.get("/live_byte_heatmaps.png")
    def live_byte_heatmaps() -> Response:
        data = service.byte_diagnostics.image()
        if data is None:
            abort(503, "Live byte heatmap has not been generated yet")
        return Response(data, mimetype="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/health")
    def health() -> Response:
        status = service.status()
        code = 200 if status["state"] == "RUNNING" else 503
        return strict_json_response(status, status=code)

    @app.get("/download/<path:name>")
    def download(name: str):
        allowed = {value for value in service.files().values() if value}
        if name not in allowed:
            abort(404)
        directory = service.args.log_file.parent if name == service.args.log_file.name else service.output_dir
        return send_from_directory(directory, name, as_attachment=True)

    return app


def build_logger(path: Path, verbose: bool) -> logging.Logger:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("camera_entropy_exposure_benchmark")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s")
    file_handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def display_value(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def collect_explicit_destinations(parser: argparse.ArgumentParser, argv: list[str]) -> set[str]:
    option_to_dest: dict[str, str] = {}
    for action in parser._actions:
        for option in action.option_strings:
            option_to_dest[option] = action.dest
    explicit: set[str] = set()
    for token in argv:
        if not token.startswith("-"):
            continue
        option = token.split("=", 1)[0]
        destination = option_to_dest.get(option)
        if destination:
            explicit.add(destination)
    return explicit


def build_startup_parameters(
    parser: argparse.ArgumentParser, args: argparse.Namespace, explicit: set[str]
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for action in parser._actions:
        if action.dest == "help" or not action.option_strings:
            continue
        value = getattr(args, action.dest)
        source = "explicit" if action.dest in explicit else "default"
        if action.dest == "log_file" and action.dest not in explicit:
            source = "derived default"
        display = redact_url(str(value)) if action.dest == "rtsp_url" and value else display_value(value)
        rows.append(
            {
                "destination": action.dest,
                "options": ", ".join(action.option_strings),
                "value": display,
                "default": display_value(action.default),
                "source": source,
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Distributed temporal entropy processor for V4L2, mTLS Y8, RTSP and buffered datasets"
    )
    parser.add_argument(
        "--source-type",
        choices=("v4l2", "tls-y", "rtsp", "dataset-y"),
        default="v4l2",
        help="Frame input: local V4L2, remote raw-Y mTLS agent, decoded RTSP luma, or a buffered dataset",
    )
    parser.add_argument("--device")
    parser.add_argument("--tls-host")
    parser.add_argument("--tls-port", type=int, default=9443)
    parser.add_argument("--tls-ca", type=Path)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument("--tls-server-name")
    parser.add_argument("--source-connect-timeout-seconds", type=float, default=15.0)
    parser.add_argument(
        "--source-frame-timeout-seconds", type=float, default=60.0,
        help="Maximum silence while waiting for a remote TLS-Y frame before reconnect",
    )
    parser.add_argument(
        "--source-reconnect-attempts", type=int, default=5,
        help="TLS-Y reconnect attempts after timeout/EOF; pre-production state is restarted",
    )
    parser.add_argument("--source-reconnect-backoff-seconds", type=float, default=2.0)
    parser.add_argument(
        "--allow-source-reconnect-during-production",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Diagnostic only; default fail-closed after production has begun",
    )
    parser.add_argument(
        "--allow-source-frame-gaps",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Diagnostic only: continue when the remote capture sequence has a gap",
    )
    parser.add_argument("--rtsp-url")
    parser.add_argument("--rtsp-url-file", type=Path)
    parser.add_argument("--rtsp-transport", choices=("tcp", "udp", "http", "https"), default="tcp")
    parser.add_argument("--rtsp-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--rtsp-luma-mode", choices=("extract-y", "gray-convert"), default="extract-y")
    parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    parser.add_argument("--ffprobe-bin", default="ffprobe")
    parser.add_argument("--dataset-dir", type=Path)
    parser.add_argument("--dataset-start-frame", type=int, default=0)
    parser.add_argument("--dataset-max-frames", type=int, default=0)
    parser.add_argument(
        "--dataset-realtime",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Replay buffered frames with their recorded timing instead of processing as fast as possible",
    )
    parser.add_argument("--dataset-rate", type=float, default=1.0)
    parser.add_argument(
        "--dataset-verify-hashes",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Verify SHA-256 of chunks already closed by the dataset recorder",
    )
    parser.add_argument(
        "--dataset-verify-cache",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse a completed SHA-256 verification when the stopped dataset fingerprint is unchanged",
    )
    parser.add_argument(
        "--dataset-verify-cache-dir",
        type=Path,
        help="Optional cache directory; default is .integrity-cache next to the dataset directory",
    )
    parser.add_argument(
        "--dataset-verify-workers",
        type=int,
        default=1,
        help="Parallel SHA-256 workers for dataset verification (1..64)",
    )
    parser.add_argument(
        "--dataset-verify-progress-seconds",
        type=float,
        default=2.0,
        help="Dataset SHA-256 progress log interval",
    )
    parser.add_argument(
        "--dataset-follow",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Follow new committed frames while the dataset is still being recorded",
    )
    parser.add_argument("--dataset-poll-seconds", type=float, default=0.1)
    parser.add_argument(
        "--dataset-follow-timeout-seconds",
        type=float,
        default=0.0,
        help="Stop waiting after this many idle seconds; 0 waits until the recorder finishes",
    )
    parser.add_argument("--vid", default=TARGET_VID)
    parser.add_argument("--pid", default=TARGET_PID)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--camera-fps", type=float, default=10.0)
    parser.add_argument("--strict-mode", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--manual-exposure", action="store_true")
    parser.add_argument("--exposure-value", type=int)
    parser.add_argument(
        "--thermal-warmup-seconds",
        type=float,
        default=0.0,
        help="Discard physical frames for this many seconds before calibration",
    )

    parser.add_argument(
        "--sample-mode",
        choices=SAMPLE_MODES,
        default="xor",
        help="Sample symbols behind the frozen mask: temporal XOR, direct Y, or modulo-256 temporal delta",
    )
    parser.add_argument(
        "--lsb-bits",
        type=int,
        default=1,
        help="Number of least-significant Y/sample bits to serialize, 1..4",
    )
    parser.add_argument(
        "--entropy-credit-bits-per-pixel",
        type=float,
        default=1.0,
        help=(
            "Conservative entropy budget per selected pixel, independent of captured LSB width. "
            "This is an external assessment input, not a value established by statistical tests."
        ),
    )
    parser.add_argument(
        "--pairing-mode",
        choices=("disjoint", "sliding"),
        default="disjoint",
        help=(
            "disjoint: collect k first-half frames and pair them with the next k frames; "
            "sliding: pair every frame with the frame k positions earlier"
        ),
    )
    parser.add_argument(
        "--pair-lag-frames",
        type=int,
        default=4,
        help="Frame distance k. Disjoint mode conceptually uses blocks of 2k frames.",
    )
    parser.add_argument(
        "--spatial-mask-pattern",
        choices=("legacy", "full", "checkerboard-even", "checkerboard-odd", "grid", "block"),
        default="legacy",
        help="Production spatial selection; legacy preserves --spatial-sampling",
    )
    parser.add_argument("--spatial-step-x", type=int, default=1)
    parser.add_argument("--spatial-step-y", type=int, default=1)
    parser.add_argument("--spatial-phase-x", type=int, default=0)
    parser.add_argument("--spatial-phase-y", type=int, default=0)
    parser.add_argument("--spatial-block-width", type=int, default=4)
    parser.add_argument("--spatial-block-height", type=int, default=4)
    parser.add_argument("--temporal-spatial-offset-x", type=int, default=0,
                        help="Use older Y(x+dx,y+dy); invalid edges are excluded")
    parser.add_argument("--temporal-spatial-offset-y", type=int, default=0)
    parser.add_argument("--serialization-order",
                        choices=("row-major", "serpentine", "tile-interleave"),
                        default="row-major",
                        help="Ordering only; this is not entropy conditioning")
    parser.add_argument("--serialization-tile-width", type=int, default=16)
    parser.add_argument("--serialization-tile-height", type=int, default=16)
    parser.add_argument(
        "--spatial-sampling",
        choices=SPATIAL_SAMPLING_CHOICES,
        default="checkerboard-even",
        help="Production pixel pattern; checkerboard removes horizontal/vertical d=1 neighbours",
    )

    parser.add_argument("--calibration-pairs", type=int, default=512)
    parser.add_argument("--mask-p1-min", type=float, default=0.30)
    parser.add_argument("--mask-p1-max", type=float, default=0.70)
    parser.add_argument("--mask-transition-min", type=float, default=0.20)
    parser.add_argument("--mask-transition-max", type=float, default=0.80)
    parser.add_argument("--mask-clip-max", type=float, default=0.01)
    parser.add_argument("--clip-low", type=int, default=16)
    parser.add_argument("--clip-high", type=int, default=250)
    parser.add_argument(
        "--dynamic-clip-filter",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Compatibility mode: remove currently clipped pixels per pair; production default uses a frozen map",
    )
    parser.add_argument(
        "--max-active-clip-rate",
        type=float,
        default=0.001,
        help="Skip/latch when the fraction of frozen active pixels clipped in a pair exceeds this limit",
    )
    parser.add_argument("--clip-fail-consecutive", type=int, default=3)

    parser.add_argument(
        "--control-check-seconds",
        type=float,
        default=60.0,
        help="Periodically verify manual exposure controls; 0 disables checks",
    )
    parser.add_argument("--control-fail-consecutive", type=int, default=1)
    parser.add_argument(
        "--control-stop-on-mismatch",
        action=argparse.BooleanOptionalAction,
        default=True,
    )

    parser.add_argument("--shadow-half-life-pairs", type=float, default=512.0)
    parser.add_argument("--shadow-update-every", type=int, default=5)
    parser.add_argument("--shadow-grace-pairs", type=int, default=128)
    parser.add_argument("--shadow-min-active-retention", type=float, default=0.95)
    parser.add_argument("--shadow-min-jaccard", type=float, default=0.90)
    parser.add_argument("--shadow-fail-consecutive", type=int, default=5)
    parser.add_argument("--shadow-stop-on-drift", action=argparse.BooleanOptionalAction, default=True)

    parser.add_argument(
        "--assessed-min-entropy",
        type=float,
        default=0.50,
        help="Provisional min-entropy in bits per source sample symbol, used only to derive RCT/APT cutoffs",
    )
    parser.add_argument("--health-alpha", type=float, default=2**-20)
    parser.add_argument(
        "--apt-window",
        type=int,
        default=None,
        help="APT window in source symbols; default: 1024 for binary, 512 for non-binary",
    )
    parser.add_argument("--write-output", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--max-output-bytes",
        type=int,
        default=0,
        help="Stop accepting output after exactly this many full bytes; 0 means unlimited",
    )
    parser.add_argument(
        "--validation-output-bytes",
        type=int,
        default=0,
        help="Benchmark-only bytes saved for each pre-conditioner validation stream",
    )
    parser.add_argument(
        "--conditioner",
        choices=("none", "sha3-512"),
        default="sha3-512",
        help="Vetted hash-function candidate applied to selected pre-VN samples",
    )
    parser.add_argument(
        "--conditioner-input-bits",
        type=int,
        default=2048,
        help="Input bits per SHA3-512 output block; default 2048 -> 512 (4:1)",
    )
    parser.add_argument(
        "--conditioned-output-bytes",
        type=int,
        default=0,
        help="Stop conditioned output after exactly this many bytes; 0 means unlimited",
    )
    parser.add_argument(
        "--write-conditioned-output",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write camera_entropy_sha3_512.bin when conditioner is enabled",
    )
    parser.add_argument(
        "--spatial-comparison",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Generate full, both checkerboard phases and one-phase 2x2 outputs in parallel from "
            "the same frame pairs. max-output-bytes applies independently to each variant."
        ),
    )
    parser.add_argument(
        "--dual-weave-comparison",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Pair consecutive disjoint temporal maps A/B, weave A-even+B-odd and "
            "B-even+A-odd, and compare row-major with serpentine ordering."
        ),
    )
    parser.add_argument(
        "--dual-weave-orders",
        type=lambda text: tuple(dict.fromkeys(item.strip() for item in text.split(",") if item.strip())),
        default=("serpentine", "row-major"),
        help="Comma-separated dual-weave orderings: serpentine,row-major",
    )
    parser.add_argument(
        "--dual-weave-alignments",
        type=lambda text: tuple(dict.fromkeys(item.strip() for item in text.split(",") if item.strip())),
        default=("same-group",),
        help="Comma-separated conditioner alignments: same-group,stagger-1,stagger-2",
    )
    parser.add_argument(
        "--stream-stats-window-pairs",
        type=int,
        default=1024,
        help="Production frame pairs per complete-dataset streaming statistics window",
    )
    parser.add_argument(
        "--correlation-distances",
        type=parse_positive_int_list,
        default=parse_positive_int_list("1,2,3,4,6,8,12,16,24,32,48,64"),
        help="Spatial pixel distances measured for horizontal/vertical/diagonal correlation",
    )
    parser.add_argument(
        "--correlation-every",
        type=int,
        default=50,
        help="Accumulate full-frame spatial correlation every N production frame pairs",
    )
    parser.add_argument(
        "--mask-snapshot-interval-seconds",
        type=float,
        default=0.0,
        help="Archive changed shadow-mask images at this minimum interval; 0 disables",
    )
    parser.add_argument(
        "--mask-drift-history-points",
        type=int,
        default=720,
        help="Maximum periodic mask-drift points retained in the live web API",
    )
    parser.add_argument(
        "--live-byte-diagnostics",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Accumulate live byte histograms and adjacent-byte transition matrices for pipeline stages",
    )
    parser.add_argument(
        "--live-heatmap-interval-seconds",
        type=float,
        default=60.0,
        help="Generate a multi-stage live byte-transition PNG at this interval; 0 disables snapshots",
    )
    parser.add_argument(
        "--live-heatmap-max-stages",
        type=int,
        default=8,
        help="Maximum pipeline stages included in each periodic live heatmap image",
    )
    parser.add_argument(
        "--live-heatmap-min-bytes",
        type=int,
        default=4096,
        help="Minimum complete bytes in a stage before it appears in the periodic live heatmap",
    )
    parser.add_argument(
        "--exit-on-output-limit",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Terminate the process after max-output-bytes is reached",
    )
    parser.add_argument(
        "--exit-on-failure",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Terminate after a non-ignored health, mask-drift, or runtime failure",
    )
    parser.add_argument(
        "--continue-output-after-health-failure",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Benchmark-only: keep writing after RCT/APT latches; output is not production-valid",
    )
    parser.add_argument("--jpeg-quality", type=int, default=88)
    parser.add_argument(
        "--web-images",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Generate and expose camera/mask images in the web UI; disabled by default to reduce CPU and network load",
    )
    parser.add_argument(
        "--mask-snapshot-images",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Write active/shadow/difference mask PNG snapshots; drift metrics and JSON remain enabled when images are disabled",
    )
    parser.add_argument(
        "--von-neumann-stage",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run the Von Neumann extractor and optional diagnostic output; disable for the simplified temporal-SHA3 pipeline",
    )
    parser.add_argument(
        "--von-neumann-passes",
        type=int,
        default=1,
        help="Number of consecutive Von Neumann extraction passes (0..4); 0 disables the VN stage",
    )
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8087)
    parser.add_argument("--verbose", action="store_true")

    explicit_destinations = collect_explicit_destinations(parser, sys.argv[1:])
    args = parser.parse_args()
    if args.rtsp_url_file:
        try:
            file_url = args.rtsp_url_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            parser.error(f"cannot read --rtsp-url-file: {exc}")
        if not file_url:
            parser.error("--rtsp-url-file is empty")
        if args.rtsp_url and args.rtsp_url != file_url:
            parser.error("use either --rtsp-url or --rtsp-url-file, not both")
        args.rtsp_url = file_url
    args.output_dir = Path(args.output_dir).resolve()
    args.log_file = (
        args.log_file or args.output_dir / "camera_entropy_exposure_benchmark.log"
    ).resolve()

    if args.source_type == "tls-y":
        missing = [name for name in ("tls_host", "tls_ca", "tls_cert", "tls_key", "tls_server_name") if not getattr(args, name)]
        if missing:
            parser.error("tls-y requires: " + ", ".join("--" + name.replace("_", "-") for name in missing))
        for path in (args.tls_ca, args.tls_cert, args.tls_key):
            if not Path(path).is_file():
                parser.error(f"TLS file not found: {path}")
    if args.source_type == "rtsp" and not args.rtsp_url:
        parser.error("rtsp source requires --rtsp-url")
    if args.source_type == "dataset-y":
        if not args.dataset_dir:
            parser.error("dataset-y source requires --dataset-dir")
        args.dataset_dir = args.dataset_dir.expanduser().resolve()
        if args.dataset_verify_cache_dir is not None:
            args.dataset_verify_cache_dir = args.dataset_verify_cache_dir.expanduser().resolve()
        if not (args.dataset_dir / "manifest.json").is_file():
            parser.error(f"dataset manifest not found: {args.dataset_dir / 'manifest.json'}")
        if args.dataset_start_frame < 0:
            parser.error("dataset-start-frame cannot be negative")
        if args.dataset_max_frames < 0:
            parser.error("dataset-max-frames cannot be negative")
        if args.dataset_rate <= 0:
            parser.error("dataset-rate must be positive")
        if args.dataset_poll_seconds <= 0:
            parser.error("dataset-poll-seconds must be positive")
        if not 1 <= args.dataset_verify_workers <= 64:
            parser.error("dataset-verify-workers must be in 1..64")
        if args.dataset_verify_progress_seconds <= 0:
            parser.error("dataset-verify-progress-seconds must be positive")
        if args.dataset_follow_timeout_seconds < 0:
            parser.error("dataset-follow-timeout-seconds cannot be negative")
    if args.source_type == "dataset-y":
        if not args.dataset_dir:
            parser.error("dataset-y source requires --dataset-dir")
        args.dataset_dir = args.dataset_dir.expanduser().resolve()
        if not (args.dataset_dir / "manifest.json").is_file():
            parser.error(f"dataset manifest not found: {args.dataset_dir / 'manifest.json'}")
        if args.dataset_start_frame < 0:
            parser.error("dataset-start-frame cannot be negative")
        if args.dataset_max_frames < 0:
            parser.error("dataset-max-frames cannot be negative")
        if args.dataset_rate <= 0:
            parser.error("dataset-rate must be positive")
    if args.source_connect_timeout_seconds <= 0 or args.source_frame_timeout_seconds <= 0:
        parser.error("source timeouts must be positive")
    if args.rtsp_timeout_seconds <= 0:
        parser.error("rtsp-timeout-seconds must be positive")
    if args.source_type in {"rtsp", "dataset-y"} and args.manual_exposure:
        # RTSP and buffered datasets do not expose generic V4L2 controls.
        args.manual_exposure = False
    if not (0 <= args.clip_low < args.clip_high <= 255):
        parser.error("invalid clip limits")
    if not (0.0 <= args.max_active_clip_rate <= 1.0):
        parser.error("max-active-clip-rate must be in [0, 1]")
    if args.clip_fail_consecutive < 1:
        parser.error("clip-fail-consecutive must be >= 1")
    if args.source_connect_timeout_seconds <= 0:
        parser.error("source-connect-timeout-seconds must be positive")
    if args.source_frame_timeout_seconds <= 0:
        parser.error("source-frame-timeout-seconds must be positive")
    if args.source_reconnect_attempts < 0:
        parser.error("source-reconnect-attempts cannot be negative")
    if args.source_reconnect_backoff_seconds < 0:
        parser.error("source-reconnect-backoff-seconds cannot be negative")
    if args.control_check_seconds < 0:
        parser.error("control-check-seconds cannot be negative")
    if args.control_fail_consecutive < 1:
        parser.error("control-fail-consecutive must be >= 1")
    if args.thermal_warmup_seconds < 0:
        parser.error("thermal-warmup-seconds cannot be negative")
    if args.exposure_value is not None and args.exposure_value <= 0:
        parser.error("exposure-value must be positive")
    if args.max_output_bytes < 0:
        parser.error("max-output-bytes cannot be negative")
    if args.validation_output_bytes < 0:
        parser.error("validation-output-bytes cannot be negative")
    if args.conditioned_output_bytes < 0:
        parser.error("conditioned-output-bytes cannot be negative")
    if args.conditioner_input_bits < 512 or args.conditioner_input_bits % 8:
        parser.error("conditioner-input-bits must be a multiple of 8 and >= 512")
    if args.conditioner == "none" and args.conditioned_output_bytes:
        parser.error("conditioned-output-bytes requires --conditioner sha3-512")
    if args.conditioned_output_bytes and not args.write_conditioned_output:
        parser.error("conditioned-output-bytes requires --write-conditioned-output")
    if args.correlation_every <= 0:
        parser.error("correlation-every must be positive")
    if args.stream_stats_window_pairs <= 0:
        parser.error("stream-stats-window-pairs must be positive")
    if args.mask_snapshot_interval_seconds < 0:
        parser.error("mask-snapshot-interval-seconds cannot be negative")
    if args.mask_drift_history_points < 10:
        parser.error("mask-drift-history-points must be >= 10")
    if args.live_heatmap_interval_seconds < 0:
        parser.error("live-heatmap-interval-seconds cannot be negative")
    if not 1 <= args.live_heatmap_max_stages <= 32:
        parser.error("live-heatmap-max-stages must be in 1..32")
    if args.live_heatmap_min_bytes < 256:
        parser.error("live-heatmap-min-bytes must be >= 256")
    if not 0 <= args.von_neumann_passes <= 4:
        parser.error("von-neumann-passes must be in 0..4")
    if not args.von_neumann_stage:
        args.von_neumann_passes = 0
    elif args.von_neumann_passes == 0:
        args.von_neumann_stage = False
    if args.max_output_bytes and not args.write_output:
        parser.error("max-output-bytes requires --write-output")
    if not args.von_neumann_stage and (args.write_output or args.max_output_bytes):
        parser.error("--no-von-neumann-stage requires --no-write-output and --max-output-bytes 0")
    if args.exit_on_output_limit and not (args.max_output_bytes or args.conditioned_output_bytes):
        parser.error("exit-on-output-limit requires max-output-bytes or conditioned-output-bytes")
    if not (0 < args.mask_p1_min < args.mask_p1_max < 1):
        parser.error("invalid mask p1 range")
    if not (0 < args.mask_transition_min < args.mask_transition_max < 1):
        parser.error("invalid transition range")
    for name in (
        "spatial_step_x", "spatial_step_y", "spatial_block_width", "spatial_block_height",
        "serialization_tile_width", "serialization_tile_height",
    ):
        if getattr(args, name) < 1:
            parser.error(f"{name.replace('_', '-')} must be >= 1")
    if args.spatial_mask_pattern == "grid":
        if not 0 <= args.spatial_phase_x < args.spatial_step_x:
            parser.error("spatial-phase-x must be in [0, spatial-step-x) for grid")
        if not 0 <= args.spatial_phase_y < args.spatial_step_y:
            parser.error("spatial-phase-y must be in [0, spatial-step-y) for grid")
    if args.spatial_mask_pattern == "block":
        if not 0 <= args.spatial_phase_x < args.spatial_block_width:
            parser.error("spatial-phase-x must be in [0, spatial-block-width) for block")
        if not 0 <= args.spatial_phase_y < args.spatial_block_height:
            parser.error("spatial-phase-y must be in [0, spatial-block-height) for block")
    if args.spatial_comparison and args.spatial_mask_pattern != "legacy":
        parser.error(
            "--spatial-comparison uses the fixed compatibility variants; "
            "run custom masks as separate profiles with --no-spatial-comparison"
        )
    if not 1 <= args.lsb_bits <= 4:
        parser.error("lsb-bits must be in 1..4")
    if not math.isfinite(args.assessed_min_entropy) or not 0 < args.assessed_min_entropy <= args.lsb_bits:
        parser.error("assessed-min-entropy must be finite and in (0, lsb-bits]")
    try:
        args.minimum_conditioner_input_bits = minimum_conditioner_input_bits(
            args.lsb_bits, args.entropy_credit_bits_per_pixel
        )
    except ValueError as exc:
        parser.error(str(exc))
    if (
        args.conditioner == "sha3-512"
        and args.conditioner_input_bits < args.minimum_conditioner_input_bits
    ):
        parser.error(
            "conditioner-input-bits is below the entropy-credit budget: "
            f"need at least {args.minimum_conditioner_input_bits} bits for "
            f"lsb-bits={args.lsb_bits}, credit={args.entropy_credit_bits_per_pixel:g}"
        )
    if args.dual_weave_comparison and (args.sample_mode != "xor" or args.lsb_bits != 1):
        parser.error("dual-weave comparison currently requires sample-mode=xor and lsb-bits=1")
    if args.pair_lag_frames < 1:
        parser.error("pair-lag-frames must be >= 1")
    if args.pair_lag_frames > 4096:
        parser.error("pair-lag-frames must be <= 4096")
    if args.dual_weave_comparison:
        invalid_orders = set(args.dual_weave_orders) - {"serpentine", "row-major"}
        if invalid_orders or not args.dual_weave_orders:
            parser.error("dual-weave-orders supports only serpentine,row-major")
        invalid_alignments = set(args.dual_weave_alignments) - {"same-group", "stagger-1", "stagger-2"}
        if invalid_alignments or not args.dual_weave_alignments:
            parser.error("dual-weave-alignments supports only same-group,stagger-1,stagger-2")
        if args.pairing_mode != "disjoint":
            parser.error("dual-weave-comparison requires --pairing-mode disjoint")
        if args.pair_lag_frames % 2:
            parser.error("dual-weave-comparison requires an even pair-lag-frames")
        if args.calibration_pairs % args.pair_lag_frames:
            parser.error("dual-weave-comparison requires calibration-pairs divisible by pair-lag-frames")
        if args.dynamic_clip_filter:
            parser.error("dual-weave-comparison requires a frozen production mask (--no-dynamic-clip-filter)")
        if args.conditioner_input_bits % 16:
            parser.error("dual-weave conditioner input bits must be divisible by 16")
    if args.calibration_pairs < 32:
        parser.error("calibration-pairs must be >= 32")
    expected_apt_window = 1024 if args.lsb_bits == 1 else 512
    if args.apt_window is None:
        args.apt_window = expected_apt_window
    elif args.apt_window != expected_apt_window:
        parser.error(
            f"SP800-90B APT window must be {expected_apt_window} for "
            f"{'binary' if args.lsb_bits == 1 else 'non-binary'} source samples"
        )
    if args.shadow_half_life_pairs <= 0:
        parser.error("shadow-half-life-pairs must be positive")
    if args.shadow_update_every <= 0:
        parser.error("shadow-update-every must be positive")
    if args.shadow_grace_pairs < 0:
        parser.error("shadow-grace-pairs cannot be negative")
    if not (0 < args.shadow_min_active_retention <= 1):
        parser.error("shadow-min-active-retention must be in (0, 1]")
    if not (0 < args.shadow_min_jaccard <= 1):
        parser.error("shadow-min-jaccard must be in (0, 1]")
    if args.shadow_fail_consecutive <= 0:
        parser.error("shadow-fail-consecutive must be positive")
    redacted_argv = list(sys.argv)
    for index, item in enumerate(redacted_argv[:-1]):
        if item == "--rtsp-url":
            redacted_argv[index + 1] = redact_url(redacted_argv[index + 1])
    redacted_argv = [
        ("--rtsp-url=" + redact_url(item.split("=", 1)[1]))
        if item.startswith("--rtsp-url=") else item
        for item in redacted_argv
    ]
    args._command_line = shlex.join(redacted_argv)
    args._startup_parameters = build_startup_parameters(
        parser, args, explicit_destinations
    )
    return args


def main() -> int:
    args = parse_args()
    logger = build_logger(args.log_file, args.verbose)
    logger.info("Starting %s", APP_VERSION)
    service = Service(args, logger)
    service.start()
    app = create_app(service)

    def stop(signum: int, _frame: Any) -> None:
        logger.info("signal %s", signum)
        service.stop()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
    return 2 if service.run_failed_path.exists() else 0


if __name__ == "__main__":
    raise SystemExit(main())
