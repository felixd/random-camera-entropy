#!/usr/bin/env bash
set -Eeuo pipefail
OUT_FILE="${OUT_FILE:-/etc/camera-entropy/report-share.token}"
mkdir -p "$(dirname "$OUT_FILE")"
umask 077
python3 - "$OUT_FILE" <<'PY'
from pathlib import Path
import secrets,sys
Path(sys.argv[1]).write_text(secrets.token_urlsafe(48)+"\n",encoding="ascii")
PY
chmod 600 "$OUT_FILE"
printf 'Token zapisany w %s\n' "$OUT_FILE"
