#!/usr/bin/env python3
from __future__ import annotations

import logging
from types import SimpleNamespace

from app.sources.control_values import control_integer, normalize_controls
from app.sources.frame_sources import TlsYSource

assert control_integer(1) == 1
assert control_integer(7000.0) == 7000
assert control_integer("1") == 1
assert control_integer("auto_exposure: 1 (Manual Mode)") == 1
assert control_integer("exposure_time_absolute: 7000") == 7000
assert control_integer("not available") is None
assert normalize_controls({
    "auto_exposure": "auto_exposure: 1 (Manual Mode)",
    "exposure_time_absolute": "exposure_time_absolute: 7000",
}) == {"auto_exposure": 1, "exposure_time_absolute": 7000}

args = SimpleNamespace()
source = TlsYSource(args, logging.getLogger("control-values-selftest"))
source.latest_controls_raw = {
    "auto_exposure": "auto_exposure: 1 (Manual Mode)",
    "exposure_time_absolute": 7000,
}
source.latest_controls = normalize_controls(source.latest_controls_raw)
assert source.control_snapshot() == {"auto_exposure": 1, "exposure_time_absolute": 7000}

print("control value normalization self-test: PASS")
