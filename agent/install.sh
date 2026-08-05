#!/usr/bin/env bash
# Build, probe and install the Camera Entropy Go agent as a systemd service.
set -Eeuo pipefail

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "Run as root: sudo $0" >&2; exit 1; }
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
AGENT_USER="${AGENT_USER:-cameraentropy}"
AGENT_GROUP="${AGENT_GROUP:-cameraentropy}"
CONFIG_DIR="${CONFIG_DIR:-/etc/camera-entropy}"
PKI_SOURCE="${PKI_SOURCE:-$PROJECT_ROOT/pki}"
INSTALL_UDEV_RULE="${INSTALL_UDEV_RULE:-1}"
ALLOW_NO_CAMERA="${ALLOW_NO_CAMERA:-0}"

log(){ printf '[camera-agent-install] %s\n' "$*"; }
fail(){ printf '[camera-agent-install] ERROR: %s\n' "$*" >&2; exit 1; }
need(){ command -v "$1" >/dev/null 2>&1 || fail "Missing command: $1"; }

install_package() {
  local package="$1"
  if command -v apt-get >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$package"
  elif command -v dnf >/dev/null 2>&1; then dnf install -y "$package"
  elif command -v yum >/dev/null 2>&1; then yum install -y "$package"
  else fail "Install package manually: $package"; fi
}

if ! command -v v4l2-ctl >/dev/null 2>&1; then
  log "Installing v4l-utils"; command -v apt-get >/dev/null 2>&1 && apt-get update -y || true; install_package v4l-utils
fi
if ! command -v go >/dev/null 2>&1; then fail "Go >= 1.22 is required to build the agent"; fi
go_version="$(go env GOVERSION 2>/dev/null || true)"
if [[ "$go_version" =~ ^go([0-9]+)\.([0-9]+) ]]; then
  go_major="${BASH_REMATCH[1]}"; go_minor="${BASH_REMATCH[2]}"
  (( go_major > 1 || (go_major == 1 && go_minor >= 22) )) || fail "Go >= 1.22 is required; found $go_version"
else
  fail "Cannot determine Go version: $go_version"
fi
need systemctl; need install; need getent; need runuser

if ! getent group video >/dev/null; then groupadd --system video; fi
if ! getent group "$AGENT_GROUP" >/dev/null; then groupadd --system "$AGENT_GROUP"; fi
if ! id "$AGENT_USER" >/dev/null 2>&1; then
  useradd --system --gid "$AGENT_GROUP" --groups video --home-dir /nonexistent --shell /usr/sbin/nologin "$AGENT_USER"
else
  usermod -a -G video "$AGENT_USER"
fi

if [[ "$INSTALL_UDEV_RULE" == 1 ]]; then
  cat > /etc/udev/rules.d/70-camera-entropy-video.rules <<'EOF'
SUBSYSTEM=="video4linux", KERNEL=="video[0-9]*", GROUP="video", MODE="0660"
EOF
  udevadm control --reload-rules || true
  udevadm trigger --subsystem-match=video4linux || true
fi

log "Building Go agent"
(cd "$SCRIPT_DIR" && go test ./... && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o camera-entropy-agent ./cmd/camera-entropy-agent)
install -o root -g root -m 0755 "$SCRIPT_DIR/camera-entropy-agent" /usr/local/bin/camera-entropy-agent
rm -f "$SCRIPT_DIR/camera-entropy-agent"

install -d -o root -g "$AGENT_GROUP" -m 0750 "$CONFIG_DIR" "$CONFIG_DIR/pki"
if [[ ! -f "$CONFIG_DIR/camera-agent.env" ]]; then
  install -o root -g "$AGENT_GROUP" -m 0640 "$SCRIPT_DIR/config/camera-entropy-agent.env.example" "$CONFIG_DIR/camera-agent.env"
fi
for file in ca.crt agent.crt agent.key; do
  [[ -r "$PKI_SOURCE/$file" ]] || fail "Missing PKI file: $PKI_SOURCE/$file"
  mode=0640; [[ "$file" == *.key ]] && mode=0640
  install -o root -g "$AGENT_GROUP" -m "$mode" "$PKI_SOURCE/$file" "$CONFIG_DIR/pki/$file"
done
install -o root -g root -m 0644 "$SCRIPT_DIR/camera-entropy-agent.service" /etc/systemd/system/camera-entropy-agent.service
systemctl daemon-reload

log "Testing /dev/video0..2 as service user $AGENT_USER"
set +e
runuser -u "$AGENT_USER" -- /usr/local/bin/camera-entropy-agent --config "$CONFIG_DIR/camera-agent.env" --probe
probe_rc=$?
set -e
if (( probe_rc != 0 )); then
  groups "$AGENT_USER" || true
  ls -l /dev/video0 /dev/video1 /dev/video2 2>/dev/null || true
  [[ "$ALLOW_NO_CAMERA" == 1 ]] || fail "No camera passed the probe. Check group video, device permissions, YUYV support and whether another process holds the camera."
  log "Probe failed, but ALLOW_NO_CAMERA=1 permits installation"
fi
systemctl enable --now camera-entropy-agent.service
systemctl --no-pager --full status camera-entropy-agent.service || true
log "Installed. Logs: journalctl -u camera-entropy-agent -f"
