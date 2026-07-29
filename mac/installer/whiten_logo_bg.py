#!/usr/bin/env python3
"""Lift the animated logo's near-black palette entries to white.

WHY this exists as a transform rather than a re-render: the source animation is
drawn light-on-dark, and the installer window is white, so the logo sat in the
corner as a black rectangle. The fix does not need the artwork re-encoded at
all — the frames are palette INDICES and the background is a range of near-black
entries in the `'clut'`. Repainting those entries leaves every `'Gfrm'` blob
byte-for-byte identical, which is the smallest change that can produce the
result and the easiest to undo.

WHY a threshold rather than one index: the background is not flat. Index 255
(RGB 12,15,20) covers 59% of a frame, but an anti-aliased spatter of other
near-black entries sits on top of it — flipping only 255 leaves the logo
speckled with dark dots, which was measured, looked at, and rejected. Every
entry below the luminance threshold goes white together.

The threshold is deliberately BELOW the artwork's own dark strokes (the
mascot's eyes and mouth, the Macintosh outline), so those survive; that is why
40 works and, say, 90 would erase the drawing. Re-run and look before changing
it.

    python3 whiten_logo_bg.py [--threshold 40] [--check]

`--check` reports what would change and writes nothing.
"""
import argparse
import re
import sys

R_FILE = "installer_logo.r"
CLUT_TAG = "data 'clut' (200)"
MARK = "/* Palette post-processed by whiten_logo_bg.py"


def parse_hex(block):
    return bytes.fromhex("".join(re.findall(r'\$"([0-9A-Fa-f ]+)"', block)).replace(" ", ""))


def hexblock(b, per=16):
    lines = []
    for i in range(0, len(b), per):
        chunk = b[i:i + per].hex()
        lines.append('\t$"%s"' % " ".join(chunk[j:j + 4] for j in range(0, len(chunk), 4)))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--threshold", type=int, default=40,
                    help="max channel value still counted as background (default 40)")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    src = open(R_FILE, encoding="utf-8").read()
    if MARK in src:
        sys.exit("already whitened — the palette is not black any more; "
                 "regenerate from gif_to_rez.py first if you want to redo it")

    start = src.index(CLUT_TAG)
    end = src.index("};", start)
    body = src[start:end]
    blob = parse_hex(body)

    count = int.from_bytes(blob[6:8], "big") + 1
    out = bytearray(blob)
    changed = []
    for i in range(count):
        o = 8 + i * 8
        r, g, b = blob[o + 2], blob[o + 4], blob[o + 6]
        if max(r, g, b) < args.threshold:
            out[o + 2:o + 8] = b"\xff\xff\xff\xff\xff\xff"
            changed.append((int.from_bytes(blob[o:o + 2], "big"), (r, g, b)))

    print("palette entries: %d, below threshold %d: %d"
          % (count, args.threshold, len(changed)))
    if changed:
        print("  e.g. %s" % ", ".join("#%d%s" % (i, c) for i, c in changed[:4]))
    if args.check:
        return
    if not changed:
        sys.exit("nothing below the threshold — refusing to write an identical file")

    head = "%s (threshold %d): the frames are UNTOUCHED,\n   only near-black " \
           "clut entries were repainted white so the animation sits on the\n" \
           "   installer's white window instead of a black rectangle. */\n" \
           % (MARK, args.threshold)
    new = src[:start] + head + CLUT_TAG + " {\n" + hexblock(bytes(out)) + "\n" + src[end:]
    open(R_FILE, "w", encoding="utf-8").write(new)
    print("rewrote %s (%d entries -> white)" % (R_FILE, len(changed)))


if __name__ == "__main__":
    main()
