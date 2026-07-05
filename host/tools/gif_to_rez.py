#!/usr/bin/env python3
"""gif_to_rez.py — turn an animated GIF into classic-Mac About-box frames.

Pipeline (host side):
    GIF  --magick -coalesce--> full RGBA frames
         --scale 320x120------> exact-size frames
         --Pillow shared CLUT-> N indexed frames + one 256-entry palette

Emits, into <out_dir>:
    clut.bin            a classic 'clut' ColorTable (ctSeed/ctFlags/ctSize + entries)
    frame_NN.bin        raw 8-bit index pixels, rowBytes = width, top-to-bottom
    about_frames.r      Rez source: reads the .bin blobs into 'clut'/'Gfrm' + a 'GFin' info res
    preview.gif         the downscaled, frame-reduced animation, for eyeballing on the host

The guest builds an offscreen PixMap over each 'Gfrm' blob (pmTable = the 'clut')
and CopyBits it into the About window on a timed WaitNextEvent tick.

Usage:
    uv run --with Pillow python gif_to_rez.py INPUT.gif OUT_DIR \
        [--width 320] [--height 120] [--frames 16] [--base-id 128] [--delay 6]
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

from PIL import Image


def coalesce_and_scale(src, w, h, tmp):
    """Use ImageMagick to disposal-coalesce and hard-scale every frame to WxH PNGs."""
    out = os.path.join(tmp, "f_%04d.png")
    subprocess.run(
        ["magick", src, "-coalesce", "-scale", f"{w}x{h}!", out],
        check=True,
    )
    files = sorted(
        os.path.join(tmp, f) for f in os.listdir(tmp)
        if f.startswith("f_") and f.endswith(".png")
    )
    if not files:
        sys.exit("error: ImageMagick produced no frames")
    return files


def pick(files, n):
    """Evenly sample n frames across the sequence (keeps the loop's rhythm)."""
    total = len(files)
    if n >= total:
        return files
    return [files[round(i * total / n)] for i in range(n)]


def build_palette(frames_rgb, w, h):
    """Derive ONE shared 256-colour palette across all frames (stable colours = no flicker)."""
    montage = Image.new("RGB", (w, h * len(frames_rgb)))
    for i, f in enumerate(frames_rgb):
        montage.paste(f, (0, i * h))
    # Source pixel art is ~128 colours; median-cut to 256, no dither -> crisp pixels.
    return montage.quantize(colors=256, method=Image.MEDIANCUT, dither=Image.NONE)


def packbits_row(src):
    """Apple-standard PackBits on one scanline. UnpackBits(&src,&dst,rowBytes) reverses it.

    Control byte c:  0..127 -> copy next c+1 literals;  129..255 -> repeat next
    byte (257-c) times;  128 -> no-op. We only emit an RLE run for length >= 3,
    so short 2-runs stay in literals (smaller output, still valid).
    """
    dst = bytearray()
    n = len(src)
    i = 0
    while i < n:
        j = i
        while j < n and j - i < 128 and src[j] == src[i]:
            j += 1
        run = j - i
        if run >= 3:
            dst.append(257 - run)      # -(run-1) as an unsigned byte
            dst.append(src[i])
            i = j
        else:
            lit = bytearray()
            while i < n and len(lit) < 128:
                k = i
                while k < n and k - i < 3 and src[k] == src[i]:
                    k += 1
                if k - i == 3:         # a >=3 run starts here -> end the literal
                    break
                lit.append(src[i])
                i += 1
            dst.append(len(lit) - 1)
            dst += lit
    return bytes(dst)


def unpackbits_row(src, off, rowbytes):
    """Reference UnpackBits (for the round-trip self-check). Returns (bytes, new_off)."""
    out = bytearray()
    while len(out) < rowbytes:
        c = src[off]; off += 1
        if c < 128:                    # c+1 literals
            cnt = c + 1
            out += src[off:off + cnt]; off += cnt
        elif c > 128:                  # repeat one byte (257-c) times
            cnt = 257 - c
            out += bytes([src[off]]) * cnt; off += 1
        # c == 128: no-op
    return bytes(out), off


def pack_frame(data, w, h):
    """PackBits a full WxH index frame, row by row; verify it round-trips."""
    packed = bytearray()
    for row in range(h):
        packed += packbits_row(data[row * w:(row + 1) * w])
    # self-check: unpack all rows back and compare
    check = bytearray(); off = 0
    for _ in range(h):
        r, off = unpackbits_row(bytes(packed), off, w)
        check += r
    assert bytes(check) == data, "PackBits round-trip mismatch!"
    return bytes(packed)


def clut_bytes(pal_img):
    """Pack a Pillow palette into a classic-Mac 'clut' ColorTable resource."""
    rgb = pal_img.getpalette()             # RGB triples for the colours actually used
    rgb = (rgb + [0] * (256 * 3))[: 256 * 3]  # pad unused entries to a full 256
    n = 256
    out = bytearray()
    out += struct.pack(">I", 0)          # ctSeed
    out += struct.pack(">H", 0x0000)     # ctFlags (0 for a PixMap colour table)
    out += struct.pack(">H", n - 1)      # ctSize = entries - 1
    for i in range(n):
        r, g, b = rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]
        # 8-bit -> 16-bit component by replication (v * 0x0101).
        out += struct.pack(">HHHH", i, r * 0x0101, g * 0x0101, b * 0x0101)
    return bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("out_dir")
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--height", type=int, default=120)
    ap.add_argument("--frames", type=int, default=16)
    ap.add_argument("--base-id", type=int, default=128)
    ap.add_argument("--delay", type=int, default=6, help="ticks between frames (60/s)")
    args = ap.parse_args()

    W, H, N, BASE = args.width, args.height, args.frames, args.base_id
    os.makedirs(args.out_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        all_files = coalesce_and_scale(args.input, W, H, tmp)
        sel = pick(all_files, N)
        n = len(sel)
        frames_rgb = [Image.open(p).convert("RGB") for p in sel]

        pal_img = build_palette(frames_rgb, W, H)

        # clut
        clut = clut_bytes(pal_img)
        with open(os.path.join(args.out_dir, "clut.bin"), "wb") as f:
            f.write(clut)

        # frames: remap each to the shared palette, PackBits per row, dump packed bytes
        max_idx = 0
        raw_total = packed_total = 0
        for i, fr in enumerate(frames_rgb):
            idx_img = fr.quantize(palette=pal_img, dither=Image.NONE)
            data = idx_img.tobytes()  # W*H bytes, row-major, top-to-bottom
            assert len(data) == W * H, (len(data), W * H)
            max_idx = max(max_idx, max(data))
            packed = pack_frame(data, W, H)  # verified round-trip inside
            raw_total += len(data)
            packed_total += len(packed)
            with open(os.path.join(args.out_dir, f"frame_{i:02d}.bin"), "wb") as f:
                f.write(packed)

        # info resource: count, w, h, baseID, delay, packed(1=PackBits per row)
        gfin = struct.pack(">HHHHHH", n, W, H, BASE, args.delay, 1)
        with open(os.path.join(args.out_dir, "gfin.bin"), "wb") as f:
            f.write(gfin)

        # Self-contained Rez source: frame bytes inlined as `data` hex (one file,
        # no companion blobs -> a single text transfer to the Mac).
        def hexblock(b, indent="\t"):
            lines = []
            for off in range(0, len(b), 16):
                chunk = b[off:off + 16]
                hx = " ".join(chunk[k:k + 2].hex().upper() for k in range(0, len(chunk), 2))
                lines.append('%s$"%s"' % (indent, hx))
            return "\n".join(lines)

        def data_res(typ, rid, blob):
            return "data '%s' (%d) {\n%s\n};\n" % (typ, rid, hexblock(blob))

        inline = ["/* Auto-generated by gif_to_rez.py — self-contained About-box animation. */\n"]
        inline.append(data_res("clut", BASE, clut))
        inline.append(data_res("GFin", BASE, gfin))
        for i in range(n):
            with open(os.path.join(args.out_dir, f"frame_{i:02d}.bin"), "rb") as f:
                inline.append(data_res("Gfrm", BASE + i, f.read()))
        with open(os.path.join(args.out_dir, "about_inline.r"), "w") as f:
            f.write("\n".join(inline))

        # Rez source: pull the blobs in via `read` (keeps the .r tiny)
        r = [
            "/* Auto-generated by gif_to_rez.py — About-box animation frames. */",
            '#include "SysTypes.r"' if False else "",  # no template needed; raw reads
            'read \'clut\' (%d) "clut.bin";' % BASE,
            'read \'GFin\' (%d) "gfin.bin";' % BASE,
        ]
        for i in range(n):
            r.append('read \'Gfrm\' (%d) "frame_%02d.bin";' % (BASE + i, i))
        with open(os.path.join(args.out_dir, "about_frames.r"), "w") as f:
            f.write("\n".join(x for x in r if x != "") + "\n")

        # preview.gif for host-side eyeballing (reassemble the selected frames)
        preview_frames = [Image.open(p).convert("P", palette=Image.ADAPTIVE) for p in sel]
        preview_frames[0].save(
            os.path.join(args.out_dir, "preview.gif"),
            save_all=True, append_images=preview_frames[1:],
            duration=int(args.delay / 60 * 1000), loop=0, optimize=True,
        )

        fork_bytes = len(clut) + len(gfin) + packed_total
        print(f"input        : {args.input}")
        print(f"source frames: {len(all_files)}  ->  selected: {n}")
        print(f"frame size   : {W}x{H}  (rowBytes={W})")
        print(f"palette      : 256 entries, max index used = {max_idx}")
        print(f"base res id  : {BASE}  ('clut'/'GFin'={BASE}, 'Gfrm'={BASE}..{BASE + n - 1})")
        print(f"delay        : {args.delay} ticks/frame (~{60/args.delay:.1f} fps)")
        print(f"pixels raw   : {raw_total:,} bytes")
        print(f"PackBits     : {packed_total:,} bytes  ({packed_total/raw_total*100:.1f}% of raw)")
        print(f"resource data: {fork_bytes:,} bytes (~{fork_bytes/1024:.0f} KB in the fork)  [round-trip OK]")
        print(f"out dir      : {args.out_dir}")
        print("  clut.bin, gfin.bin, frame_00..%02d.bin, about_frames.r, preview.gif" % (n - 1))


if __name__ == "__main__":
    main()
