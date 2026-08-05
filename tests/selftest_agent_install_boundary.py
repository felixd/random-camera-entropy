#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
build = (root / "agent" / "build.sh").read_text(encoding="utf-8")
install = (root / "agent" / "install.sh").read_text(encoding="utf-8")

assert "Do not run this build with sudo/root" in build
assert "go test ./..." in build
assert "go build" in build
assert "bin/camera-entropy-agent" in install
assert "Prebuilt agent not found" in install
assert "Prebuilt agent version mismatch" in install
for forbidden in ("go test", "go build", "go env GOVERSION", "command -v go"):
    assert forbidden not in install, f"install.sh must not contain {forbidden!r}"
assert "mv -f --" in install
print("agent build/install privilege-boundary self-test: PASS")
