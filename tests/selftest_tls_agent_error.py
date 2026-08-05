#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression test for structured camera-agent errors before the hello frame."""
from __future__ import annotations

import logging
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from app.sources.frame_sources import TlsYSource
from app.sources.frame_transport import make_server_context, send_message


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
        listener.listen(1)
        port = listener.getsockname()[1]
        context = make_server_context(root / "server.crt", root / "server.key", root / "ca.crt")

        def server() -> None:
            raw, _ = listener.accept()
            with context.wrap_socket(raw, server_side=True) as client:
                send_message(client, {
                    "type": "error",
                    "version": 1,
                    "code": "camera-unavailable",
                    "reason": "camera controls cannot be restored",
                    "retryable": False,
                })
            listener.close()

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        args = SimpleNamespace(
            tls_cert=root / "client.crt", tls_key=root / "client.key", tls_ca=root / "ca.crt",
            tls_host="127.0.0.1", tls_port=port, tls_server_name="camera",
            source_connect_timeout_seconds=1.0, source_frame_timeout_seconds=1.0,
            source_reconnect_attempts=0, source_reconnect_backoff_seconds=0.0,
            strict_mode=True, width=4, height=2, allow_source_frame_gaps=False,
        )
        logger = logging.getLogger("tls-agent-error-selftest")
        logger.addHandler(logging.NullHandler())
        source = TlsYSource(args, logger)
        try:
            source.open()
        except RuntimeError as exc:
            text = str(exc)
            assert "camera-unavailable" in text, text
            assert "camera controls cannot be restored" in text, text
        else:
            raise AssertionError("source.open() accepted an agent error as hello")
        thread.join(timeout=3.0)
        assert not thread.is_alive()

    print("TLS structured agent error self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
