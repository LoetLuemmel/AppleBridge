#!/usr/bin/env python3
"""q1_native_surface.py — does the ToolServer-less surface hold on this host?

Answers one question with evidence instead of inference: on a machine with no
MPW and no ToolServer, do the two capabilities that matter actually work —
**a screenshot** and a **fork-aware file transfer that survives a round trip**?

Every existing measurement path in this repository goes through ToolServer
(`bench_transport.py` uses `Catenate`/`DumpFile`/`Echo`), so none of them can be
run where the question is interesting. This one speaks only verbs the daemon
answers itself: STATUS, DISKINFO, LISTDIR, screenshot, WRITEFILE, READFILE.

Run it **on the machine whose control port serves the bridge** — the control
port binds 127.0.0.1 and is deliberately not reachable from elsewhere:

    /usr/bin/python3 host/tools/q1_native_surface.py --volume Macintosh
    /usr/bin/python3 host/tools/q1_native_surface.py --path 'MeinMac:AppleBridge:' --keep

Sizes cross the daemon's 64 KB buffer on purpose: the serial-transport defect of
2026-07-26 was invisible to every small payload and only bulk writes exposed it,
so a size sweep is the shape of test this class of fault requires.

Integrity is judged on a **data fork of random bytes** (D-013): a resource fork
may legitimately differ after a rename, because the Resource Manager rewrites
the name inside the map. Here nothing is renamed, so a resource-fork difference
is reported — loudly — but as a finding to investigate rather than a verdict.
"""

import argparse
import base64
import os
import sys
import time

CTRL_HOST = "127.0.0.1"
CTRL_PORT = 9001
SIZES = (4096, 65537, 524288)      # below / just above / well above the 64 KB buffer

# The control-port client the MCP tools use, reused rather than reimplemented.
# A second parser written for this script got the framing wrong on the first
# live reply: the frame is documented with CR separators, the daemon emits LF
# in places, and MacConnection already normalises both — plus it terminates a
# request with "\n\n" and downgrades a truncated reply to an error. That
# behaviour has its own regression suite in tests/test_parse_response.py, and
# a copy of it here would only drift away from the original.
# The path insert avoids the name clash between this repo's ./mcp package and
# the installed `mcp` SDK — same reason and same workaround as that test file.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir, "mcp"))
from mac_connection import MacConnection             # noqa: E402

_CONN = MacConnection(host=CTRL_HOST, port=CTRL_PORT)


def send(command, timeout=300.0):
    """-> (status, stdout, stderr). Raises OSError if the control port is absent."""
    return _CONN.send_command(command, timeout=timeout)


# --- checks ------------------------------------------------------------------

class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail):
        self.rows.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}: {detail}", flush=True)

    def failed(self):
        return [n for n, ok, _ in self.rows if not ok]


def check_link(rep):
    """Liveness is 'the daemon answered', NOT status == 0.

    Asked via **MACSTATUS**, which the host answers from its own view of the
    link (`daemon_responding=1` means the daemon replied to a STAT just now).

    This used to ask `STATUS` and look for the daemon's `initAE=` trace. That
    worked on the machine it was written for — a guest with no ToolServer —
    and inverted itself on a guest that has one: `STATUS` has no host route, so
    it falls through to ToolServer, which swallows it and answers `STATUS:0`
    with empty output. The liveness check then failed *because* the command
    tier was working (observed 2026-07-27). A probe must not depend on the
    absence of the thing it is measuring around.
    """
    status, out, err = send("MACSTATUS", timeout=20.0)
    text = (out + " " + err).strip()
    alive = "daemon_responding=1" in text
    rep.add("bridge link", alive, text[:120] or f"status={status}")
    if alive:
        has_ts = "toolserver=1" in text
        tier = "ToolServer present" if has_ts else "native verbs only"
        rep.add("command tier", True, f"{tier} (an absent ToolServer is a tier, not a fault)")
    return alive


def check_native_verbs(rep, base):
    for verb in ("DISKINFO", "LISTDIR:" + base):
        status, out, err = send(verb, timeout=60.0)
        first = out.strip().splitlines()[0][:100] if out.strip() else ""
        rep.add(f"{verb.split(':')[0]} (no ToolServer)", status == 0 and bool(first),
                first or (err or f"status={status}"))


def check_screenshot(rep, outdir):
    t0 = time.time()
    status, out, err = send("screenshot", timeout=180.0)
    dt = time.time() - t0
    if status != 0 or not out.strip():
        rep.add("screenshot", False, (err or f"status={status}")[:120])
        return
    try:
        png = base64.b64decode(out.strip())
    except Exception as e:                                    # noqa: BLE001
        rep.add("screenshot", False, f"reply is not base64: {e}")
        return
    ok = png[:8] == b"\x89PNG\r\n\x1a\n"
    path = os.path.join(outdir, "q1_screenshot.png")
    if ok:
        with open(path, "wb") as fh:
            fh.write(png)
    rep.add("screenshot", ok,
            f"{len(png)} B PNG in {dt:.1f}s -> {path}" if ok
            else f"not a PNG (first bytes {png[:8]!r})")


def _put(mac_path, data, rsrc):
    cmd = "WRITEFILE:" + ":".join((
        base64.b64encode(mac_path.encode("mac_roman", "replace")).decode("ascii"),
        b"BINA".hex(), b"ABQ1".hex(),
        base64.b64encode(data).decode("ascii"),
        base64.b64encode(rsrc).decode("ascii"),
    ))
    return send(cmd)


def _get(mac_path):
    status, out, err = send("READFILE:" + mac_path)
    if status != 0:
        return None, err or f"status={status}"
    try:
        blob = base64.b64decode(out)
    except Exception as e:                                    # noqa: BLE001
        return None, f"reply is not base64: {e}"
    # MacBinary II: 128-byte header, data fork, padded to 128, then resource fork.
    if len(blob) < 128:
        return None, f"short reply ({len(blob)} B)"
    dlen = int.from_bytes(blob[83:87], "big")
    rlen = int.from_bytes(blob[87:91], "big")
    pad = (-dlen) % 128
    data = blob[128:128 + dlen]
    rsrc = blob[128 + dlen + pad:128 + dlen + pad + rlen]
    return (data, rsrc), None


def _judge_resource_fork(sent, got, mac_path):
    """-> (ok, why). Byte equality is the WRONG contract for a resource fork.

    A resource fork is a structure, not a byte container: the Resource Manager
    stamps the owning file's name into the map as a Pascal string. Sending
    random bytes and demanding them back verbatim therefore tests an invalid
    payload — the mistake that produced D-012 and that D-013 corrected. The
    2026-07-27 run identified the rewritten bytes exactly: 77-78 bytes between
    offsets 48 and 125, containing <length><leaf name>, the length byte
    tracking the name (9/10/11 characters -> 0x09/0x0a/0x0b).

    So the contract checked here is: the fork comes back at its **full length**,
    and any difference is confined to the name stamp. That is a real check —
    a truncation, a shifted fork or a corrupted transfer all still fail it.
    Byte-exactness is proven on the data fork, which has no such structure.
    """
    leaf = mac_path.rsplit(":", 1)[-1]
    if len(got) != len(sent):
        return False, f"length changed: sent {len(sent)} B, got {len(got)} B"
    diffs = [i for i in range(len(sent)) if sent[i] != got[i]]
    if not diffs:
        return True, f"{len(sent)} B byte-exact (no name stamp written)"

    stamp = bytes([len(leaf)]) + leaf.encode("mac_roman", "replace")
    window = got[max(0, diffs[0] - 4):diffs[-1] + 1]
    if stamp in window:
        return True, (f"{len(sent)} B, intact except the Resource Manager's name stamp "
                      f"({len(diffs)} B at offsets {diffs[0]}-{diffs[-1]}, "
                      f"reads {stamp[1:].decode('mac_roman', 'replace')!r}) — D-013")
    return False, _diff_report(sent, got, mac_path)


def _diff_report(sent, got, mac_path):
    """Describe *how* two forks differ, and whether the difference is stable.

    Same length with different content is a transformation, not a truncation,
    and the two candidate causes leave different fingerprints. A few altered
    bytes clustered at the start or end look like a Resource Manager rewriting
    a map — in which case random bytes were never a legitimate payload, since
    they are not a valid resource fork. Differences spread across the whole
    range look like a data-handling fault. A second read distinguishes "the
    write transformed it, stably" from "the read is not reproducible".
    """
    n = min(len(sent), len(got))
    diffs = [i for i in range(n) if sent[i] != got[i]]
    if not diffs:
        return f"lengths differ only: sent {len(sent)} B, got {len(got)} B"

    first, last = diffs[0], diffs[-1]
    span = "front" if last < n * 0.25 else "back" if first > n * 0.75 else "spread"
    again, err = _get(mac_path)
    if err:
        stable = f"second read failed ({err})"
    else:
        stable = "stable across two reads" if again[1] == got else "NOT reproducible between two reads"

    lo = max(0, first - 8)
    return (f"same length ({len(sent)} B), {len(diffs)} bytes differ "
            f"({100.0 * len(diffs) / n:.1f}%), first at offset {first}, last at {last} "
            f"[{span}]; {stable}; at {first}: sent {sent[lo:first + 8].hex()} "
            f"got {got[lo:first + 8].hex()}")


def check_roundtrip(rep, base_path, keep):
    for size in SIZES:
        name = f"{base_path}ABQ1_{size}"
        data = os.urandom(size)
        rsrc = os.urandom(min(size, 8192))
        t0 = time.time()
        status, _, err = _put(name, data, rsrc)
        if status != 0:
            rep.add(f"round trip {size} B", False, f"WRITEFILE: {err or status}")
            continue
        got, gerr = _get(name)
        dt = time.time() - t0
        if got is None:
            rep.add(f"round trip {size} B", False, f"READFILE: {gerr}")
            continue
        back_data, back_rsrc = got
        ok = back_data == data
        rate = (2 * size / dt / 1024) if dt > 0 else 0
        detail = f"data fork byte-exact, {dt:.1f}s (~{rate:.0f} KiB/s both ways)"
        if not ok:
            first = next((i for i, (a, b) in enumerate(zip(data, back_data)) if a != b), None)
            detail = (f"DATA FORK DIFFERS: sent {len(data)} B, got {len(back_data)} B, "
                      f"first difference at offset {first}")
        rep.add(f"round trip {size} B", ok, detail)
        if ok:
            good, why = _judge_resource_fork(rsrc, back_rsrc, name)
            rep.add(f"resource fork {size} B", good, why)
        if not keep:
            try:                                              # best effort: truncate to 0 B
                _put(name, b"", b"")
            except OSError:
                pass          # cleanup must never turn a passed measurement into a failure


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--volume", help="guest volume name; builds '<volume>:AppleBridge:'")
    ap.add_argument("--path", help="explicit guest folder path, must end in ':'")
    ap.add_argument("--out", default=".", help="where to write the screenshot (default: .)")
    ap.add_argument("--keep", action="store_true", help="leave the test files on the guest")
    args = ap.parse_args()

    base = args.path or (f"{args.volume}:AppleBridge:" if args.volume else None)
    if not base or not base.endswith(":"):
        ap.error("need --volume NAME or --path 'Volume:Folder:' (trailing colon required)")

    print("Q1 — native surface (no ToolServer required)")
    print(f"  control port {CTRL_HOST}:{CTRL_PORT}, guest folder {base!r}\n")

    rep = Report()
    try:
        if not check_link(rep):
            print("\nThe daemon is not answering — nothing else can be measured.")
            return 2
    except OSError as e:
        print(f"  control port unreachable: {e}")
        print("  Run this on the machine whose host server is running.")
        return 2

    check_native_verbs(rep, base)
    check_screenshot(rep, args.out)
    check_roundtrip(rep, base, args.keep)

    bad = rep.failed()
    print()
    if bad:
        print(f"{len(bad)} of {len(rep.rows)} checks failed: {', '.join(bad)}")
        return 1
    print(f"all {len(rep.rows)} checks passed — the ToolServer-less surface holds here")
    return 0


if __name__ == "__main__":
    sys.exit(main())
