#!/usr/bin/env python3
"""
Convert a raw classic-Mac screen pixmap to PNG — pure standard library.

The AppleBridge daemon captures the main GDevice PixMap and streams it over the
bridge with a header that declares everything needed to decode it:

    IMAGE:<width>:<height>:<depth>:<rowBytes>:<clutCount>:<dataSize>\\n
    <clutCount*3 bytes CLUT>      (RGB triples; omitted when clutCount == 0)
    <dataSize bytes raw pixels>

`raw_to_png` turns that into a truecolor PNG. No Pillow — only zlib/struct/
binascii — so it runs under the firewall-mandated /usr/bin/python3 that
host_server.py uses.

Supported depths: 1, 2, 4, 8 (indexed via the CLUT, or grayscale fallback),
16 (RGB 5-5-5, big-endian), 32 (xRGB-8888, big-endian as classic stores it).
"""
import binascii
import struct
import zlib


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF))


def _palette(clut: bytes):
    """CLUT bytes (RGB triples) -> list of (r, g, b)."""
    return [(clut[i], clut[i + 1], clut[i + 2]) for i in range(0, len(clut) - 2, 3)]


def _expand5(v: int) -> int:
    """5-bit channel -> 8-bit."""
    return (v << 3) | (v >> 2)


def raw_to_png(width: int, height: int, depth: int, row_bytes: int,
               clut: bytes, pixels: bytes, region=None) -> bytes:
    """Decode a raw Mac pixmap into an 8-bit truecolor PNG (bytes).

    `region` optionally crops the output to (x, y, w, h) in screen pixels — so a
    caller can read one dialog instead of decoding the full 1024x768 frame. The
    crop is clamped to the image bounds.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"bad dimensions {width}x{height}")
    if len(pixels) < height * row_bytes:
        raise ValueError(f"short pixel buffer: {len(pixels)} < {height*row_bytes}")

    if region is not None:
        rx, ry, rw, rh = region
        rx = max(0, min(int(rx), width))
        ry = max(0, min(int(ry), height))
        rw = max(0, min(int(rw), width - rx))
        rh = max(0, min(int(rh), height - ry))
        if rw == 0 or rh == 0:
            raise ValueError(f"empty crop region {region} on {width}x{height}")
    else:
        rx, ry, rw, rh = 0, 0, width, height

    pal = _palette(clut)

    def indexed_rgb(idx: int):
        if pal and idx < len(pal):
            return pal[idx]
        # grayscale fallback: map the index range onto 0..255
        maxv = (1 << depth) - 1 if depth <= 8 else 255
        # classic 1-bit convention without a CLUT: 0 = white, 1 = black
        if depth == 1:
            g = 0 if idx else 255
            return (g, g, g)
        g = (idx * 255) // maxv if maxv else 0
        return (g, g, g)

    raw = bytearray()
    for y in range(ry, ry + rh):
        raw.append(0)  # PNG filter: none
        base = y * row_bytes
        if depth == 8:
            for x in range(rx, rx + rw):
                raw += bytes(indexed_rgb(pixels[base + x]))
        elif depth == 4:
            for x in range(rx, rx + rw):
                b = pixels[base + (x >> 1)]
                idx = (b >> 4) if (x & 1) == 0 else (b & 0x0F)
                raw += bytes(indexed_rgb(idx))
        elif depth == 2:
            for x in range(rx, rx + rw):
                b = pixels[base + (x >> 2)]
                idx = (b >> (6 - 2 * (x & 3))) & 0x03
                raw += bytes(indexed_rgb(idx))
        elif depth == 1:
            for x in range(rx, rx + rw):
                b = pixels[base + (x >> 3)]
                idx = (b >> (7 - (x & 7))) & 0x01
                raw += bytes(indexed_rgb(idx))
        elif depth == 16:
            for x in range(rx, rx + rw):
                off = base + x * 2
                val = (pixels[off] << 8) | pixels[off + 1]
                raw += bytes((_expand5((val >> 10) & 0x1F),
                              _expand5((val >> 5) & 0x1F),
                              _expand5(val & 0x1F)))
        elif depth == 32:
            for x in range(rx, rx + rw):
                off = base + x * 4
                # classic stores xRGB big-endian: skip the leading (alpha/unused) byte
                raw += pixels[off + 1:off + 4]
        else:
            raise ValueError(f"unsupported depth {depth}")

    ihdr = struct.pack(">IIBBBBB", rw, rh, 8, 2, 0, 0, 0)  # 8-bit truecolor RGB
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _png_chunk(b"IEND", b""))


def _selftest():
    """Synthetic 8-bit + CLUT round-trip: a 4x2 image of known palette indices."""
    # palette: 0=red, 1=green, 2=blue, 3=white
    clut = bytes([255, 0, 0,   0, 255, 0,   0, 0, 255,   255, 255, 255])
    width, height, depth, row_bytes = 4, 2, 8, 4
    pixels = bytes([0, 1, 2, 3,   3, 2, 1, 0])
    png = raw_to_png(width, height, depth, row_bytes, clut, pixels)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "bad PNG signature"
    # decode IHDR to confirm dimensions
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (4, 2), f"IHDR dims {w}x{h}"
    # verify IDAT decompresses to the expected RGB scanlines
    idat_start = png.find(b"IDAT") + 4
    idat_len = struct.unpack(">I", png[idat_start - 8:idat_start - 4])[0]
    raw = zlib.decompress(png[idat_start:idat_start + idat_len])
    # row 0: filter(0) + RGB for indices 0,1,2,3
    assert raw[0] == 0 and raw[1:13] == bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255]), raw[:13]
    print("screenshot_decode self-test OK:", len(png), "byte PNG")


if __name__ == "__main__":
    _selftest()
