#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
source "$PROJECT_ROOT/scripts/lib.sh"
ensure_environment

GLOBAL_CAMPAIGN="${CAMPAIGN:-global-all-$(date -u +%Y%m%dT%H%M%SZ)}"
DATA_ROOT="${DATA_ROOT:-$PROJECT_ROOT/data}"
ROOT="$DATA_ROOT/$GLOBAL_CAMPAIGN"
STATE_DIR="$ROOT/global_steps"
CONTINUE="${GLOBAL_CONTINUE_ON_ERROR:-1}"
mkdir -p "$STATE_DIR"

# Exact web profiles known before v7.9.0, plus the new LSB campaign. Aggregate
# profiles are retained intentionally: GLOBAL means every public execution path.
steps=(
  "smoke|scripts/smoke/smoke_preproduction.sh"
  "temporal-sha3|scripts/smoke/smoke_temporal_sha3.sh"
  "single|scripts/run/run_one.sh"
  "qualification|scripts/qualification/qualification_preproduction.sh"
  "cold-start|scripts/qualification/qualification_cold_start_run.sh"
  "spatial-phases|scripts/smoke/smoke_checkerboard_phases.sh"
  "spatial-baseline|scripts/smoke/smoke_spatial_baseline.sh"
  "spatial-checker-even|scripts/smoke/smoke_spatial_checkerboard_even.sh"
  "spatial-checker-odd|scripts/smoke/smoke_spatial_checkerboard_odd.sh"
  "spatial-grid2|scripts/smoke/smoke_spatial_grid_2x2.sh"
  "spatial-grid4|scripts/smoke/smoke_spatial_grid_4x4.sh"
  "spatial-block4|scripts/smoke/smoke_spatial_block_4x4_phase_1_2.sh"
  "spatial-offset11|scripts/smoke/smoke_spatial_offset_diagonal_1.sh"
  "spatial-offset22|scripts/smoke/smoke_spatial_offset_diagonal_2.sh"
  "spatial-serpentine|scripts/smoke/smoke_spatial_serpentine.sh"
  "spatial-tile16|scripts/smoke/smoke_spatial_tile_interleave_16.sh"
  "spatial-campaign|scripts/smoke/smoke_spatial_profiles.sh"
  "dual-weave|scripts/smoke/smoke_dual_weave.sh"
  "dual-weave-stagger2|scripts/smoke/smoke_dual_weave_stagger2.sh"
  "dual-weave-lags|scripts/smoke/smoke_dual_weave_lags.sh"
  "dual-weave-stagger-qualification|scripts/qualification/qualification_dual_weave_stagger.sh"
  "lsb-campaign|scripts/smoke/smoke_lsb_profiles.sh"
  "production-assessment|scripts/qualification/production_assessment.sh"
)

failures=0
index=0
for specification in "${steps[@]}"; do
  index=$((index + 1))
  IFS='|' read -r profile script <<< "$specification"
  step_id="$(printf '%02d-%s' "$index" "$profile")"
  step_output="$GLOBAL_CAMPAIGN/profiles/$step_id"
  log_name="$step_id.log"
  state_file="$STATE_DIR/$step_id.json"
  if [[ ! -x "$PROJECT_ROOT/$script" ]]; then
    "$VENV_PYTHON" - "$state_file" "$index" "$profile" "$script" "$step_output" "$log_name" <<'PY'
import json,sys
p,i,profile,script,output,log=sys.argv[1:]
open(p,'w').write(json.dumps({'index':int(i),'profile':profile,'script':script,'status':'skipped','exit_code':None,'output':output,'log':log,'reason':'script missing'},indent=2,ensure_ascii=False))
PY
    failures=$((failures + 1))
    run_module app.reporting.summarize_global_campaign "$ROOT" >/dev/null
    if (( CONTINUE != 1 )); then break; fi
    continue
  fi
  log "GLOBAL [$index/${#steps[@]}] $profile ($script)"
  set +e
  env DATA_ROOT="$DATA_ROOT" RUN_NAME="$step_output" CAMPAIGN="$step_output" \
      SAMPLE_MODE=xor LSB_BITS=1 ENTROPY_CREDIT_BITS_PER_PIXEL=1.0 \
      "$PROJECT_ROOT/$script" > >(tee "$STATE_DIR/$log_name") 2>&1
  rc=$?
  set -e
  status=passed
  if (( rc != 0 )); then status=failed; failures=$((failures + 1)); fi
  "$VENV_PYTHON" - "$state_file" "$index" "$profile" "$script" "$status" "$rc" "$step_output" "$log_name" <<'PY'
import json,sys
p,i,profile,script,status,rc,output,log=sys.argv[1:]
open(p,'w').write(json.dumps({'index':int(i),'profile':profile,'script':script,'status':status,'exit_code':int(rc),'output':output,'log':log},indent=2,ensure_ascii=False))
PY
  run_module app.reporting.summarize_global_campaign "$ROOT" >/dev/null
  if (( rc != 0 && CONTINUE != 1 )); then break; fi
done
run_module app.reporting.summarize_global_campaign "$ROOT"
if (( failures > 0 )); then
  rm -f "$ROOT/READY.json"
  "$VENV_PYTHON" - "$ROOT/run_failed.json" "$failures" <<'PYFAIL'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"status": "failed", "failed_steps": int(sys.argv[2])}, stream, indent=2)
PYFAIL
  log "Globalna kampania wykonała wszystkie możliwe kroki, błędy: $failures"
  exit 1
fi
rm -f "$ROOT/run_failed.json"
"$VENV_PYTHON" - "$ROOT/READY.json" <<'PYREADY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"status": "complete", "report": "global_campaign_report.html"}, stream, indent=2)
PYREADY
log "Globalna kampania zakończona: $ROOT/global_campaign_report.html"
