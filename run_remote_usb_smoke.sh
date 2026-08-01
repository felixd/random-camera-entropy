#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export SOURCE_TYPE=tls-y
export RUN_NAME="${RUN_NAME:-remote-usb-smoke-$(date -u +%Y%m%dT%H%M%SZ)}"
export WARMUP_SECONDS="${WARMUP_SECONDS:-60}"
export CALIBRATION_PAIRS="${CALIBRATION_PAIRS:-128}"
export DIAGNOSTIC_VN_BYTES="${DIAGNOSTIC_VN_BYTES:-5242880}"
export CONDITIONED_BYTES="${CONDITIONED_BYTES:-5242880}"
export VALIDATION_BYTES="${VALIDATION_BYTES:-2097152}"
exec "$SCRIPT_DIR/run_one.sh"
