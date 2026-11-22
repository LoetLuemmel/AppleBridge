#!/usr/bin/env python3
"""Framebuffer capture of the s7test guest: one frame, or a series for a clip.

    /usr/bin/python3 host/tools/s7floor/record.py --once OUT.png
    /usr/bin/python3 host/tools/s7floor/record.py FRAMES_DIR STOPFILE      # ~1 frame/s until STOPFILE exists

The container runs the fb-export Basilisk build: SIGUSR1 makes it dump its framebuffer to $BASILISK_FB_DUMP
(/data/fb.bin = ~/s7test/fb.bin on the C2), which host/fb_export.py parses. No bridge traffic, so it also works
while the guest boots, sits in an alert, or has no daemon yet. The signal goes to the process by NAME -- its PID
changed between container starts (7, then not 7).

A series keeps only frames that differ from the previous one; the clip's pacing is done afterwards
(AppleBridge_Media: frames/<clip>/ -> build_clip.sh), from the HHMMSS in each file name.
"""
import hashlib
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import fb_export          # noqa: E402
import screenshot_decode  # noqa: E402

HOST = os.environ.get("S7FLOOR_HOST", "pit@192.168.3.4")
CONTAINER = os.environ.get("S7FLOOR_CONTAINER", "basilisk-s7test")
DUMP = "~/s7test/fb.bin"


def frame():
    cmd = (f"rm -f {DUMP}; docker exec {CONTAINER} pkill -USR1 -x BasiliskII || exit 1; "
           f"for i in 1 2 3 4 5 6 7 8 9 10; do [ -s {DUMP} ] && break; sleep 0.2; done; sleep 0.2; cat {DUMP}")
    buf = subprocess.run(["ssh", HOST, cmd], capture_output=True, timeout=20, check=True).stdout
    return fb_export.parse_dump(buf)


def png(f):
    return screenshot_decode.raw_to_png_indexed(f["width"], f["height"], f["depth"], f["row_bytes"], f["palette"],
                                                f["pixels"])


def series(out, stop):
    os.makedirs(out, exist_ok=True)
    last, n = None, 0
    while not os.path.exists(stop):
        t0 = time.time()
        try:
            f = frame()
            h = hashlib.sha1(f["pixels"]).hexdigest()
            if h != last:
                open(os.path.join(out, "f%s.png" % time.strftime("%H%M%S")), "wb").write(png(f))
                last, n = h, n + 1
        except Exception as e:                         # noqa: BLE001 -- a missed frame must not end the series
            print("skip:", e, flush=True)
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))
    print("frames:", n)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--once":
        f = frame()
        open(sys.argv[2], "wb").write(png(f))
        print(f["width"], f["height"], f["depth"])
    elif len(sys.argv) == 3:
        series(sys.argv[1], sys.argv[2])
    else:
        sys.exit(__doc__)
