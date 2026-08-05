"""HOSTSHOT — the seeing half, which was the only one missing.

`HOSTCLICK:` and `HOSTMENU:` have been on the control port for a while: a session
on another machine could already drive this Mac's real mouse. It could not
photograph the result, and that was an omission rather than a decision.

Why the caller cannot simply run `screencapture` itself — measured 2026-08-04
from the parallel session's ssh into this Mac:

    cliclick (mouse)      -> works
    screencapture -x      -> exit 0, 11 MB, and NO WINDOWS in the image

Windows belong to the WindowServer session an ssh process is not attached to,
and granting TCC does not change it, because it is not a permission question.
So the remote half could OPEN a menu and had no way to READ it.

`mac_host_screenshot` cannot fill that gap: MCP is stdio, one server per
session, running wherever its session runs — there is no route from another
machine's session to this one's. The control port is the one surface that
crosses machines, and this host server is a LaunchAgent in `gui/501`, type
login: the Aqua session. Moving the capture into it moves it into a session
that can see windows.

Run: python3 tests/test_hostshot_verb.py   (or via pytest)
"""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host"))
import host_server  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"payload"


def framed(reply):
    """(status, stdout_len, stdout) from the wire framing."""
    parts = reply.split("\r")
    status = int(parts[0].split(":")[1])
    length = int(parts[1].split(":")[1])
    return status, length, parts[2] if len(parts) > 2 else ""


def test_a_capture_comes_back_as_base64_in_a_correct_frame():
    """The reader takes STDOUT by the DECLARED length. A number that disagrees
    with its payload does not truncate one field, it desynchronises the rest."""
    reply = host_server.hostshot_reply(capture=lambda p, r: p,
                                       read=lambda p: PNG)
    status, length, body = framed(reply)
    assert status == 0
    assert length == len(body), f"declared {length}, carried {len(body)}"
    import base64
    assert base64.b64decode(body) == PNG


def test_an_empty_capture_is_an_ERROR_not_an_empty_success():
    """`screencapture` exits 0 and writes a valid file even when it saw nothing,
    so "the command worked" is not evidence. Same class as `Exists` answering
    true after a failed link — and the reason this verb exists at all."""
    status, length, _ = framed(
        host_server.hostshot_reply(capture=lambda p, r: p, read=lambda p: b""))
    assert status == -1
    assert length == 0


def test_a_refusal_from_the_driver_is_reported_not_swallowed():
    """guest_input refuses when the emulator is not frontmost or a point is off
    screen. That refusal is the safe outcome and must reach the caller — who is
    on another machine and cannot see the screen to work out why."""
    def refuses(path, region):
        raise host_server.guest_input.InputError("BasiliskII is not frontmost")
    status, _, _ = framed(host_server.hostshot_reply(capture=refuses))
    assert status == -1
    assert "not frontmost" in host_server.hostshot_reply(capture=refuses)


def test_the_region_reaches_the_driver_in_guest_coordinates():
    """Guest coordinates, like every other argument on this surface: they come
    straight off a mac_screenshot image, which IS the guest framebuffer."""
    seen = []
    host_server.hostshot_reply(region=(10, 20, 30, 40),
                               capture=lambda p, r: seen.append(r) or p,
                               read=lambda p: PNG)
    assert seen == [(10, 20, 30, 40)]


def test_the_verb_is_exempt_from_the_daemon_guard():
    """The moment it is most needed is exactly the moment the daemon cannot
    answer — a menu or a modal tracking loop owning the guest. A HOSTSHOT that
    required a live daemon would be unavailable precisely then."""
    src = open(os.path.join(_ROOT, "host", "host_server.py"),
               encoding="utf-8").read()
    guard = src[src.index("if not server.connected and cmd not in"):][:400]
    assert '"HOSTSHOT"' in guard, guard
    assert 'HOSTSHOT:' in guard, "the region form must be exempt too"


def test_the_acting_verbs_were_already_there():
    """The point of the change in one assertion: the control port could already
    CLICK and pull a MENU on this host. Only seeing was missing."""
    src = open(os.path.join(_ROOT, "host", "host_server.py"),
               encoding="utf-8").read()
    for verb in ("HOSTCLICK:", "HOSTMENU:", "HOSTSHOT"):
        assert f'cmd.startswith("{verb}")' in src or f'cmd == "{verb}"' in src, verb


def test_it_is_listed_as_routed_so_the_fall_through_hint_is_right():
    """An unrouted command that LOOKS like a verb gets a hint naming the routed
    ones. Leaving HOSTSHOT out would send a caller who mistyped it to
    ToolServer, which is the wrong layer entirely."""
    assert "HOSTSHOT" in host_server.ROUTED_VERBS


def test_the_temp_file_does_not_survive_the_call():
    """It is a screenshot of somebody's desktop. It goes over the wire and does
    not stay on disk afterwards."""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "applebridge_hostshot.png")
    with open(path, "wb") as fh:
        fh.write(PNG)
    host_server.hostshot_reply(capture=lambda p, r: p, read=lambda p: PNG)
    assert not os.path.exists(path)


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
