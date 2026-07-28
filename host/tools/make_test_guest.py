#!/usr/bin/env python3
"""Make a guest that has never heard of AppleBridge, for testing an install.

Why this exists
---------------
`install_bridge.py --export-guest-kit` assembles a folder the guest installs
itself from, and nothing had ever installed *from* one: the payload was verified
at the destination, but the end-to-end run needs a machine with no AppleBridge
on it. Every emulator here already has one.

So: copy a working image, strip AppleBridge out of the COPY, and write a
separate Basilisk config pointing at it. The original is never opened for
writing — the copy is made first and only the copy is touched, which is the
whole safety argument. A tool that strips in place would be one bad path away
from destroying somebody's System 7 install.

    host/tools/make_test_guest.py --dry-run
    host/tools/make_test_guest.py
    BasiliskII --config ~/.basilisk_ii_prefs_test

Then inside that guest: open `Unix:AppleBridge Kit` and run
`AppleBridgeInstaller`. The host log shows the daemon connecting if it worked.

What "pristine" means here
--------------------------
The three things an install leaves behind, and nothing else:

  * the install folder (`:AppleBridge:`) with the suite in it
  * `:System Folder:Preferences:AppleBridge Prefs`
  * the Startup Items alias the watchdog makes for itself

The MPW build tree is deliberately LEFT: a stranger's machine would not have it,
but removing it would also remove the toolchain, and this image is the only
place the binaries come from. A guest that cannot build is a different test.

Only one emulator may run at a time — the host serves a single daemon on :9000 —
so this refuses while one is up, and you must stop the other before booting the
test machine.
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # host/
import bridge_doctor  # noqa: E402

PREFS = os.path.expanduser("~/.basilisk_ii_prefs")
TEST_PREFS = os.path.expanduser("~/.basilisk_ii_prefs_test")

# Exactly what an install leaves behind. Anything not on this list stays.
INSTALL_FOLDER = ":AppleBridge:"
GUEST_PREFS = ":System Folder:Preferences:AppleBridge Prefs"
STARTUP_ITEM = ":System Folder:Startup Items:AppleBridge Watchdog"


def run(argv, timeout=600):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return f"<<{e}>>"


def emulator_running():
    p = bridge_doctor.probe_processes(run)
    return bool(p["basilisk"] or p["sheepshaver"])


def source_image(prefs_path=PREFS):
    emu = bridge_doctor.probe_emulator_prefs(bridge_doctor._read, prefs_path,
                                             prefs_path + ".netmode")
    for d in emu.get("disks", []):
        if os.path.exists(d):
            return d, emu
    return None, emu


def _present(run, path):
    """Is `path` on the mounted volume? Not "did hls print something".

    run() returns stdout AND stderr, and hls announces a missing path on stderr
    — so "output is non-empty" was true for BOTH outcomes, and the verification
    reported everything as still present while the strip had in fact worked
    perfectly. A check that returns the opposite of the truth is worse than no
    check, and this one was guarding the claim the whole tool exists to make.
    """
    out = run(["hls", path])
    return bool(out.strip()) and "no such file" not in out.lower()


def strip_applebridge(image, run=run):
    """Remove the three things an install leaves. -> (ok, [what happened]).

    Deliberately tolerant: a machine that never had AppleBridge is the goal, so
    "it was not there" is success, not an error. What must NOT be tolerated is
    the folder surviving — that would make a later "it installed!" meaningless.
    """
    notes = []
    out = run(["hmount", image])
    if "Volume" not in out:
        return False, [f"hmount failed: {out.strip()[:160]}"]
    try:
        listing = run(["hls", "-l", INSTALL_FOLDER])
        removed = 0
        for line in listing.splitlines():
            # `f  APPL/ABrg  41696  0  Jun 30 12:55  AB.old4` — EIGHT tokens,
            # the name last and possibly containing spaces (hls quotes those).
            # Counting nine skipped almost every file, and the verification
            # below is the only reason that did not pass as a clean strip.
            parts = line.split(None, 7)
            if len(parts) == 8 and parts[0] == "f":
                name = parts[7].strip().strip("'")
                run(["hdel", INSTALL_FOLDER + name])
                removed += 1
        if removed:
            notes.append(f"removed {removed} file(s) from {INSTALL_FOLDER}")
        run(["hrmdir", INSTALL_FOLDER])
        for path, label in ((GUEST_PREFS, "guest prefs"),
                            (STARTUP_ITEM, "Startup Items alias")):
            before = _present(run, path)
            run(["hdel", path])
            if before and not _present(run, path):
                notes.append(f"removed the {label}")
            elif not before:
                notes.append(f"no {label} to remove")

        # Verify, rather than trust the deletes. A test image that still has a
        # daemon on it would make the whole exercise report a false success.
        leftovers = [p for p in (INSTALL_FOLDER, GUEST_PREFS, STARTUP_ITEM)
                     if _present(run, p)]
        if leftovers:
            return False, notes + [f"STILL PRESENT: {', '.join(leftovers)}"]
        notes.append("verified: no install folder, no prefs, no startup item")
        return True, notes
    finally:
        run(["humount"])


def write_test_config(source_prefs, image, dest=TEST_PREFS):
    """A Basilisk config for the test machine: the copy, same everything else.

    Same `extfs` on purpose — the guest kit is delivered through the shared
    folder, so the test machine must see the same one. `ether slirp` because
    that is the shipping branch (D-019).
    """
    lines = []
    for line in (bridge_doctor._read(source_prefs) or "").splitlines():
        if line.startswith("disk "):
            continue
        if line.startswith("ether "):
            continue
        lines.append(line)
    lines.insert(0, f"disk {image}")
    lines.insert(1, "ether slirp")
    body = "\n".join(lines).rstrip() + "\n"
    with open(dest, "w") as fh:
        fh.write(body)
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="make_test_guest.py",
        description="Copy a working guest image and strip AppleBridge from the "
                    "COPY, so an install can be tested end to end.",
        epilog="The source image is never written to. Exit 0 ok, 1 failed, "
               "2 bad arguments, 3 refused.")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would happen; copy and change nothing")
    ap.add_argument("--dest", default=None,
                    help="path for the test image (default: alongside the "
                         "source, named …-pristine.dmg)")
    ap.add_argument("--keep", action="store_true",
                    help="reuse an existing test image instead of re-copying")
    args = ap.parse_args(argv)

    if emulator_running():
        print("REFUSED: an emulator is running. Copying a live image gives a "
              "torn filesystem, and only one guest may hold the :9000 slot "
              "anyway. Stop it first (mac_shutdown, or Special > Shut Down).",
              file=sys.stderr)
        return 3

    src, emu = source_image()
    if not src:
        print(f"REFUSED: no readable disk image in {PREFS}", file=sys.stderr)
        return 3
    dest = args.dest or os.path.join(os.path.dirname(src),
                                     os.path.splitext(os.path.basename(src))[0]
                                     + "-pristine.dmg")

    free = shutil.disk_usage(os.path.dirname(dest)).free
    need = os.path.getsize(src)
    print(f"source image : {src}  ({need / 1e9:.1f} GB)")
    print(f"test image   : {dest}")
    print(f"free space   : {free / 1e9:.1f} GB")
    print(f"shared folder: {emu.get('shared_folder') or '— none configured —'}")
    if free < need * 1.05 and not (args.keep and os.path.exists(dest)):
        print("REFUSED: not enough free space for the copy.", file=sys.stderr)
        return 3

    if args.dry_run:
        print("\nDRY RUN — nothing copied, nothing changed. Would:")
        print(f"  1. copy the image to {dest}")
        print(f"  2. remove {INSTALL_FOLDER}, {GUEST_PREFS} and the Startup "
              "Items alias FROM THE COPY, then verify they are gone")
        print(f"  3. write {TEST_PREFS} pointing at the copy (ether slirp, "
              "same extfs so the kit is visible)")
        print(f"\nThen: BasiliskII --config {TEST_PREFS}")
        return 0

    if args.keep and os.path.exists(dest):
        print("\nreusing the existing test image (--keep)")
    else:
        print("\ncopying… (a few minutes for 2 GB)")
        shutil.copy2(src, dest)
    print(f"copied {os.path.getsize(dest) / 1e9:.1f} GB")

    ok, notes = strip_applebridge(dest)
    for n in notes:
        print(f"  {n}")
    if not ok:
        print("FAILED to make the copy pristine — not writing a config for it.",
              file=sys.stderr)
        return 1

    cfg = write_test_config(PREFS, dest)
    print(f"\nwrote {cfg}")
    print("\nA machine that has never heard of AppleBridge. To use it:")
    print(f"  1. stop any running guest (only one may hold :9000)")
    print(f"  2. BasiliskII --config {cfg}")
    print(f"  3. in the guest, open `Unix:AppleBridge Kit` and run "
          "`AppleBridgeInstaller`")
    print(f"  4. watch /tmp/applebridge_server.log for the daemon connecting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
