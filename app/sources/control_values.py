#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical parsing of camera-control values received from local or remote sources.

V4L2 menu controls may be rendered as e.g. ``auto_exposure: 1 (Manual Mode)``.
Wire consumers must compare the numeric control value, not the human-readable
``v4l2-ctl`` representation.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Optional

_COLON_INTEGER = re.compile(r":\s*(-?\d+)\b")
_PLAIN_INTEGER = re.compile(r"^\s*(-?\d+)\s*$")


def control_integer(value: Any) -> Optional[int]:
    """Return a canonical integer control value or ``None`` when unparseable.

    Accepted forms include integers, integral floats, numeric strings and the
    human-readable output produced by ``v4l2-ctl`` for menu controls.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        text = value.strip()
        match = _PLAIN_INTEGER.match(text) or _COLON_INTEGER.search(text)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                return None
    return None


def normalize_controls(values: Mapping[str, Any] | None) -> dict[str, Optional[int]]:
    """Normalize every control in a mapping to an integer-or-None value."""
    if not values:
        return {}
    return {str(name): control_integer(value) for name, value in values.items()}
