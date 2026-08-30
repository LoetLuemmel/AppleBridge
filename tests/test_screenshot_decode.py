"""Golden-input tests for host/screenshot_decode.py — raw Mac pixmap -> PNG.

raw_to_png is a pure bytes->bytes transform (no Pillow). These decode the PNG
it emits back to raw RGB scanlines and check exact pixels for each supported
depth, plus region cropping and the error paths.

Run: python3 tests/test_screenshot_decode.py   (or via pytest)
"""

import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import screenshot_decode as sd  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _png_ihdr(png):
    assert png[:8] == PNG_SIG, "bad PNG signature"
    # IHDR data starts 8 bytes after the "IHDR" tag (len+tag), width/height first.
    i = png.find(b"IHDR") + 4
    w, h, bit_depth, color = struct.unpack(">IIBB", png[i:i + 10])
    return w, h, bit_depth, color


def _png_rows(png, w, h):
    """Decompress IDAT and split into rows of (filter byte + w*3 RGB)."""
    i = png.find(b"IDAT")
    ln = struct.unpack(">I", png[i - 4:i])[0]
    raw = zlib.decompress(png[i + 4:i + 4 + ln])
    stride = 1 + w * 3
    rows = []
    for y in range(h):
        row = raw[y * stride:(y + 1) * stride]
        assert row[0] == 0, "PNG filter byte must be 0 (none)"
        rows.append([tuple(row[1 + x * 3:1 + x * 3 + 3]) for x in range(w)])
    return rows


# --- structure -------------------------------------------------------------

def test_signature_and_ihdr_truecolor():
    png = sd.raw_to_png(2, 1, 8, 2, b"\xff\x00\x00\x00\x00\x00", b"\x00\x01")
    w, h, bit_depth, color = _png_ihdr(png)
    assert (w, h) == (2, 1)
    assert bit_depth == 8 and color == 2, "8-bit truecolor RGB"


# --- depth 8 (indexed via CLUT) -------------------------------------------

def test_depth8_palette_pixels():
    # palette: 0=red 1=green 2=blue 3=white
    clut = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    pixels = bytes([0, 1, 2, 3, 3, 2, 1, 0])   # 4x2, row_bytes=4
    png = sd.raw_to_png(4, 2, 8, 4, clut, pixels)
    rows = _png_rows(png, 4, 2)
    assert rows[0] == [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
    assert rows[1] == [(255, 255, 255), (0, 0, 255), (0, 255, 0), (255, 0, 0)]


# --- depth 4 (two pixels per byte) ----------------------------------------

def test_depth4_nibbles():
    clut = bytes([0, 0, 0, 17, 17, 17, 34, 34, 34, 51, 51, 51])  # idx 0..3
    # byte 0x01 -> hi nibble 0 (idx0), lo nibble 1 (idx1); 0x23 -> idx2, idx3
    png = sd.raw_to_png(4, 1, 4, 2, clut, bytes([0x01, 0x23]))
    rows = _png_rows(png, 4, 1)
    assert rows[0] == [(0, 0, 0), (17, 17, 17), (34, 34, 34), (51, 51, 51)]


# --- depth 1 (no CLUT -> 0=white, 1=black) --------------------------------

def test_depth1_mono_convention():
    # bits 1010_0000 -> pixels 1,0,1,0,0,0,0,0 over width 8, row_bytes 1
    png = sd.raw_to_png(8, 1, 1, 1, b"", bytes([0b10100000]))
    rows = _png_rows(png, 8, 1)
    black, white = (0, 0, 0), (255, 255, 255)
    assert rows[0] == [black, white, black, white, white, white, white, white]


# --- depth 16 (RGB 5-5-5 big-endian) --------------------------------------

def test_depth16_rgb555():
    # 0x7C00 = R=31 -> expand5(31)=255; 0x001F = B=31 -> 255.
    px = struct.pack(">HH", 0x7C00, 0x001F)
    png = sd.raw_to_png(2, 1, 16, 4, b"", px)
    rows = _png_rows(png, 2, 1)
    assert rows[0] == [(255, 0, 0), (0, 0, 255)]


def test_depth16_midrange_channel():
    # 0x0400 -> G bits? layout x RRRRR GGGGG BBBBB; 0x03E0 = G=31 -> 255.
    # Use R=16 (0x4000): expand5(16) = (16<<3)|(16>>2) = 128|4 = 132.
    px = struct.pack(">H", 0x4000)
    png = sd.raw_to_png(1, 1, 16, 2, b"", px)
    assert _png_rows(png, 1, 1)[0] == [(132, 0, 0)]


# --- depth 32 (xRGB-8888 big-endian, skip leading byte) -------------------

def test_depth32_xrgb():
    px = bytes([0x00, 10, 20, 30, 0xFF, 40, 50, 60])   # two xRGB pixels
    png = sd.raw_to_png(2, 1, 32, 8, b"", px)
    rows = _png_rows(png, 2, 1)
    assert rows[0] == [(10, 20, 30), (40, 50, 60)]


# --- region crop -----------------------------------------------------------

def test_region_crop_pixels_and_dims():
    clut = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    pixels = bytes([0, 1, 2, 3, 3, 2, 1, 0])   # 4x2
    png = sd.raw_to_png(4, 2, 8, 4, clut, pixels, region=(1, 0, 2, 2))
    w, h, _, _ = _png_ihdr(png)
    assert (w, h) == (2, 2)
    rows = _png_rows(png, 2, 2)
    assert rows[0] == [(0, 255, 0), (0, 0, 255)]      # cols 1,2 of row 0
    assert rows[1] == [(0, 0, 255), (0, 255, 0)]      # cols 1,2 of row 1


def test_region_clamped_to_bounds():
    clut = bytes([1, 2, 3] * 4)
    png = sd.raw_to_png(4, 2, 8, 4, clut, bytes(8), region=(3, 1, 999, 999))
    w, h, _, _ = _png_ihdr(png)
    assert (w, h) == (1, 1), "over-wide crop clamps to remaining 1x1"


# --- error paths -----------------------------------------------------------

def _expect_valueerror(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def test_rejects_bad_dimensions():
    assert _expect_valueerror(lambda: sd.raw_to_png(0, 5, 8, 0, b"", b""))


def test_rejects_short_pixel_buffer():
    # needs height*row_bytes = 2*4 = 8 bytes; give 4
    assert _expect_valueerror(lambda: sd.raw_to_png(4, 2, 8, 4, b"", bytes(4)))


def test_rejects_unsupported_depth():
    assert _expect_valueerror(lambda: sd.raw_to_png(1, 1, 24, 3, b"", bytes(3)))


def test_rejects_empty_crop():
    assert _expect_valueerror(
        lambda: sd.raw_to_png(4, 2, 8, 4, b"", bytes(8), region=(4, 0, 2, 2)))


# --- IMAGE2: PackBits rows, row delta, indexed PNG (2026-08-30) ---------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host", "tools"))


def _packbits_row(src):
    """Reference Apple PackBits encoder (the one host/tools/gif_to_rez.py uses
    for 'Gfrm' resources; copied so this test stays free of Pillow)."""
    dst = bytearray()
    n = len(src)
    i = 0
    while i < n:
        j = i
        while j < n and j - i < 128 and src[j] == src[i]:
            j += 1
        run = j - i
        if run >= 3:
            dst.append(257 - run)
            dst.append(src[i])
            i = j
        else:
            lit = bytearray()
            while i < n and len(lit) < 128:
                k = i
                while k < n and k - i < 3 and src[k] == src[i]:
                    k += 1
                if k - i == 3:
                    break
                lit.append(src[i])
                i += 1
            dst.append(len(lit) - 1)
            dst += lit
    return bytes(dst)


def _pack_rows(rows):
    out = bytearray()
    for r in rows:
        p = _packbits_row(r)
        out += struct.pack(">H", len(p)) + p
    return bytes(out)


def _png_plte(png):
    i = png.find(b"PLTE")
    ln = struct.unpack(">I", png[i - 4:i])[0]
    return png[i + 4:i + 4 + ln]


def _png_idat_raw(png):
    i = png.find(b"IDAT")
    ln = struct.unpack(">I", png[i - 4:i])[0]
    return zlib.decompress(png[i + 4:i + 4 + ln])


def test_unpack_bits_roundtrip_desktop_like_row():
    row = bytes([0xFF] * 500 + [1, 2, 3, 4, 5, 5, 5, 5, 9] + [0] * 515)
    packed = _packbits_row(row)
    assert len(packed) < 40, len(packed)
    out, off = sd.unpack_bits(packed, 0, len(row))
    assert out == row and off == len(packed)


def test_unpack_bits_rejects_short_and_overlong_streams():
    row = bytes(range(64))
    packed = _packbits_row(row)
    assert _expect_valueerror(lambda: sd.unpack_bits(packed[:-1], 0, 64))
    assert _expect_valueerror(lambda: sd.unpack_bits(packed, 0, 63))


def test_unpack_rows_walks_length_prefixed_rows():
    rows = [bytes([y] * 16) for y in range(5)]
    payload = _pack_rows(rows)
    data, off = sd.unpack_rows(payload, 0, 5, 16)
    assert data == b"".join(rows) and off == len(payload)


def test_decode_rows_raw_and_packed_agree():
    rows = [bytes([(x * y) & 0xFF for x in range(32)]) for y in range(4)]
    raw = b"".join(rows)
    assert sd.decode_rows(0, raw, 4, 32) == raw
    assert sd.decode_rows(1, _pack_rows(rows), 4, 32) == raw
    assert _expect_valueerror(lambda: sd.decode_rows(7, raw, 4, 32))


def test_delta_composites_changed_runs_onto_previous_frame():
    rb, h = 8, 6
    prev = bytes([0x11] * (rb * h))
    new_rows = {2: bytes([0xAA] * rb), 3: bytes([0xBB] * rb), 5: bytes([0xCC] * rb)}
    # enc 2 carries row XOR previous-row, so an unchanged byte is a zero on the wire
    xor = {y: bytes(a ^ 0x11 for a in r) for y, r in new_rows.items()}
    payload = (struct.pack(">HH", 2, 2) + _pack_rows([xor[2], xor[3]])
               + struct.pack(">HH", 5, 1) + _pack_rows([xor[5]]))
    runs = sd.parse_delta(payload, rb, packed=True)
    assert [(y, len(d) // rb) for y, d in runs] == [(2, 2), (5, 1)]
    cur = sd.apply_delta(prev, rb, h, runs)
    for y in range(h):
        row = cur[y * rb:(y + 1) * rb]
        assert row == new_rows.get(y, prev[:rb]), (y, row)


def test_delta_of_one_changed_byte_is_a_zero_run_on_the_wire():
    rb = 1024
    prev = bytes(range(256)) * 4
    new = bytearray(prev); new[700] ^= 0x5A
    xrow = bytes(a ^ b for a, b in zip(prev, new))
    packed = _packbits_row(xrow)
    assert len(packed) < 24, len(packed)          # the point of XOR: one glyph, a few bytes
    runs = sd.parse_delta(struct.pack(">HH", 0, 1) + _pack_rows([xrow]), rb)
    assert sd.apply_delta(prev, rb, 1, runs) == bytes(new)


def test_enc3_up_predictor_roundtrip_and_packs_a_dither():
    rb, rows = 64, 8
    # a 2-row dither pattern: no horizontal runs, identical every other row
    a = bytes([0x55, 0xAA] * (rb // 2)); b = bytes([0xAA, 0x55] * (rb // 2))
    frame = (a + b) * (rows // 2)
    up = bytearray(frame)
    for y in range(rows - 1, 0, -1):
        s0 = y * rb
        up[s0:s0 + rb] = bytes(p ^ q for p, q in zip(frame[s0:s0 + rb], frame[s0 - rb:s0]))
    payload = _pack_rows([bytes(up[y * rb:(y + 1) * rb]) for y in range(rows)])
    plain = _pack_rows([frame[y * rb:(y + 1) * rb] for y in range(rows)])
    assert len(payload) < len(plain) // 3, (len(payload), len(plain))
    assert sd.decode_rows(3, payload, rows, rb) == frame


def test_delta_run_outside_frame_is_an_error():
    payload = struct.pack(">HH", 5, 2) + _pack_rows([bytes(4), bytes(4)])
    runs = sd.parse_delta(payload, 4, packed=True)
    assert _expect_valueerror(lambda: sd.apply_delta(bytes(24), 4, 6, runs))


def test_indexed_png_8bit_copies_rows_and_palette():
    clut = bytes([255, 0, 0,   0, 255, 0,   0, 0, 255,   255, 255, 255])
    pixels = bytes([0, 1, 2, 3,   3, 2, 1, 0])
    png = sd.raw_to_png_indexed(4, 2, 8, 4, clut, pixels)
    w, h, bd, color = _png_ihdr(png)
    assert (w, h, bd, color) == (4, 2, 8, 3)
    assert _png_plte(png) == clut
    raw = _png_idat_raw(png)
    assert raw == b"\x00" + pixels[:4] + b"\x00" + pixels[4:]


def test_indexed_png_region_slices_rows():
    clut = bytes(range(256)) * 3
    pixels = bytes([(x + y * 16) & 0xFF for y in range(4) for x in range(16)])
    png = sd.raw_to_png_indexed(16, 4, 8, 16, clut, pixels, region=(4, 1, 8, 2))
    w, h, bd, color = _png_ihdr(png)
    assert (w, h) == (8, 2)
    raw = _png_idat_raw(png)
    assert raw == b"\x00" + pixels[16 + 4:16 + 12] + b"\x00" + pixels[32 + 4:32 + 12]


def test_indexed_png_1bit_no_clut_uses_white_zero_black_one():
    png = sd.raw_to_png_indexed(8, 1, 1, 1, b"", bytes([0b10100000]))
    w, h, bd, color = _png_ihdr(png)
    assert (bd, color) == (1, 3)
    assert _png_plte(png) == bytes([255, 255, 255, 0, 0, 0])
    assert _png_idat_raw(png) == b"\x00" + bytes([0b10100000])


def test_indexed_png_unaligned_subbyte_crop_falls_back_to_truecolor():
    clut = bytes([255, 0, 0,   0, 255, 0])
    # 4 pixels at 4 bpp: indices 0,1,0,1 ; crop x=1..3 -> not byte aligned
    png = sd.raw_to_png_indexed(4, 1, 4, 2, clut, bytes([0x01, 0x01]), region=(1, 0, 2, 1))
    w, h, bd, color = _png_ihdr(png)
    assert (w, h, bd, color) == (2, 1, 8, 2)      # truecolor path
    assert _png_rows(png, 2, 1)[0] == [(0, 255, 0), (255, 0, 0)]


def test_indexed_png_16bit_delegates_to_truecolor():
    px = bytes([0x7C, 0x00])   # RGB555 red
    png = sd.raw_to_png_indexed(1, 1, 16, 2, b"", px)
    assert _png_ihdr(png)[3] == 2


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
