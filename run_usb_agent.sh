#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
export PYTHONUTF8=1 PYTHONIOENCODING=UTF-8
umask 027
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fail(){ printf '[usb-agent] ERROR: %s\n' "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || fail "Brak polecenia: $1"; }
need python3; need v4l2-ctl
VENV_DIR="${AGENT_VENV_DIR:-$SCRIPT_DIR/.venv-agent}"
PY="$VENV_DIR/bin/python"
REQ="$SCRIPT_DIR/requirements-agent.txt"
if [[ ! -x "$PY" ]]; then
  python3 -m venv "$VENV_DIR" || fail "Nie udało się utworzyć venv; zainstaluj python3-venv"
fi
hash="$(python3 - "$REQ" <<'PY'
from pathlib import Path
import hashlib,sys
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
stamp="$VENV_DIR/.requirements.sha256"
if [[ "$(cat "$stamp" 2>/dev/null || true)" != "$hash" ]]; then
  "$PY" -m pip install -r "$REQ" || fail "Instalacja zależności nie powiodła się"
  printf '%s\n' "$hash" > "$stamp"
fi
"$PY" -m py_compile "$SCRIPT_DIR/frame_transport.py" "$SCRIPT_DIR/usb_y_capture_agent.py"
DEVICE="${DEVICE:-/dev/video1}"
[[ -e "$DEVICE" && -r "$DEVICE" && -w "$DEVICE" ]] || fail "Brak dostępu R/W do $DEVICE; nie używamy sudo"
PKI_DIR="${PKI_DIR:-$SCRIPT_DIR/pki}"
TLS_CA="${TLS_CA:-$PKI_DIR/ca.crt}"
TLS_CERT="${TLS_CERT:-$PKI_DIR/agent.crt}"
TLS_KEY="${TLS_KEY:-$PKI_DIR/agent.key}"
for file in "$TLS_CA" "$TLS_CERT" "$TLS_KEY"; do [[ -r "$file" ]] || fail "Brak pliku TLS: $file"; done
command=(
  "$PY" "$SCRIPT_DIR/usb_y_capture_agent.py"
  --device "$DEVICE"
  --width "${WIDTH:-1280}" --height "${HEIGHT:-720}" --camera-fps "${CAMERA_FPS:-10}"
  --manual-exposure --exposure-value "${EXPOSURE:-7000}" --strict-mode
  --control-check-seconds "${CONTROL_CHECK_SECONDS:-60}"
  --listen-host "${LISTEN_HOST:-0.0.0.0}" --listen-port "${TLS_PORT:-9443}"
  --socket-timeout-seconds "${SOCKET_TIMEOUT_SECONDS:-60}"
  --client-backlog-frames "${CLIENT_BACKLOG_FRAMES:-16}"
  --tls-ca "$TLS_CA" --tls-cert "$TLS_CERT" --tls-key "$TLS_KEY"
  --source-id "${SOURCE_ID:-camera}"
)
ALLOWED_CLIENT_CN="${ALLOWED_CLIENT_CN:-camera-compute}"
[[ -n "$ALLOWED_CLIENT_CN" ]] && command+=(--allowed-client-cn "$ALLOWED_CLIENT_CN")
[[ "${VERBOSE:-0}" == 1 ]] && command+=(--verbose)
exec "${command[@]}"
