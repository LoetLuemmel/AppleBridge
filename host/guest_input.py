#!/usr/bin/env python3
"""guest_input.py — drive the emulated Mac's REAL mouse in GUEST coordinates.

Why this exists
---------------
Some classic-Mac controls can only be driven by a real mouse. Menus, Standard
File lists and other modal tracking loops POLL the hardware pointer, so the
daemon's synthetic clicks (which set the low-memory mouse for one instant) never
reach them. The working answer on a LOCAL emulator is to move the host's own
cursor with `cliclick` — but doing that by hand went wrong in three distinct
ways in a single session (2026-07-25), and each is now handled here:

  1. The emulator was not frontmost, so the click landed in the host's browser.
     -> every gesture verifies the front application first, and by default
        activates the emulator and restores the previous front app afterwards.
  2. Window geometry was cached; the window then MOVED mid-session
     (448,128 -> 605,104) and every later coordinate was silently wrong.
     -> geometry is re-read immediately before each gesture, never cached.
  3. A menu was held open across two screenshots (~10 s). That blocks the
     guest's event loop, which starves the background daemon: the bridge logged
     `OTSnd err=-3158` and dropped for 30 s.
     -> `menu` performs press-move-release as ONE cliclick invocation, and
        `shot` grabs the window host-side, because the daemon cannot answer
        while a tracking loop owns the machine.

Coordinates are always GUEST coordinates (0,0 = top-left of the emulated
screen), which is what a screenshot of the guest shows. The host mapping
(window origin + title-bar height) is this module's job, not the caller's.

Scope: the LOCAL emulator on this host only — a remote guest has no host cursor
to borrow. stdlib only; needs `cliclick` on PATH.

Usage
    guest_input.py geometry
    guest_input.py move  <gx> <gy>
    guest_input.py click <gx> <gy> [--count N] [--hold cmd,shift]
    guest_input.py menu  <titleX> <titleY> <itemX> <itemY>
    guest_input.py shot  [out.png] [--region gx,gy,w,h]
    guest_input.py menushot <titleX> <titleY> [out.png] [--dwell ms] [--region ...]
                            [--over gx,gy]   # reveal a hierarchical submenu

    --app NAME      emulator process (default: auto-detect Basilisk/SheepShaver)
    --no-activate   never bring the emulator forward; abort if it is not front
    --dry-run       print what would run, touch nothing
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

EMULATORS = ("BasiliskII", "SheepShaver")
PREFS_PATH = os.path.expanduser("~/.basilisk_ii_prefs")
DEFAULT_GUEST_SIZE = (1024, 768)
# Fallback only: the title bar is normally derived as window height - guest
# height, which stays correct if the emulator is ever resized or restyled.
FALLBACK_TITLE_H = 28


class InputError(Exception):
    """A refusal to act — never a partially-executed gesture."""


# --------------------------------------------------------------------------
# pure helpers (unit-tested without a mouse)
# --------------------------------------------------------------------------
def parse_guest_size(prefs_text, default=DEFAULT_GUEST_SIZE):
    """Read the emulated screen size from a Basilisk prefs body ('screen win/W/H')."""
    m = re.search(r"^screen\s+\w+/(\d+)/(\d+)", prefs_text or "", re.M)
    return (int(m.group(1)), int(m.group(2))) if m else default


def parse_window_geometry(osascript_output):
    """Parse '448, 128, 1024, 796' -> (x, y, w, h)."""
    nums = [int(n) for n in re.findall(r"-?\d+", osascript_output or "")]
    if len(nums) < 4:
        raise InputError(f"could not read the emulator window rect: "
                         f"{osascript_output!r}")
    return tuple(nums[:4])


def parse_front_and_geometry(output):
    """Parse 'Terminal|605|104|1024|796' -> ('Terminal', (605, 104, 1024, 796)).

    A separator rather than AppleScript's own ', ' list form, and the name kept
    OUT of the number scan: `parse_window_geometry` reads every integer it can
    find, so an application with a digit in its name ("Basilisk II 2") would
    have donated it to the window rect and shifted every click by that amount.
    """
    parts = (output or "").split("|")
    if len(parts) != 5:
        raise InputError(f"could not read front app + window rect: {output!r}")
    return parts[0].strip(), parse_window_geometry("|".join(parts[1:]))


def title_bar_height(win_h, guest_h):
    """Height of the host window chrome above the emulated screen.

    Derived rather than assumed: a wrong constant shifts every click by exactly
    that many pixels, which reads as "my coordinates are off" instead of "the
    title bar is a different size".
    """
    h = win_h - guest_h
    return h if 0 <= h <= 200 else FALLBACK_TITLE_H


def guest_to_host(origin, title_h, gx, gy):
    """Map a guest point to a host screen point."""
    return (origin[0] + gx, origin[1] + title_h + gy)


def check_in_bounds(gx, gy, guest_size):
    """Refuse coordinates outside the emulated screen.

    This is the guard that would have prevented clicking into the host's
    browser: an out-of-range guest point maps to a host point that belongs to
    some other application entirely.
    """
    w, h = guest_size
    if not (0 <= gx < w and 0 <= gy < h):
        raise InputError(f"guest point ({gx},{gy}) is outside the {w}x{h} "
                         f"emulated screen — refusing to click outside the emulator")


def modifier_args(hold):
    """Turn 'cmd,shift' into the cliclick key-down/key-up pair."""
    if not hold:
        return [], []
    known = {"cmd": "cmd", "command": "cmd", "shift": "shift",
             "alt": "alt", "option": "alt", "ctrl": "ctrl", "control": "ctrl"}
    keys = []
    for raw in hold.split(","):
        k = raw.strip().lower()
        if not k:
            continue
        if k not in known:
            raise InputError(f"unknown modifier {raw!r} "
                             f"(use: {', '.join(sorted(set(known)))})")
        keys.append(known[k])
    if not keys:
        return [], []
    joined = ",".join(keys)
    return [f"kd:{joined}"], [f"ku:{joined}"]


def build_click(host_pt, count=1, hold=None):
    """cliclick args for a click: move FIRST, then click at the same point.

    The separate move matters — a click at a point the cursor has not reached
    is treated by some classic controls as a click at the OLD location.
    """
    down, up = modifier_args(hold)
    x, y = host_pt
    args = ["m:%d,%d" % (x, y), "w:120"] + down
    args += ["c:%d,%d" % (x, y)] * max(1, int(count))
    return args + up


def build_menu_gesture(title_pt, item_pt):
    """cliclick args for a full menu pull-down in ONE invocation.

    Press on the title, drag to the item, release. Split across invocations the
    menu stays open between them, and a menu that stays open blocks the guest's
    event loop — which starves the background daemon and drops the bridge.
    """
    tx, ty = title_pt
    ix, iy = item_pt
    return ["m:%d,%d" % (tx, ty), "w:150",
            "dd:%d,%d" % (tx, ty), "w:400",
            "m:%d,%d" % (ix, iy), "w:250",
            "du:%d,%d" % (ix, iy)]


def build_capture_region(origin, title_h, guest_size, region=None):
    """screencapture -R rect for the whole guest screen, or a guest sub-rect."""
    if region is None:
        x, y = guest_to_host(origin, title_h, 0, 0)
        return (x, y, guest_size[0], guest_size[1])
    gx, gy, w, h = region
    x, y = guest_to_host(origin, title_h, gx, gy)
    return (x, y, w, h)


def parse_region(text):
    """'gx,gy,w,h' -> tuple of four ints."""
    parts = [p.strip() for p in (text or "").split(",")]
    if len(parts) != 4 or not all(p.lstrip("-").isdigit() for p in parts):
        raise InputError(f"--region wants 'gx,gy,w,h', got {text!r}")
    return tuple(int(p) for p in parts)


# --------------------------------------------------------------------------
# host interaction
# --------------------------------------------------------------------------
# How long any single gesture step may take before it is abandoned. It exists
# for one case: `hold_and_capture` leaves the real mouse button DOWN across a
# screencapture, and the release waits for that capture to return. Unbounded,
# a hung capture leaves the button held — and a held button is a stuck machine
# for the person sitting in front of it, not just for us.
STEP_TIMEOUT = 10


def _run(argv, check=True, timeout=None):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise InputError(f"{argv[0]} did not finish within {timeout}s")
    if check and p.returncode != 0:
        raise InputError(f"{argv[0]} failed: {(p.stderr or p.stdout).strip()}")
    return (p.stdout or "").strip()


def _osascript(script):
    return _run(["osascript", "-e", script])


def running_emulator(explicit=None):
    """Which emulator process to drive."""
    if explicit:
        return explicit
    for name in EMULATORS:
        if subprocess.run(["pgrep", "-x", name],
                          capture_output=True).returncode == 0:
            return name
    raise InputError("no emulator process found (BasiliskII / SheepShaver)")


def frontmost_app():
    return _osascript('tell application "System Events" to get name of first '
                      'process whose frontmost is true')


def front_and_geometry(app):
    """Who is frontmost AND where the emulator window is — in ONE osascript.

    Measured 2026-08-04: a host click costs 1.851 s, of which four separate
    `osascript` invocations account for ~1.03 s. Each pays its own interpreter
    start, so asking both questions in one call removes a whole start (~0.15 s)
    without changing what is asked or when.

    Reading the rect BEFORE the activation is safe — bringing a window forward
    does not move it — and it is still read once per gesture, which is the
    property that matters: the emulator window moved mid-session once, and a
    rect cached ACROSS gestures turns every later click into a silent miss.
    """
    return parse_front_and_geometry(_osascript(
        'tell application "System Events"\n'
        '  set f to name of first process whose frontmost is true\n'
        f'  tell process "{app}"\n'
        '    set p to position of window 1\n'
        '    set s to size of window 1\n'
        '  end tell\n'
        '  return f & "|" & (item 1 of p) & "|" & (item 2 of p) & '
        '"|" & (item 1 of s) & "|" & (item 2 of s)\n'
        'end tell'))


class Session:
    """Geometry + focus handling for one gesture.

    Geometry is read per gesture on purpose: the emulator window moved mid-run
    once already, and a cached origin turns every later click into a silent miss.
    """

    def __init__(self, app=None, activate=True, dry_run=False,
                 keep_front=False):
        self.app = running_emulator(app)
        self.activate = activate
        self.dry_run = dry_run
        # Leave the emulator frontmost instead of handing focus back. OFF by
        # default, and deliberately opt-in: the restore exists because a stray
        # click once landed in the host's browser, and a driver that silently
        # keeps the machine is worse than a slow one. Measured 2026-08-04, the
        # cost of the courtesy is 0.695 s per gesture — 37 % — because handing
        # focus back means the NEXT gesture must take it again (set frontmost,
        # a deliberate 400 ms settle, set back). For a run of gestures that is
        # paid every time; for a single one it is paid once and is the right
        # trade. Hand focus back with `guest_input.py front <app>`.
        self.keep_front = keep_front
        self.previous_front = None
        self._geom = None

    def guest_size(self):
        try:
            with open(PREFS_PATH) as fh:
                return parse_guest_size(fh.read())
        except OSError:
            return DEFAULT_GUEST_SIZE

    def geometry(self):
        """Where the emulator window is — read once per gesture, then reused.

        Once per GESTURE, not once per session-of-many: `__enter__` reads it
        alongside the frontmost check and caches it here, so a menu (which needs
        the rect for both the title and the item) no longer pays for it twice.
        Across gestures it is always re-read; a stale origin is a silent miss.
        """
        if self._geom is not None:
            return self._geom
        out = _osascript(f'tell application "System Events" to tell process '
                         f'"{self.app}" to get {{position, size}} of window 1')
        x, y, w, h = parse_window_geometry(out)
        return self._cache_geometry(x, y, w, h)

    def _cache_geometry(self, x, y, w, h):
        gw, gh = self.guest_size()
        self._geom = {"app": self.app, "origin": (x, y), "window": (w, h),
                      "guest_size": (gw, gh), "title_h": title_bar_height(h, gh)}
        return self._geom

    def __enter__(self):
        # One osascript for both questions; see front_and_geometry().
        front, rect = front_and_geometry(self.app)
        self._cache_geometry(*rect)
        if front != self.app:
            if not self.activate:
                raise InputError(f"{self.app} is not frontmost ({front!r} is) — "
                                 f"refusing to click into another application")
            self.previous_front = front
            if not self.dry_run:
                _osascript('tell application "System Events" to set frontmost '
                           f'of process "{self.app}" to true')
                # Let the switch land before the first synthetic motion.
                _run(["cliclick", "w:400"])
        return self

    def __exit__(self, *exc):
        if self.previous_front and not self.dry_run and not self.keep_front:
            _osascript('tell application "System Events" to set frontmost of '
                       f'process "{self.previous_front}" to true')
        return False

    def cliclick(self, args, timeout=None):
        if self.dry_run:
            print("cliclick " + " ".join(args))
            return ""
        return _run(["cliclick"] + args, timeout=timeout)

    def point(self, gx, gy):
        g = self.geometry()
        check_in_bounds(gx, gy, g["guest_size"])
        return guest_to_host(g["origin"], g["title_h"], gx, gy)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_geometry(args):
    g = Session(args.app, dry_run=True).geometry()
    print(f"app:        {g['app']}")
    print(f"origin:     {g['origin'][0]},{g['origin'][1]}")
    print(f"window:     {g['window'][0]}x{g['window'][1]}")
    print(f"guest:      {g['guest_size'][0]}x{g['guest_size'][1]}")
    print(f"title bar:  {g['title_h']} px")
    print(f"front app:  {frontmost_app()}")
    print(f"mapping:    host = ({g['origin'][0]} + gx, "
          f"{g['origin'][1] + g['title_h']} + gy)")
    return 0


def cmd_move(args):
    with Session(args.app, not args.no_activate, args.dry_run,
                 keep_front=args.keep_front) as s:
        s.cliclick(["m:%d,%d" % s.point(args.x, args.y)])
    return 0


def cmd_click(args):
    with Session(args.app, not args.no_activate, args.dry_run,
                 keep_front=args.keep_front) as s:
        s.cliclick(build_click(s.point(args.x, args.y), args.count, args.hold))
    return 0


def cmd_menu(args):
    with Session(args.app, not args.no_activate, args.dry_run,
                 keep_front=args.keep_front) as s:
        g = s.geometry()
        check_in_bounds(args.title_x, args.title_y, g["guest_size"])
        check_in_bounds(args.item_x, args.item_y, g["guest_size"])
        title = guest_to_host(g["origin"], g["title_h"], args.title_x, args.title_y)
        item = guest_to_host(g["origin"], g["title_h"], args.item_x, args.item_y)
        s.cliclick(build_menu_gesture(title, item))
    return 0


def cmd_front(args):
    """Hand focus back to a named application.

    The counterpart to --keep-front, and the reason that flag is safe to offer:
    a driver that can take the machine must have a one-liner that gives it back.
    """
    app = args.name
    _osascript('tell application "System Events" to set frontmost of process '
               f'"{app}" to true')
    print(f"front app:  {frontmost_app()}")
    return 0


def to_guest_scale(out_path, want_w, want_h, run=None):
    """Resample a host capture down to the size it was ASKED for. -> (w, h) or None.

    A capture of a 1024x768 guest area on a 2x display comes back 2048x1536.
    Those extra pixels carry no extra information — the emulator maps one guest
    pixel to a 2x2 block — so they are four times the bytes for the same
    picture, and 4.2 MB of them travelled over the control port in one reply.

    The size is not the main reason, though. This surface's whole convention is
    that **the pixels of a capture ARE guest coordinates, 1:1** — that is what
    lets a caller read a target off an image and pass it straight back. A 2x
    capture quietly breaks that for every host-side picture while `mac_screenshot`
    keeps it, so the two sources of "a picture of the guest" would disagree
    about what a coordinate means. Resampling restores one rule for both.

    Conditional on the measurement, never on an assumption about Retina: if the
    file already has the requested size (a 1x display), nothing is resampled.
    """
    runner = run or _run
    try:
        got = runner(["sips", "-g", "pixelWidth", "-g", "pixelHeight", out_path])
        nums = [int(n) for n in re.findall(r"pixel(?:Width|Height):\s*(\d+)", got)]
        if len(nums) < 2:
            return None
        have_w, have_h = nums[0], nums[1]
        if (have_w, have_h) == (want_w, want_h):
            return (have_w, have_h)
        runner(["sips", "--resampleHeightWidth", str(want_h), str(want_w), out_path])
        return (want_w, want_h)
    except InputError:
        # A failed resample is not a failed capture: the picture is still there,
        # just bigger than asked for. Losing it over a cosmetic step would be
        # the worse trade.
        return None


def capture(out_path, region=None, app=None, dry_run=False):
    """Capture the guest screen HOST-side into out_path; returns the path.

    Deliberately not the bridge's screenshot: the daemon cannot answer while a
    menu or modal dialog owns the machine, which is exactly when a picture is
    needed to find the next coordinate. `region` is in GUEST coordinates.
    """
    g = Session(app, activate=False, dry_run=dry_run).geometry()
    x, y, w, h = build_capture_region(g["origin"], g["title_h"],
                                      g["guest_size"], region)
    _capture_rect(x, y, w, h, out_path, dry_run)
    return out_path


def _capture_rect(x, y, w, h, out_path, dry_run=False, timeout=None):
    """screencapture of one rect in POINTS, brought back to that size in PIXELS.

    `-R` is measured to take points and return pixels (2026-08-04, both ways on
    a 2x display), which is why the resample target is the requested w/h and not
    something read back off the screen.
    """
    argv = ["screencapture", "-x", f"-R{x},{y},{w},{h}", out_path]
    if dry_run:
        print(" ".join(argv))
        return out_path
    _run(argv, timeout=timeout)
    to_guest_scale(out_path, w, h)
    return out_path


def hold_and_capture(session, title_pt, out_path, region=None, dwell_ms=250,
                     over_pt=None):
    """Press-and-HOLD the real mouse on a menu title, capture the open menu
    HOST-side, then ALWAYS release.

    `over_pt` drags to a further point WHILE HELD before capturing. That is what
    a HIERARCHICAL item needs: a submenu is not drawn when its parent menu opens,
    only once the pointer rests on the parent item, so holding at the title alone
    photographs a submenu that is never there — and reads exactly like a submenu
    that is empty. (THINK's `Switch To Project` is the case that motivated it:
    the resource says it is hierarchical, submenu 104, and 104 exists only at
    runtime.) The release still happens at the TITLE, never at `over_pt`: coming
    back to the title selects nothing, whereas releasing on the item would pick
    it — a measurement must not also be a click.

    This is the one place the module deliberately does what `menu` refuses to:
    it leaves the button DOWN across a screencapture. That is required to READ a
    menu whose contents cannot be known in advance -- the Application menu's list
    of running processes is the case that motivated it, and `menu`/HOSTMENU cannot
    serve it because they want the item coordinate up front. The capture is
    host-side for the same reason `shot` is: while the menu is held the guest's
    event loop (and the background daemon) is starved, so only a picture taken off
    the daemon's path can be read during that window. The release lives in a
    `finally` -- a held button that leaks is a stuck machine, and that must never
    depend on the capture succeeding.
    """
    tx, ty = title_pt
    # cliclick leaves the button DOWN when it exits after `dd` (the difference
    # from `c`), so the menu stays open for the capture below. The trailing wait
    # is the menu's render time, paid inside this one invocation.
    steps = ["m:%d,%d" % (tx, ty), "w:150",
             "dd:%d,%d" % (tx, ty), "w:%d" % max(0, int(dwell_ms))]
    if over_pt is not None:
        # One invocation, still: the drag and the dwell must not be split across
        # two cliclick calls, because every gap is time the guest spends inside a
        # tracking loop with the daemon starved.
        steps += ["m:%d,%d" % (over_pt[0], over_pt[1]),
                  "w:%d" % max(0, int(dwell_ms))]
    session.cliclick(steps)
    try:
        g = session.geometry()
        x, y, w, h = build_capture_region(g["origin"], g["title_h"],
                                          g["guest_size"], region)
        # Bounded: the release below waits for this to return, so an unbounded
        # capture would hold the mouse button for as long as it hangs. The
        # `finally` guarantees the release RUNS; the timeout is what guarantees
        # it runs SOON. Same helper as `capture`, so a held-menu picture obeys
        # the same 1:1 guest-coordinate rule as every other picture here.
        _capture_rect(x, y, w, h, out_path, session.dry_run, STEP_TIMEOUT)
    finally:
        session.cliclick(["du:%d,%d" % (tx, ty)], timeout=STEP_TIMEOUT)
    return out_path


def cmd_shot(args):
    region = parse_region(args.region) if args.region else None
    out = capture(args.out or "guest.png", region, args.app, args.dry_run)
    if not args.dry_run:
        print(out)
    return 0


def cmd_menushot(args):
    region = parse_region(args.region) if args.region else None
    with Session(args.app, not args.no_activate, args.dry_run,
                 keep_front=args.keep_front) as s:
        title = s.point(args.title_x, args.title_y)
        over = None
        if args.over:
            ox, oy = (int(v) for v in args.over.split(","))
            over = s.point(ox, oy)
        out = hold_and_capture(s, title, args.out or "menu.png", region,
                               args.dwell, over)
    if not args.dry_run:
        print(out)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--app", help="emulator process name")
    p.add_argument("--no-activate", action="store_true",
                   help="abort instead of bringing the emulator forward")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--keep-front", action="store_true",
                   help="leave the emulator frontmost instead of handing focus "
                        "back (saves ~0.7s on EVERY following gesture; give it "
                        "back with `guest_input.py front <app>`)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("geometry").set_defaults(func=cmd_geometry)

    m = sub.add_parser("move"); m.add_argument("x", type=int)
    m.add_argument("y", type=int); m.set_defaults(func=cmd_move)

    c = sub.add_parser("click"); c.add_argument("x", type=int)
    c.add_argument("y", type=int)
    c.add_argument("--count", type=int, default=1)
    c.add_argument("--hold", help="modifiers held during the click, e.g. cmd,shift")
    c.set_defaults(func=cmd_click)

    mn = sub.add_parser("menu")
    for name in ("title_x", "title_y", "item_x", "item_y"):
        mn.add_argument(name, type=int)
    mn.set_defaults(func=cmd_menu)

    fr = sub.add_parser("front", help="hand focus back to an application")
    fr.add_argument("name")
    fr.set_defaults(func=cmd_front)

    sh = sub.add_parser("shot"); sh.add_argument("out", nargs="?")
    sh.add_argument("--region", help="guest sub-rect 'gx,gy,w,h'")
    sh.set_defaults(func=cmd_shot)

    ms = sub.add_parser("menushot",
                        help="hold a menu open and capture it (reads menus "
                             "whose items you cannot know in advance)")
    ms.add_argument("title_x", type=int)
    ms.add_argument("title_y", type=int)
    ms.add_argument("out", nargs="?")
    ms.add_argument("--region", help="guest sub-rect 'gx,gy,w,h' to capture")
    ms.add_argument("--dwell", type=int, default=250,
                    help="ms to hold before capturing (menu render time)")
    ms.add_argument("--over", metavar="GX,GY",
                    help="drag onto this guest point while held, before "
                         "capturing — required to reveal a HIERARCHICAL item's "
                         "submenu, which is not drawn until the pointer rests "
                         "on the parent item")
    ms.set_defaults(func=cmd_menushot)

    args = p.parse_args(argv)
    if not args.dry_run and args.cmd != "shot" and not shutil.which("cliclick"):
        print("guest_input: cliclick not found on PATH (brew install cliclick)",
              file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except InputError as e:
        print(f"guest_input: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
