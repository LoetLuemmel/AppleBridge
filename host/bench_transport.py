#!/usr/bin/env python3
"""
bench_transport.py — measure AppleBridge bridge performance for one Basilisk
Ethernet backend, so the etherhelper/en8 path and the slirp path can be compared
apples-to-apples before switching the default.

WHY: the slirp migration plan flags throughput as the one thing to confirm live
(slirp is user-mode NAT in host software — more CPU per packet than near-raw
frame passing — but it stays host-internal, removing a wire hop; net effect is
unknown until measured). This script captures a labelled baseline now and the
same numbers after the switch.

WHAT IT MEASURES (all via the local control port :9001, so no MCP overhead):
  * latency      — N x `Echo HELLO` round trips (ms/op). Dominated by the AE
                   round trip to ToolServer + the bridge hop; backend-sensitive
                   mostly in its tail.
  * catenate     — N x `Catenate <file>` : raw file bytes streamed back over the
                   bridge. Bytes measured on :9001 == bytes over the bridge, so
                   this is a clean MiB/s figure (~330 KB payload by default).
  * dumpfile     — N x `DumpFile <file>` : ~1.3 MB of hex text — a larger,
                   multi-hundred-KB transfer to let bandwidth dominate fixed cost.
  * screenshot   — N x `screenshot` : the daemon captures the main GDevice PixMap
                   (~768 KB raw at 1024x768x8), streams it over the bridge, host
                   decodes to PNG. Measured as wall-time (the PNG payload on :9001
                   is not the bridge payload, so time — not its byte count — is the
                   backend-sensitive metric here).

USAGE:
  /usr/bin/python3 bench_transport.py --backend etherhelper-en8
  /usr/bin/python3 bench_transport.py --backend slirp --quick
  /usr/bin/python3 bench_transport.py --backend etherhelper-en8 \
      --file 'MeinMac:MPW:AppleBridge:bin:AppleBridge.NJ'

Results are printed as a table and written to host/bench_results/<backend>-<ts>.json.
Compare two runs with:  bench_transport.py --compare A.json B.json
"""
import argparse
import json
import os
import socket
import statistics
import sys
import time

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 9001
DEFAULT_FILE = "MeinMac:MPW:AppleBridge:bin:AppleBridge.NJ"
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_results")


def run_command(cmd, timeout=240.0):
    """Send one command to the control port; return (received_bytes, elapsed_s, status).

    elapsed is wall time from just-before-connect to the server closing the
    socket — i.e. the full user-visible round trip. status is the integer parsed
    from the STATUS:<n> frame, or None if it could not be read.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    head = b""
    n = 0
    t0 = time.perf_counter()
    try:
        s.connect((CONTROL_HOST, CONTROL_PORT))
        s.sendall(cmd.encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        while True:
            c = s.recv(65536)
            if not c:
                break
            n += len(c)
            if len(head) < 64:
                head += c[: 64 - len(head)]
        dt = time.perf_counter() - t0
    finally:
        s.close()
    status = None
    if head.startswith(b"STATUS:"):
        try:
            status = int(head[7:].split(b"S", 1)[0].strip() or b"-1")
        except ValueError:
            status = None
    return n, dt, status


def summarize(samples):
    """Stats over a list of floats (already in the target unit)."""
    s = sorted(samples)
    n = len(s)
    return {
        "n": n,
        "min": s[0],
        "median": statistics.median(s),
        "mean": statistics.fmean(s),
        "p90": s[min(n - 1, int(round(0.9 * (n - 1))))],
        "max": s[-1],
        "stdev": statistics.pstdev(s) if n > 1 else 0.0,
    }


def bench_latency(iters):
    print(f"  latency    : {iters}x Echo HELLO ...", end="", flush=True)
    run_command("Echo HELLO")  # warmup
    ms = []
    for _ in range(iters):
        _, dt, st = run_command("Echo HELLO")
        if st != 0:
            print(f" FAILED (STATUS={st})")
            return None
        ms.append(dt * 1000.0)
    print(" ok")
    return {"unit": "ms", "samples": ms, "stats": summarize(ms)}


def bench_transfer(label, cmd, iters):
    print(f"  {label:11s}: {iters}x ...", end="", flush=True)
    nb, _, st = run_command(cmd)  # warmup, learn payload size
    if st != 0:
        print(f" FAILED (STATUS={st})")
        return None
    mibs, times, sizes = [], [], []
    for _ in range(iters):
        n, dt, st = run_command(cmd)
        if st != 0:
            print(f" FAILED (STATUS={st})")
            return None
        mibs.append((n / (1024 * 1024)) / dt)
        times.append(dt * 1000.0)
        sizes.append(n)
    print(f" ok ({statistics.median(sizes)/1024:.0f} KiB/transfer)")
    return {
        "command": cmd,
        "payload_bytes_median": int(statistics.median(sizes)),
        "throughput_mibs": {"unit": "MiB/s", "samples": mibs, "stats": summarize(mibs)},
        "time_ms": {"unit": "ms", "samples": times, "stats": summarize(times)},
    }


def bench_screenshot(iters):
    print(f"  screenshot : {iters}x ...", end="", flush=True)
    # The control port triggers a screenshot with the bare verb + blank line.
    cmd = "screenshot\n\n"
    nb, _, _ = run_command(cmd, timeout=60.0)  # warmup
    times, sizes = [], []
    for _ in range(iters):
        n, dt, _ = run_command(cmd, timeout=60.0)
        if n < 1000:  # a real screenshot frame is large; tiny == error
            print(f" FAILED (only {n} bytes returned)")
            return None
        times.append(dt * 1000.0)
        sizes.append(n)
    print(f" ok ({statistics.median(sizes)/1024:.0f} KiB PNG/frame)")
    return {
        "png_bytes_median": int(statistics.median(sizes)),
        "time_ms": {"unit": "ms", "samples": times, "stats": summarize(times)},
    }


def ether_line():
    try:
        with open(os.path.expanduser("~/.basilisk_ii_prefs")) as f:
            for line in f:
                if line.startswith("ether "):
                    return line.strip()
    except OSError:
        pass
    return "(unknown)"


def fmt_stats(st, unit):
    return (f"min {st['min']:.1f} / med {st['median']:.1f} / mean {st['mean']:.1f} "
            f"/ p90 {st['p90']:.1f} / max {st['max']:.1f} {unit} (sd {st['stdev']:.1f})")


def print_report(res):
    print(f"\n=== AppleBridge transport benchmark: {res['backend']} ===")
    print(f"  basilisk ether : {res['ether_line']}")
    print(f"  timestamp      : {res['timestamp']}")
    lat = res["tests"].get("latency")
    if lat:
        print(f"  latency (Echo) : {fmt_stats(lat['stats'], 'ms')}")
    for key in ("catenate", "dumpfile"):
        t = res["tests"].get(key)
        if t:
            kib = t["payload_bytes_median"] / 1024
            print(f"  {key:14s} : {fmt_stats(t['throughput_mibs']['stats'], 'MiB/s')}  "
                  f"[{kib:.0f} KiB/transfer]")
    sh = res["tests"].get("screenshot")
    if sh:
        print(f"  screenshot     : {fmt_stats(sh['time_ms']['stats'], 'ms')}  "
              f"[{sh['png_bytes_median']/1024:.0f} KiB PNG]")


def do_compare(path_a, path_b):
    a = json.load(open(path_a))
    b = json.load(open(path_b))
    print(f"\n=== Compare: {a['backend']}  vs  {b['backend']} ===\n")

    def med(res, *path):
        node = res["tests"]
        for p in path:
            node = node.get(p, {}) if isinstance(node, dict) else {}
        return node.get("median") if isinstance(node, dict) else None

    rows = [
        ("latency Echo (ms, lower better)", ("latency", "stats"), False),
        ("catenate (MiB/s, higher better)", ("catenate", "throughput_mibs", "stats"), True),
        ("dumpfile (MiB/s, higher better)", ("dumpfile", "throughput_mibs", "stats"), True),
        ("screenshot (ms, lower better)", ("screenshot", "time_ms", "stats"), False),
    ]
    print(f"  {'metric':34s} {a['backend']:>16s} {b['backend']:>16s}   delta")
    for label, path, higher_better in rows:
        va, vb = med(a, *path), med(b, *path)
        if va is None or vb is None:
            print(f"  {label:34s} {'n/a':>16} {'n/a':>16}")
            continue
        if higher_better:
            pct = (vb - va) / va * 100.0
            verdict = f"{pct:+.1f}% ({'faster' if pct > 0 else 'slower'})"
        else:
            pct = (vb - va) / va * 100.0
            verdict = f"{pct:+.1f}% ({'slower' if pct > 0 else 'faster'})"
        print(f"  {label:34s} {va:16.1f} {vb:16.1f}   {verdict}")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", help="label for this run, e.g. etherhelper-en8 or slirp")
    ap.add_argument("--file", default=DEFAULT_FILE,
                    help=f"Mac file to Catenate/DumpFile (default: {DEFAULT_FILE})")
    ap.add_argument("--latency-iters", type=int, default=30)
    ap.add_argument("--catenate-iters", type=int, default=15)
    ap.add_argument("--dumpfile-iters", type=int, default=8)
    ap.add_argument("--screenshot-iters", type=int, default=8)
    ap.add_argument("--quick", action="store_true", help="fewer iterations for a fast check")
    ap.add_argument("--no-screenshot", action="store_true")
    ap.add_argument("--out", help="explicit output JSON path")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"),
                    help="print a comparison of two result files and exit")
    args = ap.parse_args()

    if args.compare:
        do_compare(*args.compare)
        return

    if not args.backend:
        ap.error("--backend is required (e.g. --backend etherhelper-en8)")

    if args.quick:
        args.latency_iters, args.catenate_iters = 10, 5
        args.dumpfile_iters, args.screenshot_iters = 3, 3

    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"Benchmarking backend '{args.backend}'  (ether: {ether_line()})")
    print(f"Transfer file: {args.file}\n")

    res = {
        "backend": args.backend,
        "timestamp": ts,
        "ether_line": ether_line(),
        "file": args.file,
        "tests": {},
    }
    res["tests"]["latency"] = bench_latency(args.latency_iters)
    res["tests"]["catenate"] = bench_transfer("catenate", f"Catenate {args.file}",
                                              args.catenate_iters)
    res["tests"]["dumpfile"] = bench_transfer("dumpfile", f"DumpFile {args.file}",
                                              args.dumpfile_iters)
    if not args.no_screenshot:
        res["tests"]["screenshot"] = bench_screenshot(args.screenshot_iters)

    print_report(res)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = args.out or os.path.join(
        RESULTS_DIR, f"{args.backend}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=2)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
