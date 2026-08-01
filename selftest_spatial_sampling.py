#!/usr/bin/env python3
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from spatial_sampling import (
    align_previous_frame,
    build_pattern_set,
    build_spatial_mask,
    offset_valid_mask,
    serialize_selected_bits,
    spatial_xor_lsb,
    SpatialSerializer,
)


def main() -> int:
    shape = (4, 4)
    full = build_spatial_mask(shape, pattern="full")
    even = build_spatial_mask(shape, pattern="checkerboard-even")
    odd = build_spatial_mask(shape, pattern="checkerboard-odd")
    grid = build_spatial_mask(shape, pattern="grid", step_x=2, step_y=2)
    assert int(full.sum()) == 16
    assert int(even.sum()) == 8 and int(odd.sum()) == 8
    assert np.array_equal(even ^ odd, full)
    assert int(grid.sum()) == 4

    block = build_spatial_mask(
        (6, 8), pattern="block", block_width=4, block_height=3, phase_x=1, phase_y=2
    )
    assert int(block.sum()) == 4

    valid = offset_valid_mask((3, 4), dx=1, dy=1)
    assert int(valid.sum()) == 6
    assert not valid[:, -1].any() and not valid[-1, :].any()

    previous = np.arange(12, dtype=np.uint8).reshape(3, 4)
    aligned, aligned_valid = align_previous_frame(previous, dx=1, dy=0)
    assert np.array_equal(aligned[:, :3], previous[:, 1:])
    assert not aligned[:, 3].any()
    assert np.array_equal(aligned_valid, offset_valid_mask(previous.shape, 1, 0))
    current = np.zeros_like(previous)
    result = spatial_xor_lsb(current, aligned)
    assert np.array_equal(result[:, :3], previous[:, 1:] & 1)
    assert not result[:, 3].any()

    aligned_negative, valid_negative = align_previous_frame(previous, dx=-1, dy=-1)
    assert np.array_equal(aligned_negative[1:, 1:], previous[:-1, :-1])
    assert not valid_negative[0].any() and not valid_negative[:, 0].any()

    args = SimpleNamespace(
        spatial_mask_pattern="legacy",
        spatial_sampling="full",
        spatial_step_x=1,
        spatial_step_y=1,
        spatial_phase_x=0,
        spatial_phase_y=0,
        spatial_block_width=4,
        spatial_block_height=4,
        temporal_spatial_offset_x=1,
        temporal_spatial_offset_y=0,
    )
    patterns = build_pattern_set((3, 4), args)
    assert not patterns["full"][:, -1].any()
    assert not patterns["checkerboard-even"][:, -1].any()
    assert np.array_equal(patterns["configured"], patterns["full"])

    values = np.arange(16, dtype=np.uint8).reshape(4, 4)
    all_mask = np.ones((4, 4), dtype=bool)
    row_major = serialize_selected_bits(values, all_mask, order="row-major")
    assert np.array_equal(row_major, values.reshape(-1))
    serpentine = serialize_selected_bits(values, all_mask, order="serpentine")
    expected_serpentine = np.concatenate((values[0], values[1, ::-1], values[2], values[3, ::-1]))
    assert np.array_equal(serpentine, expected_serpentine)
    interleaved = serialize_selected_bits(
        values, all_mask, order="tile-interleave", tile_width=2, tile_height=2
    )
    expected = np.array([0, 2, 8, 10, 1, 3, 9, 11, 4, 6, 12, 14, 5, 7, 13, 15], dtype=np.uint8)
    assert np.array_equal(interleaved, expected), (interleaved, expected)
    assert sorted(interleaved.tolist()) == sorted(values.reshape(-1).tolist())

    serializer = SpatialSerializer(order="tile-interleave", tile_width=2, tile_height=2)
    first_indices = serializer.indices(all_mask)
    second_indices = serializer.indices(all_mask.copy())
    assert first_indices is second_indices  # identical mask reuses the cached traversal
    assert np.array_equal(serializer.serialize(values, all_mask), expected)

    print("spatial sampling self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
