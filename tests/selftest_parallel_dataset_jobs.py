#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import time
import types

# JobManager itself does not require a live Flask stack.
try:
    import flask  # noqa: F401
except ModuleNotFoundError:
    flask = types.ModuleType("flask")
    for name in ("Flask", "Response"):
        setattr(flask, name, object)
    for name in ("abort", "jsonify", "redirect", "render_template", "render_template_string", "request", "send_file", "url_for"):
        setattr(flask, name, lambda *args, **kwargs: None)
    flask.session = {}
    sys.modules["flask"] = flask

try:
    import werkzeug  # noqa: F401
except ModuleNotFoundError:
    werkzeug = types.ModuleType("werkzeug")
    middleware = types.ModuleType("werkzeug.middleware")
    proxy_fix = types.ModuleType("werkzeug.middleware.proxy_fix")
    security = types.ModuleType("werkzeug.security")
    proxy_fix.ProxyFix = object
    security.check_password_hash = lambda *_args, **_kwargs: False
    sys.modules["werkzeug"] = werkzeug
    sys.modules["werkzeug.middleware"] = middleware
    sys.modules["werkzeug.middleware.proxy_fix"] = proxy_fix
    sys.modules["werkzeug.security"] = security

from app.control.control_server import JobManager, Settings, load_sources


def write_dataset(root: Path) -> Path:
    dataset = root / "data" / "frame-buffer-test"
    (dataset / "chunks").mkdir(parents=True)
    (dataset / "manifest.json").write_text(json.dumps({
        "format": "camera-entropy-frame-buffer-v1",
        "status": "stopped",
        "storage_mode": "y8",
        "width": 2,
        "height": 2,
        "frame_payload_bytes": 4,
        "frame_count": 2,
    }), encoding="utf-8")
    (dataset / "frames.csv").write_text(
        "sequence,source_frame_id,chunk,offset,payload_bytes,captured_unix_ns,captured_monotonic_ns,source_warmup_seconds,source_connection_generation\n",
        encoding="utf-8",
    )
    return dataset


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        data = root / "data"
        data.mkdir()
        dataset = write_dataset(root)
        script = root / "scripts" / "run" / "run_one.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/usr/bin/env bash\nset -euo pipefail\nsleep 1\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        sources_file = root / "sources.json"
        sources_file.write_text(json.dumps({"sources": [{
            "id": "buffered", "label": "Buffered", "source_type": "dataset-y",
            "env": {"DATASET_DIR": str(dataset)},
        }, {
            "id": "live", "label": "Live", "source_type": "v4l2",
            "env": {"DEVICE": "/dev/video-test"},
        }]}), encoding="utf-8")
        secret = root / "secret"
        secret.write_text("x" * 64, encoding="utf-8")
        settings = Settings(
            root=root,
            data_root=data,
            sources_file=sources_file,
            credentials_file=root / "unused",
            secret_file=secret,
            host="127.0.0.1",
            port=8087,
            worker_host="127.0.0.1",
            worker_port=19087,
            auth_mode="none",
            secure_cookie=False,
            trust_proxy=False,
            share_token_file=None,
        )
        old_limit = os.environ.get("MAX_DATASET_JOBS")
        os.environ["MAX_DATASET_JOBS"] = "4"
        try:
            manager = JobManager(settings, load_sources(sources_file), logging.getLogger("parallel-dataset-selftest"))
            payload = {
                "profile": "single",
                "source_id": "buffered",
                "dataset_scope": "all-available",
                "sample_mode": "xor",
                "lsb_bits": 1,
            }
            first = manager.start(payload)
            second = manager.start(payload)
            assert first["worker_port"] != second["worker_port"]
            assert len(manager.active_jobs()) == 2
            # One LIVE worker may coexist with read-only dataset workers. A second
            # LIVE worker must still be rejected.
            live_payload = {k: v for k, v in payload.items() if not k.startswith("dataset_")}
            live_payload["source_id"] = "live"
            live = manager.start(live_payload)
            assert live["worker_port"] not in {first["worker_port"], second["worker_port"]}
            assert len(manager.active_jobs()) == 3
            try:
                manager.start(live_payload)
            except RuntimeError as exc:
                assert "LIVE" in str(exc)
            else:
                raise AssertionError("second LIVE job was not rejected")
            deadline = time.monotonic() + 5.0
            while manager.active_jobs() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not manager.active_jobs()
            # Give watcher threads time to persist terminal status before the
            # temporary state directory is removed.
            time.sleep(0.2)
            rows = manager.list_jobs(10)
            assert len(rows) == 3
            assert all(row["status"] == "complete" for row in rows), rows
        finally:
            if old_limit is None:
                os.environ.pop("MAX_DATASET_JOBS", None)
            else:
                os.environ["MAX_DATASET_JOBS"] = old_limit

    print("parallel dataset-y jobs self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
