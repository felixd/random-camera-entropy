#!/usr/bin/env python3
"""Multi-bit luma sample extraction for the camera entropy validation pipeline.

The frozen pixel mask remains calibrated from the historical temporal XOR of
bit plane zero.  This module only changes which sample bits are serialized
behind that mask.  That separation preserves backwards-compatible mask and
health behaviour while allowing controlled 1..8 LSB experiments.
"""
from __future__ import annotations

import math
from typing import Protocol

import numpy as np

SAMPLE_MODES = ("xor", "direct", "delta")
BIT_ORDER = "pixel-major-lsb-first"
SHA3_512_OUTPUT_BITS = 512


class Serializer(Protocol):
    def serialize(self, values: np.ndarray, mask: np.ndarray) -> np.ndarray: ...


def validate_lsb_bits(lsb_bits: int) -> int:
    value = int(lsb_bits)
    if not 1 <= value <= 8:
        raise ValueError("lsb_bits must be in 1..8")
    return value


def low_mask(lsb_bits: int) -> np.uint8:
    bits = validate_lsb_bits(lsb_bits)
    return np.uint8(0xFF if bits == 8 else (1 << bits) - 1)


def sample_values(
    current_y: np.ndarray,
    previous_y: np.ndarray,
    mode: str,
    lsb_bits: int,
) -> np.ndarray:
    """Return uint8 sample symbols with only the selected low bits retained.

    xor:    temporal bitwise XOR, backwards-compatible with the old 1-LSB path
    direct: low bits of the current Y frame
    delta:  modulo-256 temporal residual Y_t - Y_(t-k)
    """
    if mode not in SAMPLE_MODES:
        raise ValueError(f"unsupported sample mode: {mode}")
    bits = validate_lsb_bits(lsb_bits)
    current = np.asarray(current_y, dtype=np.uint8)
    previous = np.asarray(previous_y, dtype=np.uint8)
    if current.shape != previous.shape:
        raise ValueError("current_y and previous_y must have the same shape")
    if mode == "xor":
        values = np.bitwise_xor(current, previous)
    elif mode == "direct":
        values = current
    else:
        values = np.subtract(
            current.astype(np.uint16, copy=False),
            previous.astype(np.uint16, copy=False),
            dtype=np.uint16,
        ).astype(np.uint8, copy=False)
    return np.bitwise_and(values, low_mask(bits))


def serialize_sample_symbols(
    current_y: np.ndarray,
    previous_y: np.ndarray,
    serializer: Serializer,
    mask: np.ndarray,
    mode: str,
    lsb_bits: int,
) -> np.ndarray:
    """Return ordered low-bit sample symbols, one uint8 value per selected pixel.

    RCT/APT must operate on the source sample alphabet.  For a k-LSB profile the
    source sample is a k-bit symbol, not an artificial binary stream made by
    interleaving the bit planes of each pixel.
    """
    values = sample_values(current_y, previous_y, mode, lsb_bits)
    selected_mask = np.asarray(mask, dtype=bool)
    if values.shape != selected_mask.shape:
        raise ValueError("values and mask must have the same shape")
    return np.asarray(serializer.serialize(values, selected_mask), dtype=np.uint8).reshape(-1)


def serialize_symbol_bits(symbols: np.ndarray, lsb_bits: int) -> np.ndarray:
    """Serialize already ordered symbols pixel-major, LSB-first."""
    bits = validate_lsb_bits(lsb_bits)
    ordered = np.asarray(symbols, dtype=np.uint8).reshape(-1)
    if ordered.size == 0:
        return np.empty(0, dtype=np.uint8)
    shifts = np.arange(bits, dtype=np.uint8)
    return np.bitwise_and(np.right_shift(ordered[:, None], shifts[None, :]), 1).astype(
        np.uint8, copy=False
    ).reshape(-1)


def serialize_bitplanes(
    values: np.ndarray,
    serializer: Serializer,
    mask: np.ndarray,
    lsb_bits: int,
) -> np.ndarray:
    """Serialize selected symbols pixel-by-pixel, with each symbol LSB-first."""
    source = np.asarray(values, dtype=np.uint8)
    selected_mask = np.asarray(mask, dtype=bool)
    if source.shape != selected_mask.shape:
        raise ValueError("values and mask must have the same shape")
    symbols = np.asarray(serializer.serialize(source, selected_mask), dtype=np.uint8).reshape(-1)
    return serialize_symbol_bits(symbols, lsb_bits)


def serialize_samples(
    current_y: np.ndarray,
    previous_y: np.ndarray,
    serializer: Serializer,
    mask: np.ndarray,
    mode: str,
    lsb_bits: int,
) -> np.ndarray:
    return serialize_symbol_bits(
        serialize_sample_symbols(
            current_y, previous_y, serializer, mask, mode, lsb_bits
        ),
        lsb_bits,
    )


def minimum_conditioner_input_bits(
    lsb_bits: int,
    entropy_credit_bits_per_pixel: float,
    output_bits: int = SHA3_512_OUTPUT_BITS,
) -> int:
    """Conservative input block required not to emit more bits than credited.

    A serialized block contains ``lsb_bits`` input bits per selected pixel.  The
    credit is deliberately separate from capture width: collecting eight LSBs
    does not claim eight bits of min-entropy.
    """
    bits = validate_lsb_bits(lsb_bits)
    credit = float(entropy_credit_bits_per_pixel)
    if not math.isfinite(credit) or not 0.0 < credit <= bits:
        raise ValueError("entropy credit must be finite and in (0, lsb_bits]")
    required = math.ceil(float(output_bits) * bits / credit)
    return max(512, ((required + 7) // 8) * 8)


def sample_label(mode: str, lsb_bits: int) -> str:
    bits = validate_lsb_bits(lsb_bits)
    labels = {
        "xor": "Temporal XOR",
        "direct": "Direct Y",
        "delta": "Temporal delta",
    }
    if mode not in labels:
        raise ValueError(f"unsupported sample mode: {mode}")
    suffix = "full Y8" if bits == 8 else f"{bits} LSB"
    return f"{labels[mode]} — {suffix}"
