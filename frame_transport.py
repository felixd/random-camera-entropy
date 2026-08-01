#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Framed mutually-authenticated TLS transport for raw camera Y planes.

The transport deliberately sends uncompressed 8-bit luma (Y8). Lossy codecs,
JPEG and H.26x are not used on this path because they would change the bitstream
being measured. TLS provides confidentiality, peer authentication and integrity;
a per-frame SHA-256 is also carried for independent corruption diagnostics.
"""
from __future__ import annotations

import hashlib
import json
import select
import socket
import ssl
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Optional

MAGIC = b"CEYTLS01"
VERSION = 1
PREFIX = struct.Struct("!8sII")  # magic, JSON header bytes, payload bytes
MAX_HEADER_BYTES = 64 * 1024
MAX_PAYLOAD_BYTES = 128 * 1024 * 1024


class FrameProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class WireMessage:
    header: dict[str, Any]
    payload: bytes


def _read_exact(stream: BinaryIO | socket.socket, size: int) -> bytes:
    if size < 0:
        raise ValueError("negative read size")
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.recv(remaining) if hasattr(stream, "recv") else stream.read(remaining)
        if not chunk:
            raise EOFError(f"connection closed with {remaining} bytes missing")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)



def message_available(sock: socket.socket, timeout: float = 0.0) -> bool:
    """Return True when a complete-or-partial peer message may be readable.

    ``SSLSocket.pending()`` covers bytes already decrypted inside OpenSSL while
    ``select`` covers newly arrived network data.  The caller still uses
    :func:`recv_message` to validate framing.
    """
    try:
        if isinstance(sock, ssl.SSLSocket) and sock.pending() > 0:
            return True
        readable, _writable, _exceptional = select.select([sock], [], [], max(0.0, float(timeout)))
        return bool(readable)
    except (OSError, ValueError):
        return False

def send_message(sock: socket.socket, header: dict[str, Any], payload: bytes = b"") -> None:
    body = json.dumps(header, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(body) > MAX_HEADER_BYTES:
        raise FrameProtocolError(f"header too large: {len(body)}")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise FrameProtocolError(f"payload too large: {len(payload)}")
    sock.sendall(PREFIX.pack(MAGIC, len(body), len(payload)))
    sock.sendall(body)
    if payload:
        sock.sendall(payload)


def recv_message(sock: socket.socket) -> WireMessage:
    prefix = _read_exact(sock, PREFIX.size)
    magic, header_size, payload_size = PREFIX.unpack(prefix)
    if magic != MAGIC:
        raise FrameProtocolError(f"bad protocol magic: {magic!r}")
    if header_size <= 0 or header_size > MAX_HEADER_BYTES:
        raise FrameProtocolError(f"invalid header size: {header_size}")
    if payload_size < 0 or payload_size > MAX_PAYLOAD_BYTES:
        raise FrameProtocolError(f"invalid payload size: {payload_size}")
    try:
        header = json.loads(_read_exact(sock, header_size).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameProtocolError(f"invalid JSON header: {exc}") from exc
    if not isinstance(header, dict):
        raise FrameProtocolError("JSON header is not an object")
    payload = _read_exact(sock, payload_size) if payload_size else b""
    return WireMessage(header=header, payload=payload)


def frame_header(
    *,
    source_id: str,
    frame_id: int,
    captured_unix_ns: int,
    captured_monotonic_ns: int,
    width: int,
    height: int,
    payload: bytes,
    controls: Optional[dict[str, Any]] = None,
    dropped_frames: int = 0,
) -> dict[str, Any]:
    return {
        "type": "frame",
        "version": VERSION,
        "source_id": source_id,
        "frame_id": int(frame_id),
        "captured_unix_ns": int(captured_unix_ns),
        "captured_monotonic_ns": int(captured_monotonic_ns),
        "width": int(width),
        "height": int(height),
        "pixel_format": "Y8",
        "payload_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "controls": controls or {},
        "dropped_frames": int(dropped_frames),
    }


def verify_frame_message(message: WireMessage) -> dict[str, Any]:
    header = message.header
    if header.get("type") != "frame":
        raise FrameProtocolError(f"expected frame, got {header.get('type')!r}")
    if int(header.get("version", -1)) != VERSION:
        raise FrameProtocolError(f"unsupported protocol version: {header.get('version')}")
    if header.get("pixel_format") != "Y8":
        raise FrameProtocolError(f"unsupported pixel format: {header.get('pixel_format')!r}")
    width = int(header.get("width", 0))
    height = int(header.get("height", 0))
    expected = width * height
    if width <= 0 or height <= 0 or expected != len(message.payload):
        raise FrameProtocolError(
            f"frame dimensions/payload mismatch: {width}x{height}, payload={len(message.payload)}"
        )
    if int(header.get("payload_bytes", -1)) != len(message.payload):
        raise FrameProtocolError("payload_bytes does not match received payload")
    actual = hashlib.sha256(message.payload).hexdigest()
    if header.get("sha256") != actual:
        raise FrameProtocolError("frame SHA-256 mismatch")
    return header


def make_server_context(cert: Path, key: Path, ca: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    context.load_verify_locations(cafile=str(ca))
    context.verify_mode = ssl.CERT_REQUIRED
    context.options |= ssl.OP_NO_COMPRESSION
    return context


def make_client_context(cert: Path, key: Path, ca: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    context.load_verify_locations(cafile=str(ca))
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.options |= ssl.OP_NO_COMPRESSION
    return context


def peer_common_name(sock: ssl.SSLSocket) -> Optional[str]:
    cert = sock.getpeercert()
    for rdn in cert.get("subject", ()):  # tuple(tuple((key, value), ...), ...)
        for key, value in rdn:
            if key == "commonName":
                return str(value)
    return None
