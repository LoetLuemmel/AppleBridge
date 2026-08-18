#!/usr/bin/env python3
"""Which preferences file does the emulator on THIS machine read?

Why this exists
---------------
`~/.basilisk_ii_prefs` was a constant in four modules — `install_bridge.py`,
`bridge_doctor.py`, `guest_input.py`, `bench_transport.py` — and **no
SheepShaver prefs path appeared anywhere in the repository**, although the
bridge has been validated on SheepShaver since 2026-07-03.

The failure that hides behind that is this project's worst shape, and it is
self-verifying: on a SheepShaver host `install_bridge.py` writes `ether slirp`
into a file the running emulator never reads, then confirms the write by
reading **the same file** back, and reports a correctly configured backend.
The check passes because it is reading what the step just wrote, not what the
emulator uses. Nothing anywhere says the backend never changed.

Surfaced 2026-08-17 by a forum reader running SheepShaver on Linux
(emaculation t=12754) — a Linux report of a defect that was never about Linux.

What this module refuses to do
------------------------------
Guess in silence. Every answer carries `emulator`, `source` and — where two
emulators are installed — `ambiguous`, so a caller can say *which* file it
read rather than presenting one of two plausible files as fact. The old
constant was never wrong about Basilisk; it was wrong about being sure.

Order of evidence, most authoritative first:

  1. a **running** emulator process — it is using its prefs right now;
  2. the discovered emulator **bundle/executable**, which names the program;
  3. exactly **one** prefs file present on disk;
  4. both present -> the **more recently modified** one, flagged ambiguous;
  5. neither -> Basilisk's path as the default, and `source` says so, because
     a file that does not exist yet still has to be named to be created.

Stdlib only, and every filesystem call is injectable, so the whole table is
driven from canned state in the tests with no real home directory involved.
"""

import os

BASILISK = "basilisk"
SHEEPSHAVER = "sheepshaver"

# The two names the Unix and macOS builds actually use. Basilisk keeps the
# same name on both platforms; SheepShaver has always had its own file, which
# is precisely the one nothing here used to look for.
PREFS_NAMES = {
    BASILISK: ".basilisk_ii_prefs",
    SHEEPSHAVER: ".sheepshaver_prefs",
}

# The sidecar `start_stack.sh` writes to record the INTENDED backend. It has
# to follow the prefs file it belongs to, or a SheepShaver host records its
# intent beside a Basilisk file and the drift check compares two machines.
NETMODE_SUFFIX = ".netmode"

DEFAULT = BASILISK


def prefs_path(emulator, home=None):
    """-> the absolute prefs path for one emulator, without touching the disk."""
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, PREFS_NAMES[emulator])


def netmode_path(path):
    """-> the `.netmode` sidecar beside a given prefs file."""
    return path + NETMODE_SUFFIX


def _from_processes(processes):
    """A running emulator beats every other signal — it holds the file open."""
    if not processes:
        return None
    # Checked in a fixed order so a machine running BOTH (which this project
    # has warned against since the two-emulator note) is at least deterministic
    # rather than dependent on dict ordering.
    for name in (SHEEPSHAVER, BASILISK):
        if processes.get(name):
            return name
    return None


def _from_bundle(bundle):
    """The discovered emulator names the program, even when it is not running.

    Matched on the *path*, which is what discovery records. Deliberately
    case-insensitive and substring-based: the executable is `SheepShaver` in a
    bundle, `sheepshaver` in a distro package, and `SheepShaver.AppImage` on a
    Linux desktop, and all three mean the same program.
    """
    app = (bundle or {}).get("app") if isinstance(bundle, dict) else bundle
    if not app:
        return None
    low = str(app).lower()
    if "sheepshaver" in low:
        return SHEEPSHAVER
    if "basilisk" in low:
        return BASILISK
    return None


def resolve(processes=None, bundle=None, home=None, exists=None, getmtime=None):
    """-> {emulator, path, netmode, source, ambiguous, present}

    `present` lists every emulator whose prefs file exists, so a caller can
    report the road not taken instead of pretending there was only one.
    """
    exists = exists or os.path.exists
    getmtime = getmtime or os.path.getmtime

    paths = {name: prefs_path(name, home) for name in PREFS_NAMES}
    present = sorted(n for n, p in paths.items() if exists(p))

    def answer(emulator, source, ambiguous=False):
        return {
            "emulator": emulator,
            "path": paths[emulator],
            "netmode": netmode_path(paths[emulator]),
            "source": source,
            "ambiguous": ambiguous,
            "present": present,
        }

    running = _from_processes(processes)
    if running:
        return answer(running, "running process")

    named = _from_bundle(bundle)
    if named:
        return answer(named, "discovered emulator")

    if len(present) == 1:
        return answer(present[0], "the only prefs file on this machine")

    if len(present) > 1:
        # Both installed and neither running: the newest edit is the best
        # available evidence, and it is evidence rather than proof — so the
        # flag travels with the answer instead of being resolved away here.
        newest = max(present, key=lambda n: _mtime(getmtime, paths[n]))
        return answer(newest, "most recently modified of two", ambiguous=True)

    return answer(DEFAULT, "default (no prefs file exists yet)")


def _mtime(getmtime, path):
    try:
        return getmtime(path)
    except OSError:
        return -1.0


def describe(resolved):
    """One line naming the file and why it, for output a reader can check."""
    line = f"{resolved['path']}  ({resolved['emulator']}, {resolved['source']})"
    if resolved.get("ambiguous"):
        other = [n for n in resolved.get("present", []) if n != resolved["emulator"]]
        if other:
            line += f" — AMBIGUOUS: {', '.join(other)} also installed"
    return line
