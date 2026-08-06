#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
export PYTHONUTF8=1 PYTHONIOENCODING=UTF-8
umask 027

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
source "$PROJECT_ROOT/scripts/lib.sh"
ensure_environment
PY="${PYTHON:-$VENV_PYTHON}"

SOURCE_TYPE="${SOURCE_TYPE:-tls-y}"
STORAGE_MODE="${FRAME_STORAGE_MODE:-y8}"
LIMIT_BYTES="${FRAME_BUFFER_LIMIT_BYTES:-300000000000}"
CHUNK_BYTES="${FRAME_CHUNK_BYTES:-1073741824}"
RESERVE_BYTES="${FRAME_RESERVE_FREE_BYTES:-2000000000}"
WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
CAMERA_FPS="${CAMERA_FPS:-10}"
EXPOSURE="${EXPOSURE:-7000}"

[[ "$SOURCE_TYPE" =~ ^(v4l2|tls-y|rtsp)$ ]] || fail "SOURCE_TYPE=v4l2, tls-y albo rtsp"
[[ "$STORAGE_MODE" =~ ^(y8|lsb-packed)$ ]] || fail "FRAME_STORAGE_MODE=y8 albo lsb-packed"
[[ "$LIMIT_BYTES" =~ ^[0-9]+$ ]] || fail "FRAME_BUFFER_LIMIT_BYTES musi być liczbą"
[[ "$CHUNK_BYTES" =~ ^[0-9]+$ ]] || fail "FRAME_CHUNK_BYTES musi być liczbą"

command=(
  "$PY" -m app.sources.frame_buffer_worker
  --source-type "$SOURCE_TYPE"
  --storage-mode "$STORAGE_MODE"
  --limit-bytes "$LIMIT_BYTES"
  --chunk-bytes "$CHUNK_BYTES"
  --reserve-free-bytes "$RESERVE_BYTES"
  --width "$WIDTH" --height "$HEIGHT" --camera-fps "$CAMERA_FPS"
)

if [[ -n "${FRAME_BUFFER_OUTPUT_DIR:-}" ]]; then
  command+=(--output-dir "$FRAME_BUFFER_OUTPUT_DIR")
fi
if [[ "${UPDATE_LATEST_LINK:-1}" == 0 ]]; then
  command+=(--no-update-latest-link)
fi
if [[ "${VERBOSE:-0}" == 1 ]]; then
  command+=(--verbose)
fi

case "$SOURCE_TYPE" in
  v4l2)
    need v4l2-ctl
    DEVICE="${DEVICE:-/dev/video1}"
    [[ -e "$DEVICE" && -r "$DEVICE" && -w "$DEVICE" ]] || fail "Brak dostępu R/W do $DEVICE"
    command+=(--device "$DEVICE" --strict-mode --manual-exposure --exposure-value "$EXPOSURE")
    ;;
  tls-y)
    PKI_DIR="${PKI_DIR:-$PROJECT_ROOT/pki}"
    TLS_CA="${TLS_CA:-$PKI_DIR/ca.crt}"
    TLS_CERT="${TLS_CERT:-$PKI_DIR/client.crt}"
    TLS_KEY="${TLS_KEY:-$PKI_DIR/client.key}"
    TLS_HOST="${TLS_HOST:-192.168.1.2}"
    TLS_PORT="${TLS_PORT:-9443}"
    TLS_SERVER_NAME="${TLS_SERVER_NAME:-camera}"
    for file in "$TLS_CA" "$TLS_CERT" "$TLS_KEY"; do [[ -r "$file" ]] || fail "Brak pliku TLS: $file"; done
    command+=(
      --tls-host "$TLS_HOST" --tls-port "$TLS_PORT"
      --tls-ca "$TLS_CA" --tls-cert "$TLS_CERT" --tls-key "$TLS_KEY"
      --tls-server-name "$TLS_SERVER_NAME"
      --strict-mode
      --source-connect-timeout-seconds "${SOURCE_CONNECT_TIMEOUT_SECONDS:-15}"
      --source-frame-timeout-seconds "${SOURCE_FRAME_TIMEOUT_SECONDS:-60}"
      --source-reconnect-attempts "${SOURCE_RECONNECT_ATTEMPTS:-5}"
      --source-reconnect-backoff-seconds "${SOURCE_RECONNECT_BACKOFF_SECONDS:-2}"
    )
    [[ "${ALLOW_SOURCE_FRAME_GAPS:-0}" == 1 ]] && command+=(--allow-source-frame-gaps)
    ;;
  rtsp)
    need ffmpeg; need ffprobe
    rtsp_file="${RTSP_URL_FILE:-}"
    if [[ -z "$rtsp_file" ]]; then
      [[ -n "${RTSP_URL:-}" ]] || fail "Ustaw RTSP_URL_FILE albo RTSP_URL"
      rtsp_file="$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/camera-entropy-rtsp.XXXXXX")"
      trap 'rm -f "$rtsp_file"' EXIT
      printf '%s\n' "$RTSP_URL" > "$rtsp_file"
      chmod 600 "$rtsp_file"
    fi
    command+=(
      --rtsp-url-file "$rtsp_file"
      --rtsp-transport "${RTSP_TRANSPORT:-tcp}"
      --rtsp-luma-mode "${RTSP_LUMA_MODE:-extract-y}"
      --rtsp-timeout-seconds "${RTSP_TIMEOUT_SECONDS:-15}"
      --no-manual-exposure
    )
    if [[ "${RTSP_STRICT_DIMENSIONS:-0}" == 1 ]]; then command+=(--strict-mode); else command+=(--no-strict-mode); fi
    ;;
esac

printf '[frame-buffer] Start: source=%s mode=%s limit=%s bytes\n' "$SOURCE_TYPE" "$STORAGE_MODE" "$LIMIT_BYTES"
cd "$PROJECT_ROOT"
exec "${command[@]}"
