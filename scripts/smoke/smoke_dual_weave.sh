#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
export RUN_NAME="${RUN_NAME:-dual-weave-alignments-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
export WARMUP_SECONDS="${WARMUP_SECONDS:-60}"
export CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-128}"
export PAIRING_MODE=disjoint
export PAIR_LAG_FRAMES="${PAIR_LAG_FRAMES:-4}"
export SPATIAL_SAMPLING=checkerboard-even
export SPATIAL_COMPARISON=0
export DUAL_WEAVE_COMPARISON=1
export DUAL_WEAVE_ORDERS="${DUAL_WEAVE_ORDERS:-row-major}"
export DUAL_WEAVE_ALIGNMENTS="${DUAL_WEAVE_ALIGNMENTS:-same-group,stagger-1,stagger-2}"
export DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-5242880}"
export CONDITIONED_BYTES="${CONDITIONED_BYTES:-5242880}"
export VALIDATION_BYTES="${VALIDATION_BYTES:-4194304}"
export CONDITIONER_INPUT_BITS="${CONDITIONER_INPUT_BITS:-2048}"
export CORRELATION_EVERY="${CORRELATION_EVERY:-10}"
export WEB_IMAGES="${WEB_IMAGES:-0}"
exec "$PROJECT_ROOT/scripts/run/run_one.sh"
