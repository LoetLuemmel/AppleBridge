"""Let macOS take the screenshot, because we are not allowed to.

Measured 2026-08-05, three ways, on an unlocked screen with every grant in
place: a background process — the launchd host server, or an ssh session — gets
`screencapture` output containing the desktop picture and **no windows**. It is
not a permission problem (path and bundle grants were verified in the TCC
database and a logout changed nothing); a process with no window server
connection simply cannot see window content.

The way around it is not to gain the right but to stop needing it: **the system
hotkey Cmd-Shift-3 is served by macOS's own screenshot component**, which has
the rights we lack. A background process can post that keystroke — input
reaches the visible session even though capture does not, an asymmetry measured
the same day — and the resulting file is a real screenshot with real windows.

So this module does not photograph anything. It asks the system to, waits for
the file, crops it to the emulator window, and puts it where the guest can read
it — `Unix:Screenshots` on the guest side, which is the shared folder. The
capture needs privileges; cropping and moving do not.

Ownership needs no fixing: the file arrives as the invoking user, the shared
folder belongs to the same user, and the guest reads it through the emulator
running as that user. Checked rather than assumed, because a `chown` nobody
needs is a privileged step for decoration.

stdlib only, and `sips`/`cliclick`/`osascript` from the system.
"""

import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guest_input  # noqa: E402

# Where macOS drops screenshots. Set once with
#   defaults write com.apple.screencapture location <dir>
# and it is the SHARED folder on purpose: the guest sees it as `Unix:Screenshots`,
# so a remote session fetches the picture over the bridge with READFILE instead
# of needing anything host-side at all.
# Two directories, because two processes with different rights are involved.
#
# SHOT_DIR is where macOS is told to drop the file and where WE read it. It is
# deliberately NOT under ~/Desktop: that folder is TCC-protected, and a
# background agent listing it gets `[Errno 1] Operation not permitted`. The
# system component may write there; we may not look. Measured 2026-08-05, and
# only after the first version of `newest()` stopped swallowing the error and
# claiming the folder was empty.
#
# SHARE_DIR is the emulator's shared folder — `Unix:Screenshots` on the guest
# side. Copying the finished picture there is what lets a REMOTE session fetch
# it over the bridge with READFILE, needing nothing host-side. Best effort: if
# the agent may not write there either, the picture still goes back over the
# wire and the reason is reported rather than hidden.
SHOT_DIR = os.path.expanduser("~/.applebridge/shots")
SHARE_DIR = os.path.expanduser("~/Desktop/Share/Screenshots")
SHARE_SHOTS = SHOT_DIR          # kept: older callers name this

# How long to wait for the system to write the file. Measured: it appears within
# about two seconds, sometimes later under load — the first attempt at this used
# a three-second window, saw nothing, and concluded the keystroke had failed.
# It had not; the file arrived afterwards. Hence a generous bound and a poll.
SETTLE_S = 12.0


def configured_location(run=None):
    """Where macOS is currently told to put screenshots (empty => Desktop)."""
    runner = run or _sh
    out = runner(["defaults", "read", "com.apple.screencapture", "location"],
                 check=False)
    return (out or "").strip()


def _sh(argv, check=True):
    p = subprocess.run(argv, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{argv[0]} failed: {(p.stderr or p.stdout).strip()}")
    return p.stdout or ""


def pixel_size(path, run=None):
    """(w, h) of an image in PIXELS, via sips."""
    out = (run or _sh)(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path])
    nums = [int(n) for n in re.findall(r"pixel(?:Width|Height):\s*(\d+)", out)]
    if len(nums) < 2:
        raise RuntimeError(f"could not read the size of {path}")
    return nums[0], nums[1]


def screen_points(run=None):
    """(w, h) of the main display in POINTS.

    Needed because `screencapture` reports PIXELS while every coordinate this
    project uses — System Events geometry, `-R` rectangles — is in POINTS. On a
    2x display the two differ by exactly the factor this pair establishes, and
    guessing "it is Retina" would be wrong on the machines that are not.
    """
    out = (run or _sh)(["osascript", "-e",
                        'tell application "Finder" to get bounds of window of desktop'])
    nums = [int(n) for n in re.findall(r"-?\d+", out)]
    if len(nums) < 4:
        raise RuntimeError(f"could not read the desktop bounds: {out!r}")
    return nums[2] - nums[0], nums[3] - nums[1]


def newest(directory):
    """The files in `directory`, as a set of paths.

    It does NOT swallow the error, and that is the whole point. The first
    version returned an empty set on any OSError, which turned "I am not allowed
    to read this folder" into "the folder is empty" — and the caller then
    reported that no screenshot had appeared, twelve seconds after one had. The
    file was there, written at 10:20:36 inside a window that ran to 10:20:43.
    An hour of this day went into the same shape elsewhere; it is not going into
    the handler written to close it.
    """
    names = [os.path.join(directory, n) for n in os.listdir(directory)
             if not n.startswith(".")]
    return set(names)


def wait_for_new(directory, before, deadline, poll=0.4):
    """The file that appeared since `before`, or None.

    Waits rather than checks once: the system writes asynchronously, and a
    single look a few seconds later reports "nothing happened" for something
    that is merely still happening.
    """
    while time.time() < deadline:
        fresh = newest(directory) - before
        if fresh:
            # Newest by mtime, in case several arrived.
            return max(fresh, key=lambda p: os.path.getmtime(p))
        time.sleep(poll)
    return None


def crop_to(path, rect_points, scale, run=None):
    """Crop `path` in place to a POINT rectangle, scaled to pixels."""
    x, y, w, h = rect_points
    px, py = int(x * scale), int(y * scale)
    pw, ph = int(w * scale), int(h * scale)
    (run or _sh)(["sips", "-c", str(ph), str(pw),
                  "--cropOffset", str(py), str(px), path])
    return path


def capture(out_name="basilisk.png", app=None, shots_dir=None, full=False,
            require_front=True):
    """Ask the system for a screenshot of the emulator window. -> path.

    `full` keeps the whole screen; otherwise the picture is cropped to the
    emulator window, which is both smaller and the only part anybody wants.
    The result is left in the shared folder under a DETERMINISTIC name, so the
    guest can fetch `Unix:Screenshots:<out_name>` without first having to
    discover what the system called it (`Bildschirmfoto 2026-08-05 um 10.17.02`
    is not a name a remote caller can predict).
    """
    directory = shots_dir or SHARE_SHOTS
    os.makedirs(directory, exist_ok=True)

    # Front check and geometry in ONE osascript, and the check is the point.
    #
    # We photograph the full screen and CROP to the emulator's rectangle, so
    # anything lying on top of that rectangle ends up in the picture — silently,
    # and looking exactly like a real capture. macOS's window mode would be
    # immune to that, but it returns the window WITH its title bar and shadow,
    # which breaks the rule this whole surface rests on: the pixels of a capture
    # ARE guest coordinates, 1:1. `mac_screenshot` keeps that rule, and two ways
    # of photographing one guest must not disagree about what a coordinate means.
    #
    # A frontmost emulator cannot have anything above it, so the check buys the
    # same robustness without touching the coordinates — and when it fails it
    # REFUSES rather than returning a plausible picture of the wrong thing.
    resolved = guest_input.running_emulator(app)
    front, window = guest_input.front_and_geometry(resolved)
    if require_front and front != resolved:
        raise RuntimeError(
            f"{resolved} is not frontmost ({front!r} is) — refusing, because the "
            "picture would be cropped to the emulator's rectangle and show "
            "whatever is lying on top of it")
    session = guest_input.Session(resolved, activate=False, dry_run=False)
    session._cache_geometry(*window)
    geom = session.geometry()          # POINTS; works from a background process
    gx, gy = geom["origin"]
    gw, gh = geom["guest_size"]
    rect = (gx, gy + geom["title_h"], gw, gh)

    before = newest(directory)
    # The system hotkey. cliclick, not osascript: cliclick already drives this
    # desktop from the background agent (measured), while System Events
    # scripting would need an Automation grant a faceless process cannot be
    # prompted for.
    down, up = guest_input.modifier_args("cmd,shift")
    _sh(["cliclick"] + down + ["t:3"] + up)

    shot = wait_for_new(directory, before, time.time() + SETTLE_S)
    if shot is None:
        # Say where macOS is ACTUALLY told to put them, rather than asking the
        # reader to go and look. The failure this replaces was exactly a
        # mismatch between where we watched and where the file went.
        where = configured_location() or "(unset — the Desktop)"
        raise RuntimeError(
            f"no screenshot appeared in {directory} within {SETTLE_S:g}s; macOS "
            f"is configured to write them to {where}")

    if not full:
        pw, _ = pixel_size(shot)
        sw, _ = screen_points()
        scale = (pw / sw) if sw else 1.0
        crop_to(shot, rect, scale)

    final = os.path.join(directory, out_name)
    if os.path.abspath(final) != os.path.abspath(shot):
        if os.path.exists(final):
            os.remove(final)
        os.rename(shot, final)

    # Publish into the shared folder so the GUEST can read it. Best effort, and
    # loud about failing: this is the step whose silent failure cost an hour.
    share_path, share_why = None, None
    try:
        os.makedirs(SHARE_DIR, exist_ok=True)
        share_path = os.path.join(SHARE_DIR, out_name)
        with open(final, "rb") as src, open(share_path, "wb") as dst:
            dst.write(src.read())
    except OSError as exc:
        share_path, share_why = None, f"{exc}"
    return final, share_path, share_why


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "basilisk.png"
    full = "--full" in sys.argv
    path, share, why = capture(name, full=full)
    print(f"host : {path}")
    if share:
        print(f"guest: Unix:Screenshots:{os.path.basename(share)}")
    else:
        print(f"guest: NICHT abgelegt — {why}")
