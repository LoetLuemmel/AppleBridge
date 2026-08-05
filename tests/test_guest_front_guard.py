"""Which application owns the GUEST's menu bar is not the host's front app.

Confusing the two cost the parallel session a measurement on 2026-08-05.
`HOSTMENU` brings the EMULATOR forward, so the host side was right — but inside
the guest the front application was the **Finder**, not the THINK Project
Manager. The gesture hit the correct window and the wrong program's menu bar,
and opened a Finder window on MeinMac instead of a project dialog.

Its own summary, and the reason this exists: *check the foreground before every
mouse gesture, do not assume it.* The check has to be asked of the GUEST, and
PROCLIST already answers it — the last column is the front flag.

Run: python3 tests/test_guest_front_guard.py   (or via pytest)
"""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host"))
import host_server  # noqa: E402

# A real PROCLIST reply, tab-separated, front flag last.
REPLY = (
    "Finder\tFNDR\tMACS\t0\t8192\t129531088\t278528\t107052\t0\r"
    "AppleBridgeWatchdog\tAPPL\tABwd\t0\t8193\t128201216\t1054720\t1035516\t0\r"
    "AppleBridge\tAPPL\tABrg\t0\t8194\t115600384\t12599296\t12427068\t0\r"
    "THINK Project Manager\tAPPL\tKAHL\t0\t8195\t108242104\t7356416\t6757328\t1\r")


def test_it_names_the_front_guest_application():
    assert host_server.guest_front_app(None, ask=lambda c: REPLY) \
        == "THINK Project Manager"


def test_a_name_with_spaces_survives():
    """"THINK Project Manager" is the case that matters, and a `.split()` on
    whitespace would have returned "THINK"."""
    got = host_server.guest_front_app(None, ask=lambda c: REPLY)
    assert " " in got


def test_the_finder_is_reported_when_the_finder_is_front():
    """The exact situation that produced the wrong gesture."""
    swapped = REPLY.replace("107052\t0", "107052\t1").replace("6757328\t1", "6757328\t0")
    assert host_server.guest_front_app(None, ask=lambda c: swapped) == "Finder"


def test_no_reply_is_not_a_name():
    """A daemon that cannot answer must not read as 'nobody is front', which
    would let an expectation pass by accident."""
    assert host_server.guest_front_app(None, ask=lambda c: "") is None
    assert host_server.guest_front_app(None, ask=lambda c: None) is None


def test_it_asks_PROCLIST_and_nothing_else():
    seen = []
    host_server.guest_front_app(None, ask=lambda c: seen.append(c) or REPLY)
    assert seen == ["PROCLIST"], seen


def test_it_uses_the_RAW_sender():
    """One string, two senders, two meanings — and the wrong one fails QUIETLY.

    The first version called `send_command`, which wraps a verb in `COMMAND:`
    and hands it to ToolServer as an MPW command. ToolServer swallowed
    "PROCLIST" and answered STATUS:0 with an empty body, so the guard reported
    "(None does)" — and would have refused a CORRECT expectation just as
    readily. Native daemon verbs go through `send_raw`.

    Same shape as the two AESEND dialects measured the same day: an unrouted
    verb is a perfectly good MPW no-op, which is why it does not complain."""
    src = open(host_server.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    body = src[src.index("def guest_front_app"):]
    body = body[:body.index("\n\ndef ")]
    assert "server.send_raw" in body
    assert "server.send_command" not in body


def test_the_guard_refuses_before_the_gesture():
    """A refusal that has already pulled the menu down is not a refusal. Pinned
    by source position, the same way the control-port deadline is."""
    src = open(host_server.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    block = src[src.index('elif cmd.startswith("HOSTMENU:")'):][:2200]
    check = block.index("does not own the guest menu bar")
    gesture = block.index("build_menu_gesture")
    assert check < gesture, "the front check must precede the gesture"


def test_the_expectation_is_optional():
    """Every existing caller predates the field, and a menu gesture that
    suddenly required one would break them all."""
    src = open(host_server.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    block = src[src.index('elif cmd.startswith("HOSTMENU:")'):][:2200]
    assert "len(p) > 4" in block
    assert "if expect and" in block


def test_the_front_app_is_reported_even_when_nobody_asked():
    """A caller that did not think to check can still notice afterwards, and the
    cost is one field in a reply nobody has to read."""
    src = open(host_server.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    block = src[src.index('elif cmd.startswith("HOSTMENU:")'):][:2600]
    assert '"guest_front": front' in block


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
