#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# Fail-closed, finite conditioned segment. This is a preproduction candidate,
# not a statement of SP 800-90B validation or full-entropy certification.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export RUN_NAME="${RUN_NAME:-production-candidate-$(date -u +%Y%m%dT%H%M%SZ)}"
export WARMUP_SECONDS="${WARMUP_SECONDS:-1800}"
export CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-512}"
export SPATIAL_SAMPLING="${SPATIAL_SAMPLING:-checkerboard-even}"
export DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-0}"
export CONDITIONED_BYTES="${CONDITIONED_BYTES:-104857600}"
export VALIDATION_BYTES="${VALIDATION_BYTES:-0}"
export CORRELATION_EVERY="${CORRELATION_EVERY:-50}"
export WEB_IMAGES="${WEB_IMAGES:-0}"
export PORT="${PORT:-8087}"
exec "$SCRIPT_DIR/run_one.sh"
