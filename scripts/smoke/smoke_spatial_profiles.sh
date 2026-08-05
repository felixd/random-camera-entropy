#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
source "$PROJECT_ROOT/scripts/lib.sh"
ensure_environment
DATA_ROOT="${DATA_ROOT:-$PROJECT_ROOT/data}"
CAMPAIGN="${CAMPAIGN:-spatial-campaign-$(date -u +%Y%m%dT%H%M%SZ)}"
CAMPAIGN_DIR="$DATA_ROOT/$CAMPAIGN"
[[ ! -e "$CAMPAIGN_DIR" ]] || { printf 'Katalog kampanii już istnieje: %s\n' "$CAMPAIGN_DIR" >&2; exit 2; }
mkdir -p "$CAMPAIGN_DIR"

profiles=(
  spatial-baseline
  spatial-checker-even
  spatial-checker-odd
  spatial-grid2
  spatial-grid4
  spatial-block4
  spatial-offset11
  spatial-offset22
  spatial-serpentine
  spatial-tile16
)
failed=0
for profile in "${profiles[@]}"; do
    printf '[spatial-campaign] start %s\n' "$profile"
    if DATA_ROOT="$CAMPAIGN_DIR" RUN_NAME="$profile" "$PROJECT_ROOT/scripts/smoke/spatial_profile.sh" "$profile"; then
        printf '[spatial-campaign] complete %s\n' "$profile"
    else
        rc=$?
        failed=$((failed + 1))
        printf '[spatial-campaign] failed %s rc=%s\n' "$profile" "$rc" >&2
    fi
done

if ! run_module app.reporting.summarize_spatial_campaign "$CAMPAIGN_DIR"; then
    failed=$((failed + 1))
fi
(( failed == 0 ))
