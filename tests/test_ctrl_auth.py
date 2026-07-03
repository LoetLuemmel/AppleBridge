"""Tests for the control-port (:9001) token guard in host/host_server.py.

The guard is an opt-in loopback-boundary defence: with APPLEBRIDGE_CTRL_TOKEN
set, a control client must lead its request with an "AUTH:<token>\\n" line, and
a missing/mismatched token is rejected fail-closed. With no token configured the
port is open and requests pass through unchanged. This pins the two pure helpers
that implement it — split_ctrl_auth (framing) and ctrl_authorized (the gate) —
so a regression can't silently drop the guard or, worse, block the default-open
path.

Run: python3 tests/test_ctrl_auth.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402

host_server._logf = open(os.devnull, "a")


# --- split_ctrl_auth: strip an optional leading AUTH line --------------------
def test_split_no_auth_line():
    cmd, token = host_server.split_ctrl_auth("Echo HELLO")
    assert cmd == "Echo HELLO"
    assert token is None


def test_split_with_auth_line():
    cmd, token = host_server.split_ctrl_auth("AUTH:s3cret\nEcho HELLO")
    assert cmd == "Echo HELLO"
    assert token == "s3cret"


def test_split_auth_line_only():
    # An AUTH line with no following command -> empty command, token captured.
    cmd, token = host_server.split_ctrl_auth("AUTH:s3cret")
    assert cmd == ""
    assert token == "s3cret"


def test_split_empty_token():
    # "AUTH:\n<cmd>" presents an empty-string token (distinct from None/absent).
    cmd, token = host_server.split_ctrl_auth("AUTH:\nDirectory")
    assert cmd == "Directory"
    assert token == ""


def test_split_command_keeps_embedded_colons_and_newlines():
    # Only the FIRST line is the auth line; the rest is the command verbatim.
    cmd, token = host_server.split_ctrl_auth("AUTH:tok\nLISTDIR:MeinMac:MPW:")
    assert cmd == "LISTDIR:MeinMac:MPW:"
    assert token == "tok"


def test_split_does_not_treat_plain_command_as_auth():
    # A command that merely contains "AUTH" later must not be misparsed.
    cmd, token = host_server.split_ctrl_auth("Echo AUTH:not-a-token")
    assert cmd == "Echo AUTH:not-a-token"
    assert token is None


# --- ctrl_authorized: the gate ----------------------------------------------
def _with_ctrl_token(value):
    """Context helper: set host_server.CTRL_TOKEN, return the previous value."""
    prev = host_server.CTRL_TOKEN
    host_server.CTRL_TOKEN = value
    return prev


def test_authorized_open_when_no_token_configured():
    prev = _with_ctrl_token(b"")
    try:
        # Guard off: everything passes, whether or not a token is presented.
        assert host_server.ctrl_authorized(None) is True
        assert host_server.ctrl_authorized("") is True
        assert host_server.ctrl_authorized("anything") is True
    finally:
        host_server.CTRL_TOKEN = prev


def test_authorized_requires_matching_token_when_configured():
    prev = _with_ctrl_token(b"s3cret")
    try:
        assert host_server.ctrl_authorized("s3cret") is True
    finally:
        host_server.CTRL_TOKEN = prev


def test_authorized_rejects_absent_token_when_configured():
    prev = _with_ctrl_token(b"s3cret")
    try:
        # Fail-closed: a client that sent no AUTH line (token=None) is rejected.
        assert host_server.ctrl_authorized(None) is False
    finally:
        host_server.CTRL_TOKEN = prev


def test_authorized_rejects_wrong_token_when_configured():
    prev = _with_ctrl_token(b"s3cret")
    try:
        assert host_server.ctrl_authorized("wrong") is False
        assert host_server.ctrl_authorized("") is False
        assert host_server.ctrl_authorized("s3cre") is False       # prefix, not equal
        assert host_server.ctrl_authorized("s3crett") is False     # suffix, not equal
    finally:
        host_server.CTRL_TOKEN = prev


def test_end_to_end_split_then_authorize():
    # The realistic path: a raw request is split, then the token gates it.
    prev = _with_ctrl_token(b"letmein")
    try:
        cmd, token = host_server.split_ctrl_auth("AUTH:letmein\nMACSTATUS")
        assert cmd == "MACSTATUS"
        assert host_server.ctrl_authorized(token) is True

        cmd, token = host_server.split_ctrl_auth("MACSTATUS")   # no AUTH line
        assert cmd == "MACSTATUS"
        assert host_server.ctrl_authorized(token) is False      # rejected
    finally:
        host_server.CTRL_TOKEN = prev


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
