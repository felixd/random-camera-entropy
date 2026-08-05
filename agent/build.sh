#!/usr/bin/env bash
# Build the Go camera agent into agent/bin/ and print the exact embedded version.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/bin}"
mkdir -p "$OUT_DIR"
rm -f "$SCRIPT_DIR/cmd/camera-entropy-agent/camera-entropy-agent"
cd "$SCRIPT_DIR"
go test ./...
CGO_ENABLED="${CGO_ENABLED:-0}" go build -trimpath -ldflags='-s -w' \
  -o "$OUT_DIR/camera-entropy-agent" ./cmd/camera-entropy-agent
"$OUT_DIR/camera-entropy-agent" --version
