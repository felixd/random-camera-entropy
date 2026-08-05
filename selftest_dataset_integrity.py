#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from dataset_integrity import default_verification_cache_path, load_checksum_entries, verify_dataset_chunks


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        dataset = root / "dataset"
        chunks = dataset / "chunks"
        chunks.mkdir(parents=True)
        lines: list[str] = []
        total = 0
        for index in range(6):
            payload = bytes(((position * (index + 3) + index) & 0xFF) for position in range(400_000 + index * 17_333))
            path = chunks / f"chunk_{index:06d}.bin"
            path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            lines.append(f"{digest}  chunks/{path.name}\n")
            total += len(payload)
        (dataset / "checksums.sha256").write_text("".join(lines), encoding="utf-8")

        entries, manifest_digest = load_checksum_entries(dataset)
        assert len(entries) == 6
        assert len(manifest_digest) == 64

        snapshots: list[dict] = []
        expected_default = dataset.parent / ".integrity-cache" / (hashlib.sha256(str(dataset.resolve()).encode("utf-8")).hexdigest()[:24] + ".json")
        assert default_verification_cache_path(dataset) == expected_default
        cache = root / "cache" / "verified.json"
        result = verify_dataset_chunks(
            dataset,
            workers=3,
            block_bytes=1024 * 1024,
            progress_interval_seconds=0.01,
            progress_callback=snapshots.append,
            cache_path=cache,
            use_cache=True,
        )
        assert result["verified"] is True
        assert result["cached"] is False
        assert result["closed_chunks"] == 6
        assert result["bytes"] == total
        assert result["workers"] == 3
        assert cache.is_file()
        assert snapshots[0]["phase"] == "verifying"
        assert snapshots[-1]["phase"] == "complete"
        assert snapshots[-1]["fraction"] == 1.0

        cached_snapshots: list[dict] = []
        cached = verify_dataset_chunks(
            dataset,
            workers=4,
            progress_interval_seconds=0.01,
            progress_callback=cached_snapshots.append,
            cache_path=cache,
            use_cache=True,
        )
        assert cached["verified"] is True
        assert cached["cached"] is True
        assert cached_snapshots[-1]["phase"] == "cache-hit"

        # A changed chunk invalidates the cache and must fail verification.
        target = chunks / "chunk_000003.bin"
        damaged = bytearray(target.read_bytes())
        damaged[len(damaged) // 2] ^= 0x80
        target.write_bytes(damaged)
        try:
            verify_dataset_chunks(
                dataset,
                workers=2,
                block_bytes=1024 * 1024,
                progress_interval_seconds=0.01,
                cache_path=cache,
                use_cache=True,
            )
        except RuntimeError as exc:
            assert "SHA-256 mismatch" in str(exc)
        else:
            raise AssertionError("corrupted chunk must fail verification")

        # Cache format remains readable JSON for auditability.
        payload = json.loads(cache.read_text(encoding="utf-8"))
        assert payload["schema"] == "camera-entropy-dataset-verification-cache-v1"

    print("dataset integrity self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
