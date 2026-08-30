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

Since daemon 0.8d46 there is a second frame, IMAGE2, whose payload may be
PackBits-packed per row (enc 1) or a row-delta against the previous full frame
(enc 2). `unpack_rows`, `parse_delta` and `apply_delta` turn either back into
the same raw pixmap the functions above consume, and `raw_to_png_indexed`
writes an indexed PNG (colour type 3) straight from those rows — no per-pixel
Python loop, which is what made the truecolor path cost 0.3 s per frame.
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


# --- IMAGE2 payloads: PackBits rows and row deltas ---------------------------

def unpack_bits(src, off, expected):
    """Apple PackBits: decode `expected` bytes starting at src[off].

    Returns (row_bytes, new_off). Control byte c: 0..127 copies the next c+1
    literal bytes; 129..255 repeats the next byte 257-c times; 128 is a no-op.
    Raises ValueError on a short or overlong stream — a packed row that does not
    land exactly on `expected` means the wire is out of step, and a silently
    padded row would hide that."""
    out = bytearray()
    n = len(src)
    while len(out) < expected:
        if off >= n:
            raise ValueError(f"PackBits stream ended {expected - len(out)} bytes early")
        c = src[off]
        off += 1
        if c < 128:
            cnt = c + 1
            if off + cnt > n:
                raise ValueError("PackBits literal overruns the payload")
            out += src[off:off + cnt]
            off += cnt
        elif c > 128:
            if off >= n:
                raise ValueError("PackBits repeat overruns the payload")
            out += bytes((src[off],)) * (257 - c)
            off += 1
        # c == 128: no-op
    if len(out) != expected:
        raise ValueError(f"PackBits row decoded to {len(out)} bytes, expected {expected}")
    return bytes(out), off


def unpack_rows(payload, off, rows, row_bytes):
    """Decode `rows` PackBits rows, each prefixed by a 2-byte big-endian packed
    length (PICT style). Returns (bytes of rows*row_bytes, new_off)."""
    out = bytearray()
    n = len(payload)
    for _ in range(rows):
        if off + 2 > n:
            raise ValueError("row length prefix missing")
        plen = (payload[off] << 8) | payload[off + 1]
        off += 2
        if off + plen > n:
            raise ValueError("packed row overruns the payload")
        row, end = unpack_bits(payload[off:off + plen], 0, row_bytes)
        if end != plen:
            raise ValueError(f"packed row has {plen - end} trailing bytes")
        out += row
        off += plen
    return bytes(out), off


def parse_delta(payload, row_bytes, packed=True):
    """enc 2: a sequence of <y0:2><n:2> runs, each followed by n rows (packed
    as enc 1 when `packed`, raw otherwise). Returns [(y0, rows_bytes), ...]."""
    runs = []
    off = 0
    n = len(payload)
    while off < n:
        if off + 4 > n:
            raise ValueError("delta run header truncated")
        y0 = (payload[off] << 8) | payload[off + 1]
        cnt = (payload[off + 2] << 8) | payload[off + 3]
        off += 4
        if packed:
            data, off = unpack_rows(payload, off, cnt, row_bytes)
        else:
            end = off + cnt * row_bytes
            if end > n:
                raise ValueError("raw delta run overruns the payload")
            data = bytes(payload[off:end])
            off = end
        runs.append((y0, data))
    return runs


def apply_delta(prev, row_bytes, height, runs):
    """Composite delta runs onto a copy of the previous full frame.

    Each delivered row is the XOR of the new row with the previous frame's row
    (enc 2): the bytes that did not change are zero on the wire, which is what
    lets PackBits collapse a row where one glyph was redrawn to a few bytes."""
    if len(prev) < height * row_bytes:
        raise ValueError("previous frame is shorter than the declared image")
    cur = bytearray(prev)
    for y0, data in runs:
        rows = len(data) // row_bytes
        if y0 < 0 or y0 + rows > height:
            raise ValueError(f"delta run {y0}+{rows} outside {height} rows")
        a = y0 * row_bytes
        b = a + rows * row_bytes
        old = int.from_bytes(cur[a:b], "big")
        cur[a:b] = (old ^ int.from_bytes(data, "big")).to_bytes(b - a, "big")
    return bytes(cur)


def decode_rows(enc, payload, rows, row_bytes):
    """enc 0 raw / enc 1 PackBits rows -> exactly rows*row_bytes bytes."""
    if enc == 0:
        need = rows * row_bytes
        if len(payload) < need:
            raise ValueError(f"short raw payload: {len(payload)} < {need}")
        return bytes(payload[:need])
    if enc == 1:
        data, off = unpack_rows(payload, 0, rows, row_bytes)
        return data
    raise ValueError(f"unsupported encoding {enc}")


# --- indexed PNG: the fast path for depths 1/2/4/8 ---------------------------

def _indexed_palette(clut, depth):
    entries = 1 << depth
    pal = _palette(clut)[:entries]
    if not pal:
        if depth == 1:
            pal = [(255, 255, 255), (0, 0, 0)]   # classic: 0 = white
        else:
            maxv = entries - 1
            pal = [((i * 255) // maxv,) * 3 for i in range(entries)]
    return pal


def raw_to_png_indexed(width, height, depth, row_bytes, clut, pixels, region=None):
    """Indexed PNG (colour type 3) from a 1/2/4/8-bit pixmap.

    PNG packs sub-byte pixels MSB-first, exactly as QuickDraw does, so each
    scanline is copied by slice: no per-pixel loop. An x-crop that does not
    start on a byte boundary at depth < 8 cannot be sliced and falls back to
    the truecolor `raw_to_png`, which is correct at any offset."""
    if depth not in (1, 2, 4, 8):
        return raw_to_png(width, height, depth, row_bytes, clut, pixels, region=region)
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
    ppb = 8 // depth                      # pixels per byte
    if rx % ppb:
        return raw_to_png(width, height, depth, row_bytes, clut, pixels, region=(rx, ry, rw, rh))
    x0 = rx // ppb
    nbytes = (rw * depth + 7) // 8
    stride = 1 + nbytes
    raw = bytearray(stride * rh)
    mv = memoryview(pixels)
    o = 0
    for y in range(ry, ry + rh):
        base = y * row_bytes + x0
        raw[o + 1:o + stride] = mv[base:base + nbytes]
        o += stride
    pal = _indexed_palette(clut, depth)
    plte = b"".join(bytes(rgb) for rgb in pal)
    ihdr = struct.pack(">IIBBBBB", rw, rh, depth, 3, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"PLTE", plte)
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
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
