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
    for name in (
        "abort", "jsonify", "redirect", "render_template",
        "render_template_string", "request", "send_file", "url_for",
    ):
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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    template = (root / "app/web/templates/control.html").read_text(encoding="utf-8")
    for marker in (
        "const DATASET_ONLY_FIELDS=new Set(",
        "function enableContainer(id,enabled)",
        "enableContainer('datasetFields',dataset)",
        "if(!dataset&&DATASET_ONLY_FIELDS.has(f.name))continue",
    ):
        assert marker in template, marker

    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        data = base / "data"
        data.mkdir()
        sources_path = base / "sources.json"
        sources_path.write_text(
            json.dumps({
                "sources": [{
                    "id": "camera",
                    "label": "Camera agent",
                    "source_type": "tls-y",
                    "env": {
                        "TLS_HOST": "127.0.0.1",
                        "TLS_PORT": "9443",
                        "WIDTH": "1280",
                        "HEIGHT": "720",
                    },
                }]
            }),
            encoding="utf-8",
        )
        secret = base / "secret"
        secret.write_text("x" * 64, encoding="utf-8")
        settings = Settings(
            root=root,
            data_root=data,
            sources_file=sources_path,
            credentials_file=base / "unused",
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
        source = load_sources(sources_path)[0]
        manager = JobManager(settings, [source], logging.getLogger("source-specific-payload-selftest"))

        # Regression payload copied from a LIVE production-safe browser request.
        # Dataset fields may remain in an older tab or custom API client, but they
        # must neither reject nor alter a LIVE worker environment.
        payload = {
            "profile": "production-safe",
            "source_id": "camera",
            "dataset_scope": "all-available",
            "dataset_realtime": False,
            "dataset_verify_hashes": True,
            "dataset_verify_cache": True,
            "dataset_verify_workers": 8,
            "dataset_verify_progress_seconds": 2,
            "calibration_pairs": 2048,
            "conditioner": "sha3-512",
            "conditioner_input_bits": 2048,
            "entropy_credit_bits_per_pixel": 0.5,
            "exposure": 7000,
            "lsb_bits": 1,
            "pair_lag_frames": 4,
            "pairing_mode": "disjoint",
            "sample_mode": "xor",
            "spatial_mask_pattern": "full",
            "serialization_order": "row-major",
            "von_neumann_passes": 0,
            "warmup_seconds": 0,
        }
        env, slug, output = manager._build_environment(
            payload, source, "production-safe", "livepayload1234", 18087
        )
        assert env["SOURCE_TYPE"] == "tls-y"
        assert env["EXPOSURE"] == "7000"
        assert env["WARMUP_SECONDS"] == "0"
        assert env["SAMPLE_MODE"] == "xor"
        assert env["LSB_BITS"] == "1"
        assert env["CONDITIONER_INPUT_BITS"] == "2048"
        assert "DATASET_SCOPE" not in env
        assert slug.startswith("web-production-safe-")
        assert output == data / slug

    print("source-specific payload self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
