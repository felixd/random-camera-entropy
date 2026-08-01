#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration test for TLS-Y timeout, reconnect and clean close lifecycle."""
from __future__ import annotations

import logging
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from frame_sources import TlsYSource
from frame_transport import frame_header, make_server_context, recv_message, send_message


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def generate_pki(root: Path) -> None:
    run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.crt", "-subj", "/CN=Test CA", "-days", "1"], root)
    run(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "server.key", "-out", "server.csr", "-subj", "/CN=camera"], root)
    (root / "server.ext").write_text("subjectAltName=DNS:camera\nextendedKeyUsage=serverAuth\n")
    run(["openssl", "x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-out", "server.crt", "-days", "1", "-extfile", "server.ext"], root)
    run(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "client.key", "-out", "client.csr", "-subj", "/CN=compute"], root)
    (root / "client.ext").write_text("extendedKeyUsage=clientAuth\n")
    run(["openssl", "x509", "-req", "-in", "client.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-out", "client.crt", "-days", "1", "-extfile", "client.ext"], root)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generate_pki(root)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        port = listener.getsockname()[1]
        context = make_server_context(root / "server.crt", root / "server.key", root / "ca.crt")

        def hello(session_id: int) -> dict[str, object]:
            return {
                "type": "hello", "version": 1, "app_version": "selftest", "source_id": "camera",
                "session_id": session_id,
                "mode": {"width": 4, "height": 2, "reported_fps": 10.0, "controls": {}},
                "payload": "selftest",
            }

        def send_frame(client: socket.socket, frame_id: int, session_id: int) -> None:
            payload = bytes([frame_id & 0xFF]) * 8
            header = frame_header(
                source_id="camera", frame_id=frame_id,
                captured_unix_ns=time.time_ns(), captured_monotonic_ns=time.monotonic_ns(),
                width=4, height=2, payload=payload, controls={},
            )
            header["session_id"] = session_id
            send_message(client, header, payload)

        def server() -> None:
            raw, _ = listener.accept()
            try:
                with context.wrap_socket(raw, server_side=True) as client:
                    client.settimeout(2.0)
                    send_message(client, hello(1))
                    send_frame(client, 1, 1)
                    time.sleep(0.9)  # exceeds client frame timeout and forces reconnect
                    try:
                        send_frame(client, 2, 1)
                    except Exception:
                        pass
            except Exception:
                pass

            # A timed-out TLS handshake can leave a closed connection in the
            # accept queue.  Skip it and accept the first successful session.
            while True:
                raw, _ = listener.accept()
                try:
                    client = context.wrap_socket(raw, server_side=True)
                except Exception:
                    raw.close()
                    continue
                with client:
                    client.settimeout(2.0)
                    send_message(client, hello(2))
                    send_frame(client, 1, 2)
                    try:
                        message = recv_message(client)
                        if message.header.get("type") == "close":
                            send_message(client, {"type": "close-ack", "version": 1})
                    except Exception:
                        pass
                break
            listener.close()

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        args = SimpleNamespace(
            tls_cert=root / "client.crt", tls_key=root / "client.key", tls_ca=root / "ca.crt",
            tls_host="127.0.0.1", tls_port=port, tls_server_name="camera",
            source_connect_timeout_seconds=0.5, source_frame_timeout_seconds=0.3,
            source_reconnect_attempts=8, source_reconnect_backoff_seconds=0.05,
            strict_mode=True, width=4, height=2, allow_source_frame_gaps=False,
        )
        logger = logging.getLogger("tls-reconnect-selftest")
        logger.addHandler(logging.NullHandler())
        source = TlsYSource(args, logger)
        source.open()
        first = source.read()
        assert first.frame_id == 1 and source.connection_generation == 1
        second = source.read()
        assert second.frame_id == 1
        assert source.connection_generation == 2
        assert source.reconnect_count == 1
        source.close()
        thread.join(timeout=3.0)
        assert not thread.is_alive()

    print("TLS reconnect integration self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
