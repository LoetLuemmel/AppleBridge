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
import socket
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402

# host_server opens the live /tmp log at import; redirect it so this test's
# synthetic frames don't append noise to the running server's log file.
host_server._logf = open(os.devnull, "a")

# Generous next to RESYNC_BUDGET (3 s): this bound exists only to catch a resync
# loop that never returns at all, so an unbounded implementation fails the test
# instead of hanging the suite.
RESYNC_LIMIT = 8.0


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


# --- resynchronisation after an abandoned read -------------------------------
# The daemon link is PERSISTENT, so a timed-out read does not merely fail its own
# command: the rest of that reply is still coming, and the next command reads it
# instead of its own answer. Every reply after is one behind, which surfaces as
# nonsense. Measured on a Macintosh SE/30, 2026-07-28 — 117 unparseable requests
# reached the daemon in one three-minute window, and the same happened on a
# freshly swapped 0.8d32, so the daemon VERSION was never the variable. The
# host's read budgets are tuned for emulator speeds; real hardware exceeds them.
#
# The old drain was non-blocking and so removed only what had already arrived —
# missing exactly the reply still in flight.

class LateReply:
    """A socket that delivers `chunks` only after `after_calls` empty polls,
    modelling a reply that lands once the reader has given up."""

    def __init__(self, chunks, after_calls=2, noisy_chunks=0):
        self.chunks = list(chunks)
        self.calls = 0
        self.after_calls = after_calls
        # `noisy_chunks` keeps arriving, slowly, so the resync budget is reached
        # by elapsed time. It is FINITE on purpose: an implementation that never
        # checks its deadline then runs out of data and fails the assertion,
        # rather than hanging the suite. A test that hangs reports nothing.
        self.noisy_chunks = noisy_chunks
        self.blocking = True
        self.closed = False

    def setblocking(self, b):
        self.blocking = b

    def settimeout(self, _):
        pass

    def close(self):
        self.closed = True

    def recv(self, n, flags=0):
        self.calls += 1
        if self.calls <= self.after_calls:
            raise BlockingIOError()
        if self.chunks:
            return self.chunks.pop(0)
        if self.noisy_chunks > 0:
            self.noisy_chunks -= 1
            time.sleep(0.05)          # slow enough that 3 s is reached by ~60
            return b"x" * 64
        raise BlockingIOError()


def _srv(sock, desynced="COMMAND: Files"):
    srv = host_server.AppleBridgeServer()
    srv.client_socket = sock
    srv.connected = True
    srv.desynced = desynced
    return srv


class NeverAnswers:
    """Every read times out: the reply is late, not absent."""
    def setblocking(self, b):
        pass

    def settimeout(self, _):
        pass

    def close(self):
        pass

    def recv(self, n, flags=0):
        raise socket.timeout()


class AlwaysStreaming(NeverAnswers):
    """Never goes quiet — the only case the data-branch deadline bounds."""
    def recv(self, n, flags=0):
        time.sleep(0.01)
        return b"y" * 64


def test_an_abandoned_read_records_which_command_it_belonged_to():
    # The real path, not a hand-set flag: drive _read_framed_response into a
    # timeout and check it recorded the label. Setting `desynced` manually in
    # the other tests meant nothing covered this, and deleting the assignment
    # broke no test — the mutation that exposed it.
    srv = host_server.AppleBridgeServer()
    srv.client_socket = NeverAnswers()
    srv.connected = True
    srv.desynced = None
    srv._read_framed_response(0.01, label="COMMAND: Files -l")
    assert srv.desynced == "COMMAND: Files -l", srv.desynced


def test_an_unlabelled_read_still_records_something():
    srv = host_server.AppleBridgeServer()
    srv.client_socket = NeverAnswers()
    srv.connected = True
    srv.desynced = None
    srv._read_framed_response(0.01, label="")
    assert srv.desynced, "a desync with no label is still a desync"


def test_a_continuously_streaming_peer_cannot_hang_the_drain():
    # The ONLY case the data-branch deadline bounds: a peer that never goes
    # quiet never reaches the idle branch where the other check lives. Run it
    # under a watchdog so an unbounded implementation FAILS rather than hanging
    # the suite — a test that hangs reports nothing, which is how the first
    # version of this code got committed.
    import threading
    srv = _srv(AlwaysStreaming())
    done = []
    t = threading.Thread(target=lambda: done.append(srv._drain()), daemon=True)
    t.start()
    t.join(timeout=RESYNC_LIMIT)
    assert done, (f"_drain did not return within {RESYNC_LIMIT}s — the resync "
                  "loop is unbounded for a peer that keeps sending")
    assert done[0] is False, "an unrealignable link must be dropped"


def test_the_drain_waits_for_a_late_reply_and_discards_it():
    srv = _srv(LateReply([b"STDERR:0\r\r"]))
    assert srv._drain() is True
    assert srv.desynced is None, "link should be marked realigned"
    assert srv.connected is True


def test_a_stream_that_never_goes_quiet_drops_the_link():
    # Unrecoverable: continuing would hand every later command somebody else's
    # answer. The daemon redials with a clean stream.
    srv = _srv(LateReply([b"junk"], noisy_chunks=400))
    assert srv._drain() is False
    assert srv.connected is False
    assert srv.desynced is None


def test_a_healthy_idle_link_is_not_delayed():
    # No desync flag => the old non-blocking sweep, no waiting at all. A 0.4 s
    # wait before every command would be a real cost on the happy path.
    import time as _t
    srv = host_server.AppleBridgeServer()
    srv.client_socket = LateReply([], after_calls=0)
    srv.connected = True
    srv.desynced = None
    t0 = _t.monotonic()
    assert srv._drain() is True
    assert _t.monotonic() - t0 < 0.1, "idle drain must not wait"


def test_a_fresh_link_starts_aligned():
    src = open(host_server.__file__.replace(".pyc", ".py")).read()
    assert "self.desynced = None          # a fresh stream is aligned" in src


def test_teardown_is_idempotent_so_an_accurate_reason_survives():
    # What must not happen is a SECOND log line replacing a specific reason with
    # a vague one — callers of _drain() follow a False with their own generic
    # "drain detected closed socket". Asserting on `connected` instead tested
    # nothing: the flag ends up False either way.
    lines = []
    real = host_server.log
    host_server.log = lambda m: lines.append(str(m))
    try:
        srv = host_server.AppleBridgeServer()
        srv.client_socket = LateReply([], after_calls=0)
        srv.connected = True
        srv._mark_disconnected("the real reason")
        n_after_first = len(lines)
        srv._mark_disconnected("drain detected closed socket")
    finally:
        host_server.log = real
    assert any("the real reason" in l for l in lines)
    assert len(lines) == n_after_first, \
        "the generic follow-up logged again and buried the real reason"
    assert not any("drain detected closed socket" in l for l in lines)


# --- the STDERR section is length-framed, so read it by length ---------------
# The defect the SE/30 exposed, 2026-07-28. The daemon emits each piece of a
# reply as its OWN send:
#
#   STATUS:<c>|  STDOUT:<n>|  [<n bytes>|]  STDERR:<m>|  [<m bytes>|]  |
#
# (| = the daemon's separator; MPW C maps source '\r' to 0x0A.) So on a
# fragmented link they arrive as separate recv()s. STDOUT was read by its
# declared length; STDERR was found by SEARCHING for a terminator pair, which
# could return before the STDERR section had arrived — leaving it in the socket.
# The link is PERSISTENT, so the next command read those leftovers and every
# reply after it was one behind.
#
# Live: a DISKINFO whose 142-byte payload was consumed correctly returned only
# ONE further byte and abandoned `STDERR:0||` — exactly the ten bytes the
# following LISTDIR then read as its own reply. Basilisk never showed it, because
# there the whole frame lands in a single recv.
#
# Rather than reproduce the one boundary that bit us, these drive EVERY
# fragmentation: correct by construction beats correct by anecdote.

SEP = b"\n"          # what the daemon actually puts on the wire


def daemon_frame(status=0, out=b"", err=b""):
    """A reply exactly as SendCommandResult streams it (mac/src/protocol.c)."""
    f = b"STATUS:%d%s" % (status, SEP)
    f += b"STDOUT:%d%s" % (len(out), SEP)
    if out:
        f += out + SEP                     # trailer skipped when outLen == 0
    f += b"STDERR:%d%s" % (len(err), SEP)
    if err:
        f += err + SEP
    return f + SEP                         # end marker


class Fragmented:
    """Delivers `data` in fixed-size pieces — one recv per piece."""

    def __init__(self, data, size):
        self.data = bytes(data)
        self.size = size
        self.pos = 0
        self.leftover_at_end = None

    def setblocking(self, b):
        pass

    def settimeout(self, _):
        pass

    def close(self):
        pass

    def recv(self, n, flags=0):
        if self.pos >= len(self.data):
            raise socket.timeout()        # nothing more: a stalled read, not EOF
        out = self.data[self.pos:self.pos + min(self.size, n)]
        self.pos += len(out)
        return out

    def unread(self):
        return len(self.data) - self.pos


def _read_frame(frame, chunk):
    srv = host_server.AppleBridgeServer()
    sock = Fragmented(frame, chunk)
    srv.client_socket = sock
    srv.connected = True
    text = srv._read_framed_response(2.0, label="probe")
    return text, sock.unread(), srv


def test_the_whole_frame_is_consumed_at_every_fragmentation():
    # The DISKINFO shape that failed live: multi-line payload, empty STDERR.
    body = (b"Hard Disk 2048\t-1\t2145328640\t1920855040\n"
            b"AppleShare\t-2\t2147450880\t2147450880\n"
            b"Projekte\t-4\t2147450880\t2147450880\n")
    frame = daemon_frame(0, body, b"")
    for chunk in range(1, len(frame) + 2):
        text, left, _ = _read_frame(frame, chunk)
        assert left == 0, (f"chunk={chunk}: {left} bytes abandoned in the socket "
                           "— the next command would read them as its own reply")
        assert text and text.startswith("STATUS:0"), f"chunk={chunk}: {text!r}"


def test_a_reply_with_stderr_data_is_also_fully_consumed():
    frame = daemon_frame(-1712, b"", b"AESend timed out after 300 ticks")
    for chunk in range(1, len(frame) + 2):
        text, left, _ = _read_frame(frame, chunk)
        assert left == 0, f"chunk={chunk}: {left} bytes abandoned"
        assert "timed out" in text, f"chunk={chunk}: stderr lost"


def test_both_sections_empty_is_still_fully_consumed():
    frame = daemon_frame(0, b"", b"")
    for chunk in range(1, len(frame) + 2):
        text, left, _ = _read_frame(frame, chunk)
        assert left == 0, f"chunk={chunk}: {left} bytes abandoned"


def test_a_payload_containing_the_separator_pair_is_not_truncated():
    # A blank line inside the payload used to be the classic false-early-stop.
    # It is read by declared length, so it must survive verbatim.
    body = b"line one\n\nline two after a blank\n"
    frame = daemon_frame(0, body, b"")
    for chunk in (1, 3, 7, len(frame)):
        text, left, _ = _read_frame(frame, chunk)
        assert left == 0, f"chunk={chunk}: {left} bytes abandoned"
        assert "line two after a blank" in text, f"chunk={chunk}: payload truncated"


def test_the_one_byte_case_from_the_se30_specifically():
    # The exact observed failure: 142-byte payload, empty STDERR, and the
    # separator arriving alone in its own recv.
    body = b"x" * 142
    frame = daemon_frame(0, body, b"")
    text, left, _ = _read_frame(frame, 1)      # one byte per recv: worst case
    assert left == 0, f"{left} bytes abandoned (the SE/30 left 10)"
    assert text.startswith("STATUS:0")
    assert "STDERR:0" in text, "the STDERR section must be in the reply, not the socket"


# --- send_raw must read by declared length too --------------------------------
# The SE/30's actual defect, found 2026-07-28 by tracing recv boundaries after
# the ordinary log gave no hint. DISKINFO goes through send_raw, which looped on
# recv until the buffer contained "\n\n" — and that pair occurs INSIDE a normal
# reply: the payload's last line ends with a separator and the frame adds another
# immediately after it. So the scan stopped before the STDERR section, which
# stayed in the socket. The link is persistent, so the NEXT command read those
# ten bytes as its own answer.
#
# It only bites on a FRAGMENTED link. The daemon streams each piece as its own
# ABSend, so a slow peer delivers them as separate recv()s; on Basilisk the whole
# frame arrives in one recv and the leftovers are already buffered. That is why
# it never appeared on an emulator and always appeared on real hardware.

class PerSend:
    """One recv per daemon ABSend — how a slow peer actually delivers a reply."""

    def __init__(self, sends):
        self.sends = list(sends)
        self.i = 0
        self.blocking = True
        # send_raw drains the socket BEFORE sending. A fake that dispenses data
        # to that drain has its reply eaten before the read ever runs — which is
        # what the first version of this did, and it failed with a TypeError
        # rather than anything informative.
        self.armed = False

    def setblocking(self, b):
        self.blocking = b

    def settimeout(self, _):
        pass

    def close(self):
        pass

    def sendall(self, data):
        self.armed = True

    def recv(self, n, flags=0):
        if not self.armed or self.i >= len(self.sends):
            if not self.blocking:
                raise BlockingIOError()      # idle drain: nothing pending
            raise socket.timeout()
        out = self.sends[self.i][:n]
        self.sends[self.i] = self.sends[self.i][len(out):]
        if not self.sends[self.i]:
            self.i += 1
        return out

    def unread(self):
        return sum(len(x) for x in self.sends[self.i:])


def daemon_sends(out=b"", err=b"", status=0):
    """The ABSend sequence of SendCommandResult (mac/src/protocol.c)."""
    sends = [b"STATUS:%d%sSTDOUT:%d%s" % (status, SEP, len(out), SEP)]
    if out:
        sends += [out, SEP]
    sends.append(b"STDERR:%d%s" % (len(err), SEP))
    if err:
        sends += [err, SEP]
    sends.append(SEP)
    return sends


def _raw(sends, timeout=2.0):
    srv = host_server.AppleBridgeServer()
    sock = PerSend(sends)
    srv.client_socket = sock
    srv.connected = True
    text = srv.send_raw("DISKINFO", timeout=timeout)
    return text, sock.unread()


def test_a_multi_volume_diskinfo_leaves_nothing_in_the_socket():
    # Five volumes: the payload's last newline plus the frame's trailer form the
    # very pair the old scan was looking for.
    body = (b"Hard Disk 2048\t-1\t2145328640\t1920855040\n"
            b"AppleShare\t-2\t2147450880\t2147450880\n"
            b"Archiv\t-3\t2147450880\t2147450880\n"
            b"Freigabe\t-5\t2147450880\t2147450880\n"
            b"Projekte\t-4\t2147450880\t2147450880\n")
    text, left = _raw(daemon_sends(out=body))
    assert left == 0, (f"{left} bytes abandoned — the SE/30 left exactly 10 "
                       "and the next command read them as its own reply")
    assert "Projekte" in text, "the last volume must survive"
    assert "STDERR:0" in text, "the STDERR section belongs in the reply"


def test_a_single_line_payload_is_also_clean():
    # The Basilisk shape: one volume. It passed before too — the point is that it
    # still does, so the fix is not a trade.
    text, left = _raw(daemon_sends(out=b"MeinMac\t-1\t2144363520\t1623464960\n"))
    assert left == 0 and "MeinMac" in text


def test_a_ping_reply_still_works():
    text, left = _raw(daemon_sends(out=b"PONG"))
    assert left == 0 and "PONG" in text


def test_an_error_reply_keeps_its_stderr():
    text, left = _raw(daemon_sends(err=b"Invalid command format", status=-1))
    assert left == 0
    assert "Invalid command format" in text, "the reason must reach the caller"


def test_the_terminator_scan_is_gone_from_send_raw():
    src = open(host_server.__file__.replace(".pyc", ".py")).read()
    at = src.index("def send_raw")
    body = src[at:src.index("def request_log", at)]
    assert 'b"\\n\\n" in response' not in body, "the terminator scan is back"
    assert "_read_framed_response" in body, "send_raw must use the framed reader"


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
