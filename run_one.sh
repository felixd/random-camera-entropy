#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ensure_environment

SOURCE_TYPE="${SOURCE_TYPE:-v4l2}"
DATA_ROOT="${DATA_ROOT:-$SCRIPT_DIR/data}"
RUN_NAME="${RUN_NAME:-distributed-${SOURCE_TYPE}-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_DIR="${RUN_DIR:-$DATA_ROOT/$RUN_NAME}"
HOST="${HOST:-0.0.0.0}"
API_HOST="${API_HOST:-127.0.0.1}"
DISPLAY_HOST="${DISPLAY_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
PORT="${PORT:-8087}"
WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
CAMERA_FPS="${CAMERA_FPS:-10}"
EXPOSURE="${EXPOSURE:-7000}"
PAIRING_MODE="${PAIRING_MODE:-disjoint}"
PAIR_LAG_FRAMES="${PAIR_LAG_FRAMES:-4}"
SAMPLE_MODE="${SAMPLE_MODE:-xor}"
LSB_BITS="${LSB_BITS:-1}"
ENTROPY_CREDIT_BITS_PER_PIXEL="${ENTROPY_CREDIT_BITS_PER_PIXEL:-1.0}"
SPATIAL_SAMPLING="${SPATIAL_SAMPLING:-checkerboard-even}"
# CAMERA_ENTROPY_SPATIAL_V7_7
SPATIAL_MASK_PATTERN="${SPATIAL_MASK_PATTERN:-legacy}"
SPATIAL_STEP_X="${SPATIAL_STEP_X:-1}"
SPATIAL_STEP_Y="${SPATIAL_STEP_Y:-1}"
SPATIAL_PHASE_X="${SPATIAL_PHASE_X:-0}"
SPATIAL_PHASE_Y="${SPATIAL_PHASE_Y:-0}"
SPATIAL_BLOCK_WIDTH="${SPATIAL_BLOCK_WIDTH:-4}"
SPATIAL_BLOCK_HEIGHT="${SPATIAL_BLOCK_HEIGHT:-4}"
TEMPORAL_SPATIAL_OFFSET_X="${TEMPORAL_SPATIAL_OFFSET_X:-0}"
TEMPORAL_SPATIAL_OFFSET_Y="${TEMPORAL_SPATIAL_OFFSET_Y:-0}"
SERIALIZATION_ORDER="${SERIALIZATION_ORDER:-row-major}"
SERIALIZATION_TILE_WIDTH="${SERIALIZATION_TILE_WIDTH:-16}"
SERIALIZATION_TILE_HEIGHT="${SERIALIZATION_TILE_HEIGHT:-16}"
SPATIAL_COMPARISON="${SPATIAL_COMPARISON:-0}"
DUAL_WEAVE_COMPARISON="${DUAL_WEAVE_COMPARISON:-0}"
DUAL_WEAVE_ORDERS="${DUAL_WEAVE_ORDERS:-row-major}"
DUAL_WEAVE_ALIGNMENTS="${DUAL_WEAVE_ALIGNMENTS:-same-group}"
BINARY_GEOMETRY_REPORT="${BINARY_GEOMETRY_REPORT:-$DUAL_WEAVE_COMPARISON}"
BINARY_GEOMETRY_MAX_FILES="${BINARY_GEOMETRY_MAX_FILES:-8}"
BINARY_GEOMETRY_MAX_BYTES="${BINARY_GEOMETRY_MAX_BYTES:-16777216}"
WARMUP_SECONDS="${WARMUP_SECONDS:-1800}"
CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-512}"
DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-10485760}"
CONDITIONED_BYTES="${CONDITIONED_BYTES:-10485760}"
VALIDATION_BYTES="${VALIDATION_BYTES:-10485760}"
CONDITIONER_INPUT_BITS="${CONDITIONER_INPUT_BITS:-2048}"
CORRELATION_EVERY="${CORRELATION_EVERY:-10}"
CORRELATION_DISTANCES="${CORRELATION_DISTANCES:-1,2,3,4,6,8,12,16,24,32,48,64}"
WEB_IMAGES="${WEB_IMAGES:-0}"
MASK_SNAPSHOT_INTERVAL_SECONDS="${MASK_SNAPSHOT_INTERVAL_SECONDS:-60}"
MASK_SNAPSHOT_IMAGES="${MASK_SNAPSHOT_IMAGES:-0}"
ENABLE_VON_NEUMANN="${ENABLE_VON_NEUMANN:-1}"
LIVE_BYTE_DIAGNOSTICS="${LIVE_BYTE_DIAGNOSTICS:-1}"
LIVE_HEATMAP_INTERVAL_SECONDS="${LIVE_HEATMAP_INTERVAL_SECONDS:-60}"
LIVE_HEATMAP_MAX_STAGES="${LIVE_HEATMAP_MAX_STAGES:-8}"
LIVE_HEATMAP_MIN_BYTES="${LIVE_HEATMAP_MIN_BYTES:-4096}"
MAX_ACTIVE_CLIP_RATE="${MAX_ACTIVE_CLIP_RATE:-0.001}"
CLIP_FAIL_CONSECUTIVE="${CLIP_FAIL_CONSECUTIVE:-3}"
CONTROL_CHECK_SECONDS="${CONTROL_CHECK_SECONDS:-60}"
CONTROL_FAIL_CONSECUTIVE="${CONTROL_FAIL_CONSECUTIVE:-1}"
START_TIMEOUT_SECONDS="${START_TIMEOUT_SECONDS:-120}"
RTSP_TEMP_FILE=""

for value in "$DIAGNOSTIC_VN_BYTES" "$CONDITIONED_BYTES" "$VALIDATION_BYTES"; do
    [[ "$value" =~ ^[0-9]+$ ]] || fail "Rozmiary wyjścia muszą być liczbami całkowitymi"
done
[[ "$SOURCE_TYPE" =~ ^(v4l2|tls-y|rtsp|dataset-y)$ ]] || fail "SOURCE_TYPE=v4l2, tls-y, rtsp albo dataset-y"
[[ "$SAMPLE_MODE" =~ ^(xor|direct|delta)$ ]] || fail "SAMPLE_MODE=xor, direct albo delta"
[[ "$LSB_BITS" =~ ^[0-9]+$ ]] || fail "LSB_BITS musi być liczbą całkowitą"
(( LSB_BITS >= 1 && LSB_BITS <= 4 )) || fail "LSB_BITS musi być w zakresie 1..4"
awk -v c="$ENTROPY_CREDIT_BITS_PER_PIXEL" -v b="$LSB_BITS" 'BEGIN{exit !(c>0 && c<=b)}' || fail "ENTROPY_CREDIT_BITS_PER_PIXEL musi być w (0, LSB_BITS]"
[[ "$SPATIAL_MASK_PATTERN" =~ ^(legacy|full|checkerboard-even|checkerboard-odd|grid|block)$ ]] || fail "Nieprawidłowy SPATIAL_MASK_PATTERN"
[[ "$SERIALIZATION_ORDER" =~ ^(row-major|serpentine|tile-interleave)$ ]] || fail "Nieprawidłowy SERIALIZATION_ORDER"
for value in "$SPATIAL_STEP_X" "$SPATIAL_STEP_Y" "$SPATIAL_PHASE_X" "$SPATIAL_PHASE_Y" "$SPATIAL_BLOCK_WIDTH" "$SPATIAL_BLOCK_HEIGHT" "$SERIALIZATION_TILE_WIDTH" "$SERIALIZATION_TILE_HEIGHT"; do
    [[ "$value" =~ ^[0-9]+$ ]] || fail "Parametry maski i kafli muszą być nieujemnymi liczbami całkowitymi"
done
for value in "$TEMPORAL_SPATIAL_OFFSET_X" "$TEMPORAL_SPATIAL_OFFSET_Y"; do
    [[ "$value" =~ ^-?[0-9]+$ ]] || fail "Offsety przestrzenne muszą być liczbami całkowitymi"
done
(( SPATIAL_STEP_X >= 1 && SPATIAL_STEP_Y >= 1 )) || fail "SPATIAL_STEP_X/Y muszą być >= 1"
(( SPATIAL_BLOCK_WIDTH >= 1 && SPATIAL_BLOCK_HEIGHT >= 1 )) || fail "SPATIAL_BLOCK_WIDTH/HEIGHT muszą być >= 1"
(( SERIALIZATION_TILE_WIDTH >= 1 && SERIALIZATION_TILE_HEIGHT >= 1 )) || fail "SERIALIZATION_TILE_WIDTH/HEIGHT muszą być >= 1"
if [[ "$SPATIAL_MASK_PATTERN" == grid ]]; then
    (( SPATIAL_PHASE_X < SPATIAL_STEP_X && SPATIAL_PHASE_Y < SPATIAL_STEP_Y )) || fail "Dla grid faza musi być mniejsza od kroku"
fi
if [[ "$SPATIAL_MASK_PATTERN" == block ]]; then
    (( SPATIAL_PHASE_X < SPATIAL_BLOCK_WIDTH && SPATIAL_PHASE_Y < SPATIAL_BLOCK_HEIGHT )) || fail "Dla block faza musi mieścić się w bloku"
fi
if [[ "$SPATIAL_COMPARISON" == 1 && "$SPATIAL_MASK_PATTERN" != legacy ]]; then
    fail "SPATIAL_COMPARISON=1 obsługuje stałe warianty zgodności; własne maski uruchamiaj jako osobne profile"
fi
[[ "$SPATIAL_COMPARISON" == 0 || "$SPATIAL_COMPARISON" == 1 ]] || fail "SPATIAL_COMPARISON=0 albo 1"
[[ "$DUAL_WEAVE_COMPARISON" == 0 || "$DUAL_WEAVE_COMPARISON" == 1 ]] || fail "DUAL_WEAVE_COMPARISON=0 albo 1"
[[ "$BINARY_GEOMETRY_REPORT" == 0 || "$BINARY_GEOMETRY_REPORT" == 1 ]] || fail "BINARY_GEOMETRY_REPORT=0 albo 1"
[[ "$WEB_IMAGES" == 0 || "$WEB_IMAGES" == 1 ]] || fail "WEB_IMAGES=0 albo 1"
[[ "$MASK_SNAPSHOT_IMAGES" == 0 || "$MASK_SNAPSHOT_IMAGES" == 1 ]] || fail "MASK_SNAPSHOT_IMAGES=0 albo 1"
[[ "$ENABLE_VON_NEUMANN" == 0 || "$ENABLE_VON_NEUMANN" == 1 ]] || fail "ENABLE_VON_NEUMANN=0 albo 1"
[[ "$LIVE_BYTE_DIAGNOSTICS" == 0 || "$LIVE_BYTE_DIAGNOSTICS" == 1 ]] || fail "LIVE_BYTE_DIAGNOSTICS=0 albo 1"
for value in "$BINARY_GEOMETRY_MAX_FILES" "$BINARY_GEOMETRY_MAX_BYTES"; do
    [[ "$value" =~ ^[0-9]+$ ]] || fail "Limity raportu geometrii muszą być liczbami całkowitymi"
done
for value in "$LIVE_HEATMAP_INTERVAL_SECONDS" "$LIVE_HEATMAP_MAX_STAGES" "$LIVE_HEATMAP_MIN_BYTES"; do
    [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] || fail "Parametry live heatmap muszą być liczbami"
done
(( BINARY_GEOMETRY_MAX_FILES >= 1 )) || fail "BINARY_GEOMETRY_MAX_FILES musi być >= 1"
(( BINARY_GEOMETRY_MAX_BYTES >= 4096 )) || fail "BINARY_GEOMETRY_MAX_BYTES musi być >= 4096"
awk -v v="$LIVE_HEATMAP_INTERVAL_SECONDS" 'BEGIN{exit !(v>=0)}' || fail "LIVE_HEATMAP_INTERVAL_SECONDS musi być >= 0"
[[ "$LIVE_HEATMAP_MAX_STAGES" =~ ^[0-9]+$ ]] || fail "LIVE_HEATMAP_MAX_STAGES musi być całkowite"
[[ "$LIVE_HEATMAP_MIN_BYTES" =~ ^[0-9]+$ ]] || fail "LIVE_HEATMAP_MIN_BYTES musi być całkowite"
(( LIVE_HEATMAP_MAX_STAGES >= 1 && LIVE_HEATMAP_MAX_STAGES <= 32 )) || fail "LIVE_HEATMAP_MAX_STAGES musi być 1..32"
(( LIVE_HEATMAP_MIN_BYTES >= 256 )) || fail "LIVE_HEATMAP_MIN_BYTES musi być >= 256"
[[ ! -e "$RUN_DIR" ]] || fail "Katalog już istnieje: $RUN_DIR"
mkdir -p "$RUN_DIR" "$DATA_ROOT"
exec 9>"$DATA_ROOT/.camera-entropy-v7.lock"
flock -n 9 || fail "Inny test korzysta z tego katalogu data"

source_args=(--source-type "$SOURCE_TYPE")
case "$SOURCE_TYPE" in
    v4l2)
        need v4l2-ctl
        DEVICE="${DEVICE:-/dev/video1}"
        check_device "$DEVICE"
        source_args+=(
            --device "$DEVICE" --width "$WIDTH" --height "$HEIGHT" --camera-fps "$CAMERA_FPS"
            --strict-mode --manual-exposure --exposure-value "$EXPOSURE"
        )
        source_label="$DEVICE"
        ;;
    tls-y)
        PKI_DIR="${PKI_DIR:-$SCRIPT_DIR/pki}"
        TLS_HOST="${TLS_HOST:-192.168.1.2}"
        TLS_PORT="${TLS_PORT:-9443}"
        TLS_CA="${TLS_CA:-$PKI_DIR/ca.crt}"
        TLS_CERT="${TLS_CERT:-$PKI_DIR/client.crt}"
        TLS_KEY="${TLS_KEY:-$PKI_DIR/client.key}"
        TLS_SERVER_NAME="${TLS_SERVER_NAME:-camera}"
        for file in "$TLS_CA" "$TLS_CERT" "$TLS_KEY"; do [[ -r "$file" ]] || fail "Brak pliku TLS: $file"; done
        source_args+=(
            --tls-host "$TLS_HOST" --tls-port "$TLS_PORT"
            --tls-ca "$TLS_CA" --tls-cert "$TLS_CERT" --tls-key "$TLS_KEY"
            --tls-server-name "$TLS_SERVER_NAME"
            --width "$WIDTH" --height "$HEIGHT" --strict-mode
            --manual-exposure --exposure-value "$EXPOSURE"
            --source-connect-timeout-seconds "${SOURCE_CONNECT_TIMEOUT_SECONDS:-15}"
            --source-frame-timeout-seconds "${SOURCE_FRAME_TIMEOUT_SECONDS:-60}"
            --source-reconnect-attempts "${SOURCE_RECONNECT_ATTEMPTS:-5}"
            --source-reconnect-backoff-seconds "${SOURCE_RECONNECT_BACKOFF_SECONDS:-2}"
        )
        if [[ "${ALLOW_SOURCE_FRAME_GAPS:-0}" == 1 ]]; then source_args+=(--allow-source-frame-gaps); fi
        if [[ "${ALLOW_SOURCE_RECONNECT_DURING_PRODUCTION:-0}" == 1 ]]; then source_args+=(--allow-source-reconnect-during-production); fi
        source_label="tls-y://$TLS_HOST:$TLS_PORT"
        ;;
    rtsp)
        need ffmpeg
        need ffprobe
        RTSP_TRANSPORT="${RTSP_TRANSPORT:-tcp}"
        RTSP_LUMA_MODE="${RTSP_LUMA_MODE:-extract-y}"
        RTSP_TIMEOUT_SECONDS="${RTSP_TIMEOUT_SECONDS:-15}"
        rtsp_file="${RTSP_URL_FILE:-}"
        if [[ -z "$rtsp_file" ]]; then
            [[ -n "${RTSP_URL:-}" ]] || fail "Ustaw RTSP_URL_FILE albo RTSP_URL"
            rtsp_file="$(mktemp "${XDG_RUNTIME_DIR:-/tmp}/camera-entropy-rtsp.XXXXXX")"
            RTSP_TEMP_FILE="$rtsp_file"
            trap '[[ -n "${RTSP_TEMP_FILE:-}" ]] && rm -f "$RTSP_TEMP_FILE"' EXIT
            umask 077
            printf '%s\n' "$RTSP_URL" > "$rtsp_file"
            chmod 600 "$rtsp_file"
        fi
        [[ -r "$rtsp_file" ]] || fail "Nie mogę odczytać RTSP_URL_FILE=$rtsp_file"
        source_args+=(
            --rtsp-url-file "$rtsp_file" --rtsp-transport "$RTSP_TRANSPORT"
            --rtsp-luma-mode "$RTSP_LUMA_MODE" --rtsp-timeout-seconds "$RTSP_TIMEOUT_SECONDS"
            --width "$WIDTH" --height "$HEIGHT"
        )
        if [[ "${RTSP_STRICT_DIMENSIONS:-0}" == 1 ]]; then source_args+=(--strict-mode); else source_args+=(--no-strict-mode); fi
        source_label="rtsp (URL redacted)"
        ;;
    dataset-y)
        DATASET_DIR="${DATASET_DIR:-data/frame-buffer-latest}"
        if [[ "$DATASET_DIR" != /* ]]; then
            DATASET_DIR="$SCRIPT_DIR/$DATASET_DIR"
        fi
        dataset_metadata="$("$VENV_PYTHON" - "$DATASET_DIR" <<'PY'
import json
import sys
from pathlib import Path

DATASET_FORMAT = "camera-entropy-frame-buffer-v1"
READABLE = {"recording", "complete", "stopped", "failed"}
MODES = {"y8", "lsb-packed"}

path = Path(sys.argv[1]).expanduser().resolve()
if not path.is_dir():
    raise SystemExit(f"Dataset nie istnieje lub nie jest katalogiem: {path}")
manifest_path = path / "manifest.json"
index_path = path / "frames.csv"
chunks_path = path / "chunks"
if not manifest_path.is_file():
    raise SystemExit(f"Brak manifestu datasetu: {manifest_path}")
if not index_path.is_file():
    raise SystemExit(f"Brak indeksu datasetu: {index_path}")
if not chunks_path.is_dir():
    raise SystemExit(f"Brak katalogu chunków datasetu: {chunks_path}")
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"Nie można odczytać manifestu datasetu: {exc}")
if manifest.get("format") != DATASET_FORMAT:
    raise SystemExit(f"Nieobsługiwany format datasetu: {manifest.get('format')!r}")
status = str(manifest.get("status", ""))
mode = str(manifest.get("storage_mode", ""))
if status not in READABLE:
    raise SystemExit(f"Dataset ma nieobsługiwany status: {status!r}")
if mode not in MODES:
    raise SystemExit(f"Dataset ma nieobsługiwany tryb zapisu: {mode!r}")
try:
    width = int(manifest.get("width", 0))
    height = int(manifest.get("height", 0))
    payload = int(manifest.get("frame_payload_bytes", 0))
    frame_count = int(manifest.get("frame_count", 0) or 0)
except (TypeError, ValueError) as exc:
    raise SystemExit(f"Manifest datasetu zawiera nieprawidłowe liczby: {exc}")
expected = width * height if mode == "y8" else (width * height + 7) // 8
if width <= 0 or height <= 0 or payload != expected:
    raise SystemExit(
        f"Nieprawidłowa geometria/payload datasetu: {width}x{height}, payload={payload}, expected={expected}"
    )
if status != "recording" and frame_count < 2:
    raise SystemExit(f"Zakończony dataset ma zbyt mało klatek: {frame_count}")
print(path)
print(width)
print(height)
print(status)
PY
)" || fail "Preflight datasetu nie powiódł się"
        mapfile -t dataset_fields <<< "$dataset_metadata"
        (( ${#dataset_fields[@]} == 4 )) || fail "Nieprawidłowy wynik preflightu datasetu"
        DATASET_DIR="${dataset_fields[0]}"
        WIDTH="${dataset_fields[1]}"
        HEIGHT="${dataset_fields[2]}"
        DATASET_STATUS="${dataset_fields[3]}"
        log "Dataset: $DATASET_DIR status=$DATASET_STATUS size=${WIDTH}x${HEIGHT}"
        source_args+=(
            --dataset-dir "$DATASET_DIR"
            --dataset-start-frame "${DATASET_START_FRAME:-0}"
            --dataset-max-frames "${DATASET_MAX_FRAMES:-0}"
            --dataset-rate "${DATASET_RATE:-1}"
            --dataset-poll-seconds "${DATASET_POLL_SECONDS:-0.1}"
            --dataset-follow-timeout-seconds "${DATASET_FOLLOW_TIMEOUT_SECONDS:-0}"
            --width "$WIDTH" --height "$HEIGHT" --strict-mode
        )
        if [[ "${DATASET_REALTIME:-0}" == 1 ]]; then source_args+=(--dataset-realtime); else source_args+=(--no-dataset-realtime); fi
        if [[ "${DATASET_VERIFY_HASHES:-0}" == 1 ]]; then source_args+=(--dataset-verify-hashes); fi
        if [[ "${DATASET_FOLLOW:-1}" == 1 ]]; then source_args+=(--dataset-follow); else source_args+=(--no-dataset-follow); fi
        source_label="dataset-y://$DATASET_DIR"
        ;;
esac

web_flag="--web-images"; [[ "$WEB_IMAGES" == 1 ]] || web_flag="--no-web-images"
mask_snapshot_images_flag="--mask-snapshot-images"; [[ "$MASK_SNAPSHOT_IMAGES" == 1 ]] || mask_snapshot_images_flag="--no-mask-snapshot-images"
vn_stage_flag="--von-neumann-stage"; [[ "$ENABLE_VON_NEUMANN" == 1 ]] || vn_stage_flag="--no-von-neumann-stage"
live_byte_flag="--live-byte-diagnostics"; [[ "$LIVE_BYTE_DIAGNOSTICS" == 1 ]] || live_byte_flag="--no-live-byte-diagnostics"
comparison_flag="--no-spatial-comparison"; [[ "$SPATIAL_COMPARISON" == 1 ]] && comparison_flag="--spatial-comparison"
dual_weave_flag="--no-dual-weave-comparison"; [[ "$DUAL_WEAVE_COMPARISON" == 1 ]] && dual_weave_flag="--dual-weave-comparison"
if (( DIAGNOSTIC_VN_BYTES > 0 )); then
    vn_args=(--write-output --max-output-bytes "$DIAGNOSTIC_VN_BYTES")
else
    vn_args=(--no-write-output --max-output-bytes 0)
fi
if (( CONDITIONED_BYTES > 0 )); then
    conditioner_args=(--conditioner sha3-512 --conditioner-input-bits "$CONDITIONER_INPUT_BITS" --write-conditioned-output --conditioned-output-bytes "$CONDITIONED_BYTES")
else
    conditioner_args=(--conditioner none --no-write-conditioned-output --conditioned-output-bytes 0)
fi

command=(
    "$VENV_PYTHON" "$SERVER_FILE"
    "${source_args[@]}" --host "$HOST" --port "$PORT"
    --thermal-warmup-seconds "$WARMUP_SECONDS"
    --pairing-mode "$PAIRING_MODE" --pair-lag-frames "$PAIR_LAG_FRAMES"
    --sample-mode "$SAMPLE_MODE" --lsb-bits "$LSB_BITS"
    --entropy-credit-bits-per-pixel "$ENTROPY_CREDIT_BITS_PER_PIXEL"
    --spatial-sampling "$SPATIAL_SAMPLING" "$comparison_flag"
    --spatial-mask-pattern "$SPATIAL_MASK_PATTERN"
    --spatial-step-x "$SPATIAL_STEP_X" --spatial-step-y "$SPATIAL_STEP_Y"
    --spatial-phase-x "$SPATIAL_PHASE_X" --spatial-phase-y "$SPATIAL_PHASE_Y"
    --spatial-block-width "$SPATIAL_BLOCK_WIDTH" --spatial-block-height "$SPATIAL_BLOCK_HEIGHT"
    --temporal-spatial-offset-x "$TEMPORAL_SPATIAL_OFFSET_X"
    --temporal-spatial-offset-y "$TEMPORAL_SPATIAL_OFFSET_Y"
    --serialization-order "$SERIALIZATION_ORDER"
    --serialization-tile-width "$SERIALIZATION_TILE_WIDTH"
    --serialization-tile-height "$SERIALIZATION_TILE_HEIGHT"
    "$dual_weave_flag" --dual-weave-orders "$DUAL_WEAVE_ORDERS"
    --dual-weave-alignments "$DUAL_WEAVE_ALIGNMENTS"
    --calibration-pairs "$CALIBRATION_PAIRS"
    --no-dynamic-clip-filter --max-active-clip-rate "$MAX_ACTIVE_CLIP_RATE"
    --clip-fail-consecutive "$CLIP_FAIL_CONSECUTIVE"
    --control-check-seconds "$CONTROL_CHECK_SECONDS"
    --control-fail-consecutive "$CONTROL_FAIL_CONSECUTIVE" --control-stop-on-mismatch
    --shadow-stop-on-drift
    --output-dir "$RUN_DIR"
    "${vn_args[@]}" "${conditioner_args[@]}"
    --validation-output-bytes "$VALIDATION_BYTES"
    --correlation-distances "$CORRELATION_DISTANCES" --correlation-every "$CORRELATION_EVERY"
    --mask-snapshot-interval-seconds "$MASK_SNAPSHOT_INTERVAL_SECONDS"
    "$mask_snapshot_images_flag" "$vn_stage_flag"
    "$live_byte_flag"
    --live-heatmap-interval-seconds "$LIVE_HEATMAP_INTERVAL_SECONDS"
    --live-heatmap-max-stages "$LIVE_HEATMAP_MAX_STAGES"
    --live-heatmap-min-bytes "$LIVE_HEATMAP_MIN_BYTES"
    --exit-on-output-limit --exit-on-failure
    "$web_flag"
)

# The RTSP URL itself is never put in command.txt; only the protected URL-file path is present.
printf '%q ' "${command[@]}" > "$RUN_DIR/command.txt"; printf '\n' >> "$RUN_DIR/command.txt"
cat > "$RUN_DIR/runner_config.json" <<JSON
{
  "run_name": "$RUN_NAME",
  "source_type": "$SOURCE_TYPE",
  "source_label": "$source_label",
  "exposure": $EXPOSURE,
  "pairing_mode": "$PAIRING_MODE",
  "pair_lag_frames": $PAIR_LAG_FRAMES,
  "sample_mode": "$SAMPLE_MODE",
  "lsb_bits": $LSB_BITS,
  "bit_order": "pixel-major-lsb-first",
  "entropy_credit_bits_per_pixel": $ENTROPY_CREDIT_BITS_PER_PIXEL,
  "mask_calibration_source": "temporal-xor-lsb0",
  "spatial_sampling": "$SPATIAL_SAMPLING",
  "spatial_mask_pattern": "$SPATIAL_MASK_PATTERN",
  "spatial_step_x": $SPATIAL_STEP_X,
  "spatial_step_y": $SPATIAL_STEP_Y,
  "spatial_phase_x": $SPATIAL_PHASE_X,
  "spatial_phase_y": $SPATIAL_PHASE_Y,
  "spatial_block_width": $SPATIAL_BLOCK_WIDTH,
  "spatial_block_height": $SPATIAL_BLOCK_HEIGHT,
  "temporal_spatial_offset_x": $TEMPORAL_SPATIAL_OFFSET_X,
  "temporal_spatial_offset_y": $TEMPORAL_SPATIAL_OFFSET_Y,
  "serialization_order": "$SERIALIZATION_ORDER",
  "serialization_tile_width": $SERIALIZATION_TILE_WIDTH,
  "serialization_tile_height": $SERIALIZATION_TILE_HEIGHT,
  "spatial_comparison": $SPATIAL_COMPARISON,
  "dual_weave_comparison": $DUAL_WEAVE_COMPARISON,
  "dual_weave_orders": "$DUAL_WEAVE_ORDERS",
  "dual_weave_alignments": "$DUAL_WEAVE_ALIGNMENTS",
  "binary_geometry_report": $BINARY_GEOMETRY_REPORT,
  "binary_geometry_max_files": $BINARY_GEOMETRY_MAX_FILES,
  "binary_geometry_max_bytes": $BINARY_GEOMETRY_MAX_BYTES,
  "warmup_seconds": $WARMUP_SECONDS,
  "calibration_pairs": $CALIBRATION_PAIRS,
  "diagnostic_vn_bytes": $DIAGNOSTIC_VN_BYTES,
  "conditioned_bytes": $CONDITIONED_BYTES,
  "conditioner_input_bits": $CONDITIONER_INPUT_BITS,
  "validation_bytes": $VALIDATION_BYTES,
  "web_images": $WEB_IMAGES,
  "mask_snapshot_images": $MASK_SNAPSHOT_IMAGES,
  "von_neumann_stage": $ENABLE_VON_NEUMANN,
  "live_byte_diagnostics": $LIVE_BYTE_DIAGNOSTICS,
  "live_heatmap_interval_seconds": $LIVE_HEATMAP_INTERVAL_SECONDS,
  "live_heatmap_max_stages": $LIVE_HEATMAP_MAX_STAGES,
  "live_heatmap_min_bytes": $LIVE_HEATMAP_MIN_BYTES,
  "fixed_production_mask": true,
  "fail_closed": true
}
JSON

log "Start: $RUN_DIR"
log "Źródło: $SOURCE_TYPE — $source_label"
log "Nastaw: exposure=$EXPOSURE, $PAIRING_MODE/k=$PAIR_LAG_FRAMES, sample=$SAMPLE_MODE/${LSB_BITS}LSB, credit=$ENTROPY_CREDIT_BITS_PER_PIXEL bit/pixel, spatial=$SPATIAL_SAMPLING, dual-weave=$DUAL_WEAVE_COMPARISON (orders=$DUAL_WEAVE_ORDERS; alignments=$DUAL_WEAVE_ALIGNMENTS)"
log "Cele: VN=$DIAGNOSTIC_VN_BYTES B, SHA3=$CONDITIONED_BYTES B, validation=$VALIDATION_BYTES B"
"${command[@]}" > >(tee "$RUN_DIR/console.log") 2>&1 &
pid=$!
cleanup() {
    if kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
    [[ -n "${RTSP_TEMP_FILE:-}" ]] && rm -f "$RTSP_TEMP_FILE" || true
}
trap cleanup INT TERM EXIT
if ! wait_for_api "$API_HOST" "$PORT" "$pid" "$START_TIMEOUT_SECONDS"; then
    wait "$pid" 2>/dev/null || true
    fail "API nie wystartowało; sprawdź $RUN_DIR/console.log"
fi
log "Panel WWW: http://${DISPLAY_HOST:-127.0.0.1}:$PORT/"
if [[ -n "${RTSP_TEMP_FILE:-}" ]]; then
    rm -f "$RTSP_TEMP_FILE"
    RTSP_TEMP_FILE=""
fi
set +e
wait "$pid"
rc=$?
set -e
trap - INT TERM EXIT

run_failed=0
failure_reason=""
failure_state=""
if [[ -f "$RUN_DIR/run_failed.json" ]]; then
    run_failed=1
    failure_reason="$(json_field "$RUN_DIR/run_failed.json" reason)"
    failure_state="$(json_field "$RUN_DIR/run_failed.json" state)"
    warn "Przebieg zatrzymany fail-closed; generuję raport z częściowych danych: ${failure_reason:-nieznany powód}"
elif [[ ! -f "$RUN_DIR/output_complete.json" ]]; then
    fail "Przebieg nie zakończył się poprawnie i nie zapisał run_failed.json (rc=$rc)"
fi

log "Analiza plików"
analyze_one "$RUN_DIR/camera_entropy_sha3_512.bin" "$RUN_DIR/analysis_conditioned"
analyze_one "$RUN_DIR/y_temporal_vn.bin" "$RUN_DIR/analysis_vn"
analyze_one "$RUN_DIR/y_temporal_masked_validation.bin" "$RUN_DIR/analysis_selected_raw"
if [[ -s "$RUN_DIR/y_temporal_masked_validation.bin" ]]; then
    "$VENV_PYTHON" "$SCRIPT_DIR/analyze_lsb_bitplanes.py" "$RUN_DIR" \
        --lsb-bits "$LSB_BITS" --max-bytes "$BINARY_GEOMETRY_MAX_BYTES"
else
    warn "Pomijam analizę bitplane: brak danych masked validation"
fi
analyze_one "$RUN_DIR/y_temporal_raw_validation.bin" "$RUN_DIR/analysis_temporal_raw"
analyze_one "$RUN_DIR/y_direct_lsb_common_mask_validation.bin" "$RUN_DIR/analysis_direct_lsb"
if [[ "$SPATIAL_COMPARISON" == 1 ]]; then
    for file in "$RUN_DIR"/y_temporal_vn_checkerboard_*.bin "$RUN_DIR"/y_temporal_vn_grid2x2.bin; do
        [[ -e "$file" ]] || continue
        name="$(basename "$file" .bin)"
        analyze_one "$file" "$RUN_DIR/analysis_$name"
    done
fi
if [[ "$DUAL_WEAVE_COMPARISON" == 1 ]]; then
    old_lags="${ANALYSIS_LAGS:-}"
    export ANALYSIS_LAGS="${DUAL_WEAVE_ANALYSIS_LAGS:-1,2,3,4,5,6,7,8,10,12,16,24,32,48,64,128,256,512,1022,1023,1024,1025,1026,1279,1280,1281,2047,2048,2049}"
    for file in "$RUN_DIR"/dual_weave_*.bin; do
        [[ -s "$file" ]] || continue
        name="$(basename "$file" .bin)"
        analyze_one "$file" "$RUN_DIR/analysis_$name"
    done
    if [[ -n "$old_lags" ]]; then export ANALYSIS_LAGS="$old_lags"; else unset ANALYSIS_LAGS; fi
    if [[ "$BINARY_GEOMETRY_REPORT" == 1 ]]; then
        "$VENV_PYTHON" "$SCRIPT_DIR/analyze_binary_geometry.py" "$RUN_DIR" --diagram-only
    fi
    "$VENV_PYTHON" "$SCRIPT_DIR/analyze_dual_weave.py" "$RUN_DIR"
fi
if [[ "$BINARY_GEOMETRY_REPORT" == 1 ]]; then
    log "Analiza geometrii 2D plików BIN"
    "$VENV_PYTHON" "$SCRIPT_DIR/analyze_binary_geometry.py" "$RUN_DIR" \
        --max-files "$BINARY_GEOMETRY_MAX_FILES" \
        --max-bytes "$BINARY_GEOMETRY_MAX_BYTES"
fi
write_sha256s "$RUN_DIR"

"$VENV_PYTHON" - "$RUN_DIR" <<'PY'
from pathlib import Path
import json, sys
run=Path(sys.argv[1])
failure = json.loads((run/"run_failed.json").read_text()) if (run/"run_failed.json").exists() else {}
result={"run_directory":str(run.resolve()),"status":"failed" if failure else "complete","analyses":{}}
if failure:
 result["failure"]={k:failure.get(k) for k in ("reason","state","timestamp_utc","app_version")}
if (run/"dual_weave_report.json").exists():
 result["dual_weave_report"]="dual_weave_report.json"
if (run/"binary_geometry_summary.json").exists():
 result["binary_geometry_report"]="binary_geometry_report.html"
if (run/"lsb_bitplane_summary.json").exists():
 bitplanes=json.loads((run/"lsb_bitplane_summary.json").read_text())
 result["lsb_bitplane_report"]="lsb_bitplane_report.html"
 result["lsb_bitplane_summary"]={k:bitplanes.get(k) for k in ("lsb_bits","symbol_min_entropy_bits_per_symbol","symbol_min_entropy_bits_per_input_bit","max_abs_cross_plane_phi","max_cross_plane_mutual_information_bits")}
for name in ("conditioned","vn","selected_raw","temporal_raw","direct_lsb"):
 p=run/f"analysis_{name}"/"summary.json"
 if p.exists():
  d=json.loads(p.read_text())
  result["analyses"][name]={k:d.get(k) for k in ("total_bytes","p1","marginal_min_entropy_bits_per_bit","byte_min_entropy_bits_per_byte","byte_chi_square","byte_chi_square_p_value","monte_carlo_pi","lag1")}
(run/"runner_summary.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
print(json.dumps(result,indent=2,ensure_ascii=False))
PY

if (( run_failed == 0 )); then
    "$VENV_PYTHON" - "$RUN_DIR" <<'PY'
from pathlib import Path
import hashlib, json, os, sys
run = Path(sys.argv[1])
def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024): h.update(chunk)
    return h.hexdigest()
files = [{"name": p.name, "bytes": p.stat().st_size, "sha256": digest(p)} for p in sorted(run.glob("*.bin")) if p.is_file() and p.stat().st_size]
ready = {"status":"ready","condition":"output_complete.json exists, run_failed.json absent, analyses completed","files":files}
tmp=run/"READY.json.tmp"; final=run/"READY.json"
tmp.write_text(json.dumps(ready,indent=2,ensure_ascii=False),encoding="utf-8"); os.replace(tmp,final)
PY
else
    rm -f "$RUN_DIR/READY.json" "$RUN_DIR/READY.json.tmp"
fi
"$VENV_PYTHON" "$SCRIPT_DIR/generate_run_report.py" "$RUN_DIR"
if (( run_failed != 0 )); then
    fail "Przebieg zatrzymany fail-closed: ${failure_reason:-nieznany powód} (state=${failure_state:-UNKNOWN}, rc=$rc); raport częściowy zapisany"
fi
log "Zakończono: $RUN_DIR (READY.json i run_report.html zapisane)"
