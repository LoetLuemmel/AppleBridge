#!/usr/bin/env python3
"""guest_explore.py — look around a guest that has no toolchain.

On a machine with no MPW and no ToolServer there is no `Files`, no `DumpFile`
and no `DeRez`; the only things that answer are the verbs the daemon implements
itself. That is not as limiting as it sounds — this walks directories, reads
both forks, and inventories a file's resource types, which between them answer
most questions one actually asks about a classic Mac volume.

    guest_explore.py volumes
    guest_explore.py ls  'Macintosh:C Development:'
    guest_explore.py probe 'Macintosh:C Development:THINK C:THINK Project Manager'
    guest_explore.py get 'Macintosh:…:demo.c' ./demo.c

`probe` is the interesting one. It reports both fork sizes and the resource
**types** present, parsed out of the resource map rather than guessed by
searching for byte patterns — a substring hit inside compiled code would
otherwise pass for a resource. An **`aete`** means the application carries Apple
Event terminology, i.e. it is scriptable, which decides whether it can be driven
over the bridge or only by a human at its menus.

Run it on the machine whose control port serves the bridge: that port binds
127.0.0.1 and is deliberately not reachable from elsewhere.
"""

import argparse
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir, "mcp"))
from mac_connection import MacConnection             # noqa: E402

_CONN = MacConnection()


def send(cmd, timeout=240.0):
    return _CONN.send_command(cmd, timeout=timeout)


def _macbinary_forks(b64):
    """-> (data, rsrc) from the host's base64 MacBinary framing of READFILE."""
    blob = base64.b64decode(b64)
    if len(blob) < 128:
        raise ValueError(f"short reply ({len(blob)} B)")
    dlen = int.from_bytes(blob[83:87], "big")
    rlen = int.from_bytes(blob[87:91], "big")
    pad = (-dlen) % 128
    return blob[128:128 + dlen], blob[128 + dlen + pad:128 + dlen + pad + rlen]


def resource_types(rsrc):
    """-> [(type, count)] parsed from a classic resource fork's map.

    Format: header gives the map's offset; the map's type list holds one 8-byte
    entry per type (4-byte type, count-1, ref-list offset). Parsed rather than
    searched, so a 'aete' appearing by chance inside CODE cannot masquerade as
    a resource. Returns [] when the fork is absent or unparseable.
    """
    try:
        if len(rsrc) < 16:
            return []
        map_off = int.from_bytes(rsrc[4:8], "big")
        map_len = int.from_bytes(rsrc[12:16], "big")
        if map_off + map_len > len(rsrc) or map_len < 30:
            return []
        m = rsrc[map_off:map_off + map_len]
        type_list_off = int.from_bytes(m[24:26], "big")
        n = int.from_bytes(m[type_list_off:type_list_off + 2], "big") + 1
        out = []
        for i in range(n):
            e = type_list_off + 2 + i * 8
            if e + 8 > len(m):
                break
            code = m[e:e + 4].decode("mac_roman", "replace")
            count = int.from_bytes(m[e + 4:e + 6], "big") + 1
            out.append((code, count))
        return sorted(out)
    except Exception:                                 # noqa: BLE001
        return []


# --- verbs -------------------------------------------------------------------

def cmd_volumes(_args):
    st, out, err = send("DISKINFO", timeout=60)
    if st != 0 and not out.strip():
        print(f"DISKINFO failed: {err or st}")
        return 1
    print(f"{'volume':<24}{'vRef':>6}{'total':>14}{'free':>14}")
    for line in out.strip().splitlines():
        p = line.split("\t")
        if len(p) >= 4:
            print(f"{p[0]:<24}{p[1]:>6}{int(p[2]):>14,}{int(p[3]):>14,}")
    return 0


def cmd_ls(args):
    path = args.path if args.path.endswith(":") else args.path + ":"
    st, out, err = send("LISTDIR:" + path, timeout=120)
    if st != 0 and not out.strip():
        print(f"LISTDIR failed: {err or st}")
        return 1
    rows = [l.split("\t") for l in out.strip().splitlines() if l.strip()]
    folders = [r for r in rows if len(r) > 1 and r[1] == "fldr"]
    files = [r for r in rows if not (len(r) > 1 and r[1] == "fldr")]
    print(f"{path}   —   {len(folders)} folders, {len(files)} files")
    for r in folders:
        print(f"  [{r[0]}]")
    for r in files:
        typ = r[1] if len(r) > 1 else ""
        crt = r[2] if len(r) > 2 else ""
        size = int(r[3]) if len(r) > 3 and r[3].isdigit() else 0
        print(f"  {r[0]:<42}{typ:<6}{crt:<6}{size:>10,}")
    return 0


def cmd_probe(args):
    st, out, err = send("READFILE:" + args.path)
    if st != 0:
        print(f"READFILE failed: {err or st}")
        return 1
    data, rsrc = _macbinary_forks(out)
    print(args.path)
    print(f"  data fork     {len(data):>10,} B")
    print(f"  resource fork {len(rsrc):>10,} B")
    types = resource_types(rsrc)
    if not types:
        print("  no parseable resource map")
        return 0
    print(f"  {len(types)} resource types:")
    line, width = "   ", 0
    for code, count in types:
        item = f"{code}({count})"
        if width + len(item) > 68:
            print(line); line, width = "   ", 0
        line += " " + item; width += len(item) + 1
    if line.strip():
        print(line)

    # The question this tool exists to answer.
    have = {c for c, _ in types}
    if "aete" in have:
        print("\n  SCRIPTABLE: carries 'aete' (Apple Event terminology) —"
              "\n  it can be driven over the bridge, not only by hand at its menus.")
    else:
        print("\n  no 'aete': no Apple Event terminology, so it is driven by"
              "\n  synthetic input and the real mouse, not by scripting.")
    return 0


def cmd_get(args):
    st, out, err = send("READFILE:" + args.path)
    if st != 0:
        print(f"READFILE failed: {err or st}")
        return 1
    data, rsrc = _macbinary_forks(out)
    with open(args.local, "wb") as fh:
        fh.write(data)
    print(f"wrote {len(data):,} B (data fork) -> {args.local}")
    if rsrc:
        with open(args.local + ".rsrc", "wb") as fh:
            fh.write(rsrc)
        print(f"wrote {len(rsrc):,} B (resource fork) -> {args.local}.rsrc")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("volumes", help="mounted volumes (DISKINFO)")
    p_ls = sub.add_parser("ls", help="list a folder (LISTDIR)")
    p_ls.add_argument("path")
    p_pr = sub.add_parser("probe", help="fork sizes + resource types; is it scriptable?")
    p_pr.add_argument("path")
    p_gt = sub.add_parser("get", help="save a file's forks locally")
    p_gt.add_argument("path"); p_gt.add_argument("local")
    args = ap.parse_args()

    try:
        return {"volumes": cmd_volumes, "ls": cmd_ls,
                "probe": cmd_probe, "get": cmd_get}[args.verb](args)
    except OSError as e:
        print(f"control port unreachable: {e}")
        print("Run this on the machine whose host server is running.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
