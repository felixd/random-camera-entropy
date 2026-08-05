#!/usr/bin/env python3
from __future__ import annotations

import sys
import types

import numpy as np

from app.core.entropy_bitplanes import serialize_symbol_bits


def load_server():
    flask = types.ModuleType("flask")

    class Flask:
        pass

    class Response:
        pass

    def placeholder(*_args, **_kwargs):
        return None

    flask.Flask = Flask
    flask.Response = Response
    flask.abort = placeholder
    flask.render_template_string = placeholder
    flask.send_from_directory = placeholder
    sys.modules.setdefault("flask", flask)
    import app.core.camera_entropy_server as camera_entropy_server

    return camera_entropy_server


def main() -> int:
    server = load_server()
    alpha = 2**-20

    # Fourteen legitimate 3-bit zero-valued symbols serialize to 42 zero bits.
    # A binary RCT therefore trips at its cutoff of 41, even though the source
    # sample has repeated only fourteen times.  The symbol-domain RCT must not.
    symbols = np.array([0] * 14 + [1], dtype=np.uint8)
    serialized = serialize_symbol_bits(symbols, 3)
    binary = server.ContinuousHealthTests(0.5, alpha, 1024)
    assert binary.consume(serialized)
    assert binary.latched

    symbol_health = server.ContinuousHealthTests(
        0.5, alpha, 512, alphabet_size=8, sample_width_bits=3
    )
    assert symbol_health.consume(symbols) == []
    assert not symbol_health.latched
    assert symbol_health.status()["sample_domain"] == "symbol"

    # A real stuck-symbol condition still trips at exactly the same RCT cutoff.
    stuck = server.ContinuousHealthTests(
        0.5, alpha, 512, alphabet_size=8, sample_width_bits=3
    )
    assert stuck.rct_cutoff == 41
    events = stuck.consume(np.zeros(stuck.rct_cutoff, dtype=np.uint8))
    assert events and events[0][0] == "RCT"
    assert "symbol=0" in events[0][1]

    # Non-binary APT follows SP 800-90B: it counts the first/reference symbol,
    # rather than the globally most common symbol in that window.
    apt = server.ContinuousHealthTests(
        1.0, alpha, 512, alphabet_size=8, sample_width_bits=3
    )
    window = np.concatenate(
        (
            np.array([1], dtype=np.uint8),
            np.tile(np.array([0, 0, 0, 0, 2], dtype=np.uint8), 100),
            np.resize(np.arange(2, 8, dtype=np.uint8), 11),
        )
    )
    assert window.size == 512
    assert int(np.count_nonzero(window == 0)) == 400
    assert apt.consume(window) == []
    assert not apt.latched

    failing_window = np.concatenate(
        (
            np.tile(np.array([0, 0, 0, 0, 1], dtype=np.uint8), 102),
            np.array([2, 3], dtype=np.uint8),
        )
    )
    assert failing_window.size == 512
    events = apt.consume(failing_window)
    assert events and events[0][0] == "APT"
    assert "reference_symbol=0" in events[0][1]

    print("symbol-domain health self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
