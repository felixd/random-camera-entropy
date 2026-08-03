#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
ensure_environment

CAMPAIGN="${CAMPAIGN:-lsb-campaign-$(date -u +%Y%m%dT%H%M%SZ)}"
DATA_ROOT="${DATA_ROOT:-$SCRIPT_DIR/data}"
CAMPAIGN_DIR="$DATA_ROOT/$CAMPAIGN"
STATE_DIR="$CAMPAIGN_DIR/lsb_profiles"
PROFILE_BYTES="${LSB_PROFILE_BYTES:-1048576}"
PROFILE_WARMUP="${LSB_PROFILE_WARMUP_SECONDS:-30}"
PROFILE_CALIBRATION="${LSB_PROFILE_CALIBRATION_PAIRS:-128}"
CONTINUE="${LSB_CONTINUE_ON_ERROR:-1}"
mkdir -p "$CAMPAIGN_DIR" "$STATE_DIR"

# Exhaustive sweep: every sample mode and every supported capture width 1..4.
# Credits are conservative bookkeeping defaults only and can be overridden.
profiles=()
for mode in xor direct delta; do
  for bits in {1..4}; do
    suffix="lsb$bits"
    name="$mode-$suffix"; [[ "$mode" == direct ]] && name="y-$suffix"
    credit="${LSB_DEFAULT_ENTROPY_CREDIT:-0.25}"
    if [[ "$mode" == xor && "$bits" == 1 ]]; then
      credit="${LSB_XOR1_ENTROPY_CREDIT:-1.0}"
    elif [[ "$mode" == xor && "$bits" == 2 ]]; then
      credit="${LSB_XOR2_ENTROPY_CREDIT:-0.5}"
    fi
    profiles+=("$name|$mode|$bits|$credit")
  done
done

"$VENV_PYTHON" - "$CAMPAIGN_DIR/lsb_campaign_config.json" "$PROFILE_BYTES" "$PROFILE_WARMUP" "$PROFILE_CALIBRATION" "$CONTINUE" <<'PYCONFIG'
import json, os, sys
path, profile_bytes, warmup, calibration, cont = sys.argv[1:]
source_keys = (
    "SOURCE_TYPE", "DEVICE", "WIDTH", "HEIGHT", "CAMERA_FPS", "EXPOSURE",
    "TLS_HOST", "TLS_PORT", "TLS_SERVER_NAME", "DATASET_DIR", "DATASET_START_FRAME",
    "DATASET_MAX_FRAMES", "DATASET_REALTIME", "DATASET_RATE", "DATASET_FOLLOW",
)
value = {
    "schema": "camera-entropy-lsb-campaign-config-v1",
    "sample_modes": ["xor", "direct", "delta"],
    "lsb_bits": [1, 2, 3, 4],
    "profile_count": 12,
    "profile_bytes": int(profile_bytes),
    "warmup_seconds": int(warmup),
    "calibration_pairs": int(calibration),
    "continue_on_error": int(cont),
    "default_entropy_credit": float(os.environ.get("LSB_DEFAULT_ENTROPY_CREDIT", "0.25")),
    "xor1_entropy_credit": float(os.environ.get("LSB_XOR1_ENTROPY_CREDIT", "1.0")),
    "xor2_entropy_credit": float(os.environ.get("LSB_XOR2_ENTROPY_CREDIT", "0.5")),
    "source": {key: os.environ[key] for key in source_keys if key in os.environ},
}
with open(path, "w", encoding="utf-8") as stream:
    json.dump(value, stream, indent=2, ensure_ascii=False)
    stream.write("\n")
PYCONFIG

failures=0
for specification in "${profiles[@]}"; do
  IFS='|' read -r name mode bits credit <<< "$specification"
  minimum="$($VENV_PYTHON - "$bits" "$credit" <<'PY'
import math, sys
bits=int(sys.argv[1]); credit=float(sys.argv[2])
print(max(512, ((math.ceil(512 * bits / credit) + 7) // 8) * 8))
PY
)"
  configured="${LSB_CONDITIONER_INPUT_BITS:-${CONDITIONER_INPUT_BITS:-2048}}"
  (( configured < minimum )) && configured="$minimum"
  log "LSB profile: $name mode=$mode bits=$bits credit=$credit SHA3-input=$configured"
  set +e
  env \
    DATA_ROOT="$DATA_ROOT" \
    RUN_NAME="$CAMPAIGN/$name" \
    SAMPLE_MODE="$mode" \
    LSB_BITS="$bits" \
    ENTROPY_CREDIT_BITS_PER_PIXEL="$credit" \
    CONDITIONER_INPUT_BITS="$configured" \
    WARMUP_SECONDS="${WARMUP_SECONDS:-$PROFILE_WARMUP}" \
    CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-$PROFILE_CALIBRATION}" \
    DIAGNOSTIC_VN_BYTES="${LSB_DIAGNOSTIC_VN_BYTES:-0}" \
    ENABLE_VON_NEUMANN="${LSB_ENABLE_VON_NEUMANN:-0}" \
    CONDITIONED_BYTES="${CONDITIONED_BYTES:-$PROFILE_BYTES}" \
    VALIDATION_BYTES="${VALIDATION_BYTES:-$PROFILE_BYTES}" \
    WEB_IMAGES="${LSB_WEB_IMAGES:-0}" \
    LIVE_BYTE_DIAGNOSTICS="${LSB_LIVE_BYTE_DIAGNOSTICS:-0}" \
    MASK_SNAPSHOT_IMAGES=0 \
    DATASET_FOLLOW="${LSB_DATASET_FOLLOW:-0}" \
    SPATIAL_COMPARISON=0 DUAL_WEAVE_COMPARISON=0 BINARY_GEOMETRY_REPORT="${LSB_BINARY_GEOMETRY_REPORT:-0}" \
    "$SCRIPT_DIR/run_one.sh" > >(tee "$CAMPAIGN_DIR/$name.profile.log") 2>&1
  rc=$?
  set -e
  printf '%s\n' "$rc" > "$CAMPAIGN_DIR/$name.exit_code"
  "$VENV_PYTHON" - "$STATE_DIR/$name.json" "$name" "$mode" "$bits" "$credit" "$minimum" "$configured" "$rc" <<'PY'
import json, sys
path, name, mode, bits, credit, minimum, configured, rc = sys.argv[1:]
code = int(rc)
with open(path, "w", encoding="utf-8") as stream:
    json.dump({
        "profile": name,
        "sample_mode": mode,
        "lsb_bits": int(bits),
        "entropy_credit_bits_per_pixel": float(credit),
        "minimum_conditioner_input_bits": int(minimum),
        "conditioner_input_bits": int(configured),
        "exit_code": code,
        "status": "complete" if code == 0 else "failed",
    }, stream, indent=2, ensure_ascii=False)
PY
  if (( rc != 0 )); then
    failures=$((failures + 1))
    (( CONTINUE == 1 )) || break
  fi
done
"$VENV_PYTHON" "$SCRIPT_DIR/summarize_lsb_campaign.py" "$CAMPAIGN_DIR"
if (( failures > 0 )); then
  rm -f "$CAMPAIGN_DIR/READY.json"
  "$VENV_PYTHON" - "$CAMPAIGN_DIR/run_failed.json" "$failures" <<'PYFAIL'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"status": "failed", "failed_profiles": int(sys.argv[2])}, stream, indent=2)
PYFAIL
  log "Kampania LSB zakończona z błędami: $failures"
  exit 1
fi
rm -f "$CAMPAIGN_DIR/run_failed.json"
"$VENV_PYTHON" - "$CAMPAIGN_DIR/READY.json" <<'PYREADY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"status": "complete", "report": "lsb_campaign_report.html"}, stream, indent=2)
PYREADY
log "Kampania LSB zakończona: $CAMPAIGN_DIR/lsb_campaign_report.html"
