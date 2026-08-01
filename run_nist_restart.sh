#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
ensure_environment
ROW_DATASET="${ROW_DATASET:-}"
NIST_EA_DIR="${NIST_EA_DIR:-}"
H_I="${H_I:-}"
[[ -n "$ROW_DATASET" && -f "$ROW_DATASET" ]] || fail "Ustaw ROW_DATASET na plik w formacie wierszowym"
[[ -n "$NIST_EA_DIR" && -d "$NIST_EA_DIR" ]] || fail "Ustaw NIST_EA_DIR na oficjalne repo SP800-90B_EntropyAssessment"
[[ -n "$H_I" ]] || fail "Ustaw H_I na konserwatywną ocenę entropii na próbkę"
RESTART="$(find "$NIST_EA_DIR" -type f -name ea_restart -perm -111 -print -quit)"
[[ -x "$RESTART" ]] || fail "Nie znaleziono ea_restart; w repo uruchom make restart"
OUT_DIR="${OUT_DIR:-$(dirname "$ROW_DATASET")/nist_restart_assessment}"
mkdir -p "$OUT_DIR"
MODE="${RESTART_MODE:-non-iid}"
case "$MODE" in
  non-iid) mode_flag=-n ;;
  iid) mode_flag=-i ;;
  *) fail "RESTART_MODE musi być non-iid albo iid" ;;
esac
"$RESTART" "$mode_flag" "$ROW_DATASET" 1 "$H_I" | tee "$OUT_DIR/ea_restart.txt"
sha256sum "$ROW_DATASET" > "$OUT_DIR/SHA256SUMS"
log "Wyniki restart testu: $OUT_DIR"
