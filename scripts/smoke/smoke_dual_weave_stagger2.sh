#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

# Focused production-candidate smoke: the full active mask is divided into
# complementary checkerboards C0/C1, serialized row-major and aligned as
# C0_g || C1_(g+2) before the balanced SHA3-512 conditioner.
export RUN_NAME="${RUN_NAME:-dual-weave-row-major-stagger2-$(date -u +%Y%m%dT%H%M%SZ)}"
export WARMUP_SECONDS="${WARMUP_SECONDS:-60}"
export CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-128}"
export PAIRING_MODE="${PAIRING_MODE:-disjoint}"
export PAIR_LAG_FRAMES="${PAIR_LAG_FRAMES:-4}"
export SPATIAL_SAMPLING="checkerboard-even"
export SPATIAL_COMPARISON=0
export DUAL_WEAVE_COMPARISON=1
export DUAL_WEAVE_ORDERS="row-major"
export DUAL_WEAVE_ALIGNMENTS="stagger-2"
export DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-10485760}"
export CONDITIONED_BYTES="${CONDITIONED_BYTES:-10485760}"
export VALIDATION_BYTES="${VALIDATION_BYTES:-10485760}"
export CONDITIONER_INPUT_BITS=2048
export BINARY_GEOMETRY_REPORT="${BINARY_GEOMETRY_REPORT:-1}"

exec "$PROJECT_ROOT/scripts/run/run_one.sh"
