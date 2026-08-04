#!/usr/bin/env python3
from __future__ import annotations

import math
import numpy as np

from stream_statistics import StreamingBitplaneStatistics


def main() -> int:
    stats = StreamingBitplaneStatistics(2, window_pairs=2)
    # Balanced 2-bit alphabet. Repeating the pattern twice creates one complete
    # temporal window; a second observation creates a partial final window.
    stats.observe(np.array([0, 1, 2, 3] * 1000, dtype=np.uint8), 10, "t0")
    stats.observe(np.array([3, 2, 1, 0] * 1000, dtype=np.uint8), 11, "t1")
    stats.observe(np.array([0, 1, 2, 3] * 500, dtype=np.uint8), 12, "t2")
    result = stats.snapshot()
    aggregate = result["aggregate"]
    assert aggregate["total_symbols"] == 10_000
    assert aggregate["pair_updates"] == 3
    assert math.isclose(aggregate["symbol_min_entropy_bits_per_symbol"], 2.0, abs_tol=1e-12)
    assert math.isclose(aggregate["symbol_min_entropy_bits_per_input_bit"], 1.0, abs_tol=1e-12)
    assert aggregate["max_abs_cross_plane_phi"] < 1e-12
    assert aggregate["max_cross_plane_mutual_information_bits"] < 1e-12
    assert result["window_count"] == 2
    assert result["worst_window_hmin_per_input_bit"] > 0.99
    assert aggregate["first_frame_id"] == 10 and aggregate["last_frame_id"] == 12

    one = StreamingBitplaneStatistics(1, window_pairs=1)
    one.observe(np.array([0, 1] * 100, dtype=np.uint8), 1, "t")
    single = one.snapshot()
    assert single["aggregate"]["symbol_min_entropy_bits_per_input_bit"] == 1.0
    assert single["aggregate"]["cross_plane"] == []

    print("stream statistics self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
