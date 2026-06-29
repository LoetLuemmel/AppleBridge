"""
MacBinary II encode/decode — pure stdlib, no third-party deps.

AppleBridge carries a classic-Mac file's two forks + type/creator over the
bridge as *explicit fields* (see WRITEFILE/READFILE in host_server.py); the
68K daemon never sees MacBinary. This module exists only at the host edge so
that:

  * mac_put_file can accept a MacBinary file (e.g. a Retro68 ``.bin``) and
    split it into (data fork, resource fork, type, creator, name), and
  * mac_get_file can package the two forks it pulled back into a single,
    re-deployable MacBinary ``.bin``.

Reference: MacBinary II spec (128-byte header; data fork then resource fork,
each padded to a 128-byte boundary). Same pure-byte-transform shape as
screenshot_decode.py, runnable under /usr/bin/python3.
"""

import struct
import sys

HEADER_LEN = 128
PAD = 128


def _crc16_ccitt(data: bytes) -> int:
    """CRC-16/XMODEM (poly 0x1021, init 0x0000) — the MacBinary header CRC."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def _pad_to(n: int) -> int:
    """Round n up to the next multiple of 128."""
    return (n + PAD - 1) // PAD * PAD


def _as_ostype(value, field: str) -> bytes:
    """Coerce a 4-char type/creator to exactly 4 bytes."""
    if isinstance(value, str):
        value = value.encode("mac_roman", errors="replace")
    if not isinstance(value, (bytes, bytearray)):
        raise TypeError(f"{field} must be str or bytes")
    value = bytes(value)
    if len(value) > 4:
        value = value[:4]
    return value.ljust(4, b" ")  # OSTypes are space-padded, e.g. 'MPS '


def encode(data: bytes, rsrc: bytes = b"", name: str = "AppleBridgeFile",
           type_="????", creator="????", flags: int = 0) -> bytes:
    """Build a MacBinary II image from two forks + Finder info."""
    data = bytes(data or b"")
    rsrc = bytes(rsrc or b"")
    type_b = _as_ostype(type_, "type")
    creator_b = _as_ostype(creator, "creator")

    name_b = name.encode("mac_roman", errors="replace")[:63]
    if not name_b:
        name_b = b"File"

    header = bytearray(HEADER_LEN)
    header[0] = 0                                  # old version, must be 0
    header[1] = len(name_b)                        # filename length
    header[2:2 + len(name_b)] = name_b             # filename (max 63)
    header[65:69] = type_b                         # file type
    header[69:73] = creator_b                      # file creator
    header[73] = (flags >> 8) & 0xFF               # Finder flags, high byte
    header[74] = 0                                 # must be 0
    # 75..82: window position / folder id / protected — left zero
    struct.pack_into(">I", header, 83, len(data))  # data fork length
    struct.pack_into(">I", header, 87, len(rsrc))  # resource fork length
    # 91..98: creation/modification dates — left zero (epoch)
    header[101] = flags & 0xFF                      # Finder flags, low byte (MB II)
    header[122] = 129                               # version used to write (MB II)
    header[123] = 129                               # minimum version to read
    crc = _crc16_ccitt(bytes(header[0:124]))
    struct.pack_into(">H", header, 124, crc)        # header CRC

    out = bytearray(header)
    out += data
    out += b"\x00" * (_pad_to(len(data)) - len(data))
    out += rsrc
    out += b"\x00" * (_pad_to(len(rsrc)) - len(rsrc))
    return bytes(out)


def looks_like_macbinary(blob: bytes) -> bool:
    """Heuristic: is this byte string a MacBinary file?"""
    if len(blob) < HEADER_LEN:
        return False
    if blob[0] != 0 or blob[74] != 0 or blob[82] != 0:
        return False
    namelen = blob[1]
    if namelen < 1 or namelen > 63:
        return False
    data_len, rsrc_len = struct.unpack_from(">II", blob, 83)
    if HEADER_LEN + _pad_to(data_len) + _pad_to(rsrc_len) > len(blob) + PAD:
        return False
    # MacBinary II carries a header CRC; require it to match when present.
    if blob[122] >= 129:
        if _crc16_ccitt(blob[0:124]) != struct.unpack_from(">H", blob, 124)[0]:
            return False
    return True


def decode(blob: bytes) -> dict:
    """Parse a MacBinary image into forks + Finder info. Raises ValueError."""
    if not looks_like_macbinary(blob):
        raise ValueError("not a MacBinary file")
    namelen = blob[1]
    name = blob[2:2 + namelen].decode("mac_roman", errors="replace")
    type_ = blob[65:69]
    creator = blob[69:73]
    flags = (blob[73] << 8) | blob[101]
    data_len, rsrc_len = struct.unpack_from(">II", blob, 83)
    off = HEADER_LEN
    data = blob[off:off + data_len]
    off += _pad_to(data_len)
    rsrc = blob[off:off + rsrc_len]
    if len(data) != data_len or len(rsrc) != rsrc_len:
        raise ValueError("truncated MacBinary forks")
    return {
        "name": name,
        "type": type_,
        "creator": creator,
        "flags": flags,
        "data": data,
        "rsrc": rsrc,
    }


def _selftest() -> int:
    """Round-trip a synthetic forked file; exit non-zero on mismatch."""
    data = bytes(range(256)) * 5 + b"data-fork-tail"
    rsrc = b"RESOURCE\x00\x01\x02FORK" * 13
    blob = encode(data, rsrc, name="Hello.app", type_="APPL", creator="Add5",
                  flags=0x4000)  # hasBundle
    assert looks_like_macbinary(blob), "encoded blob not recognised"
    got = decode(blob)
    ok = (got["data"] == data and got["rsrc"] == rsrc
          and got["type"] == b"APPL" and got["creator"] == b"Add5"
          and got["name"] == "Hello.app" and got["flags"] == 0x4000)
    # Empty resource fork (the common data-only case).
    blob2 = encode(b"just data", b"", name="t.txt", type_="TEXT", creator="ttxt")
    got2 = decode(blob2)
    ok = ok and got2["data"] == b"just data" and got2["rsrc"] == b""
    # CRC must reject corruption.
    bad = bytearray(blob)
    bad[66] ^= 0xFF
    rejected = not looks_like_macbinary(bytes(bad))
    if ok and rejected:
        print("macbinary self-test: OK")
        return 0
    print("macbinary self-test: FAIL", {"ok": ok, "rejected_corrupt": rejected})
    return 1


if __name__ == "__main__":
    sys.exit(_selftest())
