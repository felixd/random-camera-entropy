#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_ROOT="${DATA_ROOT:-$SCRIPT_DIR/data}"
CAMPAIGN="${CAMPAIGN:-qualification-$(date -u +%Y%m%dT%H%M%SZ)}"
CAMPAIGN_DIR="$DATA_ROOT/$CAMPAIGN"
RUNS="${RUNS:-3}"
FIRST_WARMUP_SECONDS="${FIRST_WARMUP_SECONDS:-1800}"
NEXT_WARMUP_SECONDS="${NEXT_WARMUP_SECONDS:-300}"
SPATIAL_SAMPLING="${SPATIAL_SAMPLING:-checkerboard-even}"
mkdir -p "$CAMPAIGN_DIR"
[[ "$RUNS" =~ ^[0-9]+$ ]] && (( RUNS >= 1 )) || { echo "RUNS musi być >=1" >&2; exit 1; }

for ((run=1; run<=RUNS; run++)); do
    warmup="$NEXT_WARMUP_SECONDS"; (( run == 1 )) && warmup="$FIRST_WARMUP_SECONDS"
    printf '[qualification] Run %d/%d, warm-up=%ss\n' "$run" "$RUNS" "$warmup"
    RUN_NAME="run_$(printf '%02d' "$run")" \
    RUN_DIR="$CAMPAIGN_DIR/run_$(printf '%02d' "$run")" \
    WARMUP_SECONDS="$warmup" \
    CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-512}" \
    SPATIAL_SAMPLING="$SPATIAL_SAMPLING" \
    DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-26214400}" \
    CONDITIONED_BYTES="${CONDITIONED_BYTES:-104857600}" \
    VALIDATION_BYTES="${VALIDATION_BYTES:-10485760}" \
    CORRELATION_EVERY="${CORRELATION_EVERY:-10}" \
    WEB_IMAGES="${WEB_IMAGES:-0}" \
    PORT="${PORT:-8087}" \
    DATA_ROOT="$DATA_ROOT" \
    "$SCRIPT_DIR/run_one.sh"
done

"${PYTHON_BOOTSTRAP:-python3}" "$SCRIPT_DIR/summarize_campaign.py" "$CAMPAIGN_DIR"
printf '[qualification] Raport: %s\n' "$CAMPAIGN_DIR/qualification_report.html"
