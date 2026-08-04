#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Streaming statistics for the complete selected-symbol sequence.

Unlike bounded validation BIN files, this accumulator observes every accepted
production symbol.  It keeps only small symbol/transition histograms and
fixed-size temporal windows, so a complete multi-hundred-gigabyte dataset can
be assessed without writing another copy of the extracted stream.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np


def _phi(matrix: np.ndarray) -> float | None:
    values = np.asarray(matrix, dtype=np.float64).reshape(2, 2)
    n00, n01, n10, n11 = values[0, 0], values[0, 1], values[1, 0], values[1, 1]
    denominator = math.sqrt(
        (n00 + n01) * (n10 + n11) * (n00 + n10) * (n01 + n11)
    )
    if denominator <= 0.0:
        return None
    return float((n00 * n11 - n01 * n10) / denominator)


def _mutual_information(matrix: np.ndarray) -> float:
    values = np.asarray(matrix, dtype=np.float64).reshape(2, 2)
    total = float(values.sum())
    if total <= 0.0:
        return 0.0
    joint = values / total
    row = joint.sum(axis=1)
    column = joint.sum(axis=0)
    result = 0.0
    for first in range(2):
        for second in range(2):
            probability = float(joint[first, second])
            independent = float(row[first] * column[second])
            if probability > 0.0 and independent > 0.0:
                result += probability * math.log2(probability / independent)
    return float(result)


class _Histogram:
    def __init__(self, lsb_bits: int) -> None:
        self.lsb_bits = int(lsb_bits)
        if not 1 <= self.lsb_bits <= 4:
            raise ValueError("lsb_bits must be in 1..4")
        self.alphabet_size = 1 << self.lsb_bits
        self.symbol_counts = np.zeros(self.alphabet_size, dtype=np.uint64)
        self.symbol_transitions = np.zeros(
            (self.alphabet_size, self.alphabet_size), dtype=np.uint64
        )
        self.total_symbols = 0
        self.transition_pairs = 0
        self.previous_symbol: int | None = None
        self.pair_updates = 0
        self.first_frame_id: int | None = None
        self.last_frame_id: int | None = None
        self.first_timestamp_utc: str | None = None
        self.last_timestamp_utc: str | None = None

    def observe(self, symbols: np.ndarray, frame_id: int, timestamp_utc: str) -> None:
        values = np.asarray(symbols, dtype=np.uint8).reshape(-1)
        if values.size == 0:
            return
        if int(values.max(initial=0)) >= self.alphabet_size:
            raise ValueError("symbol outside configured LSB alphabet")
        self.symbol_counts += np.bincount(
            values, minlength=self.alphabet_size
        ).astype(np.uint64)
        if self.previous_symbol is not None:
            self.symbol_transitions[self.previous_symbol, int(values[0])] += 1
            self.transition_pairs += 1
        if values.size > 1:
            indices = (
                values[:-1].astype(np.uint16) * self.alphabet_size
                + values[1:].astype(np.uint16)
            )
            self.symbol_transitions.reshape(-1)[:] += np.bincount(
                indices, minlength=self.alphabet_size * self.alphabet_size
            ).astype(np.uint64)
            self.transition_pairs += int(values.size - 1)
        self.previous_symbol = int(values[-1])
        self.total_symbols += int(values.size)
        self.pair_updates += 1
        if self.first_frame_id is None:
            self.first_frame_id = int(frame_id)
            self.first_timestamp_utc = str(timestamp_utc)
        self.last_frame_id = int(frame_id)
        self.last_timestamp_utc = str(timestamp_utc)

    def snapshot(self) -> dict[str, Any]:
        total = int(self.total_symbols)
        counts = self.symbol_counts.astype(np.uint64, copy=False)
        maximum = int(counts.max(initial=0))
        symbol_hmin = -math.log2(maximum / total) if total and maximum else 0.0
        bitplanes: list[dict[str, Any]] = []
        max_abs_lag = 0.0
        max_abs_bias = 0.0
        symbols = np.arange(self.alphabet_size, dtype=np.uint8)
        for bit in range(self.lsb_bits):
            selected = ((symbols >> bit) & 1).astype(bool)
            ones = int(counts[selected].sum(dtype=np.uint64))
            p1 = ones / total if total else 0.0
            hmin = -math.log2(max(p1, 1.0 - p1)) if total else 0.0
            transition = np.zeros((2, 2), dtype=np.uint64)
            for source in range(self.alphabet_size):
                source_bit = (source >> bit) & 1
                for target in range(self.alphabet_size):
                    target_bit = (target >> bit) & 1
                    transition[source_bit, target_bit] += self.symbol_transitions[source, target]
            lag = _phi(transition)
            if lag is not None:
                max_abs_lag = max(max_abs_lag, abs(lag))
            max_abs_bias = max(max_abs_bias, abs(p1 - 0.5))
            bitplanes.append(
                {
                    "bit": bit,
                    "zeros": total - ones,
                    "ones": ones,
                    "p1": p1,
                    "min_entropy_bits_per_bit": hmin,
                    "lag1_phi": lag,
                    "lag1_transition_counts": transition.astype(int).tolist(),
                }
            )

        cross: list[dict[str, Any]] = []
        max_abs_cross_phi = 0.0
        max_cross_mi = 0.0
        for first in range(self.lsb_bits):
            for second in range(first + 1, self.lsb_bits):
                joint = np.zeros((2, 2), dtype=np.uint64)
                for symbol in range(self.alphabet_size):
                    joint[(symbol >> first) & 1, (symbol >> second) & 1] += counts[symbol]
                phi = _phi(joint)
                mi = _mutual_information(joint)
                if phi is not None:
                    max_abs_cross_phi = max(max_abs_cross_phi, abs(phi))
                max_cross_mi = max(max_cross_mi, mi)
                cross.append(
                    {
                        "first_bit": first,
                        "second_bit": second,
                        "phi": phi,
                        "mutual_information_bits": mi,
                        "joint_counts": joint.astype(int).tolist(),
                    }
                )

        return {
            "lsb_bits": self.lsb_bits,
            "alphabet_size": self.alphabet_size,
            "pair_updates": int(self.pair_updates),
            "total_symbols": total,
            "transition_pairs": int(self.transition_pairs),
            "symbol_counts": counts.astype(int).tolist(),
            "symbol_min_entropy_bits_per_symbol": symbol_hmin,
            "symbol_min_entropy_bits_per_input_bit": symbol_hmin / self.lsb_bits,
            "bitplanes": bitplanes,
            "max_abs_bit_bias": max_abs_bias,
            "max_abs_bit_lag1_phi": max_abs_lag,
            "cross_plane": cross,
            "max_abs_cross_plane_phi": max_abs_cross_phi,
            "max_cross_plane_mutual_information_bits": max_cross_mi,
            "first_frame_id": self.first_frame_id,
            "last_frame_id": self.last_frame_id,
            "first_timestamp_utc": self.first_timestamp_utc,
            "last_timestamp_utc": self.last_timestamp_utc,
        }


class StreamingBitplaneStatistics:
    """Whole-run aggregate plus bounded per-window diagnostics."""

    SCHEMA = "camera-entropy-stream-lsb-statistics-v1"

    def __init__(self, lsb_bits: int, window_pairs: int = 1024) -> None:
        self.lsb_bits = int(lsb_bits)
        self.window_pairs = int(window_pairs)
        if self.window_pairs < 1:
            raise ValueError("window_pairs must be positive")
        self.aggregate = _Histogram(self.lsb_bits)
        self.current_window = _Histogram(self.lsb_bits)
        self.windows: list[dict[str, Any]] = []

    def observe(self, symbols: np.ndarray, frame_id: int, timestamp_utc: str) -> None:
        self.aggregate.observe(symbols, frame_id, timestamp_utc)
        self.current_window.observe(symbols, frame_id, timestamp_utc)
        if self.current_window.pair_updates >= self.window_pairs:
            self._finish_window()

    def _finish_window(self) -> None:
        if self.current_window.total_symbols <= 0:
            return
        row = self.current_window.snapshot()
        row["index"] = len(self.windows)
        self.windows.append(row)
        self.current_window = _Histogram(self.lsb_bits)

    def snapshot(self, *, finalize_partial_window: bool = True) -> dict[str, Any]:
        if finalize_partial_window:
            self._finish_window()
        aggregate = self.aggregate.snapshot()
        window_hmin = [
            float(row["symbol_min_entropy_bits_per_input_bit"])
            for row in self.windows
            if int(row.get("total_symbols", 0)) > 0
        ]
        window_bias = [float(row.get("max_abs_bit_bias", 0.0)) for row in self.windows]
        window_lag = [float(row.get("max_abs_bit_lag1_phi", 0.0)) for row in self.windows]
        return {
            "schema": self.SCHEMA,
            "scope": "all accepted full-variant production symbols",
            "window_pairs": self.window_pairs,
            "aggregate": aggregate,
            "windows": self.windows,
            "window_count": len(self.windows),
            "worst_window_hmin_per_input_bit": min(window_hmin) if window_hmin else None,
            "best_window_hmin_per_input_bit": max(window_hmin) if window_hmin else None,
            "max_window_abs_bit_bias": max(window_bias) if window_bias else None,
            "max_window_abs_bit_lag1_phi": max(window_lag) if window_lag else None,
        }
