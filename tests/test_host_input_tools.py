"""Tests for the MCP wrappers around the real-mouse driver.

These tools move the HOST's cursor, so the tests must never let a gesture
actually happen: the Session is faked and every cliclick invocation is recorded
instead of run. What matters is that the wrappers pass guest coordinates
through unchanged, and that a refusal from the driver arrives as a normal
`success: False` result rather than an exception — a refusal means no click
happened, which is the safe outcome and must stay legible.

Run: python3 tests/test_host_input_tools.py   (or via pytest)
"""

import base64
import os
import sys

_MCP = os.path.join(os.path.dirname(__file__), "..", "mcp")
sys.path.insert(0, _MCP)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))


def _load_tools():
    import types
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
gi = tools.guest_input
# Captured BEFORE setup() swaps in the fake, so the surface comparison below
# has something real to compare against.
REAL_SESSION = gi.Session

# The live geometry on 2026-07-25; guest (0,0) -> host (605,132).
ORIGIN, TITLE_H, GUEST = (605, 104), 28, (1024, 768)
CALLS = []


class FakeSession:
    """Stands in for the real driver: same surface, records instead of clicking."""
    raise_on_enter = None

    def __init__(self, app=None, activate=True, dry_run=False,
                 keep_front=False):
        # The signature is copied from the real Session on purpose, and this
        # docstring's "same surface" claim went stale the moment keep_front was
        # added: every tool raised TypeError while the unit tests stayed green,
        # because a fake only fails where somebody asserts on it.
        CALLS.append(("session", app, activate, keep_front))

    def __enter__(self):
        if FakeSession.raise_on_enter:
            raise gi.InputError(FakeSession.raise_on_enter)
        return self

    def __exit__(self, *exc):
        return False

    def geometry(self):
        return {"app": "BasiliskII", "origin": ORIGIN, "window": (1024, 796),
                "guest_size": GUEST, "title_h": TITLE_H}

    def point(self, gx, gy):
        gi.check_in_bounds(gx, gy, GUEST)        # the real guard stays in play
        return gi.guest_to_host(ORIGIN, TITLE_H, gx, gy)

    def cliclick(self, args):
        CALLS.append(("cliclick", args))
        return ""


def setup():
    CALLS.clear()
    FakeSession.raise_on_enter = None
    gi.Session = FakeSession


def last_cliclick():
    return [c[1] for c in CALLS if c[0] == "cliclick"][-1]


# --- mac_host_click ---------------------------------------------------------
def test_click_maps_guest_to_host_and_reports_both():
    setup()
    r = tools.mac_host_click(18, 9)
    assert r["success"] is True
    assert r["guest"] == [18, 9]
    assert r["host"] == [623, 141]              # 605+18, 104+28+9
    assert "c:623,141" in last_cliclick()


def test_click_moves_before_clicking():
    setup()
    tools.mac_host_click(100, 200)
    args = last_cliclick()
    assert args[0].startswith("m:")             # settle the cursor first


def test_double_click_is_passed_through():
    setup()
    r = tools.mac_host_click(100, 200, count=2)
    assert r["count"] == 2
    assert last_cliclick().count("c:705,332") == 2


def test_modifier_list_becomes_a_held_chord():
    setup()
    r = tools.mac_host_click(10, 10, modifiers=["cmd", "shift"])
    assert r["modifiers"] == ["cmd", "shift"]
    assert "kd:cmd,shift" in last_cliclick()


def test_unknown_modifier_refuses_without_clicking():
    setup()
    r = tools.mac_host_click(10, 10, modifiers=["hyper"])
    assert r["success"] is False and "unknown modifier" in r["error"]
    assert not [c for c in CALLS if c[0] == "cliclick"]


def test_off_screen_point_refuses_without_clicking():
    setup()
    r = tools.mac_host_click(2000, 50)
    assert r["success"] is False
    assert "outside" in r["error"]
    assert not [c for c in CALLS if c[0] == "cliclick"]   # nothing happened


def test_a_driver_refusal_is_a_result_not_an_exception():
    setup()
    FakeSession.raise_on_enter = "BasiliskII is not frontmost ('Safari' is)"
    r = tools.mac_host_click(10, 10)
    assert r["success"] is False
    assert "not frontmost" in r["error"]


# --- mac_host_menu ----------------------------------------------------------
def test_menu_sends_one_gesture_with_both_points():
    setup()
    r = tools.mac_host_menu(18, 9, 70, 112)
    assert r["success"] is True
    args = last_cliclick()
    assert "dd:623,141" in args                 # press on the title
    assert args[-1] == "du:675,244"             # release on the item
    assert len([c for c in CALLS if c[0] == "cliclick"]) == 1   # ONE call


def test_menu_press_and_release_are_never_split():
    # Split across invocations the menu stays open, which starves the daemon.
    setup()
    tools.mac_host_menu(18, 9, 70, 112)
    args = last_cliclick()
    assert any(a.startswith("dd:") for a in args)
    assert any(a.startswith("du:") for a in args)


def test_menu_refuses_an_off_screen_item():
    setup()
    r = tools.mac_host_menu(18, 9, 70, 9999)
    assert r["success"] is False and "outside" in r["error"]
    assert not [c for c in CALLS if c[0] == "cliclick"]


# --- mac_host_screenshot ----------------------------------------------------
def test_screenshot_returns_the_shape_the_server_renders_as_an_image():
    setup()
    png = b"\x89PNG\r\n\x1a\n fake"

    def fake_capture(path, region=None, app=None, dry_run=False):
        with open(path, "wb") as fh:
            fh.write(png)
        CALLS.append(("capture", region))
        return path

    orig, gi.capture = gi.capture, fake_capture
    try:
        r = tools.mac_host_screenshot()
        assert r["success"] is True
        assert r["format"] == "png"                       # server keys off these
        assert base64.b64decode(r["image"]) == png
    finally:
        gi.capture = orig


def test_screenshot_region_is_passed_in_guest_coordinates():
    setup()

    def fake_capture(path, region=None, app=None, dry_run=False):
        with open(path, "wb") as fh:
            fh.write(b"x")
        CALLS.append(("capture", region))
        return path

    orig, gi.capture = gi.capture, fake_capture
    try:
        r = tools.mac_host_screenshot(region=[250, 80, 560, 420])
        assert r["region"] == [250, 80, 560, 420]
        assert ("capture", (250, 80, 560, 420)) in CALLS
    finally:
        gi.capture = orig


def test_malformed_region_is_rejected():
    setup()
    for bad in ([1, 2, 3], "nope", [1, 2, 3, "x"]):
        r = tools.mac_host_screenshot(region=bad)
        assert r["success"] is False
        assert "region must be" in r["error"]


def test_capture_failure_is_reported_not_raised():
    setup()

    def boom(path, region=None, app=None, dry_run=False):
        raise gi.InputError("no emulator process found")

    orig, gi.capture = gi.capture, boom
    try:
        r = tools.mac_host_screenshot()
        assert r["success"] is False and "no emulator" in r["error"]
    finally:
        gi.capture = orig


# --- registration -----------------------------------------------------------
def test_all_three_tools_are_registered():
    names = [t["name"] for t in tools.TOOLS]
    for n in ("mac_host_click", "mac_host_menu", "mac_host_screenshot"):
        assert n in names, f"{n} missing from TOOLS"
        assert n in tools.TOOL_HANDLERS, f"{n} missing from TOOL_HANDLERS"


def test_schemas_and_handlers_stay_in_sync():
    assert set(t["name"] for t in tools.TOOLS) == set(tools.TOOL_HANDLERS)
def test_the_fake_session_takes_what_the_real_one_takes():
    """The fake claims "same surface". It stopped being true once, silently:
    keep_front was added to the real Session and every tool raised TypeError
    while these tests stayed green. Compare the signatures instead of trusting
    the claim."""
    import inspect
    real = set(inspect.signature(REAL_SESSION.__init__).parameters)
    fake = set(inspect.signature(FakeSession.__init__).parameters)
    assert real == fake, f"real-only {real - fake}, fake-only {fake - real}"


def test_keep_front_reaches_the_driver():
    """A parameter the tool accepts and drops would report a saving nobody
    gets — 0.695 s per gesture, invisibly not taken."""
    setup()
    tools.mac_host_click(10, 10, keep_front=True)
    assert ("session", None, True, True) in CALLS, CALLS
    setup()
    tools.mac_host_menu(18, 9, 70, 112, keep_front=True)
    assert ("session", None, True, True) in CALLS, CALLS


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
