#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
ensure_environment
INPUT="${INPUT:-}"
NIST_EA_DIR="${NIST_EA_DIR:-}"
SAMPLES="${SAMPLES:-1000000}"
[[ -n "$INPUT" && -f "$INPUT" ]] || fail "Ustaw INPUT na y_temporal_masked_validation.bin"
[[ -n "$NIST_EA_DIR" && -d "$NIST_EA_DIR" ]] || fail "Ustaw NIST_EA_DIR na oficjalne repo SP800-90B_EntropyAssessment"
find_tool(){ find "$NIST_EA_DIR" -type f -name "$1" -perm -111 -print -quit; }
NON_IID="$(find_tool ea_non_iid)"; IID="$(find_tool ea_iid)"; CONDITIONING="$(find_tool ea_conditioning)"
[[ -x "$NON_IID" ]] || fail "Nie znaleziono ea_non_iid; w repo uruchom make non_iid"
OUT_DIR="${OUT_DIR:-$(dirname "$INPUT")/nist_assessment}"
mkdir -p "$OUT_DIR"
SAMPLE_FILE="$OUT_DIR/selected_raw_1bit_samples.bin"
"$VENV_PYTHON" "$SCRIPT_DIR/packed_bits_to_samples.py" "$INPUT" "$SAMPLE_FILE" --samples "$SAMPLES" --overwrite
log "Uruchamiam ścieżkę non-IID (podstawowa dla tego źródła)"
"$NON_IID" -i "$SAMPLE_FILE" 1 | tee "$OUT_DIR/ea_non_iid.txt"
if [[ "${RUN_IID_TOO:-0}" == 1 ]]; then
  [[ -x "$IID" ]] || fail "Nie znaleziono ea_iid; uruchom make iid"
  "$IID" -i -a "$SAMPLE_FILE" 1 | tee "$OUT_DIR/ea_iid.txt"
fi
if [[ -n "${H_PER_INPUT_BIT:-}" ]]; then
  [[ -x "$CONDITIONING" ]] || fail "Nie znaleziono ea_conditioning; uruchom make conditioning"
  H_IN="$($VENV_PYTHON - "${H_PER_INPUT_BIT}" "${CONDITIONER_INPUT_BITS:-2048}" <<'PY'
import sys
print(float(sys.argv[1])*int(sys.argv[2]))
PY
)"
  "$CONDITIONING" -v "${CONDITIONER_INPUT_BITS:-2048}" 512 512 "$H_IN" | tee "$OUT_DIR/ea_conditioning.txt"
else
  warn "Nie uruchamiam ea_conditioning: ustaw H_PER_INPUT_BIT na konserwatywny wynik non-IID"
fi
sha256sum "$SAMPLE_FILE" > "$OUT_DIR/SHA256SUMS"
log "Wyniki: $OUT_DIR"
