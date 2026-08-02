#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay completed datasets or follow a dataset while it is still being recorded."""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, BinaryIO, Optional, TextIO

import numpy as np

from frame_sources import FrameSource, SourceFrame

DATASET_FORMAT = "camera-entropy-frame-buffer-v1"
SUPPORTED_STORAGE_MODES = {"y8", "lsb-packed"}
READABLE_STATUSES = {"recording", "complete", "stopped", "failed"}
REQUIRED_INDEX_FIELDS = {
    "sequence",
    "source_frame_id",
    "chunk",
    "offset",
    "payload_bytes",
    "captured_unix_ns",
    "captured_monotonic_ns",
    "source_warmup_seconds",
    "source_connection_generation",
}


class DatasetYSource(FrameSource):
    source_type = "dataset-y"
    supports_controls = False
    timestamp_basis = "recorded_source_monotonic"

    def __init__(self, args: Any, logger: logging.Logger) -> None:
        self.args = args
        self.logger = logger
        # Resolve the symlink once. A running reader stays on the dataset it opened
        # even if frame-buffer-latest is later moved to a newer recording.
        self.dataset_dir = Path(args.dataset_dir).expanduser().resolve()
        self.dataset_manifest: dict[str, Any] = {}
        self.dataset_status = ""
        self.index_file: Optional[TextIO] = None
        self.index_fields: list[str] = []
        self.chunk_file: Optional[BinaryIO] = None
        self.chunk_path: Optional[Path] = None
        self.storage_mode = ""
        self.frame_payload_bytes = 0
        self.frame_count = 0
        self.frames_read = 0
        self.rows_consumed = 0
        self.start_frame = int(getattr(args, "dataset_start_frame", 0))
        self.max_frames = int(getattr(args, "dataset_max_frames", 0))
        self.realtime = bool(getattr(args, "dataset_realtime", False))
        self.rate = float(getattr(args, "dataset_rate", 1.0))
        self.verify_hashes = bool(getattr(args, "dataset_verify_hashes", False))
        self.follow = bool(getattr(args, "dataset_follow", True))
        self.poll_seconds = float(getattr(args, "dataset_poll_seconds", 0.10))
        self.follow_timeout_seconds = float(
            getattr(args, "dataset_follow_timeout_seconds", 0.0)
        )
        self.first_recorded_ns: Optional[int] = None
        self.playback_started_monotonic: Optional[float] = None
        self.last_progress_monotonic = time.monotonic()
        self.has_full_luma = True
        self.connection_generation = 0
        self.reconnect_count = 0

    @property
    def manifest_path(self) -> Path:
        return self.dataset_dir / "manifest.json"

    @property
    def lock_path(self) -> Path:
        return self.dataset_dir / "recording.lock"

    def _load_manifest(self) -> dict[str, Any]:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # manifest.json is replaced atomically by the writer, so this should be
            # rare. While following, tolerate a transient filesystem visibility race.
            if self.dataset_manifest and self.follow:
                self.logger.debug("cannot refresh dataset manifest yet: %s", exc)
                return self.dataset_manifest
            raise RuntimeError(f"cannot read dataset manifest: {exc}") from exc
        if value.get("format") != DATASET_FORMAT:
            raise RuntimeError(f"unsupported dataset format: {value.get('format')!r}")
        status = str(value.get("status", ""))
        if status not in READABLE_STATUSES:
            raise RuntimeError(f"dataset status is not readable: {status!r}")
        self.dataset_manifest = value
        self.dataset_status = status
        self.frame_count = max(self.frame_count, int(value.get("frame_count", 0) or 0))
        return value

    def _can_grow(self) -> bool:
        self._load_manifest()
        return self.dataset_status == "recording" or self.lock_path.exists()

    def _check_follow_timeout(self, context: str) -> None:
        if self.follow_timeout_seconds <= 0:
            return
        idle = time.monotonic() - self.last_progress_monotonic
        if idle >= self.follow_timeout_seconds:
            raise TimeoutError(
                f"timed out after {idle:.3f}s while following active dataset ({context})"
            )

    def _verify_chunks(self) -> None:
        checksum_file = self.dataset_dir / "checksums.sha256"
        if not checksum_file.is_file():
            raise RuntimeError("dataset hash verification requested but checksums.sha256 is missing")
        raw_text = checksum_file.read_text(encoding="utf-8")
        # A writer appends checksums only after closing a chunk. Ignore a possible
        # last unterminated line observed during the append syscall.
        lines = raw_text.splitlines(keepends=True)
        for line_no, raw in enumerate(lines, 1):
            if not raw.endswith("\n") and self._can_grow():
                self.logger.debug("ignoring in-flight checksum line %d", line_no)
                continue
            raw = raw.strip()
            if not raw:
                continue
            try:
                expected, relative = raw.split(None, 1)
            except ValueError as exc:
                raise RuntimeError(f"invalid checksums.sha256 line {line_no}") from exc
            relative = relative.lstrip("* ")
            path = (self.dataset_dir / relative).resolve()
            try:
                path.relative_to(self.dataset_dir)
            except ValueError as exc:
                raise RuntimeError(f"checksum path escapes dataset: {relative}") from exc
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    digest.update(block)
            actual = digest.hexdigest()
            if actual != expected:
                raise RuntimeError(
                    f"dataset checksum mismatch for {relative}: expected={expected}, actual={actual}"
                )

    def open(self) -> None:
        index_path = self.dataset_dir / "frames.csv"
        if not self.manifest_path.is_file():
            raise RuntimeError(f"dataset manifest not found: {self.manifest_path}")
        if not index_path.is_file():
            raise RuntimeError(f"dataset frame index not found: {index_path}")
        manifest = self._load_manifest()
        self.storage_mode = str(manifest.get("storage_mode", ""))
        if self.storage_mode not in SUPPORTED_STORAGE_MODES:
            raise RuntimeError(f"unsupported storage mode: {self.storage_mode!r}")
        self.width = int(manifest.get("width", 0))
        self.height = int(manifest.get("height", 0))
        self.reported_fps = float(manifest.get("reported_fps", 0.0) or 0.0)
        self.frame_payload_bytes = int(manifest.get("frame_payload_bytes", 0))
        if self.width <= 0 or self.height <= 0:
            raise RuntimeError("dataset has invalid dimensions")
        expected_payload = (
            self.width * self.height
            if self.storage_mode == "y8"
            else (self.width * self.height + 7) // 8
        )
        if self.frame_payload_bytes != expected_payload:
            raise RuntimeError(
                f"dataset payload size mismatch: manifest={self.frame_payload_bytes}, expected={expected_payload}"
            )
        if getattr(self.args, "strict_mode", False) and (
            self.width != int(self.args.width) or self.height != int(self.args.height)
        ):
            raise RuntimeError(
                f"dataset dimensions {self.width}x{self.height} do not match requested "
                f"{self.args.width}x{self.args.height}"
            )
        if self.start_frame < 0:
            raise RuntimeError("dataset-start-frame cannot be negative")
        if self.max_frames < 0:
            raise RuntimeError("dataset-max-frames cannot be negative")
        if self.rate <= 0:
            raise RuntimeError("dataset-rate must be positive")
        if self.poll_seconds <= 0:
            raise RuntimeError("dataset-poll-seconds must be positive")
        if self.follow_timeout_seconds < 0:
            raise RuntimeError("dataset-follow-timeout-seconds cannot be negative")
        if self.verify_hashes:
            self.logger.info("verifying SHA-256 of currently closed dataset chunks")
            self._verify_chunks()
            if self._can_grow():
                self.logger.warning(
                    "active dataset verification covers only chunks already closed by the recorder; "
                    "the current growing chunk has no final SHA-256 yet"
                )
        self.has_full_luma = self.storage_mode == "y8"
        self.pixel_format = "Y8" if self.has_full_luma else "Y1-PACKED"
        self.label = f"dataset-y://{self.dataset_dir}"
        self.index_file = index_path.open("r", encoding="utf-8", newline="")
        header_line = self.index_file.readline()
        if not header_line.endswith("\n"):
            raise RuntimeError("dataset index header is incomplete")
        self.index_fields = next(csv.reader([header_line]))
        missing = REQUIRED_INDEX_FIELDS.difference(self.index_fields)
        if missing:
            raise RuntimeError("dataset index missing columns: " + ", ".join(sorted(missing)))
        self.connection_generation = 1
        active = self._can_grow()
        self.logger.info(
            "opened dataset %s mode=%s committed_frames~%d size=%dx%d fps=%.3f start=%d active=%s follow=%s",
            self.dataset_dir,
            self.storage_mode,
            self.frame_count,
            self.width,
            self.height,
            self.reported_fps,
            self.start_frame,
            active,
            self.follow,
        )
        if not self.has_full_luma:
            self.logger.warning(
                "LSB-only dataset: reconstructing neutral Y values 128/129. LSB/temporal algorithms are exact, "
                "but luminance clipping and full-Y diagnostics are not available."
            )

    def _open_chunk(self, relative: str) -> BinaryIO:
        path = (self.dataset_dir / relative).resolve()
        try:
            path.relative_to(self.dataset_dir)
        except ValueError as exc:
            raise RuntimeError(f"chunk path escapes dataset: {relative}") from exc
        if self.chunk_path != path:
            if self.chunk_file is not None:
                self.chunk_file.close()
            self.chunk_file = path.open("rb")
            self.chunk_path = path
        return self.chunk_file

    def _pace(self, captured_monotonic_ns: int) -> None:
        if not self.realtime:
            return
        if self.first_recorded_ns is None:
            self.first_recorded_ns = captured_monotonic_ns
            self.playback_started_monotonic = time.monotonic()
            return
        assert self.playback_started_monotonic is not None
        elapsed_recorded = (captured_monotonic_ns - self.first_recorded_ns) / 1_000_000_000.0
        target = self.playback_started_monotonic + elapsed_recorded / self.rate
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _parse_int(row: dict[str, str], key: str, default: int = 0) -> int:
        value = row.get(key, "")
        return int(value) if value not in {None, ""} else default

    @staticmethod
    def _parse_float(row: dict[str, str], key: str, default: float = 0.0) -> float:
        value = row.get(key, "")
        return float(value) if value not in {None, ""} else default

    def _try_read_index_row(self) -> Optional[dict[str, str]]:
        assert self.index_file is not None
        position = self.index_file.tell()
        line = self.index_file.readline()
        if not line:
            return None
        # Never consume a row until its terminating newline is visible. The row is
        # the commit marker proving that the complete frame payload was published.
        if not line.endswith("\n"):
            self.index_file.seek(position)
            return None
        values = next(csv.reader([line]))
        if len(values) != len(self.index_fields):
            raise RuntimeError(
                f"invalid dataset index row at byte {position}: "
                f"fields={len(values)}, expected={len(self.index_fields)}"
            )
        self.last_progress_monotonic = time.monotonic()
        return dict(zip(self.index_fields, values))

    def _next_index_row(self) -> dict[str, str]:
        while True:
            row = self._try_read_index_row()
            if row is not None:
                self.rows_consumed += 1
                return row
            can_grow = self._can_grow()
            if self.follow and can_grow:
                self._check_follow_timeout("waiting for next committed index row")
                time.sleep(self.poll_seconds)
                continue
            raise EOFError(
                f"dataset exhausted after {self.frames_read} returned frames "
                f"(status={self.dataset_status!r}, follow={self.follow})"
            )

    def _read_payload(self, row: dict[str, str], payload_bytes: int) -> bytes:
        relative = str(row["chunk"])
        offset = self._parse_int(row, "offset")
        while True:
            handle = self._open_chunk(relative)
            handle.seek(offset)
            payload = handle.read(payload_bytes)
            if len(payload) == payload_bytes:
                self.last_progress_monotonic = time.monotonic()
                return payload
            # This should not normally happen because the recorder publishes the
            # payload before the index row. Retrying also makes network filesystems
            # with delayed visibility safe.
            if self.follow and self._can_grow():
                self._check_follow_timeout(
                    f"waiting for {relative} offset={offset} bytes={payload_bytes}"
                )
                time.sleep(self.poll_seconds)
                continue
            raise EOFError(
                f"short read in {relative} at offset {offset}: "
                f"got={len(payload)}, expected={payload_bytes}"
            )

    def read(self) -> SourceFrame:
        if self.index_file is None:
            raise RuntimeError("dataset source is not open")
        if self.max_frames and self.frames_read >= self.max_frames:
            raise EOFError(f"dataset frame limit reached after {self.frames_read} frames")
        while self.rows_consumed < self.start_frame:
            self._next_index_row()
        row = self._next_index_row()
        payload_bytes = self._parse_int(row, "payload_bytes")
        if payload_bytes != self.frame_payload_bytes:
            raise RuntimeError(
                f"frame {row.get('sequence')} payload size changed: {payload_bytes}"
            )
        payload = self._read_payload(row, payload_bytes)
        if self.storage_mode == "y8":
            y = np.frombuffer(payload, dtype=np.uint8).reshape(self.height, self.width).copy()
        else:
            packed = np.frombuffer(payload, dtype=np.uint8)
            bits = np.unpackbits(packed, bitorder="big")[: self.width * self.height]
            # 128 and 129 preserve the exact recorded LSB while remaining away from clipping limits.
            y = (bits.astype(np.uint8) + np.uint8(128)).reshape(self.height, self.width)
        captured_monotonic_ns = self._parse_int(row, "captured_monotonic_ns")
        self._pace(captured_monotonic_ns)
        self.frames_read += 1
        sequence = self._parse_int(row, "sequence")
        metadata: dict[str, Any] = {
            "dataset_sequence": sequence,
            "dataset_storage_mode": self.storage_mode,
            "dataset_has_full_luma": self.has_full_luma,
            "dataset_live_follow": self.follow and (self.dataset_status == "recording" or self.lock_path.exists()),
            "original_source_frame_id": self._parse_int(row, "source_frame_id"),
            "captured_unix_ns": self._parse_int(row, "captured_unix_ns"),
            "captured_monotonic_ns": captured_monotonic_ns,
            "source_warmup_seconds": self._parse_float(row, "source_warmup_seconds"),
            "recorded_source_connection_generation": self._parse_int(
                row, "source_connection_generation"
            ),
            # A dataset is one source epoch from the processor's perspective.
            "source_connection_generation": self.connection_generation,
        }
        timestamp = captured_monotonic_ns / 1_000_000_000.0
        return SourceFrame(self.frames_read, timestamp, y, None, metadata)

    def close(self) -> None:
        if self.chunk_file is not None:
            self.chunk_file.close()
            self.chunk_file = None
            self.chunk_path = None
        if self.index_file is not None:
            self.index_file.close()
            self.index_file = None

    def manifest(self) -> dict[str, Any]:
        return super().manifest() | {
            "dataset_dir": str(self.dataset_dir),
            "dataset_format": self.dataset_manifest.get("format"),
            "dataset_status": self.dataset_status,
            "storage_mode": self.storage_mode,
            "frame_count_at_open": self.frame_count,
            "start_frame": self.start_frame,
            "max_frames": self.max_frames,
            "follow_active_dataset": self.follow,
            "follow_poll_seconds": self.poll_seconds,
            "follow_timeout_seconds": self.follow_timeout_seconds,
            "realtime_playback": self.realtime,
            "playback_rate": self.rate,
            "has_full_luma": self.has_full_luma,
            "original_source": self.dataset_manifest.get("source"),
            "warning": (
                None
                if self.has_full_luma
                else "LSB-only dataset uses neutral 128/129 Y reconstruction; clipping/full-luma diagnostics are unavailable"
            ),
        }
