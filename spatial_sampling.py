#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spatial sampling, offset alignment and serialization helpers.

The module is independent from Flask and camera transport so the geometry can
be unit-tested without camera hardware.  Spatial selection and reordering do
not create entropy and are not cryptographic conditioning.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib

import numpy as np

MASK_PATTERNS = (
    "legacy",
    "full",
    "checkerboard-even",
    "checkerboard-odd",
    "grid",
    "block",
)
SERIALIZATION_ORDERS = ("row-major", "serpentine", "tile-interleave")


@dataclass(frozen=True)
class SpatialOptions:
    pattern: str = "legacy"
    step_x: int = 1
    step_y: int = 1
    phase_x: int = 0
    phase_y: int = 0
    block_width: int = 4
    block_height: int = 4
    offset_x: int = 0
    offset_y: int = 0
    serialization_order: str = "row-major"
    tile_width: int = 16
    tile_height: int = 16

    def validate(self) -> None:
        if self.pattern not in MASK_PATTERNS:
            raise ValueError(f"unsupported spatial mask pattern: {self.pattern}")
        if self.serialization_order not in SERIALIZATION_ORDERS:
            raise ValueError(f"unsupported serialization order: {self.serialization_order}")
        for name, value in (
            ("step_x", self.step_x),
            ("step_y", self.step_y),
            ("block_width", self.block_width),
            ("block_height", self.block_height),
            ("tile_width", self.tile_width),
            ("tile_height", self.tile_height),
        ):
            if value < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.pattern == "grid":
            if not 0 <= self.phase_x < self.step_x:
                raise ValueError("phase_x must be in [0, step_x) for grid pattern")
            if not 0 <= self.phase_y < self.step_y:
                raise ValueError("phase_y must be in [0, step_y) for grid pattern")
        if self.pattern == "block":
            if not 0 <= self.phase_x < self.block_width:
                raise ValueError("phase_x must be in [0, block_width) for block pattern")
            if not 0 <= self.phase_y < self.block_height:
                raise ValueError("phase_y must be in [0, block_height) for block pattern")


def offset_valid_mask(shape: tuple[int, int], dx: int = 0, dy: int = 0) -> np.ndarray:
    """Return pixels for which ``previous[y+dy, x+dx]`` is in bounds."""
    if len(shape) != 2:
        raise ValueError("shape must be two-dimensional")
    height, width = shape
    valid = np.zeros(shape, dtype=bool)
    x0 = max(0, -dx)
    x1 = min(width, width - dx)
    y0 = max(0, -dy)
    y1 = min(height, height - dy)
    if x0 < x1 and y0 < y1:
        valid[y0:y1, x0:x1] = True
    return valid


def align_previous_frame(
    previous_y: np.ndarray,
    *,
    dx: int = 0,
    dy: int = 0,
    invalid_fill: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Align an older frame to current-frame coordinates.

    The returned array satisfies ``aligned[y, x] = previous[y+dy, x+dx]`` for
    valid coordinates.  Invalid borders are filled, never wrapped.  The second
    return value is the exact validity mask and must be used for output
    selection.  A fill value of zero deliberately marks invalid borders as
    clipped in the existing calibration/health logic.
    """
    previous = np.asarray(previous_y, dtype=np.uint8)
    if previous.ndim != 2:
        raise ValueError("previous_y must be a 2-D array")
    height, width = previous.shape
    aligned = np.full(previous.shape, np.uint8(invalid_fill), dtype=np.uint8)
    valid = offset_valid_mask(previous.shape, dx, dy)
    x0 = max(0, -dx)
    x1 = min(width, width - dx)
    y0 = max(0, -dy)
    y1 = min(height, height - dy)
    if x0 < x1 and y0 < y1:
        aligned[y0:y1, x0:x1] = previous[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
    return aligned, valid


def _legacy_pattern(shape: tuple[int, int], legacy_sampling: str) -> np.ndarray:
    rows, cols = np.indices(shape, dtype=np.int64)
    checker_even = ((rows + cols) & 1) == 0
    aliases = {
        "full": np.ones(shape, dtype=bool),
        "checkerboard": checker_even,
        "checkerboard-even": checker_even,
        "checkerboard-odd": ~checker_even,
        "grid2x2": ((rows & 1) == 0) & ((cols & 1) == 0),
    }
    try:
        return aliases[legacy_sampling]
    except KeyError as exc:
        raise ValueError(f"unsupported legacy spatial sampling: {legacy_sampling}") from exc


def build_spatial_mask(
    shape: tuple[int, int],
    *,
    pattern: str,
    step_x: int = 1,
    step_y: int = 1,
    phase_x: int = 0,
    phase_y: int = 0,
    block_width: int = 4,
    block_height: int = 4,
    offset_x: int = 0,
    offset_y: int = 0,
    legacy_sampling: str = "full",
) -> np.ndarray:
    """Build a deterministic spatial selection mask.

    ``grid`` chooses one congruence class using ``step_x`` and ``step_y``.
    ``block`` chooses one fixed local position from each rectangular block.
    Every pattern is intersected with the non-wrapping offset-valid region.
    """
    options = SpatialOptions(
        pattern=pattern,
        step_x=step_x,
        step_y=step_y,
        phase_x=phase_x,
        phase_y=phase_y,
        block_width=block_width,
        block_height=block_height,
        offset_x=offset_x,
        offset_y=offset_y,
    )
    options.validate()

    rows, cols = np.indices(shape, dtype=np.int64)
    if pattern == "legacy":
        selected = _legacy_pattern(shape, legacy_sampling)
    elif pattern == "full":
        selected = np.ones(shape, dtype=bool)
    elif pattern == "checkerboard-even":
        selected = ((rows + cols) & 1) == 0
    elif pattern == "checkerboard-odd":
        selected = ((rows + cols) & 1) == 1
    elif pattern == "grid":
        selected = (cols % step_x == phase_x) & (rows % step_y == phase_y)
    elif pattern == "block":
        selected = (
            (cols % block_width == phase_x)
            & (rows % block_height == phase_y)
        )
    else:  # protected by validate()
        raise AssertionError(pattern)

    return selected & offset_valid_mask(shape, offset_x, offset_y)


def build_pattern_set(shape: tuple[int, int], args: object) -> dict[str, np.ndarray]:
    """Return compatibility aliases plus the configured production pattern.

    Compatibility variants are also intersected with the offset-valid region,
    preventing artificial zero borders in comparison runs.
    """
    dx = int(getattr(args, "temporal_spatial_offset_x", 0))
    dy = int(getattr(args, "temporal_spatial_offset_y", 0))
    valid = offset_valid_mask(shape, dx, dy)
    rows, cols = np.indices(shape, dtype=np.int64)
    checker_even = ((rows + cols) & 1) == 0
    patterns = {
        "full": np.ones(shape, dtype=bool) & valid,
        "checkerboard": checker_even & valid,
        "checkerboard-even": checker_even & valid,
        "checkerboard-odd": (~checker_even) & valid,
        "grid2x2": (((rows & 1) == 0) & ((cols & 1) == 0)) & valid,
    }
    patterns["configured"] = build_spatial_mask(
        shape,
        pattern=getattr(args, "spatial_mask_pattern", "legacy"),
        step_x=getattr(args, "spatial_step_x", 1),
        step_y=getattr(args, "spatial_step_y", 1),
        phase_x=getattr(args, "spatial_phase_x", 0),
        phase_y=getattr(args, "spatial_phase_y", 0),
        block_width=getattr(args, "spatial_block_width", 4),
        block_height=getattr(args, "spatial_block_height", 4),
        offset_x=dx,
        offset_y=dy,
        legacy_sampling=getattr(args, "spatial_sampling", "full"),
    )
    return patterns


def spatial_xor_lsb(
    current_y: np.ndarray,
    aligned_previous_y: np.ndarray,
) -> np.ndarray:
    """XOR current LSB with an already aligned older luminance frame."""
    current = np.asarray(current_y, dtype=np.uint8)
    previous = np.asarray(aligned_previous_y, dtype=np.uint8)
    if current.shape != previous.shape or current.ndim != 2:
        raise ValueError("current_y and aligned_previous_y must be equal-size 2-D arrays")
    return ((current & 1) ^ (previous & 1)).astype(np.uint8, copy=False)


def serialization_indices(
    mask: np.ndarray,
    *,
    order: str = "row-major",
    tile_width: int = 16,
    tile_height: int = 16,
) -> np.ndarray:
    """Return flat source indices in the requested deterministic order."""
    selected = np.asarray(mask, dtype=bool)
    if selected.ndim != 2:
        raise ValueError("mask must be a 2-D array")
    if order not in SERIALIZATION_ORDERS:
        raise ValueError(f"unsupported serialization order: {order}")
    if tile_width < 1 or tile_height < 1:
        raise ValueError("tile dimensions must be >= 1")

    height, width = selected.shape
    if order == "row-major":
        return np.flatnonzero(selected.reshape(-1))

    if order == "serpentine":
        coordinates = np.arange(height * width, dtype=np.int64).reshape(height, width)
        coordinates[1::2] = coordinates[1::2, ::-1]
        traversal = coordinates.reshape(-1)
        return traversal[selected.reshape(-1)[traversal]]

    flat = np.flatnonzero(selected.reshape(-1))
    if flat.size == 0:
        return flat.astype(np.int64, copy=False)
    y = flat // width
    x = flat % width
    tiles_x = (width + tile_width - 1) // tile_width
    local_position = (y % tile_height) * tile_width + (x % tile_width)
    tile_position = (y // tile_height) * tiles_x + (x // tile_width)
    permutation = np.lexsort((tile_position, local_position))
    return flat[permutation].astype(np.int64, copy=False)


def serialize_selected_bits(
    bits: np.ndarray,
    mask: np.ndarray,
    *,
    order: str = "row-major",
    tile_width: int = 16,
    tile_height: int = 16,
) -> np.ndarray:
    """Serialize selected samples without changing sample values."""
    array = np.asarray(bits, dtype=np.uint8)
    selected = np.asarray(mask, dtype=bool)
    if array.shape != selected.shape or array.ndim != 2:
        raise ValueError("bits and mask must be equal-size 2-D arrays")
    indices = serialization_indices(
        selected,
        order=order,
        tile_width=tile_width,
        tile_height=tile_height,
    )
    return array.reshape(-1)[indices].astype(np.uint8, copy=False)


class SpatialSerializer:
    """Cache deterministic mask-to-index mappings for production throughput."""

    def __init__(
        self,
        order: str = "row-major",
        tile_width: int = 16,
        tile_height: int = 16,
        max_entries: int = 32,
    ) -> None:
        if order not in SERIALIZATION_ORDERS:
            raise ValueError(f"unsupported serialization order: {order}")
        if tile_width < 1 or tile_height < 1 or max_entries < 1:
            raise ValueError("tile dimensions and max_entries must be positive")
        self.order = order
        self.tile_width = tile_width
        self.tile_height = tile_height
        self.max_entries = max_entries
        self._cache: OrderedDict[tuple[tuple[int, int], bytes], np.ndarray] = OrderedDict()

    @staticmethod
    def _mask_digest(mask: np.ndarray) -> bytes:
        packed = np.packbits(np.asarray(mask, dtype=bool).reshape(-1), bitorder="big")
        return hashlib.sha256(packed.tobytes()).digest()

    def indices(self, mask: np.ndarray) -> np.ndarray:
        selected = np.asarray(mask, dtype=bool)
        if selected.ndim != 2:
            raise ValueError("mask must be a 2-D array")
        key = (selected.shape, self._mask_digest(selected))
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        result = serialization_indices(
            selected,
            order=self.order,
            tile_width=self.tile_width,
            tile_height=self.tile_height,
        )
        self._cache[key] = result
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)
        return result

    def serialize(self, bits: np.ndarray, mask: np.ndarray) -> np.ndarray:
        array = np.asarray(bits, dtype=np.uint8)
        selected = np.asarray(mask, dtype=bool)
        if array.shape != selected.shape or array.ndim != 2:
            raise ValueError("bits and mask must be equal-size 2-D arrays")
        return array.reshape(-1)[self.indices(selected)].astype(np.uint8, copy=False)
