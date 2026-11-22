#!/usr/bin/env python3
"""The functional battery of the System-7 floor test: what the bridge offers without ToolServer.

    uv run python host/tools/s7floor/battery.py <ctrl-port> <label> <volume> <outdir> [--startup]

Ten checks, each PASS/FAIL with its evidence: STAT, DISKINFO, LISTDIR, PROCLIST, text write+read, a byte-identical
binary put+get, SCREENSHOT2, clipboard set+get, MONITOR 0/1, KEY. Results are appended to <outdir>/results.jsonl, the
bridge-captured screenshot to <outdir>/<label>.png. `--startup` looks for the daemon in Startup Items (a disk from
build_disk.py) instead of <volume>:AppleBridge: (an installed kit).
"""
import base64
import json
import os
import socket
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def main(port, label, vol, outdir, startup):
    os.environ["APPLEBRIDGE_CTRL_PORT"] = str(port)
    sys.path.insert(0, REPO)
    from mcp import tools

    def raw(cmd, timeout=30):
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.sendall((cmd + "\n\n").encode("mac_roman"))
            b = b""
            while True:
                d = s.recv(65536)
                if not d:
                    break
                b += d
        return b.decode("mac_roman", "replace")

    os.makedirs(outdir, exist_ok=True)
    res = {}

    def check(name, ok, ev):
        res[name] = {"ok": bool(ok), "evidence": str(ev)[:300]}
        print(f"{'PASS' if ok else 'FAIL'}  {name:12s} {str(ev)[:140]!r}", flush=True)

    st = tools.mac_status()
    check("status", st.get("daemon_responding"), st.get("raw") or st)
    r = raw("DISKINFO")
    check("diskinfo", r.startswith("STATUS:0") and vol in r, r)
    where = f"{vol}:System Folder:Startup Items:" if startup else f"{vol}:AppleBridge:"
    r = tools.mac_list_files(where)
    check("listdir", r.get("success") and any(f["name"] == "AppleBridge" for f in r.get("files", [])), r.get("raw") or r)
    r = raw("PROCLIST")
    check("proclist", r.startswith("STATUS:0") and "Finder" in r, r)
    text = f"floor test {label} {time.strftime('%H:%M:%S')}"
    w = tools.mac_write_file(f"{vol}:AppleBridge:probe.txt", text)
    rd = tools.mac_read_file(f"{vol}:AppleBridge:probe.txt")
    check("write+read", w.get("success") and text in (rd.get("content") or ""), rd.get("content") or rd)
    src, back = os.path.join(outdir, "fork_probe.bin"), os.path.join(outdir, "fork_back.bin")
    payload = os.urandom(3000)
    open(src, "wb").write(payload)
    p = tools.mac_put_file(src, f"{vol}:AppleBridge:forkprobe", type="BINA", creator="ABrg")
    g = tools.mac_get_file(f"{vol}:AppleBridge:forkprobe", back, format="data")
    same = os.path.exists(back) and open(back, "rb").read() == payload
    check("put+get", p.get("success") and g.get("success") and same, {"put": p.get("success"), "get": g.get("success"), "identical": same})
    s = tools.mac_screenshot()
    if s.get("success"):
        open(os.path.join(outdir, f"{label}.png"), "wb").write(base64.b64decode(s["image"]))
    check("screenshot", s.get("success"), {k: s.get(k) for k in ("encoding", "wire_bytes", "elapsed_ms", "error")})
    want = f"clip {label}"
    c = tools.mac_clipboard_set(want)
    cg = tools.mac_clipboard_get()
    check("clipboard", c.get("success") and cg.get("text") == want, {"set": c.get("message"), "get": cg.get("text")})
    r, r2 = raw("MONITOR:0"), raw("MONITOR:1")
    check("monitor", r.startswith("STATUS:0") and r2.startswith("STATUS:0"), r + " / " + r2)
    k = tools.mac_key(key="escape")
    check("key", k.get("success"), k)
    with open(os.path.join(outdir, "results.jsonl"), "a") as fh:
        fh.write(json.dumps({"label": label, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "results": res}) + "\n")
    print(f"{label}: {sum(v['ok'] for v in res.values())}/{len(res)} passed")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--startup"]
    if len(args) != 4:
        sys.exit(__doc__)
    main(int(args[0]), args[1], args[2], args[3], "--startup" in sys.argv)
