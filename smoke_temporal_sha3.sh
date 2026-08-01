#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Simplified production-candidate pipeline:
# temporal LSB XOR -> frozen full active mask -> RCT/APT -> SHA3-512.
# No Von Neumann output, checkerboard split, spatial comparison or dual weave.
export RUN_NAME="${RUN_NAME:-temporal-sha3-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
export WARMUP_SECONDS="${WARMUP_SECONDS:-60}"
export CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-128}"
export PAIRING_MODE="${PAIRING_MODE:-disjoint}"
export PAIR_LAG_FRAMES="${PAIR_LAG_FRAMES:-4}"
export SPATIAL_SAMPLING="full"
export SPATIAL_COMPARISON=0
export DUAL_WEAVE_COMPARISON=0
export ENABLE_VON_NEUMANN=0
export DIAGNOSTIC_VN_BYTES=0
# 65,536 raw temporal bits -> one 512-bit SHA3-512 digest (128:1 compression).
# This is deliberately conservative enough for the first comparison campaign,
# while still allowing a 1 MiB smoke test to finish in practical time.
export CONDITIONER_INPUT_BITS="${CONDITIONER_INPUT_BITS:-65536}"
export CONDITIONED_BYTES="${CONDITIONED_BYTES:-1048576}"
export VALIDATION_BYTES="${VALIDATION_BYTES:-8388608}"
export BINARY_GEOMETRY_REPORT="${BINARY_GEOMETRY_REPORT:-1}"
export WEB_IMAGES="${WEB_IMAGES:-0}"
export MASK_SNAPSHOT_IMAGES="${MASK_SNAPSHOT_IMAGES:-0}"
export LIVE_BYTE_DIAGNOSTICS="${LIVE_BYTE_DIAGNOSTICS:-1}"

exec "$SCRIPT_DIR/run_one.sh"
