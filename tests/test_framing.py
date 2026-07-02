"""Tests for the host-side length-framed reader in host/host_server.py.

The daemon streams: STATUS:<code>\\r STDOUT:<olen>\\r <olen bytes>\\r
STDERR:<elen>\\r <elen bytes>\\r \\r. host_server reads STDOUT by its DECLARED
length so a blank line *inside* the output can't end the frame early, and it
must reassemble a frame that arrives split across arbitrary recv() boundaries.
This drives _read_framed_response with a scripted fake socket (no real network),
plus the control-command reader's terminator/EOF handling.

Run: python3 tests/test_framing.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402

# host_server opens the live /tmp log at import; redirect it so this test's
# synthetic frames don't append noise to the running server's log file.
host_server._logf = open(os.devnull, "a")


class FakeSocket:
    """Dispenses a fixed byte string in <=chunk-sized pieces; b'' at EOF."""
    def __init__(self, data, chunk=1):
        self.data = bytes(data)
        self.pos = 0
        self.chunk = chunk

    def settimeout(self, _):
        pass

    def close(self):
        pass

    def recv(self, n):
        if self.pos >= len(self.data):
            return b""
        size = min(self.chunk, n, len(self.data) - self.pos)
        out = self.data[self.pos:self.pos + size]
        self.pos += size
        return out


def _frame(status, stdout=b"", stderr=b""):
    """Build a daemon response frame: STATUS/STDOUT headers each end with \\r,
    stdout data ends with \\r, and the frame closes with a \\r\\r terminator
    after the stderr data (as SendCommandResult streams it)."""
    return (f"STATUS:{status}\r".encode() +
            f"STDOUT:{len(stdout)}\r".encode() + stdout + b"\r" +
            f"STDERR:{len(stderr)}\r".encode() + stderr + b"\r\r")


def _read(frame_bytes, chunk=1):
    srv = host_server.AppleBridgeServer()
    srv.client_socket = FakeSocket(frame_bytes, chunk=chunk)
    return srv._read_framed_response(1.0, label="test")


# --- reassembly ------------------------------------------------------------

def test_clean_frame_byte_at_a_time():
    # Reassembled one byte at a time: content survives arbitrary recv() splits.
    got = _read(_frame(0, b"HELLO"), chunk=1)
    assert got.startswith("STATUS:0"), repr(got)
    assert "STDOUT:5\rHELLO" in got


def test_frame_reassembled_across_large_chunks():
    got = _read(_frame(0, b"X" * 5000), chunk=64)  # splits mid-header and mid-data
    assert "STDOUT:5000" in got
    assert "X" * 5000 in got


# --- the length-framing contract: blank line inside STDOUT survives --------

def test_embedded_blank_line_not_truncated():
    # STDOUT contains \r\r — a terminator-based reader would stop here; the
    # length-framed reader must keep all 6 bytes.
    stdout = b"AB\r\rCD"
    got = _read(_frame(0, stdout), chunk=1)
    assert "STDOUT:6\rAB\r\rCD" in got, repr(got)


def test_nonzero_status_preserved_through_framing():
    frame = _frame(2, b"", b"boom")
    got = _read(frame, chunk=3)
    assert got.startswith("STATUS:2")
    assert "boom" in got


# --- control-command reader (terminator vs EOF) ---------------------------

def test_control_reader_terminator():
    # MCP client sends '<cmd>\n\n' and keeps the socket open.
    conn = FakeSocket(b"Echo HELLO\n\n", chunk=2)
    assert host_server._recv_control_command(conn) == "Echo HELLO"


def test_control_reader_eof():
    # send_command.py shuts its write side -> EOF with no terminator.
    conn = FakeSocket(b"Directory", chunk=4)
    assert host_server._recv_control_command(conn) == "Directory"


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
