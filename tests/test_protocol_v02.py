"""Tests for wire-protocol v0.2 host-side additions (docs/PROTOCOL_v0.2.md).

Covers the three things that ship host-first in PR1, all backward compatible and
exercisable with stdlib only (no emulator):

  * fnv1a64 / ab_digest      — the auth digest primitive (known FNV-1a vectors),
                               dormant in PR1 but must match the future 68K impl.
  * parse_hello_reply        — v0.2 ABVERSION advertisement parsing.
  * negotiate_version        — golden transcripts: a v0.1 daemon's "Invalid
                               command format" reply -> legacy (v1); a v0.2
                               ABVERSION frame -> v2 + features; link stays up.
  * bounded reads            — an oversized declared STDOUT / READFILE length is
                               refused (drop, don't read gigabytes); a too-large
                               control request is rejected without wedging.

Run: python3 tests/test_protocol_v02.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402

# Redirect the live /tmp log so synthetic frames don't pollute the server's log.
host_server._logf = open(os.devnull, "a")


class ScriptedDaemon:
    """A fake daemon socket for negotiate_version()/get_file(): stays 'idle'
    (BlockingIOError while non-blocking) until the first sendall arms it, then
    dispenses `reply` in <=chunk pieces. This mirrors the real order — the
    server _drain()s an idle link, then sends, then reads the response."""

    def __init__(self, reply, chunk=65536):
        self.reply = bytes(reply)
        self.chunk = chunk
        self.pos = 0
        self.blocking = True
        self.armed = False
        self.sent = b""

    def settimeout(self, _):
        pass

    def setblocking(self, b):
        self.blocking = b

    def close(self):
        pass

    def sendall(self, data):
        self.sent += data
        self.armed = True

    def recv(self, n):
        if not self.armed or self.pos >= len(self.reply):
            if not self.blocking:
                raise BlockingIOError()   # idle drain: nothing pending
            return b""                    # blocking read past EOF -> peer closed
        size = min(self.chunk, n, len(self.reply) - self.pos)
        out = self.reply[self.pos:self.pos + size]
        self.pos += size
        return out


class MockDaemon:
    """A faithful in-process mock of the v0.2 daemon's HELLO/AUTH2 handlers,
    driving negotiate_version() through the full auth handshake. It reproduces the
    daemon's logic in Python (same ab_digest) so the host's verify+AUTH2 path is
    exercised for real against a correct — or deliberately adversarial — peer.

    Options model the failure modes: version=1 (legacy), advertise_auth=False (v2
    but no auth), wrong_proof (daemon can't prove the token), reject_auth2 (daemon
    refuses the host's proof)."""

    def __init__(self, token=b"", daemon_nonce="aabbccddeeff0011", version=2,
                 advertise_auth=True, wrong_proof=False, reject_auth2=False):
        self.token = token
        self.daemon_nonce = daemon_nonce
        self.version = version
        self.advertise_auth = advertise_auth
        self.wrong_proof = wrong_proof
        self.reject_auth2 = reject_auth2
        self.blocking = True
        self._out = b""
        self._pos = 0
        self.auth2_seen = None      # the proof the host sent in AUTH2 (or None)

    def settimeout(self, _):
        pass

    def setblocking(self, b):
        self.blocking = b

    def close(self):
        pass

    def _arm(self, data):
        self._out, self._pos = bytes(data), 0

    def sendall(self, data):
        text = data.decode("ascii", "replace")
        if text.startswith("HELLO:"):
            if self.version < 2:
                self._arm(V01_INVALID)          # legacy peer: unknown-verb error
                return
            host_nonce = (text.strip().split(":") + ["", "", ""])[2]
            if self.token and self.advertise_auth:
                proof = host_server.ab_digest(host_nonce.encode(), self.token)
                if self.wrong_proof:
                    proof = "0" * 16
                body = (f"ABVERSION:{self.version};FEAT=auth;"
                        f"NONCE={self.daemon_nonce};PROOF={proof}")
            else:
                body = f"ABVERSION:{self.version};FEAT=;NONCE=;PROOF="
            self._arm(_status_frame(0, body.encode()))
        elif text.startswith("AUTH2:"):
            self.auth2_seen = text.strip().split(":", 1)[1]
            want = host_server.ab_digest(self.daemon_nonce.encode(), self.token)
            if self.reject_auth2 or self.auth2_seen != want:
                self._arm(_status_frame(-1, b"", b"Auth failed"))
            else:
                self._arm(_status_frame(0, b"AuthOK"))

    def recv(self, n):
        if self._pos >= len(self._out):
            if not self.blocking:
                raise BlockingIOError()          # idle drain
            return b""
        chunk = self._out[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class ListSocket:
    """Dispenses a fixed byte string in <=chunk pieces for _recv_control_command;
    b'' at EOF. No arming — used where the server only reads."""

    def __init__(self, data, chunk=4096):
        self.data = bytes(data)
        self.chunk = chunk
        self.pos = 0

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


def _status_frame(status, stdout=b"", stderr=b""):
    """A daemon STATUS response frame, as SendCommandResult streams it."""
    return (f"STATUS:{status}\r".encode() +
            f"STDOUT:{len(stdout)}\r".encode() + stdout + b"\r" +
            f"STDERR:{len(stderr)}\r".encode() + stderr + b"\r\r")


# The exact bytes a v0.1 daemon sends for an unknown verb (main.c:1329).
V01_INVALID = b"STATUS:-1\nSTDOUT:0\n\nSTDERR:21\nInvalid command format\n\n"


def _server_with(reply, chunk=65536):
    """Server whose daemon socket ARMS on the first sendall — for the
    request/response verbs (negotiate_version, get_file) that send then read."""
    srv = host_server.AppleBridgeServer()
    srv.client_socket = ScriptedDaemon(reply, chunk=chunk)
    srv.connected = True
    return srv


def _server_reading(reply, chunk=65536):
    """Server whose daemon socket just dispenses `reply` — for tests that call a
    reader (_read_framed_response) directly without a preceding send."""
    srv = host_server.AppleBridgeServer()
    srv.client_socket = ListSocket(reply, chunk=chunk)
    srv.connected = True
    return srv


# --- digest primitive ------------------------------------------------------

def test_fnv1a64_known_vectors():
    # Canonical FNV-1a 64-bit test vectors — the 68K daemon must reproduce these.
    assert host_server.fnv1a64(b"") == "cbf29ce484222325"
    assert host_server.fnv1a64(b"a") == "af63dc4c8601ec8c"
    assert host_server.fnv1a64(b"foobar") == "85944171f73967e8"


def test_ab_digest_is_nonce_then_token():
    # H(nonce || token): concatenation order matters and both feed the hash.
    assert host_server.ab_digest(b"AB", b"CD") == host_server.fnv1a64(b"ABCD")
    assert host_server.ab_digest(b"AB", b"CD") != host_server.ab_digest(b"CD", b"AB")


def test_ab_digest_cross_impl_golden_vectors():
    # Pinned so the daemon's ABDigestHex (auth.c) and this host verifier can never
    # silently diverge. Convention: the nonce is hashed as its ASCII-hex string
    # exactly as sent on the wire — not decoded (docs/PROTOCOL_v0.2.md §2).
    assert host_server.ab_digest(b"1122334455667788", b"s3cret") == "cfcf7d300083ee67"
    assert host_server.ab_digest(b"deadbeefcafef00d", b"hunter2") == "0b16a20e04ade276"


# --- HELLO reply parsing ---------------------------------------------------

def test_parse_hello_v02_full():
    ver, feat, nonce, proof = host_server.parse_hello_reply(
        "ABVERSION:2;FEAT=auth,persist;NONCE=1122334455667788;PROOF=deadbeefcafef00d\r")
    assert ver == 2
    assert feat == {"auth", "persist"}
    assert nonce == "1122334455667788"
    assert proof == "deadbeefcafef00d"


def test_parse_hello_legacy_when_no_abversion():
    ver, feat, nonce, proof = host_server.parse_hello_reply(
        "STATUS:-1\rSTDOUT:0\rSTDERR:21\rInvalid command format\r\r")
    assert ver == 1 and feat == set() and nonce == "" and proof == ""


def test_parse_hello_empty_reply_is_legacy():
    assert host_server.parse_hello_reply("") == (1, set(), "", "")


# --- negotiate_version golden transcripts ----------------------------------

def test_negotiate_v01_daemon_falls_back_to_legacy():
    # The real v0.1 "Invalid command format" reply -> version 1, link intact.
    srv = _server_with(V01_INVALID, chunk=1)   # byte-at-a-time: reassembly too
    srv.negotiate_version()
    assert srv.peer_version == 1
    assert srv.connected is True
    assert srv.client_socket.sent.startswith(b"HELLO:2:")


def test_negotiate_v02_daemon():
    stdout = b"ABVERSION:2;FEAT=auth;NONCE=1122334455667788;PROOF=0011223344556677"
    srv = _server_with(_status_frame(0, stdout))
    srv.negotiate_version()
    assert srv.peer_version == 2
    assert srv.peer_feat == {"auth"}
    assert srv.connected is True


def test_negotiate_unknown_frame_is_legacy():
    # A framed reply that simply lacks ABVERSION must not be mistaken for v2.
    srv = _server_with(_status_frame(0, b"some other output"))
    srv.negotiate_version()
    assert srv.peer_version == 1


def test_negotiate_no_token_sends_empty_nonce():
    # With AUTH_TOKEN unset (the default), HELLO carries no nonce.
    assert host_server.AUTH_TOKEN == b""       # test env has none
    srv = _server_with(V01_INVALID)
    srv.negotiate_version()
    assert srv.client_socket.sent == b"HELLO:2:\n"


# --- auth handshake (mutual challenge/response) ----------------------------

class _token:
    """Context manager: set host_server.AUTH_TOKEN for one test, then restore."""
    def __init__(self, tok):
        self.tok = tok

    def __enter__(self):
        self.saved = host_server.AUTH_TOKEN
        host_server.AUTH_TOKEN = self.tok
        return self

    def __exit__(self, *a):
        host_server.AUTH_TOKEN = self.saved


def _srv_daemon(daemon):
    srv = host_server.AppleBridgeServer()
    srv.client_socket = daemon
    srv.connected = True
    return srv


def test_auth_success_end_to_end():
    with _token(b"s3cret"):
        d = MockDaemon(token=b"s3cret")
        srv = _srv_daemon(d)
        srv.negotiate_version()
        assert srv.authed is True
        assert srv.connected is True
        assert srv.peer_version == 2
        # The host proved the token over the DAEMON's nonce.
        assert d.auth2_seen == host_server.ab_digest(d.daemon_nonce.encode(), b"s3cret")


def test_auth_rejects_daemon_with_wrong_proof():
    # Daemon can't prove the token -> host drops BEFORE sending AUTH2.
    with _token(b"s3cret"):
        d = MockDaemon(token=b"s3cret", wrong_proof=True)
        srv = _srv_daemon(d)
        srv.negotiate_version()
        assert srv.authed is False
        assert srv.connected is False
        assert d.auth2_seen is None            # never proved ourselves to a bad peer


def test_auth_token_mismatch_fails_closed():
    # Host token != daemon token: the daemon's PROOF (over the daemon's token)
    # won't match what the host expects -> drop, no AUTH2.
    with _token(b"hostkey"):
        d = MockDaemon(token=b"daemonkey")
        srv = _srv_daemon(d)
        srv.negotiate_version()
        assert srv.authed is False and srv.connected is False
        assert d.auth2_seen is None


def test_auth_daemon_rejects_our_auth2():
    # Daemon's PROOF is fine, but it refuses our AUTH2 -> drop, authed False.
    with _token(b"s3cret"):
        d = MockDaemon(token=b"s3cret", reject_auth2=True)
        srv = _srv_daemon(d)
        srv.negotiate_version()
        assert d.auth2_seen is not None        # we did send AUTH2
        assert srv.authed is False and srv.connected is False


def test_auth_required_but_peer_is_legacy_v1():
    # Token set but peer only speaks v1 -> fail closed (won't run unauthenticated).
    with _token(b"s3cret"):
        d = MockDaemon(token=b"s3cret", version=1)
        srv = _srv_daemon(d)
        srv.negotiate_version()
        assert srv.authed is False and srv.connected is False


def test_auth_required_but_peer_offers_no_auth():
    # Peer is v2 but doesn't advertise FEAT=auth -> fail closed.
    with _token(b"s3cret"):
        d = MockDaemon(token=b"s3cret", advertise_auth=False)
        srv = _srv_daemon(d)
        srv.negotiate_version()
        assert srv.authed is False and srv.connected is False


def test_no_token_leaves_auth_open():
    # Default (no token): authed True even against an auth-capable v2 daemon.
    d = MockDaemon(token=b"")     # AUTH_TOKEN is b"" in the test env
    srv = _srv_daemon(d)
    srv.negotiate_version()
    assert srv.authed is True and srv.connected is True and srv.peer_version == 2


# --- bounded reads ---------------------------------------------------------

def test_oversized_stdout_is_refused():
    # A frame declaring a 95 MB STDOUT must be dropped, not read.
    huge = host_server.MAX_DECLARED + 1
    frame = f"STATUS:0\rSTDOUT:{huge}\r".encode() + b"tiny"
    srv = _server_reading(frame)
    got = srv._read_framed_response(1.0, label="test")
    assert got is None
    assert srv.connected is False              # link dropped


def test_normal_stdout_at_cap_still_reads():
    # A declared length exactly at the cap is allowed (boundary is >, not >=).
    payload = b"Z" * 32
    srv = _server_reading(_status_frame(0, payload))
    got = srv._read_framed_response(1.0, label="test")
    assert got is not None and "STDOUT:32" in got


def test_oversized_readfile_is_refused():
    huge = host_server.MAX_DECLARED + 1
    header = f"FILE:54455854:3f3f3f3f:{huge}:0\n".encode()
    srv = _server_with(header + b"x")
    got = srv.get_file("MeinMac:whatever")
    assert got is None
    assert srv.connected is False


def test_control_request_cap_rejects():
    saved = host_server.MAX_CTRL_REQUEST
    host_server.MAX_CTRL_REQUEST = 16
    try:
        conn = ListSocket(b"x" * 64, chunk=64)   # >cap, no terminator
        raised = False
        try:
            host_server._recv_control_command(conn)
        except ValueError as e:
            raised = True
            assert "exceeds" in str(e)
        assert raised, "an over-cap control request must raise"
    finally:
        host_server.MAX_CTRL_REQUEST = saved


def test_control_request_under_cap_ok():
    # A normal command well under the cap round-trips unchanged.
    conn = ListSocket(b"Echo HELLO\n\n", chunk=3)
    assert host_server._recv_control_command(conn) == "Echo HELLO"


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
