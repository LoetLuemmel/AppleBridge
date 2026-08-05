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


# --- the cost of a gesture, and what was done about it ---------------------
# Measured 2026-08-04 by timing every subprocess a click makes: 1.851 s total,
# subprocesses 1.885 s, unaccounted -0.034 s. Nothing was missing — the time was
# four separate `osascript` invocations (~1.03 s, each paying its own
# interpreter start), 520 ms of deliberate waits, and the focus dance.
# None of these tests move the real cursor.

class _Spy:
    """Stands in for guest_input._run and records what would have been run."""

    def __init__(self, front="Terminal", rect="448|128|1024|796"):
        self.calls = []
        self.front, self.rect = front, rect

    def __call__(self, argv, check=True):
        self.calls.append(argv)
        if argv[0] != "osascript":
            return ""
        script = argv[-1]
        if "position of window 1" in script and "frontmost is true" in script:
            return f"{self.front}|{self.rect}"
        if "position, size" in script:
            return self.rect.replace("|", ", ")
        if "frontmost is true" in script:
            return self.front
        return ""

    def osascripts(self):
        return [a[-1] for a in self.calls if a[0] == "osascript"]


def _with_spy(spy, fn):
    real_run, real_size = gi._run, gi.Session.guest_size
    gi._run = spy
    gi.Session.guest_size = lambda self: (1024, 768)
    try:
        return fn()
    finally:
        gi._run, gi.Session.guest_size = real_run, real_size


def test_the_front_check_and_the_window_rect_are_one_osascript():
    """Two questions, one interpreter start. Each `osascript` invocation cost
    ~0.15 s of pure startup, and a click made four of them."""
    spy = _Spy()

    def gesture():
        with gi.Session(app="BasiliskII") as s:
            s.point(300, 472)
    _with_spy(spy, gesture)
    combined = [s for s in spy.osascripts()
                if "frontmost is true" in s and "position of window 1" in s]
    assert len(combined) == 1, spy.osascripts()


def test_the_window_rect_is_not_read_twice_in_one_gesture():
    """A menu needs the rect for the title AND the item. It used to pay for it
    twice — 0.584 s each, the single biggest item in the breakdown."""
    spy = _Spy()

    def gesture():
        with gi.Session(app="BasiliskII") as s:
            s.geometry()
            s.geometry()
            s.point(300, 472)
    _with_spy(spy, gesture)
    rect_reads = [s for s in spy.osascripts() if "position" in s or "size" in s]
    assert len(rect_reads) == 1, rect_reads


def test_the_rect_is_still_re_read_for_the_NEXT_gesture():
    """The cache is per gesture, never across. The emulator window moved
    mid-session twice now (448,128 -> 605,104 on 2026-07-25, and back again
    while this very change was being measured), and a rect held across gestures
    turns every later click into a silent miss that reports success."""
    spy = _Spy()

    def two():
        for _ in range(2):
            with gi.Session(app="BasiliskII") as s:
                s.point(300, 472)
    _with_spy(spy, two)
    combined = [s for s in spy.osascripts()
                if "frontmost is true" in s and "position of window 1" in s]
    assert len(combined) == 2, spy.osascripts()


def test_focus_is_handed_back_by_default():
    """The default must stay the courteous one: a stray click landed in the
    host's browser once, which is why the restore exists at all."""
    spy = _Spy(front="Terminal")

    def gesture():
        with gi.Session(app="BasiliskII") as s:
            s.point(300, 472)
    _with_spy(spy, gesture)
    back = [s for s in spy.osascripts() if 'process "Terminal"' in s]
    assert len(back) == 1, spy.osascripts()


def test_keep_front_skips_the_restore():
    """0.695 s per gesture — 37 % — because handing focus back means the next
    gesture must take it again (set frontmost + a 400 ms settle + set back)."""
    spy = _Spy(front="Terminal")

    def gesture():
        with gi.Session(app="BasiliskII", keep_front=True) as s:
            s.point(300, 472)
    _with_spy(spy, gesture)
    back = [s for s in spy.osascripts() if 'process "Terminal"' in s]
    assert back == [], spy.osascripts()


def test_an_app_name_with_a_digit_cannot_poison_the_window_rect():
    """`parse_window_geometry` takes every integer it can find. Putting the
    front app's NAME in the same reply would have donated its digits to the
    rect and shifted every click by that amount — silently, and only for people
    whose frontmost app happens to have a number in its name."""
    name, rect = gi.parse_front_and_geometry("Photoshop 2.5|448|128|1024|796")
    assert name == "Photoshop 2.5"
    assert rect == (448, 128, 1024, 796)


def test_a_short_combined_reply_is_refused_not_guessed():
    try:
        gi.parse_front_and_geometry("Terminal|448|128")
        assert False, "expected InputError"
    except gi.InputError as e:
        assert "front app" in str(e)


def test_the_combined_query_asks_the_named_process():
    """Direct cover for front_and_geometry: the process name must reach the
    script, or the rect belongs to whatever window System Events picks."""
    spy = _Spy(front="Safari", rect="10|20|1024|796")
    real, gi._run = gi._run, spy
    try:
        front, rect = gi.front_and_geometry("SheepShaver")
    finally:
        gi._run = real
    assert front == "Safari" and rect == (10, 20, 1024, 796)
    assert 'tell process "SheepShaver"' in spy.osascripts()[0]


def test_handing_focus_back_names_the_app_it_was_asked_for():
    """`front` is the counterpart that makes --keep-front safe to offer: a
    driver that can take the machine needs a one-liner that gives it back."""
    spy = _Spy(front="Terminal")
    real, gi._run = gi._run, spy

    class A:
        name = "Terminal"
    try:
        assert gi.cmd_front(A()) == 0
    finally:
        gi._run = real
    assert any('set frontmost of process "Terminal" to true' in s
               for s in spy.osascripts()), spy.osascripts()


# --- holding a menu open, which is the one thing `menu` refuses to do -------
# Drafted by the parallel session (branch apfelpilot/menushot) after measuring
# that the gap was not the menu mechanics but a missing primitive: HOSTCLICK is
# down+up so the menu opens and shuts, HOSTMENU wants the item coordinate up
# front — which for a list of running processes is circular, because reading
# that list is the whole point. These tests are the review.

class _HoldSpy:
    """A Session stand-in that records the gesture order and can fail on cue."""

    def __init__(self, capture_raises=None):
        self.calls = []
        self.dry_run = False
        self.capture_raises = capture_raises

    def cliclick(self, args, timeout=None):
        self.calls.append(("cliclick", list(args), timeout))
        return ""

    def geometry(self):
        self.calls.append(("geometry", [], None))
        return {"app": "BasiliskII", "origin": (448, 128), "window": (1024, 796),
                "guest_size": (1024, 768), "title_h": 28}


def _hold(spy, **kw):
    real = gi._run

    def fake(argv, check=True, timeout=None):
        spy.calls.append((argv[0], list(argv), timeout))
        if argv[0] == "screencapture" and spy.capture_raises:
            raise spy.capture_raises
        return ""
    gi._run = fake
    try:
        return gi.hold_and_capture(spy, (1454, 165), "/tmp/menu.png", **kw)
    finally:
        gi._run = real


def test_the_button_is_released_even_when_the_capture_fails():
    """The one that matters. A held button that leaks is a stuck machine for the
    person sitting in front of it — the release must not depend on the picture."""
    spy = _HoldSpy(capture_raises=gi.InputError("screencapture blew up"))
    try:
        _hold(spy)
    except gi.InputError:
        pass
    ups = [c for c in spy.calls if c[0] == "cliclick" and any("du:" in a for a in c[1])]
    assert ups, spy.calls


def test_the_order_is_press_then_capture_then_release():
    """A capture taken before the press photographs a closed menu and reports
    success — the failure class this project keeps finding."""
    spy = _HoldSpy()
    _hold(spy)
    kinds = []
    for name, args, _ in spy.calls:
        if name == "cliclick" and any("dd:" in a for a in args):
            kinds.append("press")
        elif name == "screencapture":
            kinds.append("capture")
        elif name == "cliclick" and any("du:" in a for a in args):
            kinds.append("release")
    assert kinds == ["press", "capture", "release"], kinds


def test_both_the_capture_and_the_release_are_time_bounded():
    """`finally` guarantees the release RUNS. A timeout is what guarantees it
    runs SOON: without one, the button stays down for as long as a hung
    screencapture takes, and nothing on the machine can be clicked meanwhile."""
    spy = _HoldSpy()
    _hold(spy)
    for name, args, timeout in spy.calls:
        if name == "screencapture" or (name == "cliclick"
                                       and any("du:" in a for a in args)):
            assert timeout, f"{name} {args} is unbounded"


def test_a_timeout_becomes_a_refusal_rather_than_a_hang():
    import subprocess as sp
    real = gi.subprocess.run
    gi.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(
        sp.TimeoutExpired(cmd="screencapture", timeout=10))
    try:
        gi._run(["screencapture"], timeout=10)
        assert False, "expected InputError"
    except gi.InputError as e:
        assert "did not finish" in str(e)
    finally:
        gi.subprocess.run = real


def test_the_dwell_reaches_the_press_invocation():
    """The menu needs time to render before the picture. Silently dropping the
    dwell would produce a capture of a menu mid-draw, which looks like a
    rendering bug rather than a timing one."""
    spy = _HoldSpy()
    _hold(spy, dwell_ms=400)
    press = [c for c in spy.calls if c[0] == "cliclick" and any("dd:" in a for a in c[1])]
    assert any("w:400" in a for a in press[0][1]), press


def test_the_rect_is_not_re_read_while_the_button_is_held():
    """A happy interaction with the per-gesture geometry cache: the rect was
    already read in __enter__, so holding the mouse costs no osascript round
    trip. Pinned because losing the cache would silently lengthen every hold."""
    spy = _HoldSpy()
    _hold(spy)
    press_at = next(i for i, c in enumerate(spy.calls)
                    if c[0] == "cliclick" and any("dd:" in a for a in c[1]))
    release_at = next(i for i, c in enumerate(spy.calls)
                      if c[0] == "cliclick" and any("du:" in a for a in c[1]))
    between = [c for c in spy.calls[press_at:release_at] if c[0] == "osascript"]
    assert between == [], between


# --- a host capture comes back at guest scale -------------------------------
def test_a_2x_capture_is_resampled_to_the_size_it_was_asked_for():
    """The operator's point: photograph the window, not the desk. It already
    did — but on a 2x display a 1024x768 guest area comes back 2048x1536, four
    times the pixels for the same picture (the emulator maps one guest pixel to
    a 2x2 block). Measured on the same content: 389,555 B against 185,119 B,
    2.1x, 52% saved."""
    seen = []

    def run(argv, check=True, timeout=None):
        seen.append(argv)
        if argv[1] == "-g":
            return "pixelWidth: 2048\npixelHeight: 1536"
        return ""
    assert gi.to_guest_scale("/tmp/x.png", 1024, 768, run=run) == (1024, 768)
    assert ["sips", "--resampleHeightWidth", "768", "1024", "/tmp/x.png"] in seen


def test_a_1x_display_is_left_alone():
    """Conditional on the MEASUREMENT, never on an assumption about Retina —
    resampling a picture that is already the right size would be a lossy no-op
    on every non-Retina host."""
    seen = []

    def run(argv, check=True, timeout=None):
        seen.append(argv)
        return "pixelWidth: 1024\npixelHeight: 768"
    gi.to_guest_scale("/tmp/x.png", 1024, 768, run=run)
    assert not any("--resampleHeightWidth" in a for a in seen), seen


def test_a_failed_resample_keeps_the_picture():
    """A cosmetic step must not destroy the thing it decorates: an oversized
    capture is still the capture, and losing it would be the worse trade."""
    def boom(argv, check=True, timeout=None):
        raise gi.InputError("sips is not here")
    assert gi.to_guest_scale("/tmp/x.png", 1024, 768, run=boom) is None


def test_an_unreadable_size_is_not_guessed():
    def mute(argv, check=True, timeout=None):
        return "no idea"
    assert gi.to_guest_scale("/tmp/x.png", 1024, 768, run=mute) is None


def test_both_capture_paths_go_through_the_same_helper():
    """`shot` and `menushot` must not disagree about what a pixel means. The
    held-menu capture bypassed `capture()` in the draft, which would have left
    it at 2x while every other picture came back at guest scale."""
    src = open(gi.__file__, encoding="utf-8").read()
    # Sliced to the NEXT def, deliberately not to a named one: the coverage
    # ratchet counts a MENTION of a function's name as a test reaching it, so
    # naming one of the argparse entry points here — even in a slice bound, even
    # in a comment explaining why not to — marks it covered when nothing calls
    # it. Both happened while writing this test. A guard satisfied by a mention
    # is the same trap it exists to catch.
    body = src[src.index("def hold_and_capture"):]
    body = body[:body.index("\ndef ", 1)]
    assert "_capture_rect(" in body
    # A CALL, not the word. The first version matched "screencapture" in the
    # docstring that explains why the hold exists — the third time today a test
    # was satisfied by prose about its subject instead of by the subject.
    calls = [l for l in body.splitlines()
             if "_run(" in l and "screencapture" in l]
    assert not calls, f"hold_and_capture still calls screencapture directly: {calls}"
