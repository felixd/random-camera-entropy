#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parallel SHA-256 verification for buffered camera datasets.

The recorder publishes checksums only for closed chunks.  This module verifies
those immutable chunks with configurable parallelism and progress callbacks. It
is shared by dataset-y replay and the final pre-production campaign so both
paths use the same validation rules.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

APP_VERSION = "2026.08.05.camera-entropy-dataset-integrity.8.0.1"
DEFAULT_BLOCK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class ChunkChecksum:
    expected: str
    relative: str
    path: Path
    size: int
    mtime_ns: int


ProgressCallback = Callable[[dict[str, Any]], None]


def _safe_chunk_path(dataset: Path, relative: str) -> Path:
    dataset = dataset.resolve()
    path = (dataset / relative).resolve()
    try:
        path.relative_to(dataset)
    except ValueError as exc:
        raise RuntimeError(f"checksum path escapes dataset: {relative}") from exc
    return path


def load_checksum_entries(
    dataset: Path,
    *,
    allow_incomplete_last_line: bool = False,
) -> tuple[list[ChunkChecksum], str]:
    """Load and validate ``checksums.sha256``.

    Returns parsed entries and SHA-256 of the checksum manifest itself.  The
    manifest digest is useful for safe verification caching.
    """
    dataset = dataset.resolve()
    checksum_file = dataset / "checksums.sha256"
    if not checksum_file.is_file():
        raise RuntimeError(f"missing dataset checksum manifest: {checksum_file}")
    raw = checksum_file.read_bytes()
    manifest_digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    entries: list[ChunkChecksum] = []
    seen: set[str] = set()
    for line_no, raw_line in enumerate(lines, 1):
        if not raw_line.endswith("\n") and allow_incomplete_last_line:
            continue
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise RuntimeError(f"invalid checksums.sha256 line {line_no}")
        expected, relative = parts
        expected = expected.lower()
        relative = relative.lstrip("* ")
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise RuntimeError(f"invalid SHA-256 in checksums.sha256 line {line_no}")
        if relative in seen:
            raise RuntimeError(f"duplicate chunk in checksums.sha256: {relative}")
        path = _safe_chunk_path(dataset, relative)
        if not path.is_file():
            raise RuntimeError(f"missing chunk listed in checksums.sha256: {path}")
        stat = path.stat()
        entries.append(ChunkChecksum(expected, relative, path, stat.st_size, stat.st_mtime_ns))
        seen.add(relative)
    if not entries:
        raise RuntimeError("checksums.sha256 does not contain any closed chunk")
    return entries, manifest_digest


def default_verification_cache_path(dataset: Path, cache_root: Path | None = None) -> Path:
    """Return the stable cache file shared by replay and qualification paths.

    The key depends only on the resolved dataset path.  Cache validity itself is
    still guarded by the full fingerprint (checksum manifest digest, expected
    hashes, file sizes and nanosecond mtimes), so a changed dataset never reuses
    stale verification evidence.
    """
    dataset = dataset.expanduser().resolve()
    root = cache_root.expanduser().resolve() if cache_root is not None else dataset.parent / ".integrity-cache"
    key = hashlib.sha256(str(dataset).encode("utf-8")).hexdigest()[:24]
    return root / f"{key}.json"


def verification_fingerprint(dataset: Path, entries: list[ChunkChecksum], manifest_digest: str) -> dict[str, Any]:
    return {
        "schema": "camera-entropy-dataset-verification-fingerprint-v1",
        "dataset": str(dataset.resolve()),
        "checksums_sha256": manifest_digest,
        "chunks": [
            {
                "relative": entry.relative,
                "expected": entry.expected,
                "size": entry.size,
                "mtime_ns": entry.mtime_ns,
            }
            for entry in entries
        ],
    }


def load_cached_verification(cache_path: Path | None, fingerprint: dict[str, Any]) -> dict[str, Any] | None:
    if cache_path is None or not cache_path.is_file():
        return None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict) or cached.get("verified") is not True:
        return None
    if cached.get("fingerprint") != fingerprint:
        return None
    result = dict(cached.get("result") or {})
    result.update({"verified": True, "cached": True, "cache_path": str(cache_path)})
    return result


def save_cached_verification(cache_path: Path | None, fingerprint: dict[str, Any], result: dict[str, Any]) -> None:
    if cache_path is None:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "camera-entropy-dataset-verification-cache-v1",
        "verified": True,
        "fingerprint": fingerprint,
        "result": result,
    }
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, cache_path)


class _ByteCounter:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.bytes_read = 0

    def add(self, amount: int) -> None:
        with self.lock:
            self.bytes_read += amount

    def get(self) -> int:
        with self.lock:
            return self.bytes_read


def _hash_entry(
    entry: ChunkChecksum,
    *,
    block_bytes: int,
    counter: _ByteCounter,
    stop_event: threading.Event | None,
) -> tuple[ChunkChecksum, str]:
    digest = hashlib.sha256()
    with entry.path.open("rb") as stream:
        while True:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("dataset verification interrupted")
            block = stream.read(block_bytes)
            if not block:
                break
            digest.update(block)
            counter.add(len(block))
    return entry, digest.hexdigest()


def _snapshot(
    *,
    entries: list[ChunkChecksum],
    total_bytes: int,
    completed_chunks: int,
    counter: _ByteCounter,
    started: float,
    workers: int,
    phase: str,
) -> dict[str, Any]:
    elapsed = max(0.0, time.monotonic() - started)
    read_bytes = min(total_bytes, counter.get())
    rate = read_bytes / elapsed if elapsed > 0 else 0.0
    remaining = max(0, total_bytes - read_bytes)
    eta = remaining / rate if rate > 0 else None
    return {
        "schema": "camera-entropy-dataset-verification-progress-v1",
        "phase": phase,
        "verified": phase == "complete",
        "workers": workers,
        "closed_chunks": len(entries),
        "completed_chunks": completed_chunks,
        "total_bytes": total_bytes,
        "bytes_read": read_bytes,
        "fraction": (read_bytes / total_bytes) if total_bytes else 1.0,
        "elapsed_seconds": elapsed,
        "bytes_per_second": rate,
        "eta_seconds": eta,
    }


def verify_dataset_chunks(
    dataset: Path,
    *,
    workers: int = 1,
    block_bytes: int = DEFAULT_BLOCK_BYTES,
    progress_interval_seconds: float = 2.0,
    progress_callback: ProgressCallback | None = None,
    stop_event: threading.Event | None = None,
    allow_incomplete_last_line: bool = False,
    cache_path: Path | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Verify all checksum-published chunks, optionally in parallel.

    Parallel reads are helpful on NVMe/SSD arrays.  On a rotational disk one or
    two workers may be faster than a large worker count, so the caller controls
    the value explicitly.
    """
    dataset = dataset.resolve()
    workers = max(1, min(64, int(workers)))
    if block_bytes < 1024 * 1024:
        raise ValueError("block_bytes must be at least 1 MiB")
    if progress_interval_seconds <= 0:
        raise ValueError("progress_interval_seconds must be positive")
    entries, manifest_digest = load_checksum_entries(
        dataset, allow_incomplete_last_line=allow_incomplete_last_line,
    )
    fingerprint = verification_fingerprint(dataset, entries, manifest_digest)
    if use_cache:
        cached = load_cached_verification(cache_path, fingerprint)
        if cached is not None:
            if progress_callback is not None:
                progress_callback({
                    **cached,
                    "phase": "cache-hit",
                    "completed_chunks": cached.get("closed_chunks", len(entries)),
                    "closed_chunks": cached.get("closed_chunks", len(entries)),
                    "total_bytes": cached.get("bytes", sum(entry.size for entry in entries)),
                    "bytes_read": cached.get("bytes", sum(entry.size for entry in entries)),
                    "fraction": 1.0,
                    "workers": workers,
                })
            return cached

    total_bytes = sum(entry.size for entry in entries)
    counter = _ByteCounter()
    started = time.monotonic()
    completed = 0
    if progress_callback is not None:
        progress_callback(_snapshot(
            entries=entries, total_bytes=total_bytes, completed_chunks=0,
            counter=counter, started=started, workers=workers, phase="verifying",
        ))

    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dataset-sha256")
    futures: set[Future[tuple[ChunkChecksum, str]]] = {
        executor.submit(
            _hash_entry, entry, block_bytes=block_bytes, counter=counter, stop_event=stop_event,
        )
        for entry in entries
    }
    try:
        pending = set(futures)
        last_emit = 0.0
        while pending:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("dataset verification interrupted")
            done, pending = wait(
                pending,
                timeout=progress_interval_seconds,
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                entry, actual = future.result()
                if actual.lower() != entry.expected.lower():
                    raise RuntimeError(
                        f"SHA-256 mismatch: {entry.relative}: {actual} != {entry.expected}"
                    )
                completed += 1
            now = time.monotonic()
            if progress_callback is not None and (done or now - last_emit >= progress_interval_seconds):
                progress_callback(_snapshot(
                    entries=entries, total_bytes=total_bytes, completed_chunks=completed,
                    counter=counter, started=started, workers=workers, phase="verifying",
                ))
                last_emit = now
    except BaseException:
        if stop_event is not None:
            stop_event.set()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    completed_snapshot = _snapshot(
        entries=entries, total_bytes=total_bytes, completed_chunks=completed,
        counter=counter, started=started, workers=workers, phase="complete",
    )
    result = {
        "verified": True,
        "cached": False,
        "closed_chunks": completed,
        "bytes": total_bytes,
        "workers": workers,
        "elapsed_seconds": completed_snapshot["elapsed_seconds"],
        "bytes_per_second": completed_snapshot["bytes_per_second"],
        "completed_utc_unix": time.time(),
        "checksums_sha256": manifest_digest,
    }
    if progress_callback is not None:
        progress_callback(completed_snapshot)
    save_cached_verification(cache_path, fingerprint, result)
    return result
