#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# Uruchom ten skrypt po zdefiniowanym zimnym starcie / resecie badanego źródła.
# Skrypt nie wykonuje fizycznego power-cycle kamery.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
export RUN_NAME="${RUN_NAME:-cold-start-qualification-$(date -u +%Y%m%dT%H%M%SZ)}"
export WARMUP_SECONDS="${WARMUP_SECONDS:-1800}"
export CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-512}"
export SPATIAL_SAMPLING="${SPATIAL_SAMPLING:-checkerboard-even}"
export DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-26214400}"
export CONDITIONED_BYTES="${CONDITIONED_BYTES:-104857600}"
export VALIDATION_BYTES="${VALIDATION_BYTES:-10485760}"
export CORRELATION_EVERY="${CORRELATION_EVERY:-10}"
export WEB_IMAGES="${WEB_IMAGES:-0}"
export PORT="${PORT:-8087}"
exec "$PROJECT_ROOT/scripts/run/run_one.sh"
