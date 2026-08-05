#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import tempfile
import types

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

from control_server import JobManager, Settings, load_sources


HEADER = (
    "sequence,source_frame_id,chunk,offset,payload_bytes,captured_unix_ns,"
    "captured_monotonic_ns,source_warmup_seconds,source_connection_generation\n"
)


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        data = root / "data"
        data.mkdir()
        dataset = data / "frame-buffer-test"
        (dataset / "chunks").mkdir(parents=True)
        (dataset / "frames.csv").write_text(HEADER, encoding="utf-8")
        (dataset / "manifest.json").write_text(json.dumps({
            "format": "camera-entropy-frame-buffer-v1",
            "status": "stopped",
            "storage_mode": "y8",
            "width": 1280,
            "height": 720,
            "frame_payload_bytes": 1280 * 720,
            "frame_count": 2048,
            "bytes_written": 2048 * 1280 * 720,
        }), encoding="utf-8")
        sources_file = root / "sources.json"
        sources_file.write_text(json.dumps({"sources": [{
            "id": "buffered", "label": "Buffered", "source_type": "dataset-y",
            "env": {"DATASET_DIR": "data/frame-buffer-test"},
        }]}), encoding="utf-8")
        secret = root / "secret"
        secret.write_text("x" * 64, encoding="utf-8")
        settings = Settings(
            root=root, data_root=data, sources_file=sources_file,
            credentials_file=root / "unused", secret_file=secret,
            host="127.0.0.1", port=8087, worker_host="127.0.0.1", worker_port=19087,
            auth_mode="none", secure_cookie=False, trust_proxy=False, share_token_file=None,
        )
        source = load_sources(sources_file)[0]
        manager = JobManager(settings, [source], logging.getLogger("final-controls-selftest"))
        env, slug, output = manager._build_environment({
            "dataset_scope": "all-available",
            "dataset_verify_hashes": True,
            "dataset_verify_workers": 12,
            "dataset_verify_progress_seconds": 3,
            "final_preprod_workers": 4,
            "final_preprod_status_interval_seconds": 45,
            "dataset_verify_cache": False,
        }, source, "final-preproduction", "abcd1234", 19087)
        assert env["DATASET_VERIFY_HASHES"] == "1"
        assert env["DATASET_VERIFY_WORKERS"] == "12"
        assert env["DATASET_VERIFY_PROGRESS_SECONDS"] == "3"
        assert env["DATASET_VERIFY_CACHE"] == "0"
        assert env["FINAL_PREPROD_WORKERS"] == "4"
        assert env["FINAL_PREPROD_VERIFY_WORKERS"] == "12"
        assert env["FINAL_PREPROD_PROGRESS_INTERVAL_SECONDS"] == "3"
        assert env["FINAL_PREPROD_STATUS_INTERVAL_SECONDS"] == "45"
        assert env["FINAL_PREPROD_VERIFY_CACHE"] == "0"
        assert env["DATASET_MAX_FRAMES"] == "2048"
        assert env["DATASET_FOLLOW"] == "0"
        assert env["EXIT_ON_OUTPUT_LIMIT"] == "0"
        assert slug.startswith("web-final-preproduction-")
        assert output == data / slug

    print("final preproduction controls self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
