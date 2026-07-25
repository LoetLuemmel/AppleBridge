"""Tests for host/guest_input.py — the guest-coordinate mouse driver.

Every assertion here corresponds to a way driving the guest's mouse by hand went
wrong on 2026-07-25: a click that landed in another application, a cached window
origin that went stale mid-session, and a menu held open long enough to starve
the daemon into a 30 s reconnect. The pure helpers are testable without a mouse;
the gesture shape is pinned because its *timing* is the whole point.

Run: python3 tests/test_guest_input.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import guest_input as gi  # noqa: E402


# --- geometry: derived, never assumed --------------------------------------
def test_parses_the_window_rect():
    assert gi.parse_window_geometry("605, 104, 1024, 796") == (605, 104, 1024, 796)


def test_window_rect_gibberish_is_refused_not_guessed():
    try:
        gi.parse_window_geometry("no window here")
        assert False, "expected InputError"
    except gi.InputError as e:
        assert "window rect" in str(e)


def test_title_bar_is_derived_from_the_two_heights():
    assert gi.title_bar_height(796, 768) == 28


def test_absurd_title_bar_falls_back_instead_of_shifting_every_click():
    # A negative or huge difference means the sizes did not belong together;
    # using it would offset every later click by that amount.
    assert gi.title_bar_height(100, 768) == gi.FALLBACK_TITLE_H
    assert gi.title_bar_height(9999, 768) == gi.FALLBACK_TITLE_H


def test_guest_to_host_mapping():
    # The live mapping on 2026-07-25: origin 605,104 + a 28 px title bar.
    assert gi.guest_to_host((605, 104), 28, 0, 0) == (605, 132)
    assert gi.guest_to_host((605, 104), 28, 18, 9) == (623, 141)


def test_guest_size_read_from_prefs():
    assert gi.parse_guest_size("disk x\nscreen win/1024/768\nramsize 1\n") == (1024, 768)
    assert gi.parse_guest_size("screen win/640/480\n") == (640, 480)


def test_guest_size_falls_back_when_prefs_say_nothing():
    assert gi.parse_guest_size("ramsize 1\n") == gi.DEFAULT_GUEST_SIZE


# --- the guard that would have prevented the stray click -------------------
def test_out_of_bounds_point_is_refused():
    for pt in [(1024, 10), (10, 768), (-1, 10), (10, -1)]:
        try:
            gi.check_in_bounds(pt[0], pt[1], (1024, 768))
            assert False, f"expected InputError for {pt}"
        except gi.InputError as e:
            assert "outside" in str(e)


def test_in_bounds_edges_are_allowed():
    gi.check_in_bounds(0, 0, (1024, 768))
    gi.check_in_bounds(1023, 767, (1024, 768))


# --- click construction -----------------------------------------------------
def test_click_moves_before_clicking():
    args = gi.build_click((700, 300))
    assert args[0] == "m:700,300"          # move first…
    assert "c:700,300" in args             # …then click the same point
    assert args.index("m:700,300") < args.index("c:700,300")


def test_double_click_repeats_the_click_not_the_move():
    args = gi.build_click((700, 300), count=2)
    assert args.count("c:700,300") == 2
    assert args.count("m:700,300") == 1


def test_modifiers_wrap_the_click_in_key_down_and_up():
    args = gi.build_click((10, 20), hold="cmd,shift")
    assert "kd:cmd,shift" in args and "ku:cmd,shift" in args
    assert args.index("kd:cmd,shift") < args.index("c:10,20") < args.index("ku:cmd,shift")


def test_modifier_aliases_are_accepted():
    down, up = gi.modifier_args("command,option,control")
    assert down == ["kd:cmd,alt,ctrl"] and up == ["ku:cmd,alt,ctrl"]


def test_unknown_modifier_is_refused():
    try:
        gi.modifier_args("hyper")
        assert False, "expected InputError"
    except gi.InputError as e:
        assert "unknown modifier" in str(e)


def test_no_modifiers_adds_nothing():
    assert gi.modifier_args(None) == ([], [])
    assert gi.modifier_args("") == ([], [])


# --- the menu gesture: one invocation, or the daemon starves ---------------
def test_menu_is_a_single_press_move_release_sequence():
    args = gi.build_menu_gesture((623, 141), (675, 275))
    assert args[0].startswith("m:623,141")          # settle on the title
    assert any(a == "dd:623,141" for a in args)     # press
    assert any(a == "m:675,275" for a in args)      # drag to the item
    assert args[-1] == "du:675,275"                 # release on the item
    # Press and release must be in the SAME argument list: split across two
    # cliclick calls the menu stays open in between, and an open menu blocks the
    # guest's event loop until the background daemon drops the bridge.
    assert args.index("dd:623,141") < args.index("du:675,275")


def test_menu_gesture_waits_are_short_enough_to_stay_under_the_heartbeat():
    waits = [int(a[2:]) for a in gi.build_menu_gesture((1, 2), (3, 4))
             if a.startswith("w:")]
    assert sum(waits) < 2000, "a menu must not be held open for seconds"


# --- host-side capture ------------------------------------------------------
def test_full_capture_covers_exactly_the_guest_screen():
    assert gi.build_capture_region((605, 104), 28, (1024, 768)) == (605, 132, 1024, 768)


def test_region_capture_is_expressed_in_guest_coordinates():
    assert gi.build_capture_region((605, 104), 28, (1024, 768),
                                   (250, 80, 560, 420)) == (855, 212, 560, 420)


def test_region_parsing_rejects_malformed_input():
    assert gi.parse_region("1,2,3,4") == (1, 2, 3, 4)
    for bad in ["1,2,3", "a,b,c,d", "", "1,2,3,4,5"]:
        try:
            gi.parse_region(bad)
            assert False, f"expected InputError for {bad!r}"
        except gi.InputError:
            pass


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
