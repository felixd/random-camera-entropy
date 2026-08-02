#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import json
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dataset_frame_source import DatasetYSource

INDEX_FIELDS = [
    "sequence",
    "source_frame_id",
    "chunk",
    "offset",
    "payload_bytes",
    "captured_unix_ns",
    "captured_monotonic_ns",
    "source_warmup_seconds",
    "source_connection_generation",
]


def write_dataset(root: Path, frames: list[np.ndarray], mode: str) -> Path:
    dataset = root / f"dataset-{mode}"
    chunks = dataset / "chunks"
    chunks.mkdir(parents=True)
    height, width = frames[0].shape
    payloads: list[bytes] = []
    for frame in frames:
        assert frame.shape == (height, width)
        y = np.ascontiguousarray(frame, dtype=np.uint8)
        if mode == "y8":
            payloads.append(y.tobytes(order="C"))
        else:
            bits = (y.reshape(-1) & np.uint8(1)).astype(np.uint8, copy=False)
            payloads.append(np.packbits(bits, bitorder="big").tobytes())
    chunk = chunks / "chunk_000000.bin"
    offsets: list[int] = []
    with chunk.open("wb") as handle:
        for payload in payloads:
            offsets.append(handle.tell())
            handle.write(payload)
    digest = hashlib.sha256(chunk.read_bytes()).hexdigest()
    (dataset / "checksums.sha256").write_text(
        f"{digest}  chunks/{chunk.name}\n", encoding="utf-8"
    )
    with (dataset / "frames.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        for sequence, (offset, payload) in enumerate(zip(offsets, payloads)):
            writer.writerow(
                {
                    "sequence": sequence,
                    "source_frame_id": 100 + sequence,
                    "chunk": f"chunks/{chunk.name}",
                    "offset": offset,
                    "payload_bytes": len(payload),
                    "captured_unix_ns": 1_700_000_000_000_000_000 + sequence,
                    "captured_monotonic_ns": 2_000_000_000 + sequence * 100_000_000,
                    "source_warmup_seconds": f"{120.0 + sequence:.3f}",
                    "source_connection_generation": 3,
                }
            )
    payload_bytes = len(payloads[0])
    manifest = {
        "format": "camera-entropy-frame-buffer-v1",
        "writer_version": "selftest",
        "status": "stopped",
        "storage_mode": mode,
        "width": width,
        "height": height,
        "reported_fps": 10.0,
        "frame_payload_bytes": payload_bytes,
        "frame_count": len(frames),
        "bytes_written": sum(map(len, payloads)),
        "source": {"source_type": "selftest"},
    }
    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dataset


def source_args(dataset: Path, width: int, height: int) -> SimpleNamespace:
    return SimpleNamespace(
        dataset_dir=dataset,
        dataset_start_frame=0,
        dataset_max_frames=0,
        dataset_realtime=False,
        dataset_rate=1.0,
        dataset_verify_hashes=True,
        dataset_follow=False,
        dataset_poll_seconds=0.01,
        dataset_follow_timeout_seconds=0.0,
        strict_mode=True,
        width=width,
        height=height,
    )


def main() -> int:
    logger = logging.getLogger("selftest-buffered-dataset")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        original = [
            np.arange(20, dtype=np.uint8).reshape(4, 5),
            (np.arange(20, dtype=np.uint8).reshape(4, 5) * 7 + 3).astype(np.uint8),
        ]
        for mode in ("y8", "lsb-packed"):
            dataset = write_dataset(root, original, mode)
            source = DatasetYSource(source_args(dataset, 5, 4), logger)
            source.open()
            try:
                received = [source.read(), source.read()]
                if mode == "y8":
                    assert np.array_equal(received[0].y, original[0])
                    assert np.array_equal(received[1].y, original[1])
                    assert source.has_full_luma is True
                else:
                    assert np.array_equal(received[0].y & 1, original[0] & 1)
                    assert np.array_equal(received[1].y & 1, original[1] & 1)
                    assert set(np.unique(received[0].y)).issubset({128, 129})
                    assert source.has_full_luma is False
                assert received[0].metadata["source_warmup_seconds"] == 120.0
                assert received[1].metadata["original_source_frame_id"] == 101
                try:
                    source.read()
                except EOFError:
                    pass
                else:
                    raise AssertionError("stopped dataset must end with EOF after committed rows")
            finally:
                source.close()
    print("selftest_buffered_dataset: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
