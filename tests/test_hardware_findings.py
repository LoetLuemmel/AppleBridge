"""Checks derived from defects that only a real Macintosh could show.

On 2026-07-26 a physical SE/30 surfaced three defects that Basilisk II had never
shown, each because the emulator does not reproduce the property under test:

  1. the Serial Manager's 64-byte default input buffer — an emulated CPU never
     feels the timing pressure of a 16 MHz 68030 draining a UART inside a
     cooperative event loop, so 8150 of 8192 bytes went missing SILENTLY;
  2. a monitor window sized for 1024x768 meeting a 512x342 screen;
  3. a seed router advertising inside AppleTalk's reserved startup range —
     the guest behind MACNAT never asks for a routable network number.

D-014 makes one hardware run part of every milestone, but that instrument is a
wasting asset: mechanical disk, drying capacitors, a PRAM battery that can leak
onto the board. So each finding is converted here into something CI can run
without it.

WHAT THESE TESTS ARE, EXACTLY — because overselling them would be its own kind
of folklore. CI has no 68k compiler and no Mac, so the first two groups are
SOURCE GUARDS plus VALUE CHECKS: they prove the fix is still present, still
correctly ordered, and still dimensioned for the screen it was written for.
They catch a regression that removes or reorders the fix. They do NOT catch a
NEW instance of the same class somewhere else in the code, and they cannot
observe behaviour. The third group is different: `check_atalkd_conf.py` is a
real checker, and it would have caught the real fault.

What none of this preserves is the discovery of *unknown* divergences. That
capability ends with the hardware.

Run: python3 tests/test_hardware_findings.py   (or via pytest)
"""

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host", "tools"))

import check_atalkd_conf as atalkd  # noqa: E402

_SERIAL_C = os.path.join(_ROOT, "mac", "src", "transport_serial.c")
_MAIN_C = os.path.join(_ROOT, "mac", "src", "main.c")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _define(src, name):
    """The integer value of a `#define NAME <int>` in C source."""
    m = re.search(rf"^#define\s+{name}\s+(\d+)", src, re.M)
    assert m, f"#define {name} is gone from the source"
    return int(m.group(1))


def _body_of(src, signature):
    """The text of the function whose definition line contains `signature`,
    from that line to the first column-0 closing brace after it."""
    start = src.index(signature)
    end = src.index("\n}", start)
    return src[start:end]


# --- 1. The serial input buffer (SE/30, silent bulk corruption) -------------

def test_connect_installs_its_own_serial_input_buffer():
    # Without this call the driver keeps its 64-byte default and every transfer
    # larger than that is silently truncated on real hardware.
    body = _body_of(_read(_SERIAL_C), "OSStatus sr_Connect(")
    assert re.search(r"SerSetBuf\s*\(\s*inRef\s*,", body), \
        "sr_Connect no longer installs an input buffer with SerSetBuf"


def test_the_serial_input_buffer_is_large_enough_to_matter():
    src = _read(_SERIAL_C)
    size = _define(src, "kSerInBufSize")
    # 64 bytes is the default that caused the defect; a buffer that does not
    # comfortably exceed one bulk chunk buys nothing. SerSetBuf takes a short,
    # so it must also stay inside a signed 16-bit value.
    assert size >= 4096, f"kSerInBufSize shrank to {size}; the default was 64"
    assert size <= 32767, f"kSerInBufSize {size} overflows the short SerSetBuf takes"


def test_close_returns_the_drivers_buffer_before_freeing_ours():
    # Order is the whole point: dispose first and the Serial Manager keeps
    # writing into freed memory. Checked by source position, which is the only
    # thing available without a 68k runtime.
    body = _body_of(_read(_SERIAL_C), "void sr_Close(")
    restore = body.find("SerSetBuf")
    close = body.find("CloseDriver")
    dispose = body.find("DisposePtr")
    assert restore != -1, "sr_Close no longer hands the driver its buffer back"
    assert close != -1 and dispose != -1, "sr_Close no longer closes/disposes"
    assert restore < close < dispose, (
        "sr_Close must restore the driver's buffer, THEN close, THEN dispose "
        f"(found SerSetBuf@{restore}, CloseDriver@{close}, DisposePtr@{dispose})")


# --- 2. The compact-Mac monitor window (512x342) ---------------------------

# A compact Macintosh — SE/30, Plus, Classic, SE — has a 512x342 built-in screen.
COMPACT_W, COMPACT_H = 512, 342
MENU_BAR_AND_TITLE = 20 + 22   # GetMBarHeight() plus the title-bar clearance


def test_the_clamped_window_fits_a_compact_screen():
    src = _read(_MAIN_C)
    w = _define(src, "COMPACT_MON_W")
    h = _define(src, "COMPACT_MON_H")
    assert w <= COMPACT_W, f"{w} is wider than a compact Mac's {COMPACT_W} px"
    assert h + MENU_BAR_AND_TITLE <= COMPACT_H, (
        f"{h} plus menu bar and title bar exceeds {COMPACT_H} lines")


# What must remain visible, in pixels, and why — the thresholds are the reason
# the clamp exists, not a restatement of the numbers it happens to use.
ICON_COLUMN_W = 64    # the Finder's right-hand column of volume/trash icons
ROOM_FOR_A_WINDOW_H = 96   # enough rows below the console to see a small window

def test_the_clamped_window_still_leaves_the_desktop_visible():
    # The defect was never "off screen" — it was "buries everything". The old
    # default (480x296) left 32 px beside it, so the volume icons were gone and
    # the machine could not be driven while the daemon logged. Both thresholds
    # below reject that geometry and accept the shipped one, which is the
    # distinction worth holding.
    src = _read(_MAIN_C)
    w = _define(src, "COMPACT_MON_W")
    h = _define(src, "COMPACT_MON_H")
    assert COMPACT_W - w >= ICON_COLUMN_W, (
        f"{w} px leaves {COMPACT_W - w} px beside the console; the Finder's "
        f"icon column needs {ICON_COLUMN_W}")
    free_h = COMPACT_H - (h + MENU_BAR_AND_TITLE)
    assert free_h >= ROOM_FOR_A_WINDOW_H, (
        f"{h} lines leaves {free_h} rows of desktop; {ROOM_FOR_A_WINDOW_H} are "
        "needed to see anything under the console")


def test_the_clamp_stays_above_the_windows_own_minimum():
    src = _read(_MAIN_C)
    assert _define(src, "COMPACT_MON_W") > _define(src, "MON_MIN_W")
    assert _define(src, "COMPACT_MON_H") > _define(src, "MON_MIN_H")


def test_the_clamp_applies_to_the_restored_rect_as_well_as_the_default():
    # Both paths matter, and the restored one is the subtle half: a rect saved
    # on a 1024x768 screen (or by an earlier build) is still "valid" on 342
    # lines and would keep covering the whole desktop. Two call sites.
    body = _body_of(_read(_MAIN_C), "static void ComputeMonitorRect(")
    calls = body.count("ClampForCompactScreen(")
    assert calls >= 2, (
        f"ComputeMonitorRect clamps on only {calls} path(s); the restored rect "
        "and the computed default both need it")


def test_the_clamp_only_fires_on_a_small_screen():
    src = _read(_MAIN_C)
    threshold = _define(src, "COMPACT_SCREEN_H")
    assert COMPACT_H <= threshold, \
        f"a {COMPACT_H}-line screen must count as compact (threshold {threshold})"
    assert threshold < 480, \
        f"threshold {threshold} would also shrink the window on roomy displays"


# --- 3. AppleTalk seed inside the reserved startup range -------------------

BROKEN = 'eth0 -router -phase 2 -net 65280 -addr 65280.79 -zone "ApfelNetz"'
FIXED = 'eth0 -router -phase 2 -net 3-3 -addr 3.79 -zone "ApfelNetz"'


def test_the_real_broken_config_is_rejected():
    problems = atalkd.check_line(BROKEN)
    assert problems, "the configuration that actually broke the SE/30 passed"
    assert any("65280" in p for p in problems)
    # Both the -net and the -addr are inside the range; report each.
    assert len(problems) >= 2, problems


def test_the_real_fixed_config_is_accepted():
    assert atalkd.check_line(FIXED) == []


def test_both_ends_of_the_reserved_range_are_caught():
    for net in (atalkd.STARTUP_FIRST, atalkd.STARTUP_LAST, 65400):
        assert atalkd.check_line(f"eth0 -router -net {net}"), net
    # ...and the numbers just outside it are not.
    assert atalkd.check_line("eth0 -router -net 65279") == []


def test_a_range_touching_the_reserved_block_is_caught():
    # `-net 1-65300` is legal syntax and still reaches into the startup range.
    assert atalkd.check_line("eth0 -router -net 1-65300")


def test_comments_and_blank_lines_are_ignored():
    assert atalkd.check_line("") == []
    assert atalkd.check_line("   # -net 65280 was here until 2026-07-26") == []
    assert atalkd.check_line("eth0 -net 3-3   # was -net 65280") == []


def test_malformed_values_are_reported_not_ignored():
    # Silence on an unparsable value is how a checker gives false assurance.
    assert atalkd.check_line("eth0 -router -net wibble")
    assert atalkd.check_line("eth0 -router -addr 3")
    assert atalkd.check_line("eth0 -router -net 0")


def test_check_text_reports_the_offending_line_number():
    text = "# comment\n" + FIXED + "\n" + BROKEN + "\n"
    findings = atalkd.check_text(text)
    assert len(findings) == 1, findings
    lineno, line, problems = findings[0]
    assert lineno == 3, lineno
    assert line == BROKEN
    assert problems


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(list(globals().items())):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        else:
            passed += 1
            print(f"ok   {name}")
    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(1 if failed else 0)
