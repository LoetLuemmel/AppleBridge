#!/usr/bin/env python3
"""Capture the guest screen from the LOCAL Basilisk II process (fb-export).

The `fb-export` build of the emulator (macemu branch `fb-export`, a6e328b)
writes the Mac framebuffer, the current palette and the dirty tiles of the
last 10 Hz scan to $BASILISK_FB_DUMP (default /tmp/basilisk_fb.bin) when it
receives SIGUSR1 — atomically, via a temporary name, so a partial file is
never observed. Measured 2026-08-30: 12–31 ms from signal to a decoded PNG,
zero guest traffic. This module is the reusable half of that spike: request a
dump, parse it, decode it to PNG with host/screenshot_decode.

SAFETY — why the binary is checked before the signal: SIGUSR1's default
action TERMINATES a process that has no handler for it, and Basilisk II must
never be hard-killed (the guest image can corrupt — D-004). The patched build
carries the literal "BASILISK_FB_DUMP" in its text; `binary_has_export` reads
the *running pid's own executable* for that marker, so a published (unpatched)
bundle, a SheepShaver, or any other stand-in is refused without ever being
signalled. The refusal is the safe outcome.

Dump format (all little-endian, host order):
    "ABFB" u32 x  u32 y  u32 rowBytes  u32 depth(bpp)  u32 nBoxes
    256 * {r,g,b}                          (current 8-bit palette)
    nBoxes * {u16 x, u16 y, u16 w, u16 h}  (64x64 tiles dirty in the LAST scan)
    y * rowBytes bytes of pixels

The dirty boxes are what the refresh that performed the dump found changed
since the PREVIOUS 10 Hz scan — not since the previous dump. A consumer that
wants "since my last capture" keeps its own copy.

The env var is shared: the emulator reads $BASILISK_FB_DUMP from ITS
environment at dump time, this module from its own. Launch both with the same
value (or neither — the default matches) or the reader waits on a file the
emulator never writes.
"""

import os
import signal
import struct
import subprocess
import sys
import time

_HOST_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)
import screenshot_decode  # noqa: E402

MARKER = b"BASILISK_FB_DUMP"


def dump_path():
    return os.environ.get("BASILISK_FB_DUMP", "/tmp/basilisk_fb.bin")


class FbExportError(Exception):
    """The fb-export path did not produce a frame. `reason` is a short code
    (no_emulator / not_basilisk / unpatched / signal_failed / timeout /
    bad_dump) so a caller can decide about a fallback without parsing prose."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def find_basilisk():
    """(pid, executable path) of the running LOCAL Basilisk II.

    Only Basilisk II — the export patch lives in its SDL video driver, and a
    SheepShaver would be terminated by the signal, not helped by it."""
    out = subprocess.run(["pgrep", "-x", "BasiliskII"],
                         capture_output=True, text=True).stdout.split()
    if not out:
        raise FbExportError("no_emulator", "no local BasiliskII process")
    pid = int(out[0])
    exe = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                         capture_output=True, text=True).stdout.strip()
    if not exe:
        raise FbExportError("no_emulator", f"BasiliskII pid {pid} vanished")
    return pid, exe


def binary_has_export(exe):
    """True iff the executable carries the fb-export marker string.

    A plain byte search in chunks (with overlap, so a marker straddling a
    chunk boundary is still seen) — deliberately not `grep`, which declined
    to match inside this Mach-O binary while `strings` found the marker."""
    keep = len(MARKER) - 1
    try:
        with open(exe, "rb") as fh:
            tail = b""
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    return False
                if MARKER in tail + chunk[:keep] or MARKER in chunk:
                    return True
                tail = chunk[-keep:]
    except OSError as e:
        raise FbExportError("unpatched", f"cannot read emulator binary: {e}")


def request_dump(pid, path=None, timeout=5.0):
    """SIGUSR1 the (verified) emulator and return the fresh dump's bytes.

    Freshness is the mtime changing: the emulator writes to a temp name and
    renames, so a changed mtime is always a complete file. The dump happens on
    the next video refresh tick (~10 Hz), hence the poll."""
    path = path or dump_path()
    try:
        before = os.stat(path).st_mtime_ns
    except OSError:
        before = -1
    t0 = time.monotonic()
    try:
        os.kill(pid, signal.SIGUSR1)
    except (ProcessLookupError, PermissionError) as e:
        raise FbExportError("signal_failed", f"cannot signal pid {pid}: {e}")
    while True:
        try:
            if os.stat(path).st_mtime_ns != before:
                break
        except OSError:
            pass
        if time.monotonic() - t0 > timeout:
            raise FbExportError(
                "timeout",
                f"no dump at {path} within {timeout:g}s — emulator paused, "
                "or launched with a different $BASILISK_FB_DUMP")
        time.sleep(0.002)
    with open(path, "rb") as fh:
        return fh.read()


def parse_dump(buf):
    """Dump bytes -> dict of the frame's parts."""
    if len(buf) < 24:
        raise FbExportError("bad_dump", f"dump too short: {len(buf)} bytes")
    magic, x, y, rb, depth, nboxes = struct.unpack("<4sIIIII", buf[:24])
    if magic != b"ABFB":
        raise FbExportError("bad_dump", f"bad magic {magic!r}")
    off = 24
    palette = buf[off:off + 768]
    off += 768
    boxes = [struct.unpack("<HHHH", buf[off + i * 8:off + i * 8 + 8])
             for i in range(nboxes)]
    off += nboxes * 8
    pixels = buf[off:off + y * rb]
    if len(pixels) != y * rb:
        raise FbExportError("bad_dump",
                            f"short pixel block: {len(pixels)} != {y * rb}")
    return {"width": x, "height": y, "row_bytes": rb, "depth": depth,
            "palette": palette, "boxes": boxes, "pixels": pixels}


def check():
    """Availability without side effects: (pid, exe) if a signal would be
    safe AND useful, else the FbExportError a capture would raise."""
    pid, exe = find_basilisk()
    if not binary_has_export(exe):
        raise FbExportError(
            "unpatched",
            f"{exe} has no fb-export handler — signalling it would TERMINATE "
            "the emulator, refused (build: macemu branch fb-export)")
    return pid, exe


def capture_png(region=None, timeout=5.0):
    """One frame as (png_bytes, meta). Raises FbExportError, never signals an
    unverified process. `region` = (x, y, w, h) guest pixels, cropped at
    decode — the dump itself is always the full frame (it is local and cheap;
    the crop only shrinks the PNG)."""
    t0 = time.monotonic()
    pid, exe = check()
    frame = parse_dump(request_dump(pid, timeout=timeout))
    png = screenshot_decode.raw_to_png_indexed(
        frame["width"], frame["height"], frame["depth"], frame["row_bytes"],
        frame["palette"], frame["pixels"],
        region=tuple(region) if region else None)
    meta = {
        "width": frame["width"], "height": frame["height"],
        "depth": frame["depth"], "dirty_tiles": len(frame["boxes"]),
        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
        "pid": pid,
    }
    return png, meta


def main(argv):
    args = list(argv)

    def opt(name):
        if name in args:
            i = args.index(name)
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return None

    out = opt("--out") or "fb_shot.png"
    region = opt("--region")
    if region:
        region = tuple(int(v) for v in region.split(","))
        if len(region) != 4:
            raise SystemExit("--region wants x,y,w,h")
    if "--check" in args:
        try:
            pid, exe = check()
            print(f"available: pid {pid} {exe}")
            return 0
        except FbExportError as e:
            print(f"unavailable ({e.reason}): {e}")
            return 1
    try:
        png, meta = capture_png(region=region)
    except FbExportError as e:
        raise SystemExit(f"capture failed ({e.reason}): {e}")
    with open(out, "wb") as fh:
        fh.write(png)
    print(f"{out}: {meta['width']}x{meta['height']}x{meta['depth']} "
          f"{len(png)} B in {meta['elapsed_ms']} ms, "
          f"dirty tiles last scan: {meta['dirty_tiles']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
