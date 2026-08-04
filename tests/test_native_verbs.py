"""Routing tests for the native verbs DISKINFO and MONITOR.

Both are answered by the daemon itself (File Manager / Window Manager calls, no
ToolServer). The one way they can fail invisibly is a missing host route: an
unrouted verb is passed to ToolServer, which swallows the unknown command and
replies STATUS:0 with EMPTY output — the caller sees success while nothing
happened. That cost a debugging detour with AFPMOUNT on 2026-07-25, so the
routes are pinned here rather than trusted.

Run: python3 tests/test_native_verbs.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402

host_server._logf = open(os.devnull, "a")

HOST_SRC = open(host_server.__file__.replace(".pyc", ".py")).read()
DAEMON_SRC = open(os.path.join(os.path.dirname(__file__), "..", "mac", "src",
                               "main.c")).read()


def _fallthrough_at():
    return HOST_SRC.index('log(f"cmd: {redact_secrets(cmd)')


# --- host routing -----------------------------------------------------------
def test_diskinfo_is_routed_before_the_mpw_fall_through():
    assert HOST_SRC.index('cmd.startswith("DISKINFO:")') < _fallthrough_at()


def test_monitor_is_routed_before_the_mpw_fall_through():
    assert HOST_SRC.index('cmd.startswith("MONITOR:")') < _fallthrough_at()


def test_both_verbs_accept_the_bare_form_too():
    # DISKINFO with no volume lists every mounted one; MONITOR with no argument
    # defaults to showing. Routing must not require the colon.
    assert 'cmd == "DISKINFO"' in HOST_SRC
    assert 'cmd == "MONITOR"' in HOST_SRC


# --- daemon dispatch --------------------------------------------------------
def test_daemon_dispatches_both_verbs():
    for verb in ("PROTO_DISKINFO", "PROTO_MONITOR"):
        assert f"strncmp(request, {verb}" in DAEMON_SRC, f"{verb} not dispatched"


def test_daemon_dispatch_precedes_the_generic_command_parse():
    # Same trap on the guest side: a verb that reaches ParseCommand is treated
    # as an MPW command line instead of a native call.
    parse_at = DAEMON_SRC.index("result = ParseCommand(request")
    for verb in ("PROTO_DISKINFO", "PROTO_MONITOR"):
        assert DAEMON_SRC.index(f"strncmp(request, {verb}") < parse_at


# --- the two window-manager traps this feature ran into ---------------------
def test_show_path_does_not_move_the_window():
    """MoveWindow takes the STRUCTURE origin, the port gives the CONTENT origin.

    Using one for the other walks the window up by a title-bar height on every
    show, until the title bar hides under the menu bar. It looked like a drawing
    bug and was a positioning bug.
    """
    show_path = DAEMON_SRC[DAEMON_SRC.index("Boolean MonitorVerb"):]
    show_path = show_path[:show_path.index("\n}")]
    assert "MoveWindow(" not in show_path


def test_restored_bounds_are_clamped_below_the_menu_bar():
    # The saved rect is the CONTENT rect; the title bar sits above it, so a
    # window saved near the top reopens undraggable and unclosable by hand.
    compute = DAEMON_SRC[DAEMON_SRC.index("static void ComputeMonitorRect"):]
    compute = compute[:compute.index("\n}")]
    assert "usableTop" in compute
    assert "r->top < usableTop" in compute


def test_apple_menu_items_reach_opendeskacc():
    """Without this the daemon's Apple menu draws entries that do nothing.

    Reported twice on 2026-07-25 — first as an unreachable Chooser, then by the
    user as "with AppleBridge frontmost you cannot open a control panel".
    """
    assert "OpenDeskAcc(daName)" in DAEMON_SRC


# --- link identity: the one field that survives a reconnect meaningfully -----
# Everything else MACSTATUS reports about the daemon (uptime, rx, tx, err) is
# cumulative for the daemon PROCESS and continues unchanged through a redial —
# measured on the SE/30, 2026-07-28, where err=117 carried straight across a
# reconnect. So nothing told a caller that the link its long-running work
# started on was gone. link_epoch:link_generation does.

def _server():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
    return host_server.AppleBridgeServer()


def test_a_fresh_server_has_generation_zero():
    srv = _server()
    assert srv.link_generation == 0, "no link accepted yet"


def test_each_accepted_link_increments_the_generation():
    srv = _server()
    srv.link_generation = 0
    for expected in (1, 2, 3):
        srv.link_generation += 1        # what accept_mac does
        assert srv.link_generation == expected


def test_the_epoch_differs_between_host_processes():
    # A bare counter restarts at 1 with the host server, so generation 3 of
    # today would collide with generation 3 of an hour ago — a value that looks
    # continuous and is not. Two servers stand in for two processes.
    a, b = _server(), _server()
    assert a.link_epoch != b.link_epoch, "epochs collide; a counter alone is ambiguous"
    assert len(a.link_epoch) == 8 and int(a.link_epoch, 16) >= 0


def test_both_link_kinds_increment_it():
    # TCP accept and a serial open are both "a new link". Counting only one
    # would make the field silently wrong on the other transport.
    src = open(host_server.__file__.replace(".pyc", ".py")).read()
    assert src.count("self.link_generation += 1") == 2, \
        "expected exactly two increment sites: TCP accept and serial open"


def test_it_is_reported_host_side_so_a_silent_daemon_still_answers():
    # The point of the field is to be readable when work has been orphaned —
    # i.e. possibly while the daemon is not answering at all. Sourcing it from
    # the daemon's STAT would make it unavailable exactly then.
    # Assert each landmark EXISTS before indexing on it. Without that, deleting
    # the field raises ValueError and the runner — which catches AssertionError
    # only — dies with a traceback instead of naming what went missing. That is
    # a red build that does not say why, and it has now happened three times in
    # one day, so it is written out here rather than remembered.
    emit = 'f"link_generation={server.link_generation}"'
    assert emit in HOST_SRC, "link_generation is not reported at all"
    assert 'server.send_raw("STAT"' in HOST_SRC, "STAT probe missing"
    assert HOST_SRC.index(emit) < HOST_SRC.index('server.send_raw("STAT"'), \
        "link_generation must be emitted independently of the daemon's STAT"


def test_the_mcp_tool_exposes_a_single_comparable_id():
    tools_src = open(os.path.join(os.path.dirname(__file__), "..", "mcp",
                                  "tools.py"), encoding="utf-8").read()
    assert '"link_id"' in tools_src
    assert '"link_generation": _int("link_generation")' in tools_src


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


# --- PROCLIST: the running processes, and the routing trap it must not repeat -
def test_proclist_is_routed_before_the_mpw_fall_through():
    """The trap this verb family keeps falling into, measured three times on
    2026-08-04: an unrouted verb is sent to ToolServer as an MPW command. If
    ToolServer is up it swallows it and answers STATUS:0 with empty output —
    "it worked and did nothing". If it is down, the reply blames ToolServer for
    what is a missing route. `STATUS`/`STAT` cost 237 daemon errors from one
    polling loop, and `MENUTREE` cost an hour spent believing a fresh daemon
    deploy had failed."""
    assert HOST_SRC.index('cmd == "PROCLIST"') < _fallthrough_at()


def test_daemon_dispatches_proclist_before_the_generic_parse():
    parse_at = DAEMON_SRC.index("result = ParseCommand(request")
    assert DAEMON_SRC.index("strncmp(request, PROTO_PROCLIST") < parse_at


def test_proclist_is_implemented_where_it_is_declared():
    """Routed + dispatched + DECLARED is still not implemented. The three-way
    agreement is what makes the verb real."""
    header = open(os.path.join(os.path.dirname(__file__), "..", "mac", "include",
                               "applebridge.h")).read()
    impl = open(os.path.join(os.path.dirname(__file__), "..", "mac", "src",
                             "fileio.c")).read()
    assert '#define PROTO_PROCLIST "PROCLIST"' in header
    assert "Boolean ProcListVerb(" in header
    assert "Boolean ProcListVerb(ABConn *conn" in impl


# --- the fall-through says what happened ------------------------------------
def test_an_mpw_command_line_is_never_mistaken_for_a_verb():
    """The hint must not fire on real MPW work — a false hint on every compile
    is noise, and noise gets a hint switched off."""
    for line in ("Files -l :bin:", "SC main.c -o :obj:main.c.o", "Directory",
                 "Catenate :bld.err", "Duplicate -y a b"):
        assert not host_server.looks_like_verb(line), line


def test_a_verb_shaped_token_is_recognised():
    for verb in ("MENUTREE", "STAT", "STATUS", "PROCLIST", "LAUNCH:MeinMac:x"):
        assert host_server.looks_like_verb(verb), verb


def test_the_hint_cannot_name_a_verb_the_dispatch_does_not_route():
    """A hint listing the wrong verbs is worse than none: it reads as
    authoritative and sends the caller somewhere else again. So every name it
    offers must actually appear in the routing block above it."""
    block = HOST_SRC[:_fallthrough_at()]
    for verb in host_server.ROUTED_VERBS:
        if verb.endswith(":") or "-via-" in verb:
            continue          # prefix forms and the documented mac_status alias
        assert f'"{verb}"' in block, f"hint names {verb}, dispatch does not route it"
