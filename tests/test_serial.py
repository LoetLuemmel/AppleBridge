"""Tests for the host-side serial transport (docs/SERIAL_TRANSPORT.md).

Exercises host_server.SerialConn over a real pseudo-terminal pair (os.openpty) —
no hardware, no emulator — proving that the socket-shim moves bytes correctly and
that the existing length-framed reader works unchanged over a serial fd. This is
the same lossless-pty path the on-device test harness uses.

Run: python3 tests/test_serial.py   (or via pytest)
"""

import os
import select
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402

host_server._logf = open(os.devnull, "a")


def _pty():
    """A pty pair -> (master_fd, slave_device_path). The slave is closed here so
    SerialConn reopens it by name, exactly as it would a real /dev/tty device."""
    master, slave = os.openpty()
    path = os.ttyname(slave)
    os.close(slave)
    return master, path


def _drain_master(master, n=256, timeout=1.0):
    r, _, _ = select.select([master], [], [], timeout)
    return os.read(master, n) if r else b""


# --- SerialConn moves bytes both ways -------------------------------------

def test_serialconn_recv_and_sendall_over_pty():
    master, path = _pty()
    conn = host_server.SerialConn(path, 9600)
    try:
        conn.settimeout(1.0)
        os.write(master, b"HELLO")
        got = b""
        while len(got) < 5:
            got += conn.recv(16)
        assert got == b"HELLO", got

        conn.sendall(b"WORLD")
        assert _drain_master(master) == b"WORLD"
    finally:
        conn.close()
        os.close(master)


def test_serialconn_nonblocking_idle_raises_blockingio():
    # _drain relies on BlockingIOError meaning "healthy idle link".
    master, path = _pty()
    conn = host_server.SerialConn(path, 9600)
    try:
        conn.setblocking(False)
        raised = False
        try:
            conn.recv(16)
        except BlockingIOError:
            raised = True
        assert raised, "idle non-blocking recv must raise BlockingIOError"
    finally:
        conn.close()
        os.close(master)


def test_serialconn_recv_timeout():
    import socket as _socket
    master, path = _pty()
    conn = host_server.SerialConn(path, 9600)
    try:
        conn.settimeout(0.2)
        timed_out = False
        try:
            conn.recv(16)          # nothing written -> should time out
        except _socket.timeout:
            timed_out = True
        assert timed_out, "a blocking recv with no data must raise socket.timeout"
    finally:
        conn.close()
        os.close(master)


# --- the length-framed reader works unchanged over a serial fd -------------

def test_framed_response_reads_over_serial():
    master, path = _pty()
    srv = host_server.AppleBridgeServer(serial_dev=path)
    srv.client_socket = host_server.SerialConn(path, 9600)
    srv.connected = True
    try:
        frame = b"STATUS:0\rSTDOUT:5\rHELLO\rSTDERR:0\r\r"
        os.write(master, frame)
        got = srv._read_framed_response(1.0, label="serial")
        assert got is not None, "framed read over serial returned None"
        assert "STDOUT:5\rHELLO" in got, repr(got)
    finally:
        srv.client_socket.close()
        os.close(master)


def test_send_command_roundtrips_over_serial():
    # Full send_command path with a faithful responder: it must read the COMMAND
    # frame off the wire FIRST, then reply — a pre-loaded reply would (correctly)
    # be eaten by send_command's anti-desync _drain().
    master, path = _pty()
    srv = host_server.AppleBridgeServer(serial_dev=path)
    srv.client_socket = host_server.SerialConn(path, 9600)
    srv.connected = True
    seen = {}

    def responder():
        buf = b""
        while b"\n" not in buf:                 # COMMAND:<len>\n<payload>
            r, _, _ = select.select([master], [], [], 2.0)
            if not r:
                return
            buf += os.read(master, 256)
        seen["cmd"] = buf
        os.write(master, b"STATUS:0\rSTDOUT:2\rOK\rSTDERR:0\r\r")

    t = threading.Thread(target=responder)
    t.start()
    try:
        resp = srv.send_command("Echo OK")
        t.join(3)
        assert seen.get("cmd", b"").startswith(b"COMMAND:"), seen
        assert resp is not None and "STDOUT:2\rOK" in resp, repr(resp)
    finally:
        srv.client_socket.close()
        os.close(master)


# --- serial-mode server wiring --------------------------------------------

def test_bind_listen_serial_mode_skips_tcp():
    srv = host_server.AppleBridgeServer(serial_dev="/dev/null-ish")
    srv.bind_listen()                 # must not raise / must not bind a socket
    assert srv.server_socket is None


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
        except Exception as e:  # surface unexpected errors per-test
            failed += 1
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
