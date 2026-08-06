#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
source "$PROJECT_ROOT/scripts/lib.sh"
ensure_environment
export DATA_ROOT="${DATA_ROOT:-$PROJECT_ROOT/data}"
export WEB_HOST="${WEB_HOST:-127.0.0.1}"
export WEB_PORT="${WEB_PORT:-8087}"
export WORKER_HOST="${WORKER_HOST:-127.0.0.1}"
export WORKER_PORT="${WORKER_PORT:-18087}"
export MAX_DATASET_JOBS="${MAX_DATASET_JOBS:-$(python3 - <<'PYCPU'
import os
print(min(16, os.cpu_count() or 4))
PYCPU
)}"
export WEB_SOURCES_FILE="${WEB_SOURCES_FILE:-$PROJECT_ROOT/sources.json}"
export WEB_CREDENTIALS_FILE="${WEB_CREDENTIALS_FILE:-/etc/camera-entropy/web-user.json}"
export WEB_SECRET_FILE="${WEB_SECRET_FILE:-/etc/camera-entropy/web-secret.key}"
export WEB_AUTH_MODE="${WEB_AUTH_MODE:-app}"
export WEB_SECURE_COOKIE="${WEB_SECURE_COOKIE:-1}"
export WEB_TRUST_PROXY="${WEB_TRUST_PROXY:-1}"
if [[ -f "${WEB_SHARE_TOKEN_FILE:-/etc/camera-entropy/report-share.token}" ]]; then export WEB_SHARE_TOKEN_FILE="${WEB_SHARE_TOKEN_FILE:-/etc/camera-entropy/report-share.token}"; fi
cd "$PROJECT_ROOT"
exec "$VENV_PYTHON" -m app.control.control_server
