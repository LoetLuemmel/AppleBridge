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
