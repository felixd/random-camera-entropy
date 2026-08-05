#!/usr/bin/env bash
# Compatibility wrapper retained for existing automation.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
exec "$PROJECT_ROOT/scripts/run/run_production.sh" "$@"
