#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
ensure_environment
DATA_ROOT="${DATA_ROOT:-$SCRIPT_DIR/data}"
CAMPAIGN="${CAMPAIGN:-dual-weave-stagger-qualification-$(date -u +%Y%m%dT%H%M%SZ)}"
CAMPAIGN_DIR="$DATA_ROOT/$CAMPAIGN"
[[ ! -e "$CAMPAIGN_DIR" ]] || fail "Katalog kampanii już istnieje: $CAMPAIGN_DIR"
mkdir -p "$CAMPAIGN_DIR"
RUNS="${RUNS:-3}"
FIRST_WARMUP_SECONDS="${FIRST_WARMUP_SECONDS:-1800}"
NEXT_WARMUP_SECONDS="${NEXT_WARMUP_SECONDS:-300}"
CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-512}"
DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-10485760}"
CONDITIONED_BYTES="${CONDITIONED_BYTES:-104857600}"
VALIDATION_BYTES="${VALIDATION_BYTES:-10485760}"
DUAL_WEAVE_ORDERS="${DUAL_WEAVE_ORDERS:-row-major}"
DUAL_WEAVE_ALIGNMENTS="${DUAL_WEAVE_ALIGNMENTS:-stagger-2}"
SOURCE_FRAME_TIMEOUT_SECONDS="${SOURCE_FRAME_TIMEOUT_SECONDS:-60}"
SOURCE_RECONNECT_ATTEMPTS="${SOURCE_RECONNECT_ATTEMPTS:-10}"
SOURCE_RECONNECT_BACKOFF_SECONDS="${SOURCE_RECONNECT_BACKOFF_SECONDS:-2}"

cat > "$CAMPAIGN_DIR/campaign_config.json" <<JSON
{
  "campaign": "$CAMPAIGN",
  "runs": $RUNS,
  "first_warmup_seconds": $FIRST_WARMUP_SECONDS,
  "next_warmup_seconds": $NEXT_WARMUP_SECONDS,
  "calibration_pairs": $CALIBRATION_PAIRS,
  "diagnostic_vn_bytes": $DIAGNOSTIC_VN_BYTES,
  "conditioned_bytes": $CONDITIONED_BYTES,
  "validation_bytes": $VALIDATION_BYTES,
  "orders": "$DUAL_WEAVE_ORDERS",
  "alignments": "$DUAL_WEAVE_ALIGNMENTS",
  "pairing": "disjoint/k4",
  "baseline": "checkerboard-even",
  "conditioner": "SHA3-512 2048->512",
  "source_frame_timeout_seconds": $SOURCE_FRAME_TIMEOUT_SECONDS,
  "source_reconnect_attempts": $SOURCE_RECONNECT_ATTEMPTS,
  "source_reconnect_policy": "restart warm-up/calibration before production; fail closed during production"
}
JSON

for ((index=1; index<=RUNS; index++)); do
    printf -v run_name 'run_%02d' "$index"
    warmup="$NEXT_WARMUP_SECONDS"
    (( index == 1 )) && warmup="$FIRST_WARMUP_SECONDS"
    log "Dual-weave qualification: $run_name, warm-up=${warmup}s"
    DATA_ROOT="$CAMPAIGN_DIR" \
    RUN_NAME="$run_name" \
    WARMUP_SECONDS="$warmup" \
    CALIBRATION_PAIRS="$CALIBRATION_PAIRS" \
    PAIRING_MODE=disjoint \
    PAIR_LAG_FRAMES=4 \
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
    SOURCE_FRAME_TIMEOUT_SECONDS="$SOURCE_FRAME_TIMEOUT_SECONDS" \
    SOURCE_RECONNECT_ATTEMPTS="$SOURCE_RECONNECT_ATTEMPTS" \
    SOURCE_RECONNECT_BACKOFF_SECONDS="$SOURCE_RECONNECT_BACKOFF_SECONDS" \
    "$SCRIPT_DIR/run_one.sh"
done

"$VENV_PYTHON" "$SCRIPT_DIR/summarize_dual_weave_campaign.py" "$CAMPAIGN_DIR"
write_sha256s "$CAMPAIGN_DIR"
cat > "$CAMPAIGN_DIR/READY.json" <<JSON
{"status":"ready","report":"dual_weave_campaign_report.html","runs":$RUNS,"alignment":"$DUAL_WEAVE_ALIGNMENTS"}
JSON
log "Raport kwalifikacyjny: $CAMPAIGN_DIR/dual_weave_campaign_report.html"
