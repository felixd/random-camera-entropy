#!/usr/bin/env python3
"""Regression tests for crediting warm-up performed by the remote camera agent."""
from __future__ import annotations

from types import SimpleNamespace
import sys
import types

try:
    import flask  # noqa: F401
except ModuleNotFoundError:
    module = types.ModuleType("flask")
    module.Flask = object
    module.Response = object
    module.abort = lambda *args, **kwargs: None
    module.render_template_string = lambda *args, **kwargs: ""
    module.send_from_directory = lambda *args, **kwargs: None
    sys.modules["flask"] = module

from app.core.camera_entropy_server import Service


def service(manifest: dict[str, object]) -> Service:
    instance = object.__new__(Service)
    instance.args = SimpleNamespace(thermal_warmup_seconds=3600.0)
    instance.source_manifest = manifest
    instance.warmup_started_monotonic = None
    instance.warmup_deadline_monotonic = None
    instance.source_warmup_seconds = None
    instance.source_warmup_observed_monotonic = None
    instance.warmup_complete = False
    return instance


def main() -> int:
    # v8.0.4 Go agents expose the source age in hello only.  The worker must
    # credit it instead of starting a fresh one-hour timer.
    remote = service({"hello": {"source_warmup_seconds": 900.0}})
    remote.begin_warmup_epoch(10_000.0)
    assert remote.source_warmup_seconds == 900.0
    assert remote.warmup_deadline_monotonic == 12_700.0
    assert remote.warmup_started_monotonic == 9_100.0
    assert remote.current_source_warmup_seconds(10_030.0) == 930.0

    # New agents refresh source_warmup_seconds in every frame.  A later frame
    # must update the effective deadline without adding worker-local warm-up.
    observed = remote.record_source_warmup(1_200.0, 10_030.0)
    assert observed == 1_200.0
    assert remote.warmup_deadline_monotonic == 12_430.0
    assert remote.current_source_warmup_seconds(10_060.0) == 1_230.0

    # A source already older than the requirement reaches a zero remaining
    # deadline and can enter calibration as soon as its first frame is read.
    ready = service({"hello": {"source_warmup_seconds": 4_000.0}})
    ready.begin_warmup_epoch(20_000.0)
    assert ready.warmup_deadline_monotonic == 20_000.0
    assert ready.warmup_complete is False  # first frame still validates the path

    # Local/legacy sources without metadata retain the original worker-local
    # fail-closed timer.
    local = service({})
    local.begin_warmup_epoch(30_000.0)
    assert local.source_warmup_seconds is None
    assert local.warmup_deadline_monotonic == 33_600.0

    # Invalid values are ignored in hello and rejected by the frame-processing
    # path through record_source_warmup returning None.
    invalid = service({"hello": {"source_warmup_seconds": "not-a-number"}})
    invalid.begin_warmup_epoch(40_000.0)
    assert invalid.source_warmup_seconds is None
    assert invalid.record_source_warmup(float("nan"), 40_001.0) is None

    print("source warm-up accounting self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
