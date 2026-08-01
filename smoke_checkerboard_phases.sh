#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export RUN_NAME="${RUN_NAME:-checkerboard-phases-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
export DATA_ROOT="${DATA_ROOT:-$SCRIPT_DIR/data}"
export RUN_DIR="${RUN_DIR:-$DATA_ROOT/$RUN_NAME}"
export WARMUP_SECONDS="${WARMUP_SECONDS:-60}"
export CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-128}"
export SPATIAL_COMPARISON=1
export SPATIAL_SAMPLING=checkerboard-even
export DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-5242880}"
export CONDITIONED_BYTES=0
export VALIDATION_BYTES="${VALIDATION_BYTES:-2097152}"
export CORRELATION_EVERY="${CORRELATION_EVERY:-10}"
export PORT="${PORT:-8087}"
export WEB_IMAGES="${WEB_IMAGES:-0}"
"$SCRIPT_DIR/run_one.sh"
"${VENV_PYTHON:-$SCRIPT_DIR/.venv/bin/python}" "$SCRIPT_DIR/summarize_spatial_run.py" "$RUN_DIR"
printf '[spatial-smoke] Raport: %s\n' "$RUN_DIR/spatial_comparison_report/index.html"
