#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import sys
import tempfile
import types
from pathlib import Path

# The build container may omit Flask/Werkzeug.  JobManager and configuration
# validation do not need a live WSGI stack, so provide import-only shims.
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

from jinja2 import Environment

import control_server
from spatial_docs import discover_documents, render_markdown


def main() -> int:
    root = Path(__file__).resolve().parent
    for profile, script in control_server.ALLOWED_PROFILES.items():
        assert (root / script).is_file(), (profile, script)
    assert control_server.ALLOWED_PROFILES["spatial-campaign"] == "smoke_spatial_profiles.sh"

    template = (root / "templates" / "control.html").read_text(encoding="utf-8")
    Environment().parse(template)
    for marker in (
        '<header class="top">',
        'id="previewBtn"',
        'position:sticky',
        '<h2>Uruchom test</h2>',
        '<h3>Źródło danych</h3>',
        '<h3>Kalibracja i maska „bad pixels”</h3>',
        '<h3>Tworzenie i ekstrakcja bitów</h3>',
        '<h3>Ekstraktor Von Neumanna</h3>',
        '<h3>Kondycjonowanie kryptograficzne</h3>',
        '<h3>Health tests i fail-closed</h3>',
        '<h3>Wyniki, diagnostyka i kampania</h3>',
        'name="dataset_scope"',
        '<option value="all-available">Całość dostępna w chwili startu</option>',
        'name="von_neumann_passes"',
        '<option value="4">4</option>',
        'name="stream_stats_window_pairs"',
        "p?.id==='final-preproduction'",
        "stage.classList.add('profile-locked')",
        "f.classList.add('preset-field')",
    ):
        assert marker in template, marker
    assert '<iframe' not in template.lower()
    assert 'id="floatingHelp"' not in template
    assert 'id="contextHelp"' not in template
    assert 'name="entropy_credit_bits_per_pixel" type="number" min="0.000001" max="4" step="any"' in template
    assert 'id="assessment_level" name="assessment_level"' in template
    assert '<option value="full" selected>' in template
    assert "f.valueAsNumber" in template

    for name in (
        "spatial_mask_pattern", "spatial_step_x", "spatial_step_y",
        "spatial_phase_x", "spatial_phase_y", "spatial_block_width",
        "spatial_block_height", "temporal_spatial_offset_x",
        "temporal_spatial_offset_y", "serialization_order",
        "serialization_tile_width", "serialization_tile_height",
        "sample_mode", "lsb_bits", "entropy_credit_bits_per_pixel",
    ):
        assert f'name="{name}"' in template
        assert name in control_server.PARAMETER_HELP

    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        data = base / "data"
        data.mkdir()
        sources_file = base / "sources.json"
        sources_file.write_text(json.dumps({
            "sources": [{"id": "local", "label": "Local", "source_type": "v4l2", "env": {"DEVICE": "/dev/null"}}]
        }))
        secret = base / "secret"
        secret.write_text("x" * 64)
        settings = control_server.Settings(
            root=root,
            data_root=data,
            sources_file=sources_file,
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
        sources = control_server.load_sources(sources_file)
        manager = control_server.JobManager(settings, sources, logging.getLogger("spatial-control-selftest"))
        payload = {
            "spatial_sampling": "full",
            "spatial_mask_pattern": "grid",
            "spatial_step_x": "4",
            "spatial_step_y": "3",
            "spatial_phase_x": "1",
            "spatial_phase_y": "2",
            "spatial_block_width": "8",
            "spatial_block_height": "8",
            "temporal_spatial_offset_x": "-2",
            "temporal_spatial_offset_y": "3",
            "serialization_order": "tile-interleave",
            "serialization_tile_width": "16",
            "serialization_tile_height": "12",
            "sample_mode": "delta",
            "lsb_bits": "4",
            "entropy_credit_bits_per_pixel": "0.5",
        }
        env, slug, output = manager._build_environment(payload, sources[0], "spatial-grid4", "1234567890abcdef", 19001)
        assert env["SPATIAL_MASK_PATTERN"] == "grid"
        assert env["SPATIAL_STEP_X"] == "4" and env["SPATIAL_PHASE_Y"] == "2"
        assert env["TEMPORAL_SPATIAL_OFFSET_X"] == "-2"
        assert env["SERIALIZATION_ORDER"] == "tile-interleave"
        assert env["SAMPLE_MODE"] == "delta"
        assert env["LSB_BITS"] == "4"
        assert env["ENTROPY_CREDIT_BITS_PER_PIXEL"] == "0.5"

        comma_payload = dict(payload, entropy_credit_bits_per_pixel="0,5")
        comma_env, _, _ = manager._build_environment(
            comma_payload, sources[0], "single", "comma-credit-test", 19002
        )
        assert comma_env["ENTROPY_CREDIT_BITS_PER_PIXEL"] == "0.5"
        assert slug.startswith("web-spatial-grid4-") and output.name == slug

        campaign_env, campaign_slug, campaign_output = manager._build_environment(
            {}, sources[0], "spatial-campaign", "abcdef1234567890", 19003
        )
        assert campaign_env["CAMPAIGN"] == campaign_slug
        assert campaign_output == data / campaign_slug
        for profile in ("lsb-campaign", "global-all"):
            aggregate_env, aggregate_slug, aggregate_output = manager._build_environment(
                {}, sources[0], profile, "abcdef1234567890", 19004
            )
            assert aggregate_env["CAMPAIGN"] == aggregate_slug
            assert aggregate_output == data / aggregate_slug

        bad = dict(payload, spatial_phase_x="4")
        try:
            manager._build_environment(bad, sources[0], "spatial-grid4", "badbadbadbadbadb", 19005)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid grid phase was accepted")

        bad_credit = dict(payload, lsb_bits="2", entropy_credit_bits_per_pixel="3")
        try:
            manager._build_environment(bad_credit, sources[0], "single", "badcreditbadcred", 19006)
        except ValueError:
            pass
        else:
            raise AssertionError("entropy credit above lsb_bits was accepted")

        campaign = data / "campaign-result"
        campaign.mkdir()
        (campaign / "spatial_campaign_report.html").write_text("<h1>ok</h1>")
        (campaign / "spatial_campaign_summary.json").write_text(json.dumps({
            "complete_profiles": 10, "total_profiles": 10, "failed_profiles": 0
        }))
        rows = control_server.scan_data_root(data, 20)
        row = next(item for item in rows if item["name"] == "campaign-result")
        assert row["status"] == "complete"
        assert row["report_label"] == "Spatial campaign"
        assert "complete 10/10" in row["headline"]

        docs = base / "docs-root"
        docs.mkdir()
        (docs / "README.md").write_text("# Title\n\n<script>alert(1)</script>\n")
        (docs / "data").mkdir()
        (docs / "data" / "hidden.md").write_text("secret")
        found = [item.name for item in discover_documents(docs)]
        assert found == ["README.md"]
        rendered = render_markdown((docs / "README.md").read_text())
        assert "<h1>Title</h1>" in rendered
        assert "&lt;script&gt;" in rendered and "<script>" not in rendered

    print("control/spatial/docs self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
