#!/usr/bin/env python3
from __future__ import annotations

import numpy as np

from entropy_bitplanes import (
    BIT_ORDER,
    minimum_conditioner_input_bits,
    sample_values,
    serialize_sample_symbols,
    serialize_samples,
    serialize_symbol_bits,
)


class RowMajor:
    def serialize(self, values: np.ndarray, mask: np.ndarray) -> np.ndarray:
        return values[mask].astype(np.uint8, copy=False)


def main() -> int:
    current = np.array([[0x00, 0x03], [0x06, 0xFF]], dtype=np.uint8)
    previous = np.array([[0x01, 0x01], [0x02, 0xF0]], dtype=np.uint8)
    mask = np.array([[True, True], [False, True]], dtype=bool)

    assert sample_values(current, previous, "xor", 4).tolist() == [[1, 2], [4, 15]]
    assert sample_values(current, previous, "direct", 2).tolist() == [[0, 3], [2, 3]]
    assert sample_values(current, previous, "delta", 4).tolist() == [[15, 2], [4, 15]]

    symbols = serialize_sample_symbols(
        current, previous, RowMajor(), mask, "direct", 2
    )
    assert symbols.tolist() == [0, 3, 3]
    bits = serialize_samples(current, previous, RowMajor(), mask, "direct", 2)
    # each selected pixel contributes bit 0, then bit 1
    assert bits.tolist() == [0, 0, 1, 1, 1, 1]
    assert serialize_symbol_bits(symbols, 2).tolist() == bits.tolist()
    assert BIT_ORDER == "pixel-major-lsb-first"
    assert minimum_conditioner_input_bits(1, 1.0) == 512
    assert minimum_conditioner_input_bits(4, 1.0) == 2048
    assert minimum_conditioner_input_bits(4, 0.25) == 8192
    print("entropy bit-plane self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
