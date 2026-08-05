#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

required = [
    "app/core/camera_entropy_server.py",
    "app/core/masking.py",
    "app/sources/frame_sources.py",
    "app/control/control_server.py",
    "app/reporting/generate_run_report.py",
    "app/qualification/qualification_final_preproduction.py",
    "app/web/templates/control.html",
    "scripts/run/run_one.sh",
    "scripts/run/start_control_server.sh",
    "scripts/qualification/final_preproduction.sh",
    "agent/go.mod",
    "agent/cmd/camera-entropy-agent/main.go",
    "agent/install.sh",
    "agent/camera-entropy-agent.service",
]
for relative in required:
    assert (ROOT / relative).is_file(), relative

for legacy in (
    "camera_entropy_server.py",
    "control_server.py",
    "frame_sources.py",
    "usb_y_capture_agent.py",
    "run_one.sh",
    "run_usb_agent.sh",
    "start_control_server.sh",
):
    assert not (ROOT / legacy).exists(), legacy

control = (ROOT / "app/web/templates/control.html").read_text(encoding="utf-8")
assert 'id="logModal"' in control
assert 'class="modal-window"' in control
assert '<h2>Log zadania</h2>' not in control
assert "data-log=\"${esc(j.id)}\"" in control

server = (ROOT / "app/control/control_server.py").read_text(encoding="utf-8")
assert "len(active_dataset) >= self.max_dataset_jobs" in server
assert 'raise RuntimeError("Źródło LIVE jest już używane przez inny test")' in server
assert "--max-dataset-jobs" in server

agent = (ROOT / "agent/cmd/camera-entropy-agent/main.go").read_text(encoding="utf-8")
for token in ("/dev/video0,/dev/video1,/dev/video2", "CEYTLS01", "continuous_capture", "source_warmup_seconds"):
    assert token in agent, token

print("project layout self-test: PASS")
