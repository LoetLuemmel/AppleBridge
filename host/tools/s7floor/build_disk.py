#!/usr/bin/env python3
"""Assemble a bootable System 7.0 / 7.0.1 test disk for the AppleBridge System-7 floor test.

    uv run --with machfs python host/tools/s7floor/build_disk.py 70  out.img          # daemon in Startup Items
    uv run --with machfs python host/tools/s7floor/build_disk.py 701 out.img
    uv run --with machfs python host/tools/s7floor/build_disk.py 70  out.img --kit    # nothing installed: the kit

Why a hand-assembled System Folder and not an Installer run
-----------------------------------------------------------
What is under test is the daemon against a given System, not Apple's Installer. For 7.0 and 7.0.1 the System file is
universal, so the System Folder can be put together from the Developer CD's net-install folders directly, and the
disk is built on the host in seconds instead of driven through an Installer in the guest. Two traps found doing it
(2026-11-22):

  * 7.0's `Install 1:System` is only 194 resources; the Installer merges `Install 2:Cerulean Data` (556) into it.
    The only overlap is an identical 'vers' 2, so the merge is a plain union -- done here.
  * 7.0's `Disk Tools` System is **6.0.7** ('vers' says so), not 7.0. Do not use it as a shortcut.

Inputs (none of them in the repo -- binaries and Apple media):
  $S7FLOOR_CDS   folder with "Dev.CD Jan 94.toast" (System installers) and "Dev.CD Jan 96 SDK1.toast" (MacTCP 2.0.6);
                 default ~/Documents/Basilisk/CD Images
  $S7FLOOR_WORK  default ~/.applebridge/s7floor:
                   payload/AppleBridge.bin    the daemon, MacBinary (mac_get_file format=macbinary)
                   payload/MacTCP Prep.bin    optional: a configured MacTCP (Ethernet, 10.0.2.15, gateway 10.0.2.2 for
                                              Basilisk's slirp); without it, MacTCP is set once by hand in the guest
                   kit/<leaf>.bin             for --kit: AppleBridgeInstaller, AppleBridge, AppleBridgeWatchdog,
                                              AppleBridgeConfig, ABJournalDRVR
"""
import argparse
import os
import struct
import sys

import machfs
import macresources

CDS = os.path.expanduser(os.environ.get("S7FLOOR_CDS", "~/Documents/Basilisk/CD Images"))
WORK = os.path.expanduser(os.environ.get("S7FLOOR_WORK", "~/.applebridge/s7floor"))
JAN94 = os.path.join(CDS, "Dev.CD Jan 94.toast")
SDK1 = os.path.join(CDS, "Dev.CD Jan 96 SDK1.toast")
US = "System Software:U.S. System Software:"
MACTCP = "Development Kits (Disc 1):MacTCP:2.0.6 Files:MacTCP"

SPECS = {
    "701": {
        "volume": "S7 701",
        "system": US + "U.S. 7.0.1 Installer:Install 1:System",
        "finder": US + "U.S. 7.0.1 Installer:Install 2:Finder",
        "cdevs": US + "U.S. 7.0.1 Installer:Tidbits:Control Panels",
        "das": US + "U.S. 7.0.1 Installer:Tidbits:Desk Accessories",
    },
    "70": {
        "volume": "S7 70",
        "system": US + "U.S. 7.0 Installer:Net Install:Install 1:System",
        "merge": US + "U.S. 7.0 Installer:Net Install:Install 2:Cerulean Data",
        "finder": US + "U.S. 7.0 Installer:Net Install:Install 3:Finder",
        "cdevs": US + "U.S. 7.0 Installer:Net Install:Tidbits:Control Panels",
        "das": US + "U.S. 7.0 Installer:Net Install:Tidbits:Desk Accessories",
    },
}
CDEVS = ["General Controls", "Memory", "Monitors", "Mouse", "Keyboard", "Sound", "Startup Disk", "Network"]
DAS = ["Chooser", "Calculator", "Note Pad"]
KIT_LEAVES = ("AppleBridgeInstaller", "AppleBridge", "AppleBridgeWatchdog", "AppleBridgeConfig", "ABJournalDRVR")


def hfs_partition(path):
    """(offset, length) of the Apple_HFS partition in an Apple-partition-mapped image; (0, None) for a bare volume."""
    with open(path, "rb") as f:
        f.seek(512)
        hdr = f.read(512)
        if hdr[:2] != b"PM":
            return 0, None
        for i in range(struct.unpack(">I", hdr[4:8])[0]):
            f.seek(512 * (1 + i))
            e = f.read(512)
            start, count = struct.unpack(">II", e[8:16])
            if e[48:80].split(b"\0")[0] == b"Apple_HFS":
                return start * 512, count * 512
    raise SystemExit(f"{path}: no Apple_HFS partition")


def load(path):
    off, ln = hfs_partition(path)
    with open(path, "rb") as f:
        f.seek(off)
        data = f.read(ln) if ln else f.read()
    v = machfs.Volume()
    v.read(data)
    return v


def get(vol, path):
    node = vol
    for part in path.split(":"):
        node = node[part]
    return node


def copy(src):
    f = machfs.File()
    # Clear "inited": the CD's positions are not copied, and an inited file at (0,0) stacks every icon on one spot.
    f.type, f.creator, f.flags = src.type, src.creator, src.flags & ~0x0100
    f.data, f.rsrc = bytearray(src.data), bytearray(src.rsrc)
    f.crdate = f.mddate = src.crdate
    return f


def macbinary_file(path):
    b = open(path, "rb").read()
    dlen, rlen = int.from_bytes(b[83:87], "big"), int.from_bytes(b[87:91], "big")
    f = machfs.File()
    f.type, f.creator = b[65:69], b[69:73]
    f.flags = ((b[73] << 8) | b[101]) & ~0x0100
    r0 = 128 + ((dlen + 127) // 128) * 128
    f.data, f.rsrc = bytearray(b[128:128 + dlen]), bytearray(b[r0:r0 + rlen])
    f.crdate, f.mddate = int.from_bytes(b[91:95], "big"), int.from_bytes(b[95:99], "big")
    return f


def text_file(text, ftype=b"TEXT", creator=b"ttxt"):
    f = machfs.File()
    f.type, f.creator = ftype, creator
    f.data = bytearray(text.replace("\n", "\r").encode("mac_roman"))
    return f


def build(spec_name, out, size_mb, host, tcpport, name, kit, bare=False):
    spec = SPECS[spec_name]
    cd, sdk = load(JAN94), load(SDK1)
    v = machfs.Volume()
    v.name = spec["volume"]
    sf = v["System Folder"] = machfs.Folder()
    sf["System"] = copy(get(cd, spec["system"]))
    if "merge" in spec:
        have = list(macresources.parse_file(bytes(sf["System"].rsrc)))
        keys = {(r.type, r.id) for r in have}
        extra = [r for r in macresources.parse_file(bytes(get(cd, spec["merge"]).rsrc)) if (r.type, r.id) not in keys]
        sf["System"].rsrc = bytearray(macresources.make_file(have + extra))
        print(f"System: {len(have)} + {len(extra)} merged resources")
    sf["Finder"] = copy(get(cd, spec["finder"]))
    for sub in ("Apple Menu Items", "Control Panels", "Extensions", "Preferences", "Startup Items"):
        sf[sub] = machfs.Folder()
    cdevs, das = get(cd, spec["cdevs"]), get(cd, spec["das"])
    for n in CDEVS:
        if n in cdevs:
            sf["Control Panels"][n] = copy(cdevs[n])
    for n in DAS:
        if n in das:
            sf["Apple Menu Items"][n] = copy(das[n])
    sf["Control Panels"]["MacTCP"] = copy(get(sdk, MACTCP))
    prep = os.path.join(WORK, "payload", "MacTCP Prep.bin")
    if os.path.exists(prep):
        sf["Preferences"]["MacTCP Prep"] = macbinary_file(prep)
    prefs = f"IP={host}\nNET=MacTCP\nDEBUG=1\nTCPPORT={tcpport}\nNAME={name}\n"
    if bare:
        pass                                          # nothing of AppleBridge: install it from a kit disk
    elif kit:
        # Nothing installed: the kit folder as it ships, with the seed prefs the installer carries over (it adds HOME=).
        k = v["AppleBridge Kit"] = machfs.Folder()
        for leaf in KIT_LEAVES:
            k[leaf] = macbinary_file(os.path.join(WORK, "kit", leaf + ".bin"))
        k["AppleBridge Prefs"] = text_file(prefs)
    else:
        sf["Preferences"]["AppleBridge Prefs"] = text_file(prefs + f"HOME={spec['volume']}:AppleBridge:\n")
        v["AppleBridge"] = machfs.Folder()            # HOME: where battery.py's probe files land
        sf["Startup Items"]["AppleBridge"] = macbinary_file(os.path.join(WORK, "payload", "AppleBridge.bin"))
    img = v.write(size=size_mb * 1024 * 1024)     # machfs writes the boot blocks from the System's 'boot' 1 and blesses
    with open(out, "wb") as fh:
        fh.write(img)
    print(f"{out}: {len(img)} bytes, volume {v.name!r}{' (kit)' if kit else ' (bare)' if bare else ''}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("spec", choices=sorted(SPECS))
    ap.add_argument("out")
    ap.add_argument("--kit", action="store_true", help="the AppleBridge Kit folder instead of an installed daemon")
    ap.add_argument("--bare", action="store_true", help="no AppleBridge at all - install it from a kit on a second disk")
    ap.add_argument("--size-mb", type=int, default=40)
    ap.add_argument("--host", default="192.168.3.232", help="IP= the daemon dials (the hub)")
    ap.add_argument("--tcpport", type=int, default=9050, help="TCPPORT= (the hub's daemon port for this guest)")
    ap.add_argument("--name", default="s7test", help="NAME= reported in HELLO")
    a = ap.parse_args()
    build(a.spec, a.out, a.size_mb, a.host, a.tcpport, a.name, a.kit, a.bare)


if __name__ == "__main__":
    main()
