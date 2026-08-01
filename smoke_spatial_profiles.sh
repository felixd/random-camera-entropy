#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_ROOT="${DATA_ROOT:-$SCRIPT_DIR/data}"
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
    if DATA_ROOT="$CAMPAIGN_DIR" RUN_NAME="$profile" "$SCRIPT_DIR/spatial_profile.sh" "$profile"; then
        printf '[spatial-campaign] complete %s\n' "$profile"
    else
        rc=$?
        failed=$((failed + 1))
        printf '[spatial-campaign] failed %s rc=%s\n' "$profile" "$rc" >&2
    fi
done

if ! "${VENV_PYTHON:-$SCRIPT_DIR/.venv/bin/python}" "$SCRIPT_DIR/summarize_spatial_campaign.py" "$CAMPAIGN_DIR"; then
    failed=$((failed + 1))
fi
(( failed == 0 ))
