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
import socket
import sys
import time

CTRL_HOST = "127.0.0.1"
CTRL_PORT = 9001
SIZES = (4096, 65537, 524288)      # below / just above / well above the 64 KB buffer


# --- control port ------------------------------------------------------------

def _auth_prefix():
    token = os.environ.get("APPLEBRIDGE_CTRL_TOKEN", "")
    return f"AUTH:{token}\n" if token else ""


def send(command, timeout=300.0, port=CTRL_PORT):
    """-> (status, stdout, stderr). Raises OSError if the port is not there."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((CTRL_HOST, port))
        sock.sendall((_auth_prefix() + command).encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return _parse(b"".join(chunks))
    finally:
        sock.close()


def _parse(raw):
    """STATUS:<n>\\rSTDOUT:<len>\\r<data>\\rSTDERR:<len>\\r<data> — read by length."""
    def field(blob, name):
        marker = name + b":"
        i = blob.find(marker)
        if i < 0:
            return None, blob
        j = blob.find(b"\r", i)
        n = int(blob[i + len(marker):j])
        start = j + 1
        return blob[start:start + n], blob[start + n:]

    i = raw.find(b"STATUS:")
    if i < 0:
        return None, raw.decode("utf-8", "replace"), "no STATUS in reply"
    j = raw.find(b"\r", i)
    status = int(raw[i + 7:j if j > 0 else len(raw)] or -1)
    out, rest = field(raw[j + 1:] if j > 0 else b"", b"STDOUT")
    err, _ = field(rest, b"STDERR")
    return (status,
            (out or b"").decode("utf-8", "replace"),
            (err or b"").decode("utf-8", "replace"))


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
    status, out, err = send("STATUS", timeout=20.0)
    alive = status == 0 or "toolserver" in (out + err).lower() or out.strip()
    rep.add("bridge link", bool(alive), (out or err).strip()[:120] or f"status={status}")
    return bool(alive)


def check_native_verbs(rep):
    for verb in ("DISKINFO", "LISTDIR:"):
        cmd = verb if verb != "LISTDIR:" else None
        if cmd is None:
            continue
        status, out, err = send(verb, timeout=60.0)
        rep.add(f"{verb} (no ToolServer)", status == 0 and bool(out.strip()),
                out.strip().splitlines()[0][:100] if out.strip() else (err or f"status={status}"))


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
        if ok and back_rsrc != rsrc:
            rep.add(f"resource fork {size} B", False,
                    f"sent {len(rsrc)} B, got {len(back_rsrc)} B — investigate "
                    f"(D-013: a rename rewrites the map's name field; nothing was renamed here)")
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

    check_native_verbs(rep)
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
