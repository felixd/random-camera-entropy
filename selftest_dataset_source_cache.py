#!/usr/bin/env python3
from __future__ import annotations

from argparse import Namespace
import hashlib
import json
import logging
from pathlib import Path
import tempfile

from dataset_frame_source import DatasetYSource
from dataset_integrity import default_verification_cache_path


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def make_args(dataset: Path) -> Namespace:
    return Namespace(
        dataset_dir=dataset,
        dataset_start_frame=0,
        dataset_max_frames=0,
        dataset_realtime=False,
        dataset_rate=1.0,
        dataset_verify_hashes=True,
        dataset_verify_cache=True,
        dataset_verify_cache_dir=None,
        dataset_verify_workers=2,
        dataset_verify_progress_seconds=0.01,
        dataset_follow=False,
        dataset_poll_seconds=0.01,
        dataset_follow_timeout_seconds=0.0,
        strict_mode=False,
        width=2,
        height=2,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        dataset = root / "frame-buffer-test"
        chunks = dataset / "chunks"
        chunks.mkdir(parents=True)
        payload = b"\x10\x11\x12\x13"
        chunk = chunks / "chunk_000000.bin"
        chunk.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        (dataset / "checksums.sha256").write_text(
            f"{digest}  chunks/{chunk.name}\n", encoding="utf-8"
        )
        (dataset / "manifest.json").write_text(json.dumps({
            "format": "camera-entropy-frame-buffer-v1",
            "status": "stopped",
            "storage_mode": "y8",
            "width": 2,
            "height": 2,
            "frame_payload_bytes": 4,
            "frame_count": 1,
            "reported_fps": 1.0,
        }), encoding="utf-8")
        (dataset / "frames.csv").write_text(
            "sequence,source_frame_id,chunk,offset,payload_bytes,captured_unix_ns,"
            "captured_monotonic_ns,source_warmup_seconds,source_connection_generation\n"
            "0,0,chunks/chunk_000000.bin,0,4,1,1,0,1\n",
            encoding="utf-8",
        )

        logger = logging.getLogger("dataset-source-cache-selftest")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = ListHandler()
        logger.handlers[:] = [handler]

        first = DatasetYSource(make_args(dataset), logger)
        first.open()
        first.close()
        cache = default_verification_cache_path(dataset)
        assert cache.is_file(), cache
        assert not any("cache hit" in line.lower() for line in handler.messages)

        handler.messages.clear()
        second = DatasetYSource(make_args(dataset), logger)
        second.open()
        second.close()
        assert any("cache hit" in line.lower() for line in handler.messages), handler.messages

    print("dataset source verification cache self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
