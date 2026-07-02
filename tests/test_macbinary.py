"""Golden-input tests for host/macbinary.py — the MacBinary II codec.

macbinary.encode/decode is a deterministic bytes->bytes transform at the host
edge (mac_put_file / mac_get_file). A regression here silently corrupts a
deployed binary's forks, so this pins the header layout, the CRC, fork padding,
and the round-trip.

Run: python3 tests/test_macbinary.py   (or via pytest)
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import macbinary  # noqa: E402


# --- CRC-16/XMODEM known-answer -------------------------------------------

def test_crc16_xmodem_check_vector():
    # The canonical CRC-16/XMODEM check value for b"123456789" is 0x31C3.
    assert macbinary._crc16_ccitt(b"123456789") == 0x31C3


def test_crc16_empty_is_zero():
    assert macbinary._crc16_ccitt(b"") == 0


# --- padding rounding ------------------------------------------------------

def test_pad_to_boundaries():
    assert macbinary._pad_to(0) == 0
    assert macbinary._pad_to(1) == 128
    assert macbinary._pad_to(128) == 128
    assert macbinary._pad_to(129) == 256
    assert macbinary._pad_to(256) == 256


# --- OSType coercion -------------------------------------------------------

def test_ostype_space_pads_short():
    assert macbinary._as_ostype("MPS", "t") == b"MPS "
    assert macbinary._as_ostype("AB", "t") == b"AB  "


def test_ostype_truncates_long():
    assert macbinary._as_ostype("ABCDE", "t") == b"ABCD"


def test_ostype_accepts_bytes():
    assert macbinary._as_ostype(b"APPL", "t") == b"APPL"


def test_ostype_rejects_nonstr_nonbytes():
    try:
        macbinary._as_ostype(1234, "t")
    except TypeError:
        return
    raise AssertionError("expected TypeError for int OSType")


# --- header layout (golden offsets) ---------------------------------------

def test_header_field_offsets():
    data = b"hello world"
    rsrc = b"RSRC"
    blob = macbinary.encode(data, rsrc, name="File.txt", type_="TEXT",
                            creator="ttxt", flags=0x4000)
    assert blob[0] == 0, "old-version byte must be 0"
    assert blob[1] == len(b"File.txt"), "name-length byte"
    assert blob[2:2 + 8] == b"File.txt"
    assert blob[65:69] == b"TEXT", "type at offset 65"
    assert blob[69:73] == b"ttxt", "creator at offset 69"
    assert struct.unpack_from(">I", blob, 83)[0] == len(data), "data-fork len @83"
    assert struct.unpack_from(">I", blob, 87)[0] == len(rsrc), "rsrc-fork len @87"
    assert blob[73] == 0x40 and blob[101] == 0x00, "finder flags split hi/lo"
    assert blob[122] == 129 and blob[123] == 129, "MacBinary II version bytes"


def test_encoded_length_is_128_aligned():
    blob = macbinary.encode(b"x" * 130, b"y" * 3, name="t")
    # 128 header + pad(130)=256 + pad(3)=128 = 512
    assert len(blob) == 512
    assert len(blob) % 128 == 0


# --- round trips -----------------------------------------------------------

def test_roundtrip_two_forks():
    data = bytes(range(256)) * 5 + b"data-tail"
    rsrc = b"RESOURCE\x00\x01\x02FORK" * 13
    blob = macbinary.encode(data, rsrc, name="Hello.app", type_="APPL",
                            creator="Add5", flags=0x4000)
    assert macbinary.looks_like_macbinary(blob)
    got = macbinary.decode(blob)
    assert got["data"] == data
    assert got["rsrc"] == rsrc
    assert got["type"] == b"APPL"
    assert got["creator"] == b"Add5"
    assert got["name"] == "Hello.app"
    assert got["flags"] == 0x4000


def test_roundtrip_empty_resource_fork():
    blob = macbinary.encode(b"just data", b"", name="t.txt", type_="TEXT",
                            creator="ttxt")
    got = macbinary.decode(blob)
    assert got["data"] == b"just data"
    assert got["rsrc"] == b""


def test_roundtrip_empty_data_fork():
    # 68K apps have a 0-length data fork (code lives in the resource fork).
    blob = macbinary.encode(b"", b"CODERESOURCE", name="App", type_="APPL")
    got = macbinary.decode(blob)
    assert got["data"] == b""
    assert got["rsrc"] == b"CODERESOURCE"


def test_long_name_clamped_to_63():
    blob = macbinary.encode(b"d", name="N" * 200)
    assert blob[1] == 63, "name length byte clamps to 63"
    assert macbinary.decode(blob)["name"] == "N" * 63


# --- CRC / validation rejects corruption ----------------------------------

def test_crc_rejects_header_corruption():
    blob = bytearray(macbinary.encode(b"d", b"r", name="x", type_="APPL"))
    blob[66] ^= 0xFF   # flip a byte inside the type field
    assert not macbinary.looks_like_macbinary(bytes(blob))
    try:
        macbinary.decode(bytes(blob))
    except ValueError:
        return
    raise AssertionError("decode must reject a bad-CRC header")


def test_rejects_too_short():
    assert not macbinary.looks_like_macbinary(b"\x00" * 10)


def test_rejects_bad_version_byte():
    blob = bytearray(macbinary.encode(b"d", name="x"))
    blob[0] = 1   # old-version byte must be 0
    assert not macbinary.looks_like_macbinary(bytes(blob))


def test_decode_rejects_truncated_input():
    blob = macbinary.encode(b"x" * 200, b"y" * 10, name="t")
    truncated = blob[:macbinary.HEADER_LEN + 10]   # header + a sliver, forks gone
    try:
        macbinary.decode(truncated)
    except ValueError:
        return
    raise AssertionError("decode must reject truncated input")


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
