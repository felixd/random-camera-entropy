#!/usr/bin/env bash
# -*- coding: utf-8 -*-
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
source "$PROJECT_ROOT/scripts/lib.sh"
ensure_environment
OUT_DIR="${OUT_DIR:-/etc/camera-entropy}"
USERNAME="${USERNAME:-admin}"
CREDENTIALS_FILE="${CREDENTIALS_FILE:-$OUT_DIR/web-user.json}"
SECRET_FILE="${SECRET_FILE:-$OUT_DIR/web-secret.key}"
mkdir -p "$OUT_DIR"
umask 077
if [[ -n "${WEB_PASSWORD:-}" ]]; then
    password="$WEB_PASSWORD"
else
    read -r -s -p "Hasło dla użytkownika $USERNAME: " password; printf '\n'
    read -r -s -p "Powtórz hasło: " password2; printf '\n'
    [[ "$password" == "$password2" ]] || fail "Hasła nie są identyczne"
fi
(( ${#password} >= 12 )) || fail "Hasło musi mieć co najmniej 12 znaków"
"$VENV_PYTHON" - "$USERNAME" "$password" "$CREDENTIALS_FILE" "$SECRET_FILE" <<'PY'
from pathlib import Path
from werkzeug.security import generate_password_hash
import json, secrets, sys
username,password,cred,secret=sys.argv[1:]
Path(cred).write_text(json.dumps({"username":username,"password_hash":generate_password_hash(password, method="scrypt")},indent=2),encoding="utf-8")
Path(secret).write_text(secrets.token_urlsafe(64)+"\n",encoding="ascii")
PY
chmod 600 "$CREDENTIALS_FILE" "$SECRET_FILE"
printf 'Zapisano:\n  %s\n  %s\n' "$CREDENTIALS_FILE" "$SECRET_FILE"
