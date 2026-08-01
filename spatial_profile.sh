#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
profile="${1:?usage: spatial_profile.sh PRESET}"

# Shared smoke defaults.  Callers may override sizes and timing, while the
# geometry encoded by a named profile remains fixed and reproducible.
export RUN_NAME="${RUN_NAME:-${profile}-$(date -u +%Y%m%dT%H%M%SZ)}"
export WARMUP_SECONDS="${WARMUP_SECONDS:-60}"
export CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-128}"
export PAIRING_MODE="${PAIRING_MODE:-disjoint}"
export PAIR_LAG_FRAMES="${PAIR_LAG_FRAMES:-4}"
export SPATIAL_SAMPLING="full"
export SPATIAL_COMPARISON=0
export DUAL_WEAVE_COMPARISON=0
export ENABLE_VON_NEUMANN="${ENABLE_VON_NEUMANN:-1}"
export DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-1048576}"
export CONDITIONED_BYTES="${CONDITIONED_BYTES:-1048576}"
export VALIDATION_BYTES="${VALIDATION_BYTES:-8388608}"
export CONDITIONER_INPUT_BITS="${CONDITIONER_INPUT_BITS:-2048}"
export BINARY_GEOMETRY_REPORT="${BINARY_GEOMETRY_REPORT:-1}"
export CORRELATION_EVERY="${CORRELATION_EVERY:-10}"
export WEB_IMAGES="${WEB_IMAGES:-0}"
export MASK_SNAPSHOT_IMAGES="${MASK_SNAPSHOT_IMAGES:-0}"
export LIVE_BYTE_DIAGNOSTICS="${LIVE_BYTE_DIAGNOSTICS:-1}"

export SPATIAL_STEP_X=1 SPATIAL_STEP_Y=1
export SPATIAL_PHASE_X=0 SPATIAL_PHASE_Y=0
export SPATIAL_BLOCK_WIDTH=4 SPATIAL_BLOCK_HEIGHT=4
export TEMPORAL_SPATIAL_OFFSET_X=0 TEMPORAL_SPATIAL_OFFSET_Y=0
export SERIALIZATION_ORDER=row-major
export SERIALIZATION_TILE_WIDTH=16 SERIALIZATION_TILE_HEIGHT=16

case "$profile" in
    spatial-baseline)
        export SPATIAL_MASK_PATTERN=full
        ;;
    spatial-checker-even)
        export SPATIAL_MASK_PATTERN=checkerboard-even
        ;;
    spatial-checker-odd)
        export SPATIAL_MASK_PATTERN=checkerboard-odd
        ;;
    spatial-grid2)
        export SPATIAL_MASK_PATTERN=grid SPATIAL_STEP_X=2 SPATIAL_STEP_Y=2
        ;;
    spatial-grid4)
        export SPATIAL_MASK_PATTERN=grid SPATIAL_STEP_X=4 SPATIAL_STEP_Y=4
        ;;
    spatial-block4)
        export SPATIAL_MASK_PATTERN=block
        export SPATIAL_BLOCK_WIDTH=4 SPATIAL_BLOCK_HEIGHT=4
        export SPATIAL_PHASE_X=1 SPATIAL_PHASE_Y=2
        ;;
    spatial-offset11)
        export SPATIAL_MASK_PATTERN=full
        export TEMPORAL_SPATIAL_OFFSET_X=1 TEMPORAL_SPATIAL_OFFSET_Y=1
        ;;
    spatial-offset22)
        export SPATIAL_MASK_PATTERN=full
        export TEMPORAL_SPATIAL_OFFSET_X=2 TEMPORAL_SPATIAL_OFFSET_Y=2
        ;;
    spatial-serpentine)
        export SPATIAL_MASK_PATTERN=full SERIALIZATION_ORDER=serpentine
        ;;
    spatial-tile16)
        export SPATIAL_MASK_PATTERN=full SERIALIZATION_ORDER=tile-interleave
        export SERIALIZATION_TILE_WIDTH=16 SERIALIZATION_TILE_HEIGHT=16
        ;;
    *)
        printf 'Nieznany profil przestrzenny: %s\n' "$profile" >&2
        exit 2
        ;;
esac

exec "$SCRIPT_DIR/run_one.sh"
