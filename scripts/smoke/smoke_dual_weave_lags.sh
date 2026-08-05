#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
source "$PROJECT_ROOT/scripts/lib.sh"
ensure_environment
DATA_ROOT="${DATA_ROOT:-$PROJECT_ROOT/data}"
CAMPAIGN="${CAMPAIGN:-dual-weave-lags-$(date -u +%Y%m%dT%H%M%SZ)}"
CAMPAIGN_DIR="$DATA_ROOT/$CAMPAIGN"
[[ ! -e "$CAMPAIGN_DIR" ]] || fail "Katalog kampanii już istnieje: $CAMPAIGN_DIR"
mkdir -p "$CAMPAIGN_DIR"
FIRST_WARMUP_SECONDS="${FIRST_WARMUP_SECONDS:-1800}"
NEXT_WARMUP_SECONDS="${NEXT_WARMUP_SECONDS:-60}"
CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-128}"
DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-3145728}"
CONDITIONED_BYTES="${CONDITIONED_BYTES:-3145728}"
VALIDATION_BYTES="${VALIDATION_BYTES:-2097152}"
DUAL_WEAVE_ORDERS="${DUAL_WEAVE_ORDERS:-row-major}"
DUAL_WEAVE_ALIGNMENTS="${DUAL_WEAVE_ALIGNMENTS:-same-group,stagger-2}"

cat > "$CAMPAIGN_DIR/campaign_config.json" <<JSON
{
  "campaign": "$CAMPAIGN",
  "sequence": ["k4-start", "k2", "k8", "k4-repeat"],
  "first_warmup_seconds": $FIRST_WARMUP_SECONDS,
  "next_warmup_seconds": $NEXT_WARMUP_SECONDS,
  "calibration_pairs": $CALIBRATION_PAIRS,
  "diagnostic_vn_bytes": $DIAGNOSTIC_VN_BYTES,
  "conditioned_bytes": $CONDITIONED_BYTES,
  "validation_bytes": $VALIDATION_BYTES,
  "orders": "$DUAL_WEAVE_ORDERS",
  "alignments": "$DUAL_WEAVE_ALIGNMENTS"
}
JSON

run_case() {
    local name="$1" lag="$2" warmup="$3"
    log "Dual-weave lag case: $name, k=$lag, warm-up=${warmup}s"
    DATA_ROOT="$CAMPAIGN_DIR" \
    RUN_NAME="$name" \
    WARMUP_SECONDS="$warmup" \
    CALIBRATION_PAIRS="$CALIBRATION_PAIRS" \
    PAIRING_MODE=disjoint \
    PAIR_LAG_FRAMES="$lag" \
    SPATIAL_SAMPLING=checkerboard-even \
    SPATIAL_COMPARISON=0 \
    DUAL_WEAVE_COMPARISON=1 \
    DUAL_WEAVE_ORDERS="$DUAL_WEAVE_ORDERS" \
    DUAL_WEAVE_ALIGNMENTS="$DUAL_WEAVE_ALIGNMENTS" \
    DIAGNOSTIC_VN_BYTES="$DIAGNOSTIC_VN_BYTES" \
    CONDITIONED_BYTES="$CONDITIONED_BYTES" \
    VALIDATION_BYTES="$VALIDATION_BYTES" \
    CONDITIONER_INPUT_BITS="${CONDITIONER_INPUT_BITS:-2048}" \
    WEB_IMAGES="${WEB_IMAGES:-0}" \
    HOST="${HOST:-0.0.0.0}" API_HOST="${API_HOST:-127.0.0.1}" PORT="${PORT:-8087}" \
    SOURCE_TYPE="${SOURCE_TYPE:-v4l2}" \
    "$PROJECT_ROOT/scripts/run/run_one.sh"
}

run_case k4-start 4 "$FIRST_WARMUP_SECONDS"
run_case k2 2 "$NEXT_WARMUP_SECONDS"
run_case k8 8 "$NEXT_WARMUP_SECONDS"
run_case k4-repeat 4 "$NEXT_WARMUP_SECONDS"

run_module app.reporting.summarize_dual_weave_campaign "$CAMPAIGN_DIR"
write_sha256s "$CAMPAIGN_DIR"
cat > "$CAMPAIGN_DIR/READY.json" <<JSON
{"status":"ready","report":"dual_weave_campaign_report.html","runs":4}
JSON
log "Raport kampanii: $CAMPAIGN_DIR/dual_weave_campaign_report.html"
