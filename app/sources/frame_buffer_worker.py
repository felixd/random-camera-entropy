#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record source frames into live-readable chunked Y8 or packed-LSB datasets."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import shutil
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Optional

import numpy as np

from app.sources.frame_sources import create_frame_source

APP_VERSION = "2026.08.05.camera-entropy-frame-buffer.8.0.1"
DATASET_FORMAT = "camera-entropy-frame-buffer-v1"
DEFAULT_LIMIT_BYTES = 300_000_000_000
DEFAULT_CHUNK_BYTES = 1_073_741_824
DEFAULT_RESERVE_BYTES = 2_000_000_000
INDEX_FIELDS = (
    "sequence",
    "source_frame_id",
    "chunk",
    "offset",
    "payload_bytes",
    "captured_unix_ns",
    "captured_monotonic_ns",
    "source_warmup_seconds",
    "source_connection_generation",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_output_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("data") / f"frame-buffer-{stamp}"


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("camera-entropy-frame-buffer")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.handlers[:] = [handler]
    return logger


class ChunkWriter:
    def __init__(
        self,
        dataset_dir: Path,
        chunk_target_bytes: int,
        checksums: Any,
        logger: logging.Logger,
    ) -> None:
        self.dataset_dir = dataset_dir
        self.chunks_dir = dataset_dir / "chunks"
        self.chunk_target_bytes = chunk_target_bytes
        self.checksums = checksums
        self.logger = logger
        self.chunk_number = -1
        self.handle: Optional[BinaryIO] = None
        self.relative_path = ""
        self.offset = 0
        self.digest = hashlib.sha256()

    def _open_next(self) -> None:
        self.close_chunk()
        self.chunk_number += 1
        self.relative_path = f"chunks/chunk_{self.chunk_number:06d}.bin"
        path = self.dataset_dir / self.relative_path
        self.handle = path.open("xb")
        self.offset = 0
        self.digest = hashlib.sha256()
        self.logger.info("opened chunk %s", self.relative_path)

    def write_frame(self, payload: bytes) -> tuple[str, int]:
        if self.handle is None or (
            self.offset > 0 and self.offset + len(payload) > self.chunk_target_bytes
        ):
            self._open_next()
        assert self.handle is not None
        start = self.offset
        self.handle.write(payload)
        self.digest.update(payload)
        self.offset += len(payload)
        return self.relative_path, start

    def publish(self) -> None:
        """Make the complete current payload visible before publishing its index row."""
        if self.handle is not None:
            self.handle.flush()

    def close_chunk(self) -> None:
        if self.handle is None:
            return
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        digest = self.digest.hexdigest()
        self.checksums.write(f"{digest}  {self.relative_path}\n")
        self.checksums.flush()
        os.fsync(self.checksums.fileno())
        self.logger.info(
            "closed chunk %s bytes=%d sha256=%s",
            self.relative_path,
            self.offset,
            digest,
        )
        self.handle = None

    def close(self) -> None:
        self.close_chunk()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record complete Y8 frames or packed whole-frame LSBs into chunked datasets"
    )
    parser.add_argument("--source-type", choices=("v4l2", "tls-y", "rtsp"), default="v4l2")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--storage-mode",
        choices=("y8", "lsb-packed"),
        default="y8",
        help="y8 stores one byte per pixel; lsb-packed stores one bit per pixel",
    )
    parser.add_argument("--limit-bytes", type=int, default=DEFAULT_LIMIT_BYTES)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--reserve-free-bytes", type=int, default=DEFAULT_RESERVE_BYTES)
    parser.add_argument(
        "--update-latest-link",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Publish data/frame-buffer-latest while recording and frame-buffer-latest-complete on clean completion",
    )
    parser.add_argument("--manifest-update-seconds", type=float, default=10.0)

    parser.add_argument("--device")
    parser.add_argument("--vid", default="041e")
    parser.add_argument("--pid", default="4097")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--camera-fps", type=float, default=10.0)
    parser.add_argument("--strict-mode", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--manual-exposure", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--exposure-value", type=int, default=7000)

    parser.add_argument("--tls-host")
    parser.add_argument("--tls-port", type=int, default=9443)
    parser.add_argument("--tls-ca", type=Path)
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument("--tls-server-name")
    parser.add_argument("--source-connect-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--source-frame-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--source-reconnect-attempts", type=int, default=5)
    parser.add_argument("--source-reconnect-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--allow-source-frame-gaps", action=argparse.BooleanOptionalAction, default=False)

    parser.add_argument("--rtsp-url")
    parser.add_argument("--rtsp-url-file", type=Path)
    parser.add_argument("--rtsp-transport", choices=("tcp", "udp", "http", "https"), default="tcp")
    parser.add_argument("--rtsp-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--rtsp-luma-mode", choices=("extract-y", "gray-convert"), default="extract-y")
    parser.add_argument("--ffmpeg-bin", default="ffmpeg")
    parser.add_argument("--ffprobe-bin", default="ffprobe")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.output_dir is None:
        args.output_dir = default_output_dir()
    args.output_dir = args.output_dir.expanduser().resolve()
    if args.limit_bytes <= 0:
        parser.error("limit-bytes must be positive")
    if args.chunk_bytes <= 0:
        parser.error("chunk-bytes must be positive")
    if args.reserve_free_bytes < 0:
        parser.error("reserve-free-bytes cannot be negative")
    if args.width <= 0 or args.height <= 0 or args.camera_fps <= 0:
        parser.error("width, height and camera-fps must be positive")
    if args.manifest_update_seconds <= 0:
        parser.error("manifest-update-seconds must be positive")
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
    if args.source_type == "tls-y":
        missing = [
            name
            for name in ("tls_host", "tls_ca", "tls_cert", "tls_key", "tls_server_name")
            if not getattr(args, name)
        ]
        if missing:
            parser.error("tls-y requires: " + ", ".join("--" + x.replace("_", "-") for x in missing))
        for path in (args.tls_ca, args.tls_cert, args.tls_key):
            if not Path(path).is_file():
                parser.error(f"TLS file not found: {path}")
    if args.source_type == "rtsp" and not args.rtsp_url:
        parser.error("rtsp source requires --rtsp-url or --rtsp-url-file")
    if args.source_type == "rtsp":
        args.manual_exposure = False
    return args


def payload_for_frame(y: np.ndarray, storage_mode: str) -> bytes:
    y8 = np.ascontiguousarray(y, dtype=np.uint8)
    if storage_mode == "y8":
        return y8.tobytes(order="C")
    bits = (y8.reshape(-1) & np.uint8(1)).astype(np.uint8, copy=False)
    return np.packbits(bits, bitorder="big").tobytes()


def update_dataset_link(
    dataset_dir: Path, logger: logging.Logger, link_name: str = "frame-buffer-latest"
) -> None:
    link = dataset_dir.parent / link_name
    temporary = dataset_dir.parent / f".{link_name}.tmp"
    try:
        temporary.unlink(missing_ok=True)
        # Relative link survives moving the entire data directory.
        temporary.symlink_to(dataset_dir.name, target_is_directory=True)
        os.replace(temporary, link)
        logger.info("updated dataset link: %s -> %s", link, dataset_dir.name)
    except OSError as exc:
        logger.warning("cannot update dataset link %s: %s", link, exc)


def run(args: argparse.Namespace, logger: logging.Logger) -> int:
    output_dir = args.output_dir
    if output_dir.exists():
        raise RuntimeError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    (output_dir / "chunks").mkdir()
    lock_path = output_dir / "recording.lock"
    lock_path.write_text(f"pid={os.getpid()}\nstarted={utc_now()}\n", encoding="utf-8")

    source = create_frame_source(args, logger)
    stopped = False
    signal_number: Optional[int] = None

    def on_signal(signum: int, _frame: Any) -> None:
        nonlocal stopped, signal_number
        signal_number = signum
        stopped = True
        logger.info("signal %d received; finishing current frame", signum)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    started_utc = utc_now()
    recorder_started_monotonic = time.monotonic()
    frame_count = 0
    bytes_written = 0
    last_manifest_update = 0.0
    status = "recording"
    failure: Optional[str] = None
    source_manifest: dict[str, Any] = {}
    frame_payload_bytes = 0
    manifest: dict[str, Any] = {}

    try:
        source.open()
        source_manifest = source.manifest()
        width = int(source.width)
        height = int(source.height)
        reported_fps = float(source.reported_fps)
        frame_payload_bytes = width * height if args.storage_mode == "y8" else (width * height + 7) // 8
        if frame_payload_bytes > args.chunk_bytes:
            raise RuntimeError(
                f"one frame ({frame_payload_bytes} bytes) exceeds chunk size ({args.chunk_bytes} bytes)"
            )
        estimated_frames = args.limit_bytes // frame_payload_bytes
        if estimated_frames < 2:
            raise RuntimeError("limit is too small to store at least two complete frames")
        manifest = {
            "format": DATASET_FORMAT,
            "writer_version": APP_VERSION,
            "status": status,
            "started_utc": started_utc,
            "completed_utc": None,
            "storage_mode": args.storage_mode,
            "width": width,
            "height": height,
            "reported_fps": reported_fps,
            "pixels_per_frame": width * height,
            "valid_bits_per_frame": width * height,
            "frame_payload_bytes": frame_payload_bytes,
            "target_bytes": args.limit_bytes,
            "chunk_target_bytes": args.chunk_bytes,
            "reserve_free_bytes": args.reserve_free_bytes,
            "frame_count": 0,
            "bytes_written": 0,
            "source": source_manifest,
            "integrity": {
                "chunk_hash": "sha256",
                "index": "frames.csv",
                "checksums": "checksums.sha256",
                "live_commit_protocol": "flush-payload-then-flush-index-row",
            },
        }
        atomic_json(output_dir / "manifest.json", manifest)
        logger.info(
            "recording %s frames from %s into %s, target=%d bytes, frame=%d bytes",
            args.storage_mode,
            source.label,
            output_dir,
            args.limit_bytes,
            frame_payload_bytes,
        )
        with (output_dir / "frames.csv").open("x", encoding="utf-8", newline="") as index_handle, (
            output_dir / "checksums.sha256"
        ).open("x", encoding="utf-8") as checksum_handle:
            index = csv.DictWriter(index_handle, fieldnames=INDEX_FIELDS)
            index.writeheader()
            index_handle.flush()
            os.fsync(index_handle.fileno())
            checksum_handle.flush()
            os.fsync(checksum_handle.fileno())
            if args.update_latest_link:
                # From this point frame-buffer-latest intentionally means the newest
                # dataset, including one that is currently growing. Readers use the
                # index rows as commit markers and can safely follow it live.
                update_dataset_link(output_dir, logger, "frame-buffer-latest")
            writer = ChunkWriter(output_dir, args.chunk_bytes, checksum_handle, logger)
            try:
                while not stopped:
                    if bytes_written + frame_payload_bytes > args.limit_bytes:
                        status = "complete"
                        break
                    if frame_count % 128 == 0:
                        free = shutil.disk_usage(output_dir).free
                        required = frame_payload_bytes + args.reserve_free_bytes
                        if free < required:
                            raise RuntimeError(
                                f"insufficient free space: free={free}, required reserve+frame={required}"
                            )
                    frame = source.read()
                    payload = payload_for_frame(frame.y, args.storage_mode)
                    if len(payload) != frame_payload_bytes:
                        raise RuntimeError(
                            f"encoded frame size changed: got={len(payload)}, expected={frame_payload_bytes}"
                        )
                    relative_chunk, offset = writer.write_frame(payload)
                    # Commit ordering for concurrent readers:
                    #   1. write and flush the complete payload,
                    #   2. append and flush its frames.csv row.
                    # A visible index row therefore always points at a complete frame.
                    writer.publish()
                    metadata = frame.metadata or {}
                    captured_monotonic_ns = int(
                        metadata.get("captured_monotonic_ns")
                        or round(float(frame.timestamp_monotonic) * 1_000_000_000)
                    )
                    captured_unix_ns = int(metadata.get("captured_unix_ns") or time.time_ns())
                    source_warmup_seconds = metadata.get("source_warmup_seconds")
                    if source_warmup_seconds is None:
                        source_warmup_seconds = max(
                            0.0, float(frame.timestamp_monotonic) - recorder_started_monotonic
                        )
                    index.writerow(
                        {
                            "sequence": frame_count,
                            "source_frame_id": int(frame.frame_id),
                            "chunk": relative_chunk,
                            "offset": offset,
                            "payload_bytes": frame_payload_bytes,
                            "captured_unix_ns": captured_unix_ns,
                            "captured_monotonic_ns": captured_monotonic_ns,
                            "source_warmup_seconds": f"{float(source_warmup_seconds):.9f}",
                            "source_connection_generation": int(
                                metadata.get("source_connection_generation", 0)
                            ),
                        }
                    )
                    index_handle.flush()
                    frame_count += 1
                    bytes_written += frame_payload_bytes
                    now = time.monotonic()
                    if now - last_manifest_update >= args.manifest_update_seconds:
                        index_handle.flush()
                        os.fsync(index_handle.fileno())
                        manifest["frame_count"] = frame_count
                        manifest["bytes_written"] = bytes_written
                        manifest["last_update_utc"] = utc_now()
                        atomic_json(output_dir / "manifest.json", manifest)
                        last_manifest_update = now
                        logger.info(
                            "progress frames=%d bytes=%d/%d (%.2f%%)",
                            frame_count,
                            bytes_written,
                            args.limit_bytes,
                            100.0 * bytes_written / args.limit_bytes,
                        )
                if stopped:
                    status = "stopped"
                elif status != "complete":
                    status = "complete"
            finally:
                writer.close()
                index_handle.flush()
                os.fsync(index_handle.fileno())
    except Exception as exc:
        status = "failed"
        failure = f"{type(exc).__name__}: {exc}"
        logger.exception("frame-buffer recording failed")
    finally:
        try:
            source.close()
        except Exception:
            logger.exception("cannot close source")
        manifest.update(
            {
                "status": status,
                "completed_utc": utc_now(),
                "frame_count": frame_count,
                "bytes_written": bytes_written,
                "failure": failure,
                "signal": signal_number,
            }
        )
        atomic_json(output_dir / "manifest.json", manifest)
        lock_path.unlink(missing_ok=True)
        marker_name = {
            "complete": "READY.json",
            "stopped": "STOPPED.json",
            "failed": "FAILED.json",
        }.get(status, "STOPPED.json")
        atomic_json(
            output_dir / marker_name,
            {
                "status": status,
                "timestamp_utc": utc_now(),
                "frame_count": frame_count,
                "bytes_written": bytes_written,
                "failure": failure,
            },
        )
        if status in {"complete", "stopped"} and frame_count >= 2 and args.update_latest_link:
            # Keep a stable pointer to the newest finished dataset as well as the
            # live-capable frame-buffer-latest pointer.
            update_dataset_link(output_dir, logger, "frame-buffer-latest")
            update_dataset_link(output_dir, logger, "frame-buffer-latest-complete")
    logger.info(
        "recording finished status=%s frames=%d bytes=%d directory=%s",
        status,
        frame_count,
        bytes_written,
        output_dir,
    )
    return 0 if status in {"complete", "stopped"} and frame_count >= 2 else 1


def main() -> int:
    args = parse_args()
    logger = build_logger(args.verbose)
    logger.info("starting %s", APP_VERSION)
    try:
        return run(args, logger)
    except Exception:
        logger.exception("fatal recorder startup error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
