"""Tests for the host-side framebuffer capture (fb_export + mac_fb_screenshot).

The property that matters most is the one that must hold when everything else
is wrong: an emulator WITHOUT the fb-export handler is never signalled,
because SIGUSR1 terminates a process that has no handler for it and Basilisk
must never be hard-killed. So these tests assert on os.kill not happening,
not just on the error message.

Run: python3 tests/test_fb_export.py   (or via pytest)
"""

import base64
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
_MCP = os.path.join(os.path.dirname(__file__), "..", "mcp")
sys.path.insert(0, _MCP)

import fb_export  # noqa: E402


class _patched:
    """Monkeypatch that restores — tools.fb_export IS the imported fb_export
    module (one sys.modules entry), so a fake left behind by one test would
    quietly serve every later one."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.orig = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.orig)


def _load_tools():
    import types
    stub = types.ModuleType("mac_connection")
    stub.get_connection = lambda: None
    stub.MacConnection = object
    sys.modules.setdefault("mac_connection", stub)
    path = os.path.join(_MCP, "tools.py")
    src = open(path).read().replace("from .mac_connection import get_connection",
                                    "from mac_connection import get_connection")
    mod = types.ModuleType("abtools")
    mod.__file__ = os.path.abspath(path)
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


def _synthetic_dump(width=8, height=4, row_bytes=8, depth=8, boxes=((0, 0, 8, 4),)):
    """A minimal well-formed ABFB dump: index i pixels, greyscale palette."""
    hdr = struct.pack("<4sIIIII", b"ABFB", width, height, row_bytes, depth,
                      len(boxes))
    pal = bytes(v for i in range(256) for v in (i, i, i))
    box_blob = b"".join(struct.pack("<HHHH", *b) for b in boxes)
    pixels = bytes((y * row_bytes + x) % 256
                   for y in range(height) for x in range(row_bytes))
    return hdr + pal + box_blob + pixels


def test_parse_dump_round_trip():
    frame = fb_export.parse_dump(_synthetic_dump())
    assert frame["width"] == 8 and frame["height"] == 4
    assert frame["depth"] == 8 and frame["row_bytes"] == 8
    assert frame["boxes"] == [(0, 0, 8, 4)]
    assert len(frame["pixels"]) == 32
    assert len(frame["palette"]) == 768


def test_parse_dump_rejects_bad_magic_and_short_pixels():
    for buf, want in [(b"NOPE" + _synthetic_dump()[4:], "bad magic"),
                      (_synthetic_dump()[:-5], "short pixel"),
                      (b"AB", "too short")]:
        try:
            fb_export.parse_dump(buf)
            assert False, f"parse accepted a dump with {want}"
        except fb_export.FbExportError as e:
            assert e.reason == "bad_dump", e.reason


def test_marker_detection_including_chunk_boundary():
    # The marker is searched in 1 MB chunks; a marker STRADDLING the boundary
    # is the case a naive chunked search misses.
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"\x00" * ((1 << 20) - 3) + fb_export.MARKER + b"\x00" * 64)
        straddling = fh.name
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"\x00" * 4096)
        clean = fh.name
    try:
        assert fb_export.binary_has_export(straddling)
        assert not fb_export.binary_has_export(clean)
    finally:
        os.unlink(straddling)
        os.unlink(clean)


def test_unpatched_emulator_is_never_signalled():
    """The safety property: no marker in the binary -> no os.kill, ever."""
    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"a perfectly ordinary binary with no handler")
        unpatched = fh.name
    kills = []
    real_find, real_kill = fb_export.find_basilisk, os.kill
    fb_export.find_basilisk = lambda: (os.getpid(), unpatched)
    os.kill = lambda pid, sig: kills.append((pid, sig))
    try:
        try:
            fb_export.capture_png()
            assert False, "capture accepted an unpatched binary"
        except fb_export.FbExportError as e:
            assert e.reason == "unpatched", e.reason
        assert kills == [], f"an unpatched emulator was signalled: {kills}"
    finally:
        fb_export.find_basilisk, os.kill = real_find, real_kill
        os.unlink(unpatched)


def test_tool_success_carries_source_and_meta():
    tools = _load_tools()

    def fake_capture(region=None, timeout=5.0):
        return b"\x89PNG-fake", {"width": 1024, "height": 768, "depth": 8,
                                 "dirty_tiles": 3, "elapsed_ms": 17.0,
                                 "pid": 4711}
    with _patched(tools.fb_export, "capture_png", fake_capture):
        r = tools.mac_fb_screenshot()
    assert r["success"] and r["source"] == "fb-export"
    assert base64.b64decode(r["image"]) == b"\x89PNG-fake"
    assert r["dirty_tiles"] == 3 and r["width"] == 1024


def test_tool_falls_back_to_bridge_and_says_why():
    tools = _load_tools()

    def unavailable(region=None, timeout=5.0):
        raise tools.fb_export.FbExportError("unpatched", "no handler")
    bridged = []

    def fake_bridge(region=None):
        bridged.append(region)
        return {"success": True, "image": "QQ==", "format": "png"}
    with _patched(tools.fb_export, "capture_png", unavailable):
        tools.mac_screenshot = fake_bridge
        r = tools.mac_fb_screenshot(region=[1, 2, 3, 4])
    assert r["success"] and r["source"] == "bridge"
    assert r["fb_export_unavailable"] == "unpatched"
    assert bridged == [[1, 2, 3, 4]], "region must reach the bridge path"


def test_tool_fail_fast_when_fallback_off():
    tools = _load_tools()

    def unavailable(region=None, timeout=5.0):
        raise tools.fb_export.FbExportError("no_emulator", "none running")
    with _patched(tools.fb_export, "capture_png", unavailable):
        tools.mac_screenshot = lambda region=None: (_ for _ in ()).throw(
            AssertionError("bridge path used despite fallback=False"))
        r = tools.mac_fb_screenshot(fallback=False)
    assert not r["success"] and r["fb_export_unavailable"] == "no_emulator"


def test_tool_rejects_malformed_region():
    tools = _load_tools()
    r = tools.mac_fb_screenshot(region=[1, 2, 3])
    assert not r["success"] and "region" in r["error"]


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
