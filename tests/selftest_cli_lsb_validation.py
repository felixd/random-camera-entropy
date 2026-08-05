#!/usr/bin/env python3
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import sys
import types


def load_server():
    flask = types.ModuleType("flask")

    class Flask:
        pass

    class Response:
        pass

    def placeholder(*_args, **_kwargs):
        return None

    flask.Flask = Flask
    flask.Response = Response
    flask.abort = placeholder
    flask.render_template_string = placeholder
    flask.send_from_directory = placeholder
    sys.modules.setdefault("flask", flask)
    import app.core.camera_entropy_server as camera_entropy_server

    return camera_entropy_server


def parse(module, argv: list[str]):
    previous = sys.argv
    sys.argv = ["camera_entropy_server.py", *argv]
    try:
        return module.parse_args()
    finally:
        sys.argv = previous


def expect_error(module, argv: list[str], expected: str) -> None:
    output = io.StringIO()
    try:
        with redirect_stderr(output):
            parse(module, argv)
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError(f"expected parser failure for {argv}")
    assert expected in output.getvalue(), output.getvalue()


def main() -> int:
    server = load_server()
    args = parse(server, [])
    assert args.sample_mode == "xor"
    assert args.lsb_bits == 1
    assert args.entropy_credit_bits_per_pixel == 1.0
    assert args.minimum_conditioner_input_bits == 512
    assert args.apt_window == 1024

    args = parse(
        server,
        [
            "--sample-mode",
            "delta",
            "--lsb-bits",
            "4",
            "--entropy-credit-bits-per-pixel",
            "0.25",
            "--conditioner-input-bits",
            "8192",
        ],
    )
    assert args.sample_mode == "delta"
    assert args.lsb_bits == 4
    assert args.minimum_conditioner_input_bits == 8192
    assert args.apt_window == 512

    expect_error(server, ["--lsb-bits", "5"], "lsb-bits must be in 1..4")
    expect_error(
        server,
        [
            "--lsb-bits",
            "4",
            "--entropy-credit-bits-per-pixel",
            "0.25",
            "--conditioner-input-bits",
            "2048",
        ],
        "need at least 8192 bits",
    )
    expect_error(
        server,
        ["--lsb-bits", "2", "--entropy-credit-bits-per-pixel", "3"],
        "entropy credit must be finite and in (0, lsb_bits]",
    )
    expect_error(
        server,
        ["--lsb-bits", "3", "--apt-window", "1024"],
        "APT window must be 512 for non-binary source samples",
    )

    help_output = io.StringIO()
    try:
        with redirect_stdout(help_output):
            parse(server, ["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    for option in (
        "--sample-mode",
        "--lsb-bits",
        "--entropy-credit-bits-per-pixel",
    ):
        assert option in help_output.getvalue()

    print("CLI multi-LSB validation self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
