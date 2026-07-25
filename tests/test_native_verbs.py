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
