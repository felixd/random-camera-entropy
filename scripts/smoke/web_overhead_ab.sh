#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
source "$PROJECT_ROOT/scripts/lib.sh"
ensure_environment
DATA_ROOT="${DATA_ROOT:-$PROJECT_ROOT/data}"
CAMPAIGN="${CAMPAIGN:-web-overhead-ab-$(date -u +%Y%m%dT%H%M%SZ)}"
DIR="$DATA_ROOT/$CAMPAIGN"; mkdir -p "$DIR"
# A-B-A order limits the chance of mistaking thermal drift for a web-image cost.
labels=(images_off_1 images_on images_off_2)
flags=(0 1 0)
for i in 0 1 2; do
  RUN_NAME="${labels[$i]}" RUN_DIR="$DIR/${labels[$i]}" \
  WARMUP_SECONDS="${WARMUP_SECONDS:-60}" CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-128}" \
  DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-2097152}" CONDITIONED_BYTES="${CONDITIONED_BYTES:-2097152}" \
  VALIDATION_BYTES="${VALIDATION_BYTES:-1048576}" CORRELATION_EVERY="${CORRELATION_EVERY:-20}" \
  WEB_IMAGES="${flags[$i]}" PORT="${PORT:-8087}" DATA_ROOT="$DATA_ROOT" \
  "$PROJECT_ROOT/scripts/run/run_one.sh"
done
run_module app.reporting.summarize_campaign "$DIR"
