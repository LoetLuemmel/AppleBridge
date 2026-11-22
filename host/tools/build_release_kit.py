#!/usr/bin/env python3
"""Build the guest kit image for a release from MacBinaries - when no powered-off guest image holds the build.

    host/tools/build_release_kit.py AppleBridgeKit.dmg

install_bridge.py --export-guest-kit takes the binaries out of a POWERED-OFF guest image. When the builds to ship
exist only as MacBinaries (pulled with mac_get_file from the build guest's :bin: folder while it runs), this makes
the same kit from them: the same volume name, the same six items and the same prefs text (guest_prefs_text, LF
endings on purpose, R20), built the same way - hformat + hcopy -m, because a 2 MB volume written by another
library showed "0 items" in System 7.0's Finder (2026-11-22). Each MacBinary gets the shipping name and the build
date in its header, so the Finder shows neither the build name ("AppleBridge.d51") nor 1 January 1904.

Source folder: $APPLEBRIDGE_KIT_SOURCE (default ~/.applebridge/s7floor/kit) with <leaf>.bin for ABJournalDRVR,
AppleBridge, AppleBridgeConfig, AppleBridgeInstaller, AppleBridgeWatchdog. Needs hfsutils. Used for v0.8d51-kit1.
"""
import calendar, os, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import install_bridge as ib
KIT = os.path.expanduser(os.environ.get("APPLEBRIDGE_KIT_SOURCE", "~/.applebridge/s7floor/kit"))
if len(sys.argv) != 2:
    sys.exit(__doc__)
out = sys.argv[1]
now = calendar.timegm(time.localtime()) + 2082844800

def crc(data):
    c = 0
    for byte in data:
        c ^= byte << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c

def rewrap(src, name, dst):
    """Same forks and Finder info, the shipping NAME and today's dates in the MacBinary II header."""
    b = bytearray(open(src, "rb").read())
    nb = name.encode("mac_roman")
    b[1] = len(nb); b[2:65] = nb + bytes(63 - len(nb))
    b[91:95] = now.to_bytes(4, "big"); b[95:99] = now.to_bytes(4, "big")
    b[124:126] = crc(b[:124]).to_bytes(2, "big")
    open(dst, "wb").write(b)

stage = tempfile.mkdtemp()
items = []
for leaf in ("ABJournalDRVR", "AppleBridge", "AppleBridgeConfig", "AppleBridgeInstaller", "AppleBridgeWatchdog"):
    p = os.path.join(stage, leaf + ".bin"); rewrap(os.path.join(KIT, leaf + ".bin"), leaf, p); items.append(p)
prefs = os.path.join(stage, "prefs.txt")
open(prefs, "wb").write(ib.guest_prefs_text(ib.GUEST_ROUTER, net="OT").encode("mac_roman"))   # LF on purpose (R20)
open(out, "wb").write(b"\0" * (2 * 1024 * 1024))
run = lambda *a: subprocess.run(list(a), capture_output=True, text=True, check=True).stdout
run("hformat", "-l", ib.KIT_VOLUME, out)
run("hmount", out)
try:
    for p in items:
        run("hcopy", "-m", p, ":")
    run("hcopy", "-r", prefs, ":AppleBridge Prefs")          # raw: no LF->CR conversion
    run("hattrib", "-t", "TEXT", "-c", "ttxt", ":AppleBridge Prefs")
    print(run("hls", "-la", ":"))
finally:
    run("humount")
