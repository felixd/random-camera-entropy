#!/usr/bin/env bash
# Build the Go camera agent as the invoking, non-root user into agent/bin/.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/bin}"
OUT_FILE="$OUT_DIR/camera-entropy-agent"

log(){ printf '[camera-agent-build] %s\n' "$*"; }
fail(){ printf '[camera-agent-build] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EUID:-$(id -u)}" -ne 0 ]] || fail "Do not run this build with sudo/root. Run ./agent/build.sh as the local user."
command -v go >/dev/null 2>&1 || fail "Go >= 1.22 is required in the local user's PATH"

go_version="$(go env GOVERSION 2>/dev/null || true)"
if [[ "$go_version" =~ ^go([0-9]+)\.([0-9]+) ]]; then
  go_major="${BASH_REMATCH[1]}"
  go_minor="${BASH_REMATCH[2]}"
  (( go_major > 1 || (go_major == 1 && go_minor >= 22) )) || fail "Go >= 1.22 is required; found $go_version"
else
  fail "Cannot determine Go version: ${go_version:-unknown}"
fi

project_version="$(<"$PROJECT_ROOT/VERSION")"
expected_version="${project_version/camera-entropy-distributed/camera-entropy-go-agent}"

mkdir -p "$OUT_DIR"
# Remove historical in-tree binaries owned by the local source-tree user.
rm -f "$SCRIPT_DIR/camera-entropy-agent" \
      "$SCRIPT_DIR/cmd/camera-entropy-agent/camera-entropy-agent" \
      "$OUT_FILE"

cd "$SCRIPT_DIR"
log "Testing with $go_version"
go test ./...
log "Building $OUT_FILE"
CGO_ENABLED="${CGO_ENABLED:-0}" go build -trimpath -ldflags='-s -w' \
  -o "$OUT_FILE" ./cmd/camera-entropy-agent
chmod 0755 "$OUT_FILE"

built_version="$("$OUT_FILE" --version)"
[[ "$built_version" == "$expected_version" ]] || \
  fail "Built agent version mismatch: expected=$expected_version built=$built_version"

log "Built $built_version"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$OUT_FILE"
fi
