#!/usr/bin/env bash
# Install a prebuilt Camera Entropy Go agent as a systemd service.
# Compilation must be performed first by the local user with ./agent/build.sh.
set -Eeuo pipefail

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "Run as root: sudo $0" >&2; exit 1; }
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
AGENT_BINARY="${AGENT_BINARY:-$SCRIPT_DIR/bin/camera-entropy-agent}"
AGENT_USER="${AGENT_USER:-cameraentropy}"
AGENT_GROUP="${AGENT_GROUP:-cameraentropy}"
CONFIG_DIR="${CONFIG_DIR:-/etc/camera-entropy}"
PKI_SOURCE="${PKI_SOURCE:-$PROJECT_ROOT/pki}"
INSTALL_UDEV_RULE="${INSTALL_UDEV_RULE:-1}"
ALLOW_NO_CAMERA="${ALLOW_NO_CAMERA:-0}"
INSTALL_PATH="/usr/local/bin/camera-entropy-agent"

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

need systemctl
need install
need getent
need runuser
need mv

[[ -f "$AGENT_BINARY" ]] || fail "Prebuilt agent not found: $AGENT_BINARY. Run ./agent/build.sh as the local user first."
[[ -x "$AGENT_BINARY" ]] || fail "Prebuilt agent is not executable: $AGENT_BINARY. Run ./agent/build.sh again."

project_version="$(<"$PROJECT_ROOT/VERSION")"
expected_version="${project_version/camera-entropy-distributed/camera-entropy-go-agent}"
if ! built_version="$("$AGENT_BINARY" --version 2>/dev/null)"; then
  fail "Cannot execute prebuilt agent: $AGENT_BINARY"
fi
[[ "$built_version" == "$expected_version" ]] || \
  fail "Prebuilt agent version mismatch: expected=$expected_version found=$built_version. Run ./agent/build.sh again as the local user."
log "Using prebuilt $built_version from $AGENT_BINARY"

if ! command -v v4l2-ctl >/dev/null 2>&1; then
  log "Installing v4l-utils"
  command -v apt-get >/dev/null 2>&1 && apt-get update -y || true
  install_package v4l-utils
fi

if ! getent group video >/dev/null; then groupadd --system video; fi
if ! getent group "$AGENT_GROUP" >/dev/null; then groupadd --system "$AGENT_GROUP"; fi
if ! id "$AGENT_USER" >/dev/null 2>&1; then
  useradd --system --gid "$AGENT_GROUP" --groups video --home-dir /nonexistent --shell /usr/sbin/nologin "$AGENT_USER"
else
  usermod -a -G video "$AGENT_USER"
fi

if [[ "$INSTALL_UDEV_RULE" == 1 ]]; then
  cat > /etc/udev/rules.d/70-camera-entropy-video.rules <<'RULE'
SUBSYSTEM=="video4linux", KERNEL=="video[0-9]*", GROUP="video", MODE="0660"
RULE
  udevadm control --reload-rules || true
  udevadm trigger --subsystem-match=video4linux || true
fi

install_dir="$(dirname -- "$INSTALL_PATH")"
install_name="$(basename -- "$INSTALL_PATH")"
temporary_path="$install_dir/.${install_name}.new.$$"
install -d -o root -g root -m 0755 "$install_dir"
trap 'rm -f -- "$temporary_path"' EXIT
install -o root -g root -m 0755 "$AGENT_BINARY" "$temporary_path"
installed_candidate_version="$("$temporary_path" --version)"
[[ "$installed_candidate_version" == "$built_version" ]] || \
  fail "Copied agent version mismatch: source=$built_version copy=$installed_candidate_version"
mv -f -- "$temporary_path" "$INSTALL_PATH"
trap - EXIT
installed_version="$("$INSTALL_PATH" --version)"
[[ "$installed_version" == "$built_version" ]] || \
  fail "Installed agent version mismatch: source=$built_version installed=$installed_version"
log "Installed $installed_version to $INSTALL_PATH"

install -d -o root -g "$AGENT_GROUP" -m 0750 "$CONFIG_DIR" "$CONFIG_DIR/pki"
if [[ ! -f "$CONFIG_DIR/camera-agent.env" ]]; then
  install -o root -g "$AGENT_GROUP" -m 0640 "$SCRIPT_DIR/config/camera-entropy-agent.env.example" "$CONFIG_DIR/camera-agent.env"
fi
for file in ca.crt agent.crt agent.key; do
  [[ -r "$PKI_SOURCE/$file" ]] || fail "Missing PKI file: $PKI_SOURCE/$file"
  install -o root -g "$AGENT_GROUP" -m 0640 "$PKI_SOURCE/$file" "$CONFIG_DIR/pki/$file"
done
install -o root -g root -m 0644 "$SCRIPT_DIR/camera-entropy-agent.service" /etc/systemd/system/camera-entropy-agent.service
systemctl daemon-reload

log "Testing /dev/video0..2 as service user $AGENT_USER"
set +e
runuser -u "$AGENT_USER" -- "$INSTALL_PATH" --config "$CONFIG_DIR/camera-agent.env" --probe
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
