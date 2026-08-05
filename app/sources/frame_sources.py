#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frame-source abstraction for local V4L2, remote mTLS Y8 and RTSP inputs."""
from __future__ import annotations

import json
import logging
import re
import socket
import ssl
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import cv2
import numpy as np

from app.sources.frame_transport import (
    FrameProtocolError, make_client_context, recv_message, send_message, verify_frame_message,
)
from app.sources.control_values import normalize_controls

EXPECTED_FOURCC = "YUYV"
TARGET_VID = "041e"
TARGET_PID = "4097"


class RemoteAgentError(RuntimeError):
    """Structured error returned by the authenticated camera agent."""

    def __init__(self, reason: str, *, code: str = "agent-error", retryable: bool = False) -> None:
        self.code = str(code or "agent-error")
        self.retryable = bool(retryable)
        super().__init__(str(reason or "unknown remote agent error"))


@dataclass
class SourceFrame:
    frame_id: int
    timestamp_monotonic: float
    y: np.ndarray
    yuyv: Optional[np.ndarray]
    metadata: dict[str, Any]


class FrameSource:
    source_type = "unknown"
    label = ""
    pixel_format = ""
    width = 0
    height = 0
    reported_fps = 0.0
    timestamp_basis = "receiver_monotonic"
    supports_controls = False

    def open(self) -> None:
        raise NotImplementedError

    def read(self) -> SourceFrame:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def control_snapshot(self) -> dict[str, Optional[int]]:
        return {}

    def manifest(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "label": self.label,
            "pixel_format": self.pixel_format,
            "width": self.width,
            "height": self.height,
            "reported_fps": self.reported_fps,
            "timestamp_basis": self.timestamp_basis,
            "supports_controls": self.supports_controls,
        }


def decode_fourcc(value: float) -> str:
    integer = int(value)
    return "".join(chr((integer >> (8 * i)) & 0xFF) for i in range(4))


def extract_yuyv(frame: np.ndarray, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
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
    return np.ascontiguousarray(yuyv[:, :, 0]), yuyv


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


def v4l2_controls(device: str) -> dict[str, Optional[int]]:
    return {
        "auto_exposure": v4l2_get(device, "auto_exposure"),
        "exposure_time_absolute": v4l2_get(device, "exposure_time_absolute"),
    }


class LocalV4L2Source(FrameSource):
    source_type = "v4l2"
    timestamp_basis = "local_capture_monotonic"
    supports_controls = True

    def __init__(self, args: Any, logger: logging.Logger) -> None:
        self.args = args
        self.logger = logger
        self.capture: Optional[cv2.VideoCapture] = None
        self.device = args.device or ""
        self.frame_id = 0
        self.initial_controls: dict[str, Optional[int]] = {}

    def _configure(self) -> None:
        if self.args.manual_exposure:
            v4l2_set(self.device, "auto_exposure", 1, self.logger)
            if self.args.exposure_value is not None:
                v4l2_set(self.device, "exposure_time_absolute", self.args.exposure_value, self.logger)

    def open(self) -> None:
        self.device = self.device or find_camera(self.args.vid, self.args.pid)
        self._configure()
        capture = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not capture.isOpened():
            raise RuntimeError(f"cannot open {self.device}")
        capture.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*EXPECTED_FOURCC))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.args.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.args.height)
        capture.set(cv2.CAP_PROP_FPS, self.args.camera_fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, frame = capture.read()
        if not ok or frame is None:
            capture.release()
            raise RuntimeError("camera opened but returned no frame")
        self.pixel_format = decode_fourcc(capture.get(cv2.CAP_PROP_FOURCC))
        self.width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.reported_fps = float(capture.get(cv2.CAP_PROP_FPS))
        extract_yuyv(frame, self.width, self.height)
        if self.args.strict_mode and (
            self.pixel_format != EXPECTED_FOURCC
            or self.width != self.args.width
            or self.height != self.args.height
            or abs(self.reported_fps - self.args.camera_fps) > 0.2
        ):
            capture.release()
            raise RuntimeError(
                f"camera rejected strict mode; actual={self.pixel_format} "
                f"{self.width}x{self.height}@{self.reported_fps}"
            )
        self.capture = capture
        self._configure()
        self.initial_controls = self.control_snapshot()
        self.label = self.device

    def read(self) -> SourceFrame:
        if self.capture is None:
            raise RuntimeError("source is not open")
        ok, frame = self.capture.read()
        now = time.monotonic()
        if not ok or frame is None:
            raise RuntimeError("camera read failed")
        self.frame_id += 1
        y, yuyv = extract_yuyv(frame, self.width, self.height)
        return SourceFrame(self.frame_id, now, y, yuyv, {})

    def control_snapshot(self) -> dict[str, Optional[int]]:
        return v4l2_controls(self.device) if self.device else {}

    def close(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def manifest(self) -> dict[str, Any]:
        return super().manifest() | {
            "device": self.device,
            "initial_controls": self.initial_controls,
            "payload_origin": "direct Y bytes from local uncompressed YUYV",
        }


class TlsYSource(FrameSource):
    source_type = "tls-y"
    pixel_format = "Y8"
    timestamp_basis = "remote_capture_monotonic"
    supports_controls = True

    def __init__(self, args: Any, logger: logging.Logger) -> None:
        self.args = args
        self.logger = logger
        self.sock: Optional[ssl.SSLSocket] = None
        self.hello: dict[str, Any] = {}
        self.latest_controls: dict[str, Optional[int]] = {}
        self.latest_controls_raw: dict[str, Any] = {}
        self.agent_app_version = ""
        self.control_value_schema = "legacy"
        self.last_frame_id: Optional[int] = None
        self.server_cn: Optional[str] = None
        self.connection_generation = 0
        self.reconnect_count = 0
        self.last_reconnect_reason = ""
        self.last_connected_utc: Optional[str] = None
        self.last_frame_received_monotonic: Optional[float] = None
        self._closing = False

    @staticmethod
    def _tune_keepalive(raw: socket.socket) -> None:
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, value in (("TCP_KEEPIDLE", 15), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 3)):
            option = getattr(socket, name, None)
            if option is not None:
                try:
                    raw.setsockopt(socket.IPPROTO_TCP, option, value)
                except OSError:
                    pass

    def _connect(self) -> None:
        context = make_client_context(self.args.tls_cert, self.args.tls_key, self.args.tls_ca)
        raw = socket.create_connection(
            (self.args.tls_host, self.args.tls_port), timeout=self.args.source_connect_timeout_seconds
        )
        self._tune_keepalive(raw)
        try:
            sock = context.wrap_socket(raw, server_hostname=self.args.tls_server_name)
        except Exception:
            raw.close()
            raise
        sock.settimeout(self.args.source_frame_timeout_seconds)
        try:
            try:
                message = recv_message(sock)
            except EOFError as exc:
                raise RuntimeError(
                    "TLS handshake completed, but the camera agent closed the connection before "
                    "sending its hello message. Check camera-entropy-agent logs; common causes are "
                    "an unauthorized client certificate CN, another active LIVE consumer, or a fatal "
                    "camera capture error."
                ) from exc
            message_type = message.header.get("type")
            if message_type == "error":
                raise RemoteAgentError(
                    str(message.header.get("reason") or "unknown remote agent error"),
                    code=str(message.header.get("code") or "agent-error"),
                    retryable=bool(message.header.get("retryable", False)),
                )
            if message.payload or message_type != "hello":
                raise FrameProtocolError(f"first TLS message is not hello: {message_type!r}")
            hello = message.header
            if int(hello.get("version", -1)) != 1:
                raise FrameProtocolError(f"unsupported agent protocol: {hello.get('version')}")
            mode = hello.get("mode") or {}
            width = int(mode.get("width", 0))
            height = int(mode.get("height", 0))
            reported_fps = float(mode.get("reported_fps", 0.0))
            if width <= 0 or height <= 0:
                raise FrameProtocolError(f"invalid source mode: {mode}")
            if self.args.strict_mode and (width != self.args.width or height != self.args.height):
                raise RuntimeError(
                    f"remote source dimensions {width}x{height} do not match "
                    f"requested {self.args.width}x{self.args.height}"
                )
        except Exception:
            try:
                sock.close()
            finally:
                pass
            raise

        self.sock = sock
        self.hello = hello
        self.width = width
        self.height = height
        self.reported_fps = reported_fps
        self.latest_controls_raw = dict(mode.get("controls") or {})
        self.latest_controls = normalize_controls(self.latest_controls_raw)
        self.agent_app_version = str(hello.get("app_version") or "unknown")
        self.control_value_schema = str(hello.get("control_value_schema") or mode.get("control_value_schema") or "legacy")
        self.label = f"tls-y://{self.args.tls_host}:{self.args.tls_port}/{hello.get('source_id','source')}"
        self.server_cn = self.args.tls_server_name
        self.last_frame_id = None
        self.last_frame_received_monotonic = None
        self.connection_generation += 1
        self.last_connected_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.logger.info(
            "connected to %s, mode=%sx%s Y8 generation=%d agent_session=%s agent=%s controls=%s",
            self.label, self.width, self.height, self.connection_generation, hello.get("session_id", "n/a"),
            self.agent_app_version, self.control_value_schema,
        )
        if self.control_value_schema == "legacy" and self.latest_controls_raw != self.latest_controls:
            self.logger.warning(
                "legacy camera control metadata normalized locally: raw=%r normalized=%r",
                self.latest_controls_raw, self.latest_controls,
            )

    def open(self) -> None:
        self._closing = False
        attempts = max(0, int(getattr(self.args, "source_reconnect_attempts", 0)))
        backoff = max(0.0, float(getattr(self.args, "source_reconnect_backoff_seconds", 1.0)))
        last_error: BaseException | None = None
        for attempt in range(0, attempts + 1):
            try:
                self._connect()
                return
            except RemoteAgentError as exc:
                last_error = exc
                self._abort_socket()
                if not exc.retryable:
                    raise RuntimeError(
                        f"camera agent rejected the connection [{exc.code}]: {exc}"
                    ) from exc
            except Exception as exc:
                last_error = exc
                self._abort_socket()

            if attempt >= attempts:
                break
            delay = backoff * (attempt + 1)
            self.logger.warning(
                "TLS-Y initial connection failed (%s: %s); retry %d/%d in %.1fs",
                type(last_error).__name__, last_error, attempt + 1, attempts, delay,
            )
            if delay:
                time.sleep(delay)

        assert last_error is not None
        raise RuntimeError(
            f"TLS-Y initial connection failed after {attempts + 1} attempt(s): "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    def _abort_socket(self) -> None:
        sock, self.sock = self.sock, None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def _reconnect(self, reason: BaseException) -> None:
        attempts = max(0, int(getattr(self.args, "source_reconnect_attempts", 0)))
        if attempts <= 0 or self._closing:
            raise reason
        self.last_reconnect_reason = f"{type(reason).__name__}: {reason}"
        self._abort_socket()
        backoff = max(0.0, float(getattr(self.args, "source_reconnect_backoff_seconds", 1.0)))
        last_error: BaseException = reason
        for attempt in range(1, attempts + 1):
            if backoff:
                time.sleep(backoff * attempt)
            try:
                self.logger.warning(
                    "TLS-Y source interrupted (%s); reconnect attempt %d/%d",
                    self.last_reconnect_reason, attempt, attempts,
                )
                self._connect()
                self.reconnect_count += 1
                self.logger.warning(
                    "TLS-Y source reconnected generation=%d reconnect_count=%d",
                    self.connection_generation, self.reconnect_count,
                )
                return
            except Exception as exc:
                last_error = exc
                self._abort_socket()
                self.logger.warning("TLS-Y reconnect attempt %d failed: %s", attempt, exc)
        raise RuntimeError(
            f"TLS-Y reconnect failed after {attempts} attempts; initial={self.last_reconnect_reason}; "
            f"last={type(last_error).__name__}: {last_error}"
        ) from last_error

    def read(self) -> SourceFrame:
        while True:
            if self.sock is None:
                raise RuntimeError("source is not open")
            try:
                message = recv_message(self.sock)
            except (TimeoutError, socket.timeout, EOFError, ConnectionResetError, BrokenPipeError, ssl.SSLError) as exc:
                self._reconnect(exc)
                continue
            message_type = message.header.get("type")
            if message_type == "error":
                error = RemoteAgentError(
                    str(message.header.get("reason") or "unknown remote agent error"),
                    code=str(message.header.get("code") or "agent-error"),
                    retryable=bool(message.header.get("retryable", False)),
                )
                if error.retryable:
                    self._reconnect(error)
                    continue
                raise error
            if message_type == "close-ack":
                if self._closing:
                    raise EOFError("remote source acknowledged close")
                self.logger.debug("ignoring unexpected close-ack")
                continue
            if message_type != "frame":
                self.logger.debug("ignoring transport message type=%r", message_type)
                continue
            header = verify_frame_message(message)
            frame_id = int(header["frame_id"])
            if self.last_frame_id is not None and frame_id != self.last_frame_id + 1:
                detail = f"remote frame gap: previous={self.last_frame_id}, current={frame_id}"
                if not self.args.allow_source_frame_gaps:
                    raise RuntimeError(detail)
                self.logger.warning(detail)
            self.last_frame_id = frame_id
            if int(header["width"]) != self.width or int(header["height"]) != self.height:
                raise FrameProtocolError("remote frame dimensions changed")
            self.latest_controls_raw = dict(header.get("controls") or {})
            self.latest_controls = normalize_controls(self.latest_controls_raw)
            y = np.frombuffer(message.payload, dtype=np.uint8).reshape(self.height, self.width).copy()
            timestamp = int(header["captured_monotonic_ns"]) / 1_000_000_000.0
            self.last_frame_received_monotonic = time.monotonic()
            metadata = dict(header)
            metadata["source_connection_generation"] = self.connection_generation
            metadata["source_reconnect_count"] = self.reconnect_count
            return SourceFrame(frame_id, timestamp, y, None, metadata)

    def control_snapshot(self) -> dict[str, Optional[int]]:
        # Always expose canonical numeric values to fail-closed control checks.
        # Older Go/Python agents may send menu controls as strings such as
        # ``auto_exposure: 1 (Manual Mode)``; normalization keeps the wire
        # protocol backward compatible without weakening the comparison.
        return dict(self.latest_controls)

    def close(self) -> None:
        sock = self.sock
        if sock is None:
            return
        self._closing = True
        self.sock = None
        clean = False
        old_timeout = sock.gettimeout()
        try:
            sock.settimeout(min(2.0, max(0.2, float(self.args.source_frame_timeout_seconds))))
            send_message(sock, {
                "type": "close",
                "version": 1,
                "reason": "compute worker shutdown",
                "connection_generation": self.connection_generation,
            })
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                message = recv_message(sock)
                if message.header.get("type") == "close-ack":
                    clean = True
                    break
                # A frame may already have been in flight when close was sent.
                # Discard it and wait briefly for the acknowledgement.
        except (OSError, TimeoutError, EOFError, ssl.SSLError, FrameProtocolError):
            pass
        finally:
            try:
                sock.settimeout(old_timeout)
            except OSError:
                pass
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
            self.logger.info("TLS-Y source closed clean=%s generation=%d", clean, self.connection_generation)

    def manifest(self) -> dict[str, Any]:
        return super().manifest() | {
            "endpoint": f"{self.args.tls_host}:{self.args.tls_port}",
            "server_name": self.args.tls_server_name,
            "hello": self.hello,
            "agent_app_version": self.agent_app_version,
            "control_value_schema": self.control_value_schema,
            "latest_controls": dict(self.latest_controls),
            "latest_controls_raw": dict(self.latest_controls_raw),
            "transport": "mutual TLS 1.3, framed uncompressed Y8, per-frame SHA-256, clean close handshake",
            "connection_generation": self.connection_generation,
            "reconnect_count": self.reconnect_count,
            "last_reconnect_reason": self.last_reconnect_reason,
        }


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<redacted>"
    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    user = "***@" if parts.username else ""
    return urlunsplit((parts.scheme, f"{user}{hostname}{port}", parts.path, parts.query, parts.fragment))


def parse_rate(value: str) -> float:
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        den = float(denominator)
        return float(numerator) / den if den else 0.0
    return float(value)


class RtspFfmpegSource(FrameSource):
    source_type = "rtsp"
    pixel_format = "Y8"
    timestamp_basis = "receiver_monotonic_after_decode"
    supports_controls = False

    def __init__(self, args: Any, logger: logging.Logger) -> None:
        self.args = args
        self.logger = logger
        self.process: Optional[subprocess.Popen[bytes]] = None
        self.frame_id = 0
        self.codec_name = ""
        self.input_pixel_format = ""
        self.stderr_lines: list[str] = []
        self.stderr_thread: Optional[threading.Thread] = None

    def _probe(self) -> dict[str, Any]:
        timeout_us = int(self.args.rtsp_timeout_seconds * 1_000_000)
        command = [
            self.args.ffprobe_bin,
            "-v", "error",
            "-rtsp_transport", self.args.rtsp_transport,
            "-rw_timeout", str(timeout_us),
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,codec_name,pix_fmt",
            "-of", "json",
            self.args.rtsp_url,
        ]
        result = subprocess.run(command, capture_output=True, timeout=self.args.rtsp_timeout_seconds + 5)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr.decode(errors='replace').strip()}")
        data = json.loads(result.stdout.decode("utf-8"))
        streams = data.get("streams") or []
        if not streams:
            raise RuntimeError("RTSP source has no video stream")
        return streams[0]

    def open(self) -> None:
        stream = self._probe()
        self.width = int(stream.get("width") or 0)
        self.height = int(stream.get("height") or 0)
        self.reported_fps = parse_rate(str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0"))
        self.codec_name = str(stream.get("codec_name") or "")
        self.input_pixel_format = str(stream.get("pix_fmt") or "")
        if self.width <= 0 or self.height <= 0:
            raise RuntimeError(f"invalid RTSP dimensions: {stream}")
        if self.args.strict_mode and (
            self.width != self.args.width or self.height != self.args.height
        ):
            raise RuntimeError(
                f"RTSP dimensions {self.width}x{self.height} do not match requested "
                f"{self.args.width}x{self.args.height}"
            )
        timeout_us = int(self.args.rtsp_timeout_seconds * 1_000_000)
        luma_filter = "extractplanes=y" if self.args.rtsp_luma_mode == "extract-y" else "format=gray"
        command = [
            self.args.ffmpeg_bin,
            "-nostdin", "-hide_banner", "-loglevel", "warning",
            "-rtsp_transport", self.args.rtsp_transport,
            "-rw_timeout", str(timeout_us),
            "-i", self.args.rtsp_url,
            "-map", "0:v:0", "-an", "-sn", "-dn",
            "-vf", luma_filter,
            "-fps_mode", "passthrough",
            "-pix_fmt", "gray",
            "-f", "rawvideo", "pipe:1",
        ]
        self.logger.info(
            "opening RTSP %s codec=%s input_pix_fmt=%s mode=%sx%s@%.3f transport=%s",
            redact_url(self.args.rtsp_url), self.codec_name, self.input_pixel_format,
            self.width, self.height, self.reported_fps, self.args.rtsp_transport,
        )
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert self.process.stderr is not None

        def drain_stderr() -> None:
            assert self.process and self.process.stderr
            for raw in iter(self.process.stderr.readline, b""):
                line = raw.decode(errors="replace").rstrip()
                if line:
                    self.stderr_lines.append(line)
                    del self.stderr_lines[:-50]
                    self.logger.warning("ffmpeg: %s", line)

        self.stderr_thread = threading.Thread(target=drain_stderr, name="ffmpeg-stderr", daemon=True)
        self.stderr_thread.start()
        self.label = redact_url(self.args.rtsp_url)

    @staticmethod
    def _read_exact(stream: Any, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = stream.read(remaining)
            if not chunk:
                raise EOFError(f"ffmpeg ended with {remaining} frame bytes missing")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def read(self) -> SourceFrame:
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("source is not open")
        if self.process.poll() is not None:
            raise RuntimeError(
                f"ffmpeg exited rc={self.process.returncode}: {' | '.join(self.stderr_lines[-5:])}"
            )
        payload = self._read_exact(self.process.stdout, self.width * self.height)
        now = time.monotonic()
        self.frame_id += 1
        y = np.frombuffer(payload, dtype=np.uint8).reshape(self.height, self.width).copy()
        return SourceFrame(
            self.frame_id,
            now,
            y,
            None,
            {
                "codec": self.codec_name,
                "input_pixel_format": self.input_pixel_format,
                "timestamp_basis": self.timestamp_basis,
            },
        )

    def close(self) -> None:
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.process = None

    def manifest(self) -> dict[str, Any]:
        return super().manifest() | {
            "url": redact_url(self.args.rtsp_url),
            "transport": self.args.rtsp_transport,
            "codec": self.codec_name,
            "input_pixel_format": self.input_pixel_format,
            "luma_mode": self.args.rtsp_luma_mode,
            "warning": (
                "RTSP video is normally ISP-processed and lossy-compressed. Its qualification must be "
                "kept separate from the direct USB YUYV source."
            ),
        }


def create_frame_source(args: Any, logger: logging.Logger) -> FrameSource:
    if args.source_type == "v4l2":
        return LocalV4L2Source(args, logger)
    if args.source_type == "tls-y":
        return TlsYSource(args, logger)
    if args.source_type == "rtsp":
        return RtspFfmpegSource(args, logger)
    if args.source_type == "dataset-y":
        # Imported lazily to avoid a module cycle: dataset_frame_source uses
        # FrameSource and SourceFrame defined in this module.
        from app.sources.dataset_frame_source import DatasetYSource
        return DatasetYSource(args, logger)
    raise ValueError(f"unsupported source type: {args.source_type}")
