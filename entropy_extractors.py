#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bit extraction and transparent debiasing helpers."""
from __future__ import annotations

import numpy as np


def von_neumann_split(bits: np.ndarray) -> np.ndarray:
    """Pair spatially separated halves: 01→0, 10→1, equal pairs discarded."""
    values = np.asarray(bits, dtype=np.uint8).reshape(-1)
    even_length = (values.size // 2) * 2
    if even_length == 0:
        return np.empty(0, dtype=np.uint8)
    half = even_length // 2
    first = values[:half]
    second = values[half : half * 2]
    different = first != second
    return first[different].astype(np.uint8, copy=False)


def repeated_von_neumann(bits: np.ndarray, passes: int) -> tuple[np.ndarray, list[dict[str, float | int]]]:
    """Apply Von Neumann 0..N times and return per-pass retention diagnostics."""
    if not 0 <= int(passes) <= 8:
        raise ValueError("Von Neumann passes must be in 0..8")
    current = np.asarray(bits, dtype=np.uint8).reshape(-1)
    metrics: list[dict[str, float | int]] = []
    for pass_number in range(1, int(passes) + 1):
        input_bits = int(current.size)
        current = von_neumann_split(current)
        output_bits = int(current.size)
        metrics.append(
            {
                "pass": pass_number,
                "input_bits": input_bits,
                "output_bits": output_bits,
                "retention": output_bits / input_bits if input_bits else 0.0,
            }
        )
    return current, metrics
