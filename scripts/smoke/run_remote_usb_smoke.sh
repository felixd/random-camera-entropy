#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
export SOURCE_TYPE=tls-y
export RUN_NAME="${RUN_NAME:-remote-usb-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
export WARMUP_SECONDS="${WARMUP_SECONDS:-60}"
export CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-128}"
export DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-5242880}"
export CONDITIONED_BYTES="${CONDITIONED_BYTES:-5242880}"
export VALIDATION_BYTES="${VALIDATION_BYTES:-2097152}"
exec "$PROJECT_ROOT/scripts/run/run_one.sh"
