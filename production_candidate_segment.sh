#!/usr/bin/env bash
# Compatibility wrapper retained for existing automation.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/run_production.sh" "$@"
