#!/usr/bin/env python3
"""Read a Basilisk II framebuffer dump (the `fb-export` spike) into a PNG.

    fb_dump_read.py [--pid PID] [--out out.png] [--runs N]

Sends SIGUSR1 to the emulator, waits for a fresh /tmp/basilisk_fb.bin (atomic
rename, so a partial file is never seen), parses
    "ABFB" u32 x y rowBytes depth nBoxes | 768 palette bytes | boxes | pixels
and writes an indexed PNG with host/screenshot_decode.raw_to_png_indexed.
Prints per-run timings: signal->file, parse+PNG, total, plus the dirty tiles
the emulator's own 64x64 scan reported for that refresh.
"""
import os, sys, time, struct, signal, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import screenshot_decode as sd

PATH = os.environ.get("BASILISK_FB_DUMP", "/tmp/basilisk_fb.bin")


def emulator_pid():
    out = subprocess.run(["pgrep", "-x", "BasiliskII"], capture_output=True, text=True).stdout.split()
    if not out:
        raise SystemExit("no BasiliskII process")
    return int(out[0])


def parse(buf):
    magic, x, y, rb, depth, nboxes = struct.unpack("<4sIIIII", buf[:24])
    if magic != b"ABFB":
        raise ValueError(f"bad magic {magic!r}")
    off = 24
    pal = buf[off:off + 768]; off += 768
    boxes = [struct.unpack("<HHHH", buf[off + i * 8:off + i * 8 + 8]) for i in range(nboxes)]
    off += nboxes * 8
    pixels = buf[off:off + y * rb]
    if len(pixels) != y * rb:
        raise ValueError(f"short pixel block {len(pixels)} != {y*rb}")
    return x, y, rb, depth, pal, boxes, pixels


def one(pid, out_path):
    try:
        before = os.stat(PATH).st_mtime_ns
    except FileNotFoundError:
        before = -1
    t0 = time.perf_counter()
    os.kill(pid, signal.SIGUSR1)
    while True:
        try:
            st = os.stat(PATH)
            if st.st_mtime_ns != before:
                break
        except FileNotFoundError:
            pass
        if time.perf_counter() - t0 > 5:
            raise SystemExit("no dump within 5 s")
        time.sleep(0.002)
    t1 = time.perf_counter()
    buf = open(PATH, "rb").read()
    x, y, rb, depth, pal, boxes, pixels = parse(buf)
    png = sd.raw_to_png_indexed(x, y, depth, rb, pal, pixels)
    t2 = time.perf_counter()
    if out_path:
        open(out_path, "wb").write(png)
    return (t1 - t0) * 1000, (t2 - t1) * 1000, len(buf), len(png), boxes, (x, y, depth)


def main():
    args = sys.argv[1:]
    pid = int(args[args.index("--pid") + 1]) if "--pid" in args else emulator_pid()
    out = args[args.index("--out") + 1] if "--out" in args else None
    runs = int(args[args.index("--runs") + 1]) if "--runs" in args else 1
    for i in range(runs):
        sig_ms, png_ms, nbytes, npng, boxes, dims = one(pid, out)
        print(f"run{i}: signal->file {sig_ms:.1f} ms, parse+png {png_ms:.1f} ms, "
              f"total {sig_ms+png_ms:.1f} ms, dump {nbytes} B, png {npng} B, "
              f"{dims[0]}x{dims[1]}x{dims[2]}, dirty tiles last scan: {len(boxes)}", flush=True)


if __name__ == "__main__":
    main()
