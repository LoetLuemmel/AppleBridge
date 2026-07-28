"""The AESEND wait is bounded, and the daemon is the side that gives up first.

On 2026-07-27 a bare `KAHL/RUN` was sent to THINK C's Project Manager. The
project could not link, so the application needed to interact, could not, and
never replied. The daemon sat inside `AESend` with `kAEWaitReply` and
`AE_SCRIPT_TIMEOUT` — five minutes — and System 7 schedules cooperatively, so an
application that is not yielding holds the whole guest. The emulator had to be
force-quit with the disk image open. Recorded as the second verb under R16 in
`docs/INSTALLER_REQUIREMENTS.md`.

The five minutes was not careless: it was reasoned about for `'dosc'` ->
ToolServer, an application we own and that always answers. The defect is that a
constant chosen for a program we control was inherited by a verb that can
address ANY program.

Two properties hold it shut, and both are invisible in a passing live run —
an event that happens to be answered looks identical either way:

  1. AESEND does not use AE_SCRIPT_TIMEOUT. Its default is interactive, and a
     caller may ask for no reply at all (kAENoReply, which cannot block).
  2. The DAEMON's bound is shorter than the HOST's read timeout. If the host
     gave up first it would report a timeout about a guest that is still being
     starved — a true statement about the wrong layer.

Run: python3 tests/test_ae_wait_bound.py   (or via pytest)
"""

import os
import sys
import types

HERE = os.path.dirname(__file__)
_MCP = os.path.join(HERE, "..", "mcp")
sys.path.insert(0, _MCP)
sys.path.insert(0, os.path.join(HERE, "..", "host"))
import host_server  # noqa: E402

host_server._logf = open(os.devnull, "a")


def _load_tools():
    """mcp/tools.py imports its connection relatively, so it cannot be imported
    as a plain module — same loader as tests/test_host_input_tools.py."""
    stub = types.ModuleType("mac_connection")
    stub.get_connection = lambda: None
    stub.MacConnection = object
    sys.modules.setdefault("mac_connection", stub)
    path = os.path.join(_MCP, "tools.py")
    src = open(path).read().replace("from .mac_connection import get_connection",
                                    "from mac_connection import get_connection")
    mod = types.ModuleType("abtools")
    mod.__file__ = os.path.abspath(path)
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


tools = _load_tools()

DAEMON_SRC = open(os.path.join(HERE, "..", "mac", "src", "main.c")).read()
COMMAND_SRC = open(os.path.join(HERE, "..", "mac", "src", "command.c")).read()
HEADER_SRC = open(os.path.join(HERE, "..", "mac", "include", "applebridge.h")).read()
HOST_SRC = open(host_server.__file__.replace(".pyc", ".py")).read()
TOOLS_SRC = open(os.path.join(HERE, "..", "mcp", "tools.py")).read()


# --- a socket that records what the host actually put on the wire -----------
class FakeSocket:
    def __init__(self):
        self.sent = b""

    def sendall(self, data):
        self.sent += data

    def settimeout(self, _):
        pass


def _server_with_fake_socket():
    """An AppleBridgeServer wired to a fake daemon socket, with the reply read
    stubbed out — these tests are about the request and the timeout, not the
    response parse."""
    srv = host_server.AppleBridgeServer.__new__(host_server.AppleBridgeServer)
    srv.connected = True
    srv.client_socket = FakeSocket()
    srv._drain = lambda: True
    srv.read_timeouts = []
    srv._read_framed_response = lambda to, label="": (srv.read_timeouts.append(to)
                                                      or "STATUS:0\rSTDOUT:0\r\rSTDERR:0\r\r\r")
    return srv


def _header(srv):
    return srv.client_socket.sent.split(b"\n", 1)[0].decode("ascii")


# --- 1. the request carries the bound ---------------------------------------
def test_a_stated_bound_reaches_the_wire():
    srv = _server_with_fake_socket()
    srv.send_apple_event("4b41484c", "4b41484c", "52554e20", b"", wait_ticks=600)
    assert _header(srv) == "AESEND:4b41484c:4b41484c:52554e20:0:600"


def test_zero_ticks_is_sent_as_zero_not_dropped():
    # 0 means kAENoReply on the daemon. Falsy-checking it away would silently
    # restore the blocking send this whole change exists to prevent.
    srv = _server_with_fake_socket()
    srv.send_apple_event("4b41484c", "4b41484c", "4d414b45", b"", wait_ticks=0)
    assert _header(srv).endswith(":0:0")


def test_no_bound_omits_the_field_so_an_old_daemon_still_parses_it():
    # The field is an ADDITION to the wire format; a daemon that predates it
    # must still read the request, and it then applies its own default.
    srv = _server_with_fake_socket()
    srv.send_apple_event("4b41484c", "4b41484c", "52554e20", b"")
    assert _header(srv) == "AESEND:4b41484c:4b41484c:52554e20:0"


def test_the_direct_object_length_is_still_the_frame():
    srv = _server_with_fake_socket()
    srv.send_apple_event("4d505358", "6d697363", "646f7363", b"Files -l", wait_ticks=1800)
    assert _header(srv) == "AESEND:4d505358:6d697363:646f7363:8:1800"
    assert srv.client_socket.sent.endswith(b"Files -l")


# --- 2. the host outlasts the daemon, always --------------------------------
def test_read_timeout_leaves_the_daemon_room_to_time_out_first():
    for ticks in (0, 60, 600, 1800, host_server.AE_SEND_MAX_TIMEOUT_TICKS):
        got = host_server.AppleBridgeServer._ae_read_timeout(ticks)
        assert got > ticks / 60.0, f"host would give up first at {ticks} ticks"


def test_the_unstated_case_budgets_for_the_daemons_default():
    got = host_server.AppleBridgeServer._ae_read_timeout(None)
    assert got > host_server.AE_SEND_DEFAULT_TIMEOUT_TICKS / 60.0


def test_read_timeout_never_exceeds_the_long_timeout():
    assert (host_server.AppleBridgeServer._ae_read_timeout(10 ** 9)
            == host_server.LONG_TIMEOUT)


def test_the_daemon_ceiling_stays_under_the_host_read_ceiling():
    # The invariant behind the whole arrangement, stated as arithmetic rather
    # than as a comment: even at its maximum the daemon returns before the host
    # stops listening.
    assert (host_server.AE_SEND_MAX_TIMEOUT_TICKS / 60.0
            < host_server.LONG_TIMEOUT)


def test_the_host_constants_still_match_the_daemon_header():
    # Two files, one number each. They drifted apart is exactly the failure this
    # pins: the host would budget for a bound the daemon no longer honours.
    for const, want in (("AE_SEND_DEFAULT_TIMEOUT",
                         host_server.AE_SEND_DEFAULT_TIMEOUT_TICKS),
                        ("AE_SEND_MAX_TIMEOUT",
                         host_server.AE_SEND_MAX_TIMEOUT_TICKS)):
        assert f"#define {const} ({want}L)" in HEADER_SRC, \
            f"{const} in applebridge.h no longer reads {want}"


# --- 3. the control port clamps rather than trusts --------------------------
def test_control_port_parses_the_optional_wait_field():
    assert "wait_ticks=wait_ticks" in HOST_SRC


def test_control_port_clamps_the_caller_supplied_bound():
    # This field decides how long the guest can be starved, so a number off the
    # network is bounded here, not merely forwarded.
    assert "min(AE_SEND_MAX_TIMEOUT_TICKS" in HOST_SRC


# --- 4. the daemon: no AE_SCRIPT_TIMEOUT on this path -----------------------
def _generic_ae_body():
    start = COMMAND_SRC.index("static OSErr SendGenericAE")
    end = COMMAND_SRC.index("BridgeResult ExecuteAppleEvent", start)
    return COMMAND_SRC[start:end]


def test_the_arbitrary_event_sender_no_longer_uses_the_toolserver_timeout():
    # The literal defect. AE_SCRIPT_TIMEOUT is still correct for 'dosc' and must
    # stay there; it must not be reachable from a verb addressing any app.
    assert "AE_SCRIPT_TIMEOUT" not in _generic_ae_body()


def test_the_dosc_path_keeps_its_five_minutes():
    # The other half of the same claim: this change must not shorten the wait
    # for long Link/SC builds, where ~60 s produced spurious -1712.
    dosc = COMMAND_SRC[COMMAND_SRC.index("static OSErr SendDoScript"):
                       COMMAND_SRC.index("static OSErr SendGenericAE")]
    assert "AE_SCRIPT_TIMEOUT" in dosc


def test_a_zero_bound_sends_without_waiting():
    body = _generic_ae_body()
    assert "waitTicks <= 0" in body
    assert "kAENoReply" in body


def test_the_daemon_clamps_the_bound_it_was_given():
    assert "waitTicks > AE_SEND_MAX_TIMEOUT" in _generic_ae_body()


def test_an_unstated_bound_becomes_the_interactive_default():
    exec_body = COMMAND_SRC[COMMAND_SRC.index("BridgeResult ExecuteAppleEvent"):]
    assert "waitTicks = AE_SEND_DEFAULT_TIMEOUT" in exec_body


def test_a_timeout_is_reported_as_a_timeout():
    # -1712 from this path means "the target did not answer in time", not
    # "AESend failed" — the caller has to be able to tell a slow app from a
    # refused event without reading the daemon source.
    assert "errAETimeout" in COMMAND_SRC


def test_the_daemon_parses_the_optional_wait_field():
    verb = DAEMON_SRC[DAEMON_SRC.index("PROTO_AESEND, strlen(PROTO_AESEND)"):]
    verb = verb[:verb.index("PROTO_CLIPGET")]
    assert "waitTicks = -1" in verb, "no 'unstated' sentinel"
    assert "ExecuteAppleEvent(tgt, cls, eid, request + headerEnd, doLen, waitTicks" in verb


# --- 5. the MCP surface says what it costs ----------------------------------
def test_the_tool_can_decline_to_wait():
    assert "expect_reply" in tools.mac_send_apple_event.__code__.co_varnames


def test_declining_to_wait_sends_zero_ticks():
    sent = {}

    class FakeConn:
        def is_connected(self):
            return True

        def send_command(self, verb, timeout=None):
            sent["verb"] = verb
            sent["timeout"] = timeout
            return 0, "", ""

    real = tools.get_connection
    tools.get_connection = lambda: FakeConn()
    try:
        out = tools.mac_send_apple_event("KAHL", "KAHL", "RUN", expect_reply=False)
        assert sent["verb"].endswith(":0"), sent["verb"]
        assert out["waited_for_reply"] is False

        tools.mac_send_apple_event("KAHL", "KAHL", "RUN", wait_seconds=10)
        assert sent["verb"].endswith(":600"), sent["verb"]

        # Asking for an hour must not produce an hour: the daemon would clamp it
        # anyway, and a host that budgeted for the request rather than the clamp
        # would sit waiting long after the daemon had answered.
        tools.mac_send_apple_event("KAHL", "KAHL", "RUN", wait_seconds=3600)
        ticks = int(sent["verb"].rsplit(":", 1)[1])
        assert ticks <= host_server.AE_SEND_MAX_TIMEOUT_TICKS, ticks
    finally:
        tools.get_connection = real


def test_the_tool_description_names_the_cost_and_how_to_avoid_it():
    # The trap is not obvious from the signature: waiting looks free until the
    # target does not answer. The description has to say so where it is read.
    desc = next(t["description"] for t in tools.TOOLS
                if t["name"] == "mac_send_apple_event")
    assert "cooperativ" in desc.lower()
    assert "expect_reply" in desc


# --- 6. a failure has to say what failed ------------------------------------
# Both layers used to throw the cause away. The daemon tagged three different
# verbs "cmd fail", so the footer read ERR 2 with no way to tell a compile error
# from an Apple Event timeout; the host logged the REQUEST and not the response,
# so a failed command looked exactly like a successful one — a verb going out
# and nothing coming back. Noticed 2026-07-28 while reading ERR 2 off the
# monitor after the two deliberate timeouts above.
def test_a_failing_response_is_logged_with_its_status():
    # The exact bytes a live 0.8d32 daemon sent on 2026-07-28. Note the mixed
    # terminators: STATUS/STDOUT end in CR, STDERR's length in LF, because
    # classic-Mac C maps '\n' to CR and '\r' to LF. A parser that assumes one of
    # them finds the code and drops the sentence that explains it — which is
    # what the first version of this did.
    got = host_server._framed_failure(
        "STATUS:-1712\rSTDOUT:0\rSTDERR:55\n"
        "AESend timed out after 120 ticks - target did not reply\n\n")
    assert got is not None
    assert "-1712" in got
    assert "timed out after 120 ticks" in got, got


def test_a_successful_response_logs_nothing():
    assert host_server._framed_failure("STATUS:0\rSTDOUT:2\rok\rSTDERR:0\r\r") is None


def test_a_non_status_frame_is_not_mistaken_for_a_failure():
    # IMAGE frames and legacy replies come through the same reader.
    assert host_server._framed_failure("IMAGE:1024:768:8:1024:256:786432\r") is None


def test_a_failure_without_stderr_still_reports_its_code():
    got = host_server._framed_failure("STATUS:-192\rSTDOUT:0\rSTDERR:0\r\r")
    assert got == "STATUS:-192"


def test_the_daemon_tag_carries_the_verb_and_the_code():
    # "cmd fail" told you a command failed, which the counter already said.
    assert 'NoteErr("cmd fail")' not in DAEMON_SRC
    for verb in ("AESEND", "CLIPGET", "COMMAND"):
        assert f'NoteErrCode("{verb}", cmdResult.exitCode)' in DAEMON_SRC, verb


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
