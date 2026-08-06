#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail

export PYTHONUTF8=1
export PYTHONIOENCODING=UTF-8
if command -v locale >/dev/null 2>&1 && locale -a 2>/dev/null | grep -qi '^C\.utf8$'; then
    export LANG=C.utf8 LC_ALL=C.utf8
elif command -v locale >/dev/null 2>&1 && locale -a 2>/dev/null | grep -qi '^C\.UTF-8$'; then
    export LANG=C.UTF-8 LC_ALL=C.UTF-8
fi
umask 027

LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd -- "$LIB_DIR/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/.venv}"
REQUIREMENTS_FILE="${REQUIREMENTS_FILE:-$PROJECT_ROOT/requirements.txt}"
PYTHON_BOOTSTRAP="${PYTHON_BOOTSTRAP:-python3}"
VENV_PYTHON="$VENV_DIR/bin/python"
SERVER_FILE="$PROJECT_ROOT/app/core/camera_entropy_server.py"
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

log()  { printf '[camera-entropy] %s\n' "$*"; }
warn() { printf '[camera-entropy] WARNING: %s\n' "$*" >&2; }
fail() { printf '[camera-entropy] ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "Brak wymaganego polecenia: $1"; }

ensure_environment() {
    need "$PYTHON_BOOTSTRAP"
    need curl
    need sha256sum
    need flock
    [[ -f "$PROJECT_ROOT/app/core/camera_entropy_server.py" ]] || fail "Brak app/core/camera_entropy_server.py"
    [[ -f "$PROJECT_ROOT/app/reporting/analyze_entropy_stream.py" ]] || fail "Brak analizatora"
    [[ -f "$REQUIREMENTS_FILE" ]] || fail "Brak $REQUIREMENTS_FILE"
    if [[ ! -x "$VENV_PYTHON" ]]; then
        log "Tworzę środowisko Python: $VENV_DIR"
        "$PYTHON_BOOTSTRAP" -m venv "$VENV_DIR" || fail "Nie udało się utworzyć venv (sprawdź python3-venv)"
    fi
    local req_hash old_hash stamp
    req_hash="$($PYTHON_BOOTSTRAP - "$REQUIREMENTS_FILE" <<'PY'
from pathlib import Path
import hashlib,sys
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
    stamp="$VENV_DIR/.requirements.sha256"
    old_hash="$(cat "$stamp" 2>/dev/null || true)"
    if [[ "$req_hash" != "$old_hash" ]]; then
        log "Instaluję zależności z requirements.txt"
        "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS_FILE" || fail "Instalacja zależności nie powiodła się"
        printf '%s\n' "$req_hash" > "$stamp"
    fi
    "$VENV_PYTHON" -m compileall -q "$PROJECT_ROOT/app"
}

run_module() {
    local module="$1"; shift
    (cd "$PROJECT_ROOT" && "$VENV_PYTHON" -m "$module" "$@")
}

check_device() {
    local device="$1"
    [[ -e "$device" ]] || fail "Nie znaleziono urządzenia $device"
    [[ -r "$device" && -w "$device" ]] || fail "Brak dostępu R/W do $device; dodaj użytkownika do grupy video"
}

wait_for_api() {
    local host="$1" port="$2" pid="$3" timeout="${4:-120}"
    local started now
    started="$(date +%s)"
    while true; do
        if ! kill -0 "$pid" 2>/dev/null; then return 1; fi
        if curl -fsS --max-time 2 "http://$host:$port/api/stats" >/dev/null 2>&1 \
           || curl -fsS --max-time 2 "http://$host:$port/api/status" >/dev/null 2>&1; then return 0; fi
        now="$(date +%s)"; (( now - started < timeout )) || return 1; sleep 1
    done
}

analyze_one() {
    local input="$1" output="$2"
    [[ -s "$input" ]] || { warn "Pomijam analizę brakującego/pustego pliku: $input"; return 0; }
    run_module app.reporting.analyze_entropy_stream "$input" --output-dir "$output" \
        --chunk-mib "${ANALYSIS_CHUNK_MIB:-10}" \
        --lags "${ANALYSIS_LAGS:-1,2,3,4,5,6,7,8,10,12,16,24,32,48,64,128,256,512,1024}"
    if command -v ent >/dev/null 2>&1; then
        ent -b "$input" > "$output/ent-bit.txt" 2>&1 || true
        ent "$input" > "$output/ent-byte.txt" 2>&1 || true
    fi
}

write_sha256s() {
    local dir="$1"
    (cd "$dir"; find . -type f -name '*.bin' -print0 | sort -z | xargs -0 -r sha256sum) > "$dir/SHA256SUMS"
}

json_field() {
    local file="$1" expr="$2"
    "$VENV_PYTHON" - "$file" "$expr" <<'PY'
import json,sys
obj=json.load(open(sys.argv[1],encoding='utf-8')); cur=obj
for part in sys.argv[2].split('.'):
    cur=cur.get(part) if isinstance(cur,dict) else None
print('' if cur is None else cur)
PY
}
