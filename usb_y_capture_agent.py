#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""USB camera capture node for Camera Entropy Distributed.

The node owns the physical V4L2 device, extracts the exact Y bytes from the
uncompressed YUYV stream and serves them to one authenticated compute node over
mutual TLS 1.3. It performs no entropy extraction or conditioning.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from frame_transport import (
    VERSION, frame_header, make_server_context, message_available, peer_common_name,
    recv_message, send_message,
)

APP_VERSION = "2026.08.01.camera-entropy-usb-agent.7.6.1"
TARGET_VID = "041e"
TARGET_PID = "4097"
EXPECTED_FOURCC = "YUYV"


def build_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("camera-entropy-usb-agent")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.handlers[:] = [handler]
    return logger


def decode_fourcc(value: float) -> str:
    integer = int(value)
    return "".join(chr((integer >> (8 * i)) & 0xFF) for i in range(4))


def extract_yuyv(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    array = np.asarray(frame, dtype=np.uint8)
    if array.ndim == 3 and array.shape == (height, width, 2):
        yuyv = array
    elif array.ndim == 2 and array.shape == (height, width * 2):
        yuyv = array.reshape(height, width, 2)
    else:
        flat = array.reshape(-1)
        expected = width * height * 2
        if flat.size != expected:
            raise RuntimeError(
                f"unexpected raw frame shape={array.shape}, bytes={flat.size}, expected={expected}"
            )
        yuyv = flat.reshape(height, width, 2)
    return np.ascontiguousarray(yuyv[:, :, 0])


def find_camera(vid: str, pid: str) -> str:
    for path in sorted(Path("/dev").glob("video*")):
        try:
            result = subprocess.run(
                ["udevadm", "info", "--query=property", f"--name={path}"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except Exception:
            continue
        properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if (
            properties.get("ID_VENDOR_ID", "").lower() == vid.lower()
            and properties.get("ID_MODEL_ID", "").lower() == pid.lower()
        ):
            return str(path)
    raise RuntimeError(f"camera USB {vid}:{pid} not found")


def v4l2_set(device: str, control: str, value: int, logger: logging.Logger) -> None:
    result = subprocess.run(
        ["v4l2-ctl", "-d", device, "--set-ctrl", f"{control}={value}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot set {control}={value}: {result.stderr.strip()}")
    logger.info("set %s=%s", control, value)


def v4l2_get(device: str, control: str) -> Optional[int]:
    try:
        result = subprocess.run(
            ["v4l2-ctl", "-d", device, "--get-ctrl", control],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r":\s*(-?\d+)\b", result.stdout.strip())
    return int(match.group(1)) if match else None


def controls(device: str) -> dict[str, Optional[int]]:
    return {
        "auto_exposure": v4l2_get(device, "auto_exposure"),
        "exposure_time_absolute": v4l2_get(device, "exposure_time_absolute"),
    }


def open_camera(args: argparse.Namespace, device: str, logger: logging.Logger) -> tuple[cv2.VideoCapture, dict[str, Any]]:
    if args.manual_exposure:
        v4l2_set(device, "auto_exposure", 1, logger)
        v4l2_set(device, "exposure_time_absolute", args.exposure_value, logger)

    capture = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {device}")
    capture.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*EXPECTED_FOURCC))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    capture.set(cv2.CAP_PROP_FPS, args.camera_fps)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    ok, frame = capture.read()
    if not ok or frame is None:
        capture.release()
        raise RuntimeError("camera opened but returned no frame")

    actual = {
        "fourcc": decode_fourcc(capture.get(cv2.CAP_PROP_FOURCC)),
        "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "reported_fps": float(capture.get(cv2.CAP_PROP_FPS)),
    }
    extract_yuyv(frame, actual["width"], actual["height"])
    if args.strict_mode and (
        actual["fourcc"] != EXPECTED_FOURCC
        or actual["width"] != args.width
        or actual["height"] != args.height
        or abs(actual["reported_fps"] - args.camera_fps) > 0.2
    ):
        capture.release()
        raise RuntimeError(f"camera rejected strict mode: {actual}")

    # Streaming can reset controls. Reapply and verify after the first read.
    if args.manual_exposure:
        v4l2_set(device, "auto_exposure", 1, logger)
        v4l2_set(device, "exposure_time_absolute", args.exposure_value, logger)
    actual["controls"] = controls(device)
    logger.info("camera mode: %s", json.dumps(actual, ensure_ascii=False))
    return capture, actual


def client_allowed(sock: ssl.SSLSocket, expected_cn: Optional[str]) -> bool:
    if not expected_cn:
        return True
    return peer_common_name(sock) == expected_cn


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve raw USB-camera Y frames over mutual TLS 1.3")
    parser.add_argument("--device")
    parser.add_argument("--vid", default=TARGET_VID)
    parser.add_argument("--pid", default=TARGET_PID)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--camera-fps", type=float, default=10.0)
    parser.add_argument("--strict-mode", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--manual-exposure", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--exposure-value", type=int, default=7000)
    parser.add_argument("--control-check-seconds", type=float, default=60.0)
    parser.add_argument("--control-fail-consecutive", type=int, default=1)
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=9443)
    parser.add_argument("--tls-ca", type=Path, required=True)
    parser.add_argument("--tls-cert", type=Path, required=True)
    parser.add_argument("--tls-key", type=Path, required=True)
    parser.add_argument("--allowed-client-cn")
    parser.add_argument("--source-id", default=socket.gethostname())
    parser.add_argument("--socket-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.width <= 0 or args.height <= 0 or args.camera_fps <= 0:
        parser.error("width, height and camera-fps must be positive")
    if args.exposure_value <= 0:
        parser.error("exposure-value must be positive")
    if args.control_check_seconds < 0:
        parser.error("control-check-seconds cannot be negative")
    if args.control_fail_consecutive < 1:
        parser.error("control-fail-consecutive must be >= 1")
    for path in (args.tls_ca, args.tls_cert, args.tls_key):
        if not path.is_file():
            parser.error(f"TLS file not found: {path}")
    return args


def _client_control(client: ssl.SSLSocket) -> Optional[dict[str, Any]]:
    """Read one optional application-control message from the compute node."""
    if not message_available(client, 0.0):
        return None
    message = recv_message(client)
    if message.payload:
        raise RuntimeError("client control message must not contain a payload")
    return message.header


def serve(args: argparse.Namespace, logger: logging.Logger) -> int:
    device = args.device or find_camera(args.vid, args.pid)
    context = make_server_context(args.tls_cert, args.tls_key, args.tls_ca)
    stop = False

    def on_signal(signum: int, _frame: Any) -> None:
        nonlocal stop
        logger.info("signal %s", signum)
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    listener = socket.socket(socket.AF_INET6 if ":" in args.listen_host else socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.listen_host, args.listen_port))
    listener.listen(4)
    listener.settimeout(1.0)
    logger.info("mTLS Y8 endpoint: %s:%d, device=%s", args.listen_host, args.listen_port, device)

    session_id = 0
    try:
        while not stop:
            try:
                raw_client, address = listener.accept()
            except socket.timeout:
                continue
            session_id += 1
            logger.info("TCP client from %s session=%d", address, session_id)
            capture: Optional[cv2.VideoCapture] = None
            clean_close = False
            try:
                with context.wrap_socket(raw_client, server_side=True) as client:
                    client.settimeout(args.socket_timeout_seconds)
                    cn = peer_common_name(client)
                    if not client_allowed(client, args.allowed_client_cn):
                        raise PermissionError(f"client CN {cn!r} is not allowed")
                    logger.info("authenticated client CN=%r TLS=%s session=%d", cn, client.version(), session_id)

                    # The V4L2 device is deliberately opened per authenticated
                    # session and released immediately afterwards.  Leaving an
                    # OpenCV/V4L2 stream open while no client drains it can leave
                    # stale kernel buffers and has caused long capture.read()
                    # stalls in consecutive qualification rounds.
                    capture, mode = open_camera(args, device, logger)
                    hello = {
                        "type": "hello",
                        "version": VERSION,
                        "app_version": APP_VERSION,
                        "source_id": args.source_id,
                        "session_id": session_id,
                        "device": device,
                        "mode": mode,
                        "payload": "uncompressed direct Y bytes extracted from YUYV",
                    }
                    send_message(client, hello)

                    frame_id = 0
                    bad_controls = 0
                    latest_controls = mode["controls"]
                    last_control_check = 0.0
                    while not stop:
                        control = _client_control(client)
                        if control is not None:
                            message_type = control.get("type")
                            if message_type == "close":
                                send_message(client, {
                                    "type": "close-ack",
                                    "version": VERSION,
                                    "session_id": session_id,
                                    "reason": control.get("reason", "client shutdown"),
                                })
                                clean_close = True
                                logger.info("client requested clean close session=%d reason=%r", session_id, control.get("reason"))
                                break
                            raise RuntimeError(f"unsupported client control message: {message_type!r}")

                        ok, frame = capture.read()
                        captured_mono_ns = time.monotonic_ns()
                        captured_unix_ns = time.time_ns()
                        if not ok or frame is None:
                            raise RuntimeError("camera read failed")
                        frame_id += 1
                        y = extract_yuyv(frame, mode["width"], mode["height"])

                        now = time.monotonic()
                        if args.control_check_seconds > 0 and now - last_control_check >= args.control_check_seconds:
                            last_control_check = now
                            latest_controls = controls(device)
                            problems: list[str] = []
                            if args.manual_exposure:
                                if latest_controls.get("auto_exposure") != 1:
                                    problems.append(f"auto_exposure={latest_controls.get('auto_exposure')}")
                                if latest_controls.get("exposure_time_absolute") != args.exposure_value:
                                    problems.append(
                                        f"exposure_time_absolute={latest_controls.get('exposure_time_absolute')}"
                                    )
                            if problems:
                                bad_controls += 1
                                if bad_controls >= args.control_fail_consecutive:
                                    send_message(client, {
                                        "type": "error",
                                        "version": VERSION,
                                        "fatal": True,
                                        "reason": "camera controls changed: " + "; ".join(problems),
                                    })
                                    raise RuntimeError("camera control verification failed")
                            else:
                                bad_controls = 0

                        payload = y.tobytes(order="C")
                        header = frame_header(
                            source_id=args.source_id,
                            frame_id=frame_id,
                            captured_unix_ns=captured_unix_ns,
                            captured_monotonic_ns=captured_mono_ns,
                            width=mode["width"],
                            height=mode["height"],
                            payload=payload,
                            controls=latest_controls,
                        )
                        header["session_id"] = session_id
                        send_message(client, header, payload)
            except (BrokenPipeError, ConnectionResetError, TimeoutError, ssl.SSLError, EOFError) as exc:
                level = logger.info if clean_close else logger.warning
                level("client transport ended session=%d clean=%s: %s", session_id, clean_close, exc)
            except Exception:
                logger.exception("client session failed session=%d", session_id)
            finally:
                if capture is not None:
                    capture.release()
                    capture = None
                    logger.info("camera released session=%d", session_id)
                try:
                    raw_client.close()
                except Exception:
                    pass
                if clean_close:
                    logger.info("client session closed cleanly session=%d", session_id)
    finally:
        listener.close()
    return 0


def main() -> int:
    args = parse_args()
    logger = build_logger(args.verbose)
    logger.info("starting %s", APP_VERSION)
    return serve(args, logger)


if __name__ == "__main__":
    raise SystemExit(main())
