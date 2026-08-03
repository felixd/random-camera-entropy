#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import logging
import tempfile
import time
from pathlib import Path

from control_server import (
    ALLOWED_PROFILES,
    JobManager,
    Settings,
    create_app,
    load_sources,
    safe_path,
    scan_data_root,
)

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


def write_stopped_dataset(data_root: Path) -> Path:
    dataset = data_root / "frame-buffer-20260802T155643Z"
    chunks = dataset / "chunks"
    chunks.mkdir(parents=True)
    width, height = 8, 4
    payload_size = width * height
    payload = bytes(range(payload_size)) * 2
    (chunks / "chunk_000000.bin").write_bytes(payload)
    with (dataset / "frames.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        for sequence in range(2):
            writer.writerow(
                {
                    "sequence": sequence,
                    "source_frame_id": sequence + 1,
                    "chunk": "chunks/chunk_000000.bin",
                    "offset": sequence * payload_size,
                    "payload_bytes": payload_size,
                    "captured_unix_ns": 1_700_000_000_000_000_000 + sequence,
                    "captured_monotonic_ns": 2_000_000_000 + sequence,
                    "source_warmup_seconds": 3600 + sequence,
                    "source_connection_generation": 1,
                }
            )
    (dataset / "checksums.sha256").write_text("", encoding="utf-8")
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "format": "camera-entropy-frame-buffer-v1",
                "status": "stopped",
                "storage_mode": "y8",
                "width": width,
                "height": height,
                "frame_payload_bytes": payload_size,
                "frame_count": 2,
                "bytes_written": len(payload),
            }
        ),
        encoding="utf-8",
    )
    (data_root / "frame-buffer-latest").symlink_to(dataset.name, target_is_directory=True)
    return dataset


def make_settings(root: Path, data: Path, sources: Path, secret: Path) -> Settings:
    return Settings(
        root=root,
        data_root=data,
        sources_file=sources,
        credentials_file=root / "unused",
        secret_file=secret,
        host="127.0.0.1",
        port=8087,
        worker_host="127.0.0.1",
        worker_port=18087,
        auth_mode="none",
        secure_cookie=False,
        trust_proxy=False,
        share_token_file=None,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        data = base / "data"
        data.mkdir()
        (data / "sample").mkdir()
        (data / "sample" / "x.json").write_text("{}", encoding="utf-8")

        # Regression: an ordinary run report has no dual_weave_report.json.
        ordinary = data / "ordinary-run"
        ordinary.mkdir()
        (ordinary / "run_report.html").write_text("<h1>run</h1>", encoding="utf-8")

        # Incomplete report metadata must never break the persistent control plane.
        broken = data / "broken-dual-run"
        broken.mkdir()
        (broken / "run_report.html").write_text("<h1>run</h1>", encoding="utf-8")
        (broken / "dual_weave_report.json").write_text("null", encoding="utf-8")

        campaign = data / "broken-campaign"
        campaign.mkdir()
        (campaign / "dual_weave_campaign_report.html").write_text(
            "<h1>campaign</h1>", encoding="utf-8"
        )
        (campaign / "dual_weave_campaign_summary.json").write_text("{", encoding="utf-8")

        qualification = data / "broken-qualification"
        qualification.mkdir()
        (qualification / "qualification_report.html").write_text(
            "<h1>qualification</h1>", encoding="utf-8"
        )
        (qualification / "qualification_summary.json").write_text("[]", encoding="utf-8")

        dataset = write_stopped_dataset(data)
        sources_path = base / "sources.json"
        sources_path.write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "id": "local",
                            "label": "Local",
                            "source_type": "v4l2",
                            "env": {"DEVICE": "/dev/null"},
                        },
                        {
                            "id": "buffered-latest",
                            "label": "Buffered",
                            "source_type": "dataset-y",
                            "env": {
                                "DATASET_DIR": "data/frame-buffer-latest",
                                # Deliberately wrong: preflight must take geometry from manifest.
                                "WIDTH": "1280",
                                "HEIGHT": "720",
                                "DATASET_FOLLOW": "1",
                            },
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        secret = base / "secret"
        secret.write_text("x" * 64, encoding="utf-8")

        settings = make_settings(base, data, sources_path, secret)
        assert ALLOWED_PROFILES["dual-weave-stagger2"] == "smoke_dual_weave_stagger2.sh"
        assert ALLOWED_PROFILES["temporal-sha3"] == "smoke_temporal_sha3.sh"
        assert ALLOWED_PROFILES["production-assessment"] == "qualification_production_assessment.py"
        loaded_sources = load_sources(sources_path)
        assert loaded_sources[0]["id"] == "local"
        manager = JobManager(settings, loaded_sources, logging.getLogger("control-selftest"))

        env, slug, _output = manager._build_environment(
            {
                "web_images": False,
                "mask_snapshot_images": False,
                "live_byte_diagnostics": True,
                "live_heatmap_interval_seconds": "15",
                "live_heatmap_max_stages": "7",
                "live_heatmap_min_bytes": "8192",
            },
            loaded_sources[0],
            "dual-weave-stagger2",
            "1234567890abcdef",
        )
        assert env["WEB_IMAGES"] == "0"
        assert env["MASK_SNAPSHOT_IMAGES"] == "0"
        assert env["LIVE_BYTE_DIAGNOSTICS"] == "1"
        assert env["LIVE_HEATMAP_INTERVAL_SECONDS"] == "15"
        assert env["LIVE_HEATMAP_MAX_STAGES"] == "7"
        assert env["LIVE_HEATMAP_MIN_BYTES"] == "8192"
        assert env["ASSESSMENT_LEVEL"] == "full"
        assert slug.startswith("web-dual-weave-stagger2-")

        dataset_source = next(item for item in loaded_sources if item["id"] == "buffered-latest")
        dataset_env, _, _ = manager._build_environment(
            {}, dataset_source, "temporal-sha3", "fedcba0987654321"
        )
        assert Path(dataset_env["DATASET_DIR"]) == dataset.resolve()
        assert dataset_env["WIDTH"] == "8"
        assert dataset_env["HEIGHT"] == "4"

        assert safe_path(data, "sample/x.json").is_file()
        rows = scan_data_root(data, 20)
        names = {row["name"] for row in rows}
        assert names >= {
            "ordinary-run",
            "broken-dual-run",
            "broken-campaign",
            "broken-qualification",
        }
        assert not any(name.startswith("frame-buffer-") for name in names)

        # Regression for HTTP 500: with start_new_session=True the PGID is the PID.
        # A child that exits immediately must still create a tracked job instead of
        # racing os.getpgid(pid) and raising ProcessLookupError.
        exit_script = base / "exit-immediately.sh"
        exit_script.write_text("#!/usr/bin/env bash\nexit 23\n", encoding="utf-8")
        exit_script.chmod(0o755)
        ALLOWED_PROFILES["selftest-exit"] = exit_script.name
        try:
            job = manager.start({"profile": "selftest-exit", "source_id": "local"})
            assert job["pgid"] == job["pid"]
            assert Path(job["log_file"]).is_file()
            deadline = time.monotonic() + 5.0
            final = None
            while time.monotonic() < deadline:
                final = manager._load_job(job["id"])
                if final and final.get("status") == "failed":
                    break
                time.sleep(0.02)
            assert final is not None
            assert final["status"] == "failed"
            assert final["returncode"] == 23
        finally:
            ALLOWED_PROFILES.pop("selftest-exit", None)

        # Flask app uses the real project root for templates but the same data fixtures.
        project_root = Path(__file__).resolve().parent
        app_settings = make_settings(project_root, data, sources_path, secret)
        app = create_app(app_settings)
        app.testing = True
        client = app.test_client()
        assert client.get("/healthz").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/data/").status_code == 200
        assert client.get("/data/sample/x.json").status_code == 200
        assert client.get("/data/../secret").status_code == 404

    print("control_selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
