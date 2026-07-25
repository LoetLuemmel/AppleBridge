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
def _run(argv, check=True):
    p = subprocess.run(argv, capture_output=True, text=True)
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


class Session:
    """Geometry + focus handling for one gesture.

    Geometry is read per gesture on purpose: the emulator window moved mid-run
    once already, and a cached origin turns every later click into a silent miss.
    """

    def __init__(self, app=None, activate=True, dry_run=False):
        self.app = running_emulator(app)
        self.activate = activate
        self.dry_run = dry_run
        self.previous_front = None

    def guest_size(self):
        try:
            with open(PREFS_PATH) as fh:
                return parse_guest_size(fh.read())
        except OSError:
            return DEFAULT_GUEST_SIZE

    def geometry(self):
        out = _osascript(f'tell application "System Events" to tell process '
                         f'"{self.app}" to get {{position, size}} of window 1')
        x, y, w, h = parse_window_geometry(out)
        gw, gh = self.guest_size()
        return {"app": self.app, "origin": (x, y), "window": (w, h),
                "guest_size": (gw, gh), "title_h": title_bar_height(h, gh)}

    def __enter__(self):
        front = frontmost_app()
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
        if self.previous_front and not self.dry_run:
            _osascript('tell application "System Events" to set frontmost of '
                       f'process "{self.previous_front}" to true')
        return False

    def cliclick(self, args):
        if self.dry_run:
            print("cliclick " + " ".join(args))
            return ""
        return _run(["cliclick"] + args)

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
    with Session(args.app, not args.no_activate, args.dry_run) as s:
        s.cliclick(["m:%d,%d" % s.point(args.x, args.y)])
    return 0


def cmd_click(args):
    with Session(args.app, not args.no_activate, args.dry_run) as s:
        s.cliclick(build_click(s.point(args.x, args.y), args.count, args.hold))
    return 0


def cmd_menu(args):
    with Session(args.app, not args.no_activate, args.dry_run) as s:
        g = s.geometry()
        check_in_bounds(args.title_x, args.title_y, g["guest_size"])
        check_in_bounds(args.item_x, args.item_y, g["guest_size"])
        title = guest_to_host(g["origin"], g["title_h"], args.title_x, args.title_y)
        item = guest_to_host(g["origin"], g["title_h"], args.item_x, args.item_y)
        s.cliclick(build_menu_gesture(title, item))
    return 0


def cmd_shot(args):
    """Capture the guest screen HOST-side.

    Deliberately not the bridge's screenshot: the daemon cannot answer while a
    menu or modal dialog owns the machine, which is exactly when a picture is
    needed to find the next coordinate.
    """
    s = Session(args.app, activate=False, dry_run=args.dry_run)
    g = s.geometry()
    region = parse_region(args.region) if args.region else None
    x, y, w, h = build_capture_region(g["origin"], g["title_h"],
                                      g["guest_size"], region)
    out = args.out or "guest.png"
    argv = ["screencapture", "-x", f"-R{x},{y},{w},{h}", out]
    if args.dry_run:
        print(" ".join(argv))
        return 0
    _run(argv)
    print(out)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--app", help="emulator process name")
    p.add_argument("--no-activate", action="store_true",
                   help="abort instead of bringing the emulator forward")
    p.add_argument("--dry-run", action="store_true")
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

    sh = sub.add_parser("shot"); sh.add_argument("out", nargs="?")
    sh.add_argument("--region", help="guest sub-rect 'gx,gy,w,h'")
    sh.set_defaults(func=cmd_shot)

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
