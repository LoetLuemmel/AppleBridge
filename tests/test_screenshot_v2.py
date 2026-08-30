"""host_server.request_screenshot — the IMAGE2 exchange (2026-08-30).

The daemon (0.8d46+) crops to the region before the transfer, packs rows with
PackBits, and sends a row delta against the frame the host names. These tests
drive AppleBridgeServer with a scripted socket and check: the verb sent, the
pixmap reassembled from enc 1 / enc 2 payloads, the per-link base for deltas,
the region path, and the fall-back to the legacy verb when an older daemon
answers "Invalid command format" — exactly once per link.

Run: python3 tests/test_screenshot_v2.py   (or via pytest)
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402
import screenshot_decode as sd  # noqa: E402


class ScriptedSocket:
    """Answers each sendall() with the next scripted reply, byte-chunked."""
    def __init__(self, replies, chunk=4096):
        self.replies = list(replies)
        self.sent = []
        self.buf = b""
        self.chunk = chunk

    def sendall(self, data):
        self.sent.append(bytes(data))
        assert self.replies, f"no scripted reply for {data!r}"
        self.buf += self.replies.pop(0)

    def recv(self, n, flags=0):
        if not self.buf:
            return b""
        out, self.buf = self.buf[:min(n, self.chunk)], self.buf[min(n, self.chunk):]
        return out

    def settimeout(self, _):
        pass

    def setblocking(self, _):
        pass

    def close(self):
        pass


def _packbits_row(src):
    dst = bytearray()
    n, i = len(src), 0
    while i < n:
        j = i
        while j < n and j - i < 128 and src[j] == src[i]:
            j += 1
        if j - i >= 3:
            dst += bytes([257 - (j - i), src[i]])
            i = j
        else:
            lit = bytearray()
            while i < n and len(lit) < 128:
                k = i
                while k < n and k - i < 3 and src[k] == src[i]:
                    k += 1
                if k - i == 3:
                    break
                lit.append(src[i]); i += 1
            dst.append(len(lit) - 1); dst += lit
    return bytes(dst)


def _pack_rows(rows):
    return b"".join(struct.pack(">H", len(p)) + p for p in map(_packbits_row, rows))


W, H, RB = 16, 8, 16
CLUT = bytes(range(256)) * 3


def _frame_rows(seed):
    return [bytes([(x + y * 3 + seed) & 0xFF for x in range(W)]) for y in range(H)]


def _image2(rows, enc, gen, region=(0, 0, W, H), payload=None):
    rx, ry, rw, rh = region
    if payload is None:
        payload = _pack_rows(rows) if enc == 1 else b"".join(rows)
    hdr = f"IMAGE2:{W}:{H}:8:{RB}:256:{rx}:{ry}:{rw}:{rh}:{enc}:{gen}:{len(payload)}\r".encode()
    return hdr + CLUT + payload


def _legacy_image(rows):
    payload = b"".join(rows)
    return f"IMAGE:{W}:{H}:8:{RB}:256:{len(payload)}\r".encode() + CLUT + payload


INVALID = b"STATUS:-1\rSTDOUT:0\r\rSTDERR:21\rInvalid command format\r\r"


def _server(replies):
    srv = host_server.AppleBridgeServer()
    srv.connected = True
    srv.client_socket = ScriptedSocket(replies)
    srv._drain = lambda: True
    return srv


def test_full_frame_packed_then_delta_uses_the_base_it_holds():
    a = _frame_rows(0)
    b = list(a); b[3] = bytes([0xAA] * W); b[4] = bytes([0xBB] * W)
    xor = [bytes(p ^ q for p, q in zip(a[y], b[y])) for y in (3, 4)]   # enc 2 rows are XORs
    delta = struct.pack(">HH", 3, 2) + _pack_rows(xor)
    srv = _server([_image2(a, 1, 1), _image2(b, 2, 2, payload=delta)])
    s1 = srv.request_screenshot()
    assert s1["pixels"] == b"".join(a) and s1["enc"] == 1 and s1["gen"] == 1
    assert srv.client_socket.sent[0] == b"SCREENSHOT2:0:0:0:0:1:0"
    s2 = srv.request_screenshot()
    assert srv.client_socket.sent[1] == b"SCREENSHOT2:0:0:0:0:3:1", srv.client_socket.sent[1]
    assert s2["pixels"] == b"".join(b) and s2["enc"] == 2 and s2["gen"] == 2
    assert srv.shot_prev["gen"] == 2
    assert s2["wire_bytes"] < len(_image2(b, 1, 2))  # the delta was smaller than a frame


def test_delta_without_a_base_drops_the_link_instead_of_guessing():
    a = _frame_rows(0)
    delta = struct.pack(">HH", 0, 1) + _pack_rows([a[0]])
    srv = _server([_image2(a, 2, 5, payload=delta)])
    assert srv.request_screenshot() is None
    assert srv.connected is False


def test_region_is_sent_to_the_guest_and_not_cropped_again():
    a = _frame_rows(1)
    sub = [r[4:12] for r in a[2:6]]                       # x=4 w=8, y=2 h=4
    srv = _server([_image2(a, 1, 1, region=(4, 2, 8, 4), payload=_pack_rows(sub))])
    shot = srv.request_screenshot(region=(4, 2, 8, 4))
    assert srv.client_socket.sent[0] == b"SCREENSHOT2:4:2:8:4:1:0"
    assert (shot["width"], shot["height"], shot["row_bytes"]) == (8, 4, 8)
    assert shot["pixels"] == b"".join(sub)
    assert srv.shot_prev is None                          # a region never becomes a delta base
    png = host_server.screenshot_png(shot, region=(4, 2, 8, 4))
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (8, 4)


def test_old_daemon_falls_back_to_legacy_once_per_link():
    a = _frame_rows(2)
    srv = _server([INVALID, _legacy_image(a), _legacy_image(a)])
    s1 = srv.request_screenshot(region=(0, 0, 4, 4))
    assert s1 is not None and s1["pixels"] == b"".join(a) and s1["enc"] == 0
    assert s1["region"] == (0, 0, 4, 4)                   # cropped host-side, as before
    assert srv.shot_v2 is False
    s2 = srv.request_screenshot()
    sent = srv.client_socket.sent
    assert sent == [b"SCREENSHOT2:0:0:4:4:1:0", b"SCREENSHOT", b"SCREENSHOT"], sent
    assert s2["enc"] == 0


def test_daemon_error_frame_is_not_a_fallback():
    srv = _server([b"STATUS:-1\rSTDOUT:0\r\rSTDERR:17\rScreenshot failed\r\r"])
    assert srv.request_screenshot() is None
    assert srv.shot_v2 is None and srv.connected is True


def test_new_link_forgets_the_base():
    a = _frame_rows(0)
    srv = _server([_image2(a, 1, 1), _image2(a, 1, 1)])
    srv.request_screenshot()
    assert srv.shot_prev is not None
    srv.link_generation += 1                              # a reconnected daemon
    srv.request_screenshot()
    assert srv.client_socket.sent[1] == b"SCREENSHOT2:0:0:0:0:1:0"


def test_region_row_bytes_packs_pixels_without_slack():
    # The daemon writes cropped rows contiguously: no rowBytes padding survives.
    assert host_server._region_row_bytes(400, 8) == 400
    assert host_server._region_row_bytes(400, 1) == 50
    assert host_server._region_row_bytes(401, 1) == 51
    assert host_server._region_row_bytes(3, 4) == 2
    assert host_server._region_row_bytes(10, 16) == 20
    assert host_server._region_row_bytes(10, 32) == 40


def test_screenshot_png_indexed_for_full_frame():
    a = _frame_rows(0)
    srv = _server([_image2(a, 1, 1)])
    png = host_server.screenshot_png(srv.request_screenshot())
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and png[25] == 3    # colour type 3 = indexed


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
