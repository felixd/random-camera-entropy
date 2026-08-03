#!/usr/bin/env bash
# Comprehensive offline qualification producing one review_report.html.
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
ensure_environment

CAMPAIGN="${CAMPAIGN:-review-qualification-$(date -u +%Y%m%dT%H%M%SZ)}"
DATA_ROOT="${DATA_ROOT:-$SCRIPT_DIR/data}"
ROOT="$DATA_ROOT/$CAMPAIGN"
STATE="$ROOT/review_profiles"
PROFILE_BYTES="${REVIEW_PROFILE_BYTES:-1048576}"
VALIDATION_BYTES="${REVIEW_VALIDATION_BYTES:-8388608}"
CONTINUE="${REVIEW_CONTINUE_ON_ERROR:-1}"
mkdir -p "$ROOT" "$STATE"

profiles=()
add(){ profiles+=("$1|$2|$3"); }
# category | name | env assignments
for mode in direct xor delta; do
  for bits in 1 2 3 4; do
    prefix="$mode"; [[ "$mode" == direct ]] && prefix="y"
    credit="0.25"; [[ "$mode" == xor && "$bits" == 1 ]] && credit="0.5"; [[ "$mode" == xor && "$bits" == 2 ]] && credit="0.5"
    add "lsb-width" "$prefix-lsb$bits" "SAMPLE_MODE=$mode LSB_BITS=$bits ENTROPY_CREDIT_BITS_PER_PIXEL=$credit PAIR_LAG_FRAMES=4 SPATIAL_MASK_PATTERN=full SERIALIZATION_ORDER=row-major CONDITIONER_INPUT_BITS=2048"
  done
done
for bits in 1 2; do
  for lag in 1 2 4 8; do
    add "pair-lag" "xor-lsb${bits}-lag${lag}" "SAMPLE_MODE=xor LSB_BITS=$bits ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 PAIR_LAG_FRAMES=$lag SPATIAL_MASK_PATTERN=full SERIALIZATION_ORDER=row-major CONDITIONER_INPUT_BITS=2048"
  done
done
for bits in 1 2; do
  for size in 512 1024 2048 4096; do
    add "conditioner" "xor-lsb${bits}-sha3in${size}" "SAMPLE_MODE=xor LSB_BITS=$bits ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 PAIR_LAG_FRAMES=4 SPATIAL_MASK_PATTERN=full SERIALIZATION_ORDER=row-major CONDITIONER_INPUT_BITS=$size"
  done
done
add "spatial" "full-row" "SAMPLE_MODE=xor LSB_BITS=2 ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 SPATIAL_MASK_PATTERN=full SERIALIZATION_ORDER=row-major"
add "spatial" "checker-even" "SAMPLE_MODE=xor LSB_BITS=2 ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 SPATIAL_MASK_PATTERN=checkerboard-even SERIALIZATION_ORDER=row-major"
add "spatial" "checker-odd" "SAMPLE_MODE=xor LSB_BITS=2 ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 SPATIAL_MASK_PATTERN=checkerboard-odd SERIALIZATION_ORDER=row-major"
for px in 0 1; do for py in 0 1; do add "spatial" "grid2-${px}${py}" "SAMPLE_MODE=xor LSB_BITS=2 ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 SPATIAL_MASK_PATTERN=grid SPATIAL_STEP_X=2 SPATIAL_STEP_Y=2 SPATIAL_PHASE_X=$px SPATIAL_PHASE_Y=$py SERIALIZATION_ORDER=row-major"; done; done
add "serialization" "full-serpentine" "SAMPLE_MODE=xor LSB_BITS=2 ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 SPATIAL_MASK_PATTERN=full SERIALIZATION_ORDER=serpentine"
add "serialization" "full-tile8" "SAMPLE_MODE=xor LSB_BITS=2 ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 SPATIAL_MASK_PATTERN=full SERIALIZATION_ORDER=tile-interleave SERIALIZATION_TILE_WIDTH=8 SERIALIZATION_TILE_HEIGHT=8"
add "serialization" "full-tile16" "SAMPLE_MODE=xor LSB_BITS=2 ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 SPATIAL_MASK_PATTERN=full SERIALIZATION_ORDER=tile-interleave SERIALIZATION_TILE_WIDTH=16 SERIALIZATION_TILE_HEIGHT=16"
for off in 0 1 2 4; do add "temporal-offset" "offset-${off}-${off}" "SAMPLE_MODE=xor LSB_BITS=2 ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 SPATIAL_MASK_PATTERN=full TEMPORAL_SPATIAL_OFFSET_X=$off TEMPORAL_SPATIAL_OFFSET_Y=$off SERIALIZATION_ORDER=row-major"; done

failures=0; idx=0
for spec in "${profiles[@]}"; do
  idx=$((idx+1)); IFS='|' read -r category name assignments <<< "$spec"
  run_name="$CAMPAIGN/profiles/$(printf '%02d' "$idx")-$name"
  log_file="$STATE/$(printf '%02d' "$idx")-$name.log"
  state_file="$STATE/$(printf '%02d' "$idx")-$name.json"
  log "REVIEW [$idx/${#profiles[@]}] $category / $name"
  read -r -a env_args <<< "$assignments"
  set +e
  env "${env_args[@]}" DATA_ROOT="$DATA_ROOT" RUN_NAME="$run_name" \
    WARMUP_SECONDS="${REVIEW_WARMUP_SECONDS:-0}" CALIBRATION_PAIRS="${REVIEW_CALIBRATION_PAIRS:-128}" \
    CONDITIONED_BYTES="$PROFILE_BYTES" VALIDATION_BYTES="$VALIDATION_BYTES" DIAGNOSTIC_VN_BYTES="${REVIEW_VN_BYTES:-1048576}" \
    ENABLE_VON_NEUMANN="${REVIEW_ENABLE_VN:-1}" WEB_IMAGES=0 MASK_SNAPSHOT_IMAGES=0 LIVE_BYTE_DIAGNOSTICS=0 \
    DATASET_FOLLOW=0 SPATIAL_COMPARISON=0 DUAL_WEAVE_COMPARISON=0 BINARY_GEOMETRY_REPORT=0 \
    "$SCRIPT_DIR/run_one.sh" > >(tee "$log_file") 2>&1
  rc=$?; set -e
  [[ $rc -eq 0 ]] || failures=$((failures+1))
  "$VENV_PYTHON" - "$state_file" "$idx" "$category" "$name" "$run_name" "$assignments" "$rc" <<'PY'
import json,sys
p,idx,cat,name,run,assignments,rc=sys.argv[1:]
env={}
for item in assignments.split():
    if '=' in item:
        k,v=item.split('=',1); env[k]=v
json.dump({'index':int(idx),'category':cat,'name':name,'run':run,'requested_parameters':env,'exit_code':int(rc),'status':'complete' if int(rc)==0 else 'failed'},open(p,'w'),indent=2,ensure_ascii=False)
PY
  "$VENV_PYTHON" "$SCRIPT_DIR/summarize_review_campaign.py" "$ROOT" >/dev/null || true
  if [[ $rc -ne 0 && "$CONTINUE" != 1 ]]; then break; fi
done
"$VENV_PYTHON" "$SCRIPT_DIR/summarize_review_campaign.py" "$ROOT"
if (( failures )); then rm -f "$ROOT/READY.json"; exit 1; fi
printf '{"status":"complete","report":"review_report.html"}\n' > "$ROOT/READY.json"
log "Review report: $ROOT/review_report.html"
