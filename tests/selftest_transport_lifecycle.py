#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import socket
import threading
from types import SimpleNamespace

from app.sources.frame_sources import TlsYSource
from app.sources.frame_transport import message_available, recv_message, send_message


def main() -> int:
    logger = logging.getLogger("transport-lifecycle-selftest")
    logger.addHandler(logging.NullHandler())

    client, server = socket.socketpair()
    client.settimeout(2.0)
    server.settimeout(2.0)
    observed: dict[str, object] = {}

    def fake_agent() -> None:
        try:
            assert message_available(server, 1.0)
            message = recv_message(server)
            assert message.payload == b""
            header = message.header
            assert header.get("type") == "close"
            observed["reason"] = header.get("reason")
            send_message(server, {"type": "close-ack", "version": 1, "session_id": 7})
        finally:
            server.close()

    thread = threading.Thread(target=fake_agent, daemon=True)
    thread.start()

    args = SimpleNamespace(source_frame_timeout_seconds=60.0)
    source = TlsYSource(args, logger)
    source.sock = client  # plain socket is sufficient for protocol lifecycle testing
    source.connection_generation = 3
    source.close()
    thread.join(timeout=3.0)

    assert not thread.is_alive(), "agent close-ack thread did not finish"
    assert source.sock is None
    assert observed.get("reason") == "compute worker shutdown"

    # Basic framed bidirectional message sanity check.
    left, right = socket.socketpair()
    try:
        send_message(left, {"type": "ping", "version": 1})
        assert message_available(right, 1.0)
        message = recv_message(right)
        assert message.header["type"] == "ping"
        assert message.payload == b""
    finally:
        left.close()
        right.close()

    print("transport lifecycle self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
