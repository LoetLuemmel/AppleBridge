#!/usr/bin/env python3
"""Read a classic-Mac resource fork: list what is in it, or emit one resource
as source you can paste into a build.

Why this exists. `mac/journal/BuildDlgINIT.emu` describes the job in prose and
calls it out as something to do BY HAND:

    extract the exact bytes DETERMINISTICALLY (DeRez STDOUT over the bridge is
    flaky for large output): READFILE ... -> MacBinary, parse the resource fork,
    pull 'DPAT' id 128 -> N bytes, emit PatchData as DC.W + set kPatchLen = N

Every code resource this project embeds went through that by hand — `dlginit.a`'s
`DPAT` literals, `main.c`'s `kJGTemplate[]`, `kCPTemplate[]`. Hand-transcribing a
740-byte patch is not a task anybody should repeat, and on 2026-08-04 it blocked
the parallel session outright: it needed to rebuild the jGNE resource and the
recipe existed nowhere, only in somebody's earlier session.

`host/macbinary.py` already gets the fork out of a MacBinary file. What was
missing is the layer above it: the resource MAP. That is what this adds.

    rsrc_extract.py list  jgnepatch.rsrc
    rsrc_extract.py emit  jgnepatch.rsrc --type JGNE --id 128 --as c --name kJGTemplate
    rsrc_extract.py emit  dlgpatch.rsrc  --type DPAT --id 128 --as asm --name PatchData

Input may be MacBinary (what `mac_get_file` writes) or a raw resource fork; the
format is detected, because passing the wrong one is the obvious mistake and a
wrong guess here produces plausible garbage rather than an error.

Resource-fork layout (Inside Macintosh: More Macintosh Toolbox), all big-endian:

    header  +0  dataOff   +4  mapOff   +8  dataLen   +12 mapLen
    map     +24 typeOff (from map start)   +26 nameOff
            typeOff: count-1, then per type: type(4) count-1(2) refListOff(2)
            refList: id(2) nameOff(2) attrs(1) dataOff(3) handle(4)
    data    at dataOff+dataOff3: length(4) then that many bytes

The `count-1` fields are the classic trap: a fork with one resource stores 0.
Reading them as plain counts silently drops the last resource of every type —
which for a single-resource fork means finding nothing at all.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _be16(blob, at):
    return struct.unpack_from(">H", blob, at)[0]


def _be32(blob, at):
    return struct.unpack_from(">I", blob, at)[0]


def resource_fork(blob):
    """The resource fork of `blob`, whether it is MacBinary or already a fork.

    Detected rather than declared: `mac_get_file` writes MacBinary, a `READFILE`
    capture may be the bare fork, and feeding one where the other is expected
    yields a map at a plausible-looking offset full of nonsense. An explicit
    check turns that into an answer.
    """
    try:
        import macbinary
        if macbinary.looks_like_macbinary(blob):
            return macbinary.decode(blob).get("rsrc") or b""
    except Exception:
        pass
    return blob


def resources(fork):
    """-> [{type, id, name, attrs, size, data}], in map order.

    Raises ValueError with a reason rather than returning [] on a malformed
    fork: "no resources" and "this is not a resource fork" are different
    answers, and only one of them means the caller passed the wrong file.
    """
    if len(fork) < 16:
        raise ValueError(f"too short to be a resource fork ({len(fork)} bytes)")
    data_off, map_off, data_len, map_len = struct.unpack_from(">IIII", fork, 0)
    if map_off + map_len > len(fork) or data_off + data_len > len(fork):
        raise ValueError("header offsets point past the end — not a resource "
                         "fork, or a MacBinary file passed as a raw fork")
    if map_off + 30 > len(fork):
        raise ValueError("resource map does not fit in the fork")

    type_off = map_off + _be16(fork, map_off + 24)
    name_off = map_off + _be16(fork, map_off + 26)
    if type_off + 2 > len(fork):
        raise ValueError("type list starts past the end of the fork")

    def _need(at, n, what):
        if at < 0 or at + n > len(fork):
            raise ValueError(f"{what} runs past the end of the fork "
                             f"(wants {at}+{n}, have {len(fork)})")

    # `+ 1` MASKED to 16 bits, because count-1 of 0xFFFF means ZERO — the
    # encoding an empty fork actually uses. Measured on a real MPW-written file
    # (Claude2Assistant.r, 2026-08-04): 0xFFFF + 1 gave 65536 types and the walk
    # ran off the end with a struct error. The synthetic fork this is tested
    # against could never produce that case, which is the whole argument for
    # having checked against a file somebody else wrote.
    n_types = (_be16(fork, type_off) + 1) & 0xFFFF
    out = []
    for i in range(n_types):
        at = type_off + 2 + i * 8
        _need(at, 8, f"type entry {i}")
        rtype = fork[at:at + 4]
        n_res = (_be16(fork, at + 4) + 1) & 0xFFFF
        ref_off = type_off + _be16(fork, at + 6)
        for k in range(n_res):
            ref = ref_off + k * 12
            _need(ref, 12, f"reference {k} of {rtype!r}")
            rid = struct.unpack_from(">h", fork, ref)[0]
            nm_at = _be16(fork, ref + 2)
            attrs = fork[ref + 4]
            body = (fork[ref + 5] << 16) | (fork[ref + 6] << 8) | fork[ref + 7]
            name = ""
            if nm_at != 0xFFFF:
                p = name_off + nm_at
                name = fork[p + 1:p + 1 + fork[p]].decode("mac_roman", "replace")
            start = data_off + body
            _need(start, 4, f"data length of {rtype!r} {rid}")
            size = _be32(fork, start)
            _need(start + 4, size, f"data of {rtype!r} {rid}")
            out.append({"type": rtype.decode("mac_roman", "replace"),
                        "id": rid, "name": name, "attrs": attrs, "size": size,
                        "data": fork[start + 4:start + 4 + size]})
    return out


def find(items, rtype, rid):
    """The one resource named, or a ValueError that says what IS there.

    A miss is far more often a wrong type/id than a missing resource, so the
    error carries the inventory — the caller usually needs exactly that to fix
    the call, and asking them to re-run `list` is a round trip for nothing.
    """
    for r in items:
        if r["type"] == rtype and (rid is None or r["id"] == rid):
            return r
    have = ", ".join(f"{r['type']} {r['id']}" for r in items) or "nothing"
    raise ValueError(f"no {rtype} {rid if rid is not None else '(any id)'} "
                     f"in this fork — it holds: {have}")


def as_c(data, name, per_line=12):
    """A C initialiser — the shape `kJGTemplate[]` / `kCPTemplate[]` already use."""
    lines = [f"#define {name}_Size {len(data)}",
             f"static const unsigned char {name}[{name}_Size] = {{"]
    for row in range(0, len(data), per_line):
        chunk = data[row:row + per_line]
        lines.append(f"/* +{row:4d} */ " + " ".join(f"0x{b:02X}," for b in chunk))
    lines.append("};")
    return "\n".join(lines)


def as_asm(data, name, per_line=8):
    """MPW asm `DC.W` literals — the shape `dlginit.a` embeds DPAT with.

    Padded to an even length: DC.W emits words, and an odd byte count would
    otherwise drop the last byte or shift everything by one. The pad is
    reported in the emitted comment rather than applied silently.
    """
    padded = data + (b"\x00" if len(data) % 2 else b"")
    note = "  ; padded 1 byte to a word boundary" if len(padded) != len(data) else ""
    lines = [f"kPatchLen       EQU     {len(data)}",
             f"{name}{note}"]
    for row in range(0, len(padded), per_line * 2):
        words = struct.unpack_from(">" + "H" * min(per_line, (len(padded) - row) // 2),
                                   padded, row)
        lines.append("        DC.W    " + ",".join(f"${w:04X}" for w in words))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="verb", required=True)

    l = sub.add_parser("list", help="what is in this fork (type, id, size)")
    l.add_argument("path")

    e = sub.add_parser("emit", help="one resource as C or asm source")
    e.add_argument("path")
    e.add_argument("--type", required=True, help="4-char resource type, e.g. DPAT")
    e.add_argument("--id", type=int, default=None)
    e.add_argument("--as", dest="fmt", choices=("c", "asm", "raw"), default="c")
    e.add_argument("--name", default="kPatchData")
    args = ap.parse_args()

    try:
        with open(args.path, "rb") as fh:
            items = resources(resource_fork(fh.read()))
    except (OSError, ValueError) as exc:
        print(f"{args.path}: {exc}", file=sys.stderr)
        return 2

    if args.verb == "list":
        if not items:
            print("no resources")
            return 0
        print(f"{'type':<6} {'id':>6} {'size':>8}  name")
        for r in items:
            print(f"{r['type']:<6} {r['id']:>6} {r['size']:>8}  {r['name']}")
        return 0

    try:
        r = find(items, args.type, args.id)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.fmt == "raw":
        sys.stdout.buffer.write(r["data"])
    else:
        print((as_c if args.fmt == "c" else as_asm)(r["data"], args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
