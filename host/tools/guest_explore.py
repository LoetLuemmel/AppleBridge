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



def extract(rsrc, want):
    """-> [(id, bytes)] for one resource type, read out of the map's ref lists.

    Needed because the interesting resource is rarely the whole fork: an `aete`
    is a few hundred bytes inside half a megabyte of CODE and artwork.
    """
    out = []
    try:
        data_off = int.from_bytes(rsrc[0:4], "big")
        map_off = int.from_bytes(rsrc[4:8], "big")
        m = rsrc[map_off:]
        type_list_off = int.from_bytes(m[24:26], "big")
        n = int.from_bytes(m[type_list_off:type_list_off + 2], "big") + 1
        for i in range(n):
            e = type_list_off + 2 + i * 8
            if m[e:e + 4] != want:
                continue
            count = int.from_bytes(m[e + 4:e + 6], "big") + 1
            ref_off = type_list_off + int.from_bytes(m[e + 6:e + 8], "big")
            for j in range(count):
                r = ref_off + j * 12
                rid = int.from_bytes(m[r:r + 2], "big", signed=True)
                doff = int.from_bytes(m[r + 5:r + 8], "big")
                start = data_off + doff
                ln = int.from_bytes(rsrc[start:start + 4], "big")
                out.append((rid, rsrc[start + 4:start + 4 + ln]))
    except Exception:                                 # noqa: BLE001
        pass
    return out


class _Cur:
    """Byte cursor for the aete's packed layout."""

    def __init__(self, buf):
        self.b, self.i = buf, 0

    def _need(self, n):
        if self.i + n > len(self.b):
            raise EOFError("aete: read past end")

    def _even(self):
        # Strings are packed, but a numeric field starts on an even boundary —
        # Rez's alignment rule. Getting this wrong shifts everything after the
        # first empty description by one byte and the terminology turns to
        # noise, which is what "could not parse" was.
        if self.i % 2:
            self.i += 1

    def u16(self):
        self._even(); self._need(2)
        v = int.from_bytes(self.b[self.i:self.i + 2], "big"); self.i += 2; return v

    def ostype(self):
        self._even(); self._need(4)
        v = self.b[self.i:self.i + 4].decode("mac_roman", "replace"); self.i += 4; return v

    def pstr(self, align):
        self._need(1)
        n = self.b[self.i]
        self._need(1 + n)
        s = self.b[self.i + 1:self.i + 1 + n]
        self.i += 1 + n
        if align and self.i % 2:
            self.i += 1
        return s.decode("mac_roman", "replace")


# The one flag bit that decides whether an event can be built at all.
#
# An `aete` parameter carries a flags word, and bit 15 (0x8000) means OPTIONAL.
# Read off the THINK Project Manager's own terminology by the parallel session
# on 2026-08-05 and cross-checked at four independent places in the same
# resource: `create` has file mandatory and at/with/model optional, `set` has
# `to` mandatory. That is what makes it a reading rather than a guess.
#
# It matters because AESEND can only build a `typeChar` direct object. An event
# whose direct parameter is MANDATORY and of some other type therefore cannot be
# constructed with the tools here — which is a fact worth having BEFORE spending
# an afternoon trying.
AETE_OPTIONAL = 0x8000


def _param(kind, ptype, desc, flags):
    return {"kind": kind, "type": ptype, "desc": desc, "flags": flags,
            "optional": bool(flags & AETE_OPTIONAL)}


def parse_aete(buf, align):
    """-> [(suite, [(name, class, id, description)])] or None if it looks wrong.

    Only the event vocabulary is parsed; classes and enumerations are what an
    application can be *asked about*, while events are what it can be *told to
    do*, and the latter is the question here. `align` covers the one ambiguity
    in the layout — whether Pascal strings are padded to even boundaries — by
    being tried both ways and judged on whether the four-character codes come
    out printable.
    """
    try:
        return _parse_aete(buf, align)
    except (EOFError, IndexError, ValueError):
        return None                                  # wrong alignment guess


def _parse_aete(buf, align):
    c = _Cur(buf)
    c.u16(); c.u16(); c.u16()                        # version, language, script
    suites = []
    for _ in range(c.u16()):
        name = c.pstr(align); desc = c.pstr(align)
        c.ostype(); c.u16(); c.u16()                 # suite id, level, version

        declared = c.u16()
        events = []
        # Tolerant: a vocabulary read halfway beats a refusal.
        try:
            for _ in range(declared):
                ev = c.pstr(align); ed = c.pstr(align)
                cls, eid = c.ostype(), c.ostype()
                if not (cls.isprintable() and eid.isprintable()):
                    raise ValueError("implausible event code")
                # These three were READ AND DISCARDED until 2026-08-05, and
                # they are the fields that carry the answer: the reply type, the
                # direct parameter's type and flags, and the named parameters.
                # Everything above them says what an application UNDERSTANDS;
                # only these say what a caller must be able to BUILD.
                rt, rd, rf = c.ostype(), c.pstr(align), c.u16()
                dt, dd, df = c.ostype(), c.pstr(align), c.u16()
                named = []
                for _ in range(c.u16()):
                    pn = c.pstr(align); pk = c.ostype()
                    pt = c.ostype(); pd = c.pstr(align); pf = c.u16()
                    named.append(dict(_param("named", pt, pd, pf),
                                      name=pn, keyword=pk))
                events.append((ev, cls, eid, ed, {
                    "reply": _param("reply", rt, rd, rf),
                    "direct": _param("direct", dt, dd, df),
                    "named": named,
                }))
        except (EOFError, IndexError, ValueError):
            suites.append((name, desc, events, declared))
            break

        suites.append((name, desc, events, declared))

        # Skip the rest of the suite to reach the next one. Not optional: the
        # FIRST suite is usually the Required Suite with NO events, so a parser
        # that stops there reports nothing at all — which is what this did.
        try:
            for _ in range(c.u16()):                 # classes
                c.pstr(align); c.ostype(); c.pstr(align)
                for _ in range(c.u16()):             # properties
                    c.pstr(align); c.ostype(); c.ostype(); c.pstr(align); c.u16()
                for _ in range(c.u16()):             # elements
                    c.ostype()
                    for _ in range(c.u16()):         # key forms
                        c.ostype()
            for _ in range(c.u16()):                 # comparisons
                c.pstr(align); c.ostype(); c.pstr(align)
            for _ in range(c.u16()):                 # enumerations
                c.ostype()
                for _ in range(c.u16()):             # enumerators
                    c.pstr(align); c.ostype(); c.pstr(align)
        except (EOFError, IndexError, ValueError):
            break

    if not any(ev for _, _, ev, _ in suites):
        return None
    return suites


def cmd_aete(args):
    st, out, err = send("READFILE:" + args.path)
    if st != 0:
        print(f"READFILE failed: {err or st}")
        return 1
    _, rsrc = _macbinary_forks(out)
    found = extract(rsrc, b"aete")
    if not found:
        print("no 'aete' resource — this application is not scriptable")
        return 1
    rid, buf = found[0]
    print(f"{args.path}\n  aete({rid}), {len(buf)} B")
    suites = parse_aete(buf, align=False)
    if not suites:
        print("  could not parse the terminology")
        return 1
    for name, desc, events, declared in suites:
        if not events:
            print(f"\n  suite: {name}  —  (no events)")
            continue
        print(f"\n  suite: {name}  —  {desc[:60]}")
        shown = f"{len(events)} of {declared}" if len(events) != declared else f"{declared}"
        print(f"  {shown} events:")
        for ev, cls, eid, ed, sig in events:
            print(f"    {cls}/{eid}  {ev:<22} {ed[:44]}")
            if not args.full:
                continue
            d = sig["direct"]
            need = "optional" if d["optional"] else "REQUIRED"
            if d["type"] == "null":
                print(f"        direct : none")
            else:
                print(f"        direct : {d['type']}  ({need})")
            if sig["reply"]["type"] != "null":
                print(f"        reply  : {sig['reply']['type']}")
            for prm in sig["named"]:
                mark = "optional" if prm["optional"] else "REQUIRED"
                print(f"        {prm['keyword']}    {prm['name'][:18]:<18} "
                      f"{prm['type']}  ({mark})")
            print(f"        -> {verdict(sig)}")
    return 0


def verdict(sig):
    """Can this event be built with the tools in this repo? -> one sentence.

    `AESEND` sends a `typeChar` direct object and nothing else. So an event is
    reachable when it needs no direct parameter, or when the one it declares is
    optional — and unreachable when a direct parameter of another type is
    mandatory. Stated as a judgement rather than left to the reader, because the
    whole point of printing these fields is to answer the question they raise.

    Honest limit, measured the same day: this reads the DECLARATION. A THINK C
    `KAHL/MAKE` whose direct parameter is declared mandatory built a project
    anyway when sent without one. So a "no" here means "not according to the
    terminology", which is a reason to be careful and not a proof of refusal.
    """
    d = sig["direct"]
    if d["type"] == "null":
        return "AESEND-reachable (no direct parameter)"
    if d["optional"]:
        return f"AESEND-reachable (direct {d['type']} is optional)"
    if d["type"] in ("TEXT", "ctxt", "cstr"):
        return f"AESEND-reachable (direct {d['type']} is text)"
    return (f"NOT constructible with AESEND: direct {d['type']} is required "
            "and AESEND sends only typeChar")


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
    p_ae = sub.add_parser("aete", help="the Apple Event vocabulary an app accepts")
    p_ae.add_argument("--full", action="store_true",
                      help="also the PARAMETER TYPES — reply, direct object and "
                           "named parameters with their optional flag, plus a "
                           "verdict on whether AESEND can build the event")
    p_ae.add_argument("path")
    p_gt = sub.add_parser("get", help="save a file's forks locally")
    p_gt.add_argument("path"); p_gt.add_argument("local")
    args = ap.parse_args()

    try:
        return {"volumes": cmd_volumes, "ls": cmd_ls, "probe": cmd_probe,
                "aete": cmd_aete, "get": cmd_get}[args.verb](args)
    except OSError as e:
        print(f"control port unreachable: {e}")
        print("Run this on the machine whose host server is running.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
