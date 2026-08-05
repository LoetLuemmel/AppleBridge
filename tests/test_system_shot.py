"""We do not photograph the screen. We ask macOS to, and then read the file.

Measured 2026-08-05, after an evening of the other approach: a background
process — the launchd host server, or an ssh session — gets `screencapture`
output containing the desktop picture and NO WINDOWS. Not a permission problem:
path and bundle grants were verified in the TCC database itself and a logout
changed nothing. A process with no window server connection cannot see window
content, whatever it is allowed to do.

The way past it was not to gain the right but to stop needing it. The system
hotkey Cmd-Shift-3 is served by macOS's own screenshot component, which has the
rights we lack, and a background process CAN post that keystroke — input reaches
the visible session even though capture does not, an asymmetry measured the same
day. So the agent presses the key and reads the resulting file.

Two more asymmetries, both measured because both were guessed wrong first:

    ~/Desktop  listing   -> [Errno 1] Operation not permitted
    ~/Desktop  writing   -> allowed

which is why the capture lands in ~/.applebridge/shots (readable) and is then
COPIED into the shared folder (writable), where the guest reads it over the
bridge as `Unix:Screenshots:<name>`.

Run: python3 tests/test_system_shot.py   (or via pytest)
"""

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host"))
import system_shot  # noqa: E402


def test_the_capture_directory_is_not_under_desktop():
    """~/Desktop is TCC-protected: a background agent listing it gets EPERM.
    The system may write there; we may not look."""
    assert "Desktop" not in system_shot.SHOT_DIR, system_shot.SHOT_DIR
    assert system_shot.SHOT_DIR.endswith("shots")


def test_the_share_directory_is_where_the_guest_can_see_it():
    """The whole point of the copy: a REMOTE session fetches the picture over
    the bridge with READFILE and needs nothing host-side at all."""
    assert system_shot.SHARE_DIR.endswith("Share/Screenshots")


def test_listing_does_not_swallow_a_permission_error():
    """THE bug this module was debugged out of. The first version returned an
    empty set on any OSError, turning 'I am not allowed to read this folder'
    into 'the folder is empty' — and the caller then reported that no
    screenshot had appeared, twelve seconds after one had."""
    try:
        system_shot.newest("/nonexistent-directory-for-tests")
        assert False, "a missing directory must not read as an empty one"
    except OSError:
        pass


def test_the_pixel_size_is_read_not_assumed():
    out = "pixelWidth: 2048\npixelHeight: 1536"
    assert system_shot.pixel_size("x.png", run=lambda a: out) == (2048, 1536)


def test_the_screen_size_comes_back_in_points():
    """`screencapture` reports PIXELS while every coordinate here is in POINTS.
    On a 2x display they differ by exactly the factor this pair establishes —
    and assuming 'it is Retina' would be wrong on the machines that are not."""
    assert system_shot.screen_points(run=lambda a: "0, 0, 1920, 1080") == (1920, 1080)


def test_the_crop_is_scaled_from_points_to_pixels():
    """A 1024x768 guest area at 448,156 points becomes 2048x1536 at 896,312
    pixels on a 2x display. Getting this wrong crops a plausible picture of the
    wrong part of the screen — which looks like a working feature."""
    seen = []
    system_shot.crop_to("x.png", (448, 156, 1024, 768), 2.0,
                        run=lambda a: seen.append(a))
    assert seen[0] == ["sips", "-c", "1536", "2048",
                       "--cropOffset", "312", "896", "x.png"], seen[0]


def test_a_one_to_one_display_is_not_scaled():
    seen = []
    system_shot.crop_to("x.png", (10, 20, 100, 50), 1.0,
                        run=lambda a: seen.append(a))
    assert seen[0] == ["sips", "-c", "50", "100",
                       "--cropOffset", "20", "10", "x.png"], seen[0]


def test_the_wait_is_bounded_and_polls():
    """The system writes asynchronously. A single look a few seconds later
    reports 'nothing happened' for something that is merely still happening —
    the first attempt used a three-second window and concluded the keystroke
    had failed. It had not."""
    import time
    start = time.time()
    # The baseline must be built by `newest`, not by os.listdir: one returns
    # full paths and the other bare names, so mixing them makes every existing
    # file look new. That is the same class of mistake as everything else in
    # this file — two views of one thing that do not agree about what they are.
    got = system_shot.wait_for_new(_ROOT, system_shot.newest(_ROOT),
                                   time.time() + 0.5, poll=0.1)
    assert got is None
    assert 0.4 < time.time() - start < 3.0



def test_it_refuses_when_the_emulator_is_not_frontmost():
    """The guard that replaces macOS's window mode.

    We crop the full screen to the emulator's rectangle, so anything lying on
    top of it lands in the picture — silently, and looking exactly like a real
    capture. Window mode would be immune, but returns the window WITH title bar
    and shadow, breaking the rule this surface rests on: the pixels of a capture
    ARE guest coordinates, 1:1. A frontmost emulator cannot have anything above
    it, so the check buys the same robustness without touching coordinates.

    Verified live both ways 2026-08-05: emulator front -> STATUS:0; Terminal
    front -> STATUS:-1 naming who is in front."""
    real_run, real_geo = system_shot._sh, system_shot.guest_input.front_and_geometry
    system_shot.guest_input.front_and_geometry = lambda app: ("Terminal", (0, 0, 10, 10))
    system_shot._sh = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("nothing may happen after the refusal"))
    try:
        system_shot.capture("x.png", app="BasiliskII")
        assert False, "expected a refusal"
    except RuntimeError as e:
        assert "not frontmost" in str(e) and "Terminal" in str(e), e
    finally:
        system_shot._sh, system_shot.guest_input.front_and_geometry = real_run, real_geo


def test_the_refusal_happens_before_anything_is_done():
    """A refusal that has already pressed the hotkey is not a refusal — it is a
    stray screenshot plus an error. The test above asserts it by making every
    subprocess call fail loudly."""
    src = open(system_shot.__file__, encoding="utf-8").read()
    body = src[src.index("def capture("):]
    check = body.index("not frontmost")
    fired = body.index('"t:3"')
    assert check < fired, "the front check must precede the keystroke"


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
