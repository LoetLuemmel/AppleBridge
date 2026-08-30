"""The CLIPSET host mirror: after the daemon wrote the guest scrap, the host puts
the same text on its own pasteboard as plain text, because Basilisk II re-imports
the host pasteboard on a guest GetScrap and could not read back the RTF its own
PutScrap hook had written (2026-08-30: front app saw scrap size 0 after
mac_clipboard_set; a plain pbcopy was imported fine).
Run: python3 tests/test_clipset_host_mirror.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402


def test_mirror_sends_utf8_plain_text_with_lf():
    calls = []
    def runner(argv, **kw):
        calls.append((argv, kw.get("input")))
    ok = host_server._mirror_scrap_to_host_pasteboard("gr\xfc\xdf\rdich".encode("mac_roman"), runner=runner)
    if sys.platform != "darwin":
        assert ok is False and calls == []
        return
    assert ok is True
    assert calls[0][0] == ["pbcopy"]
    assert calls[0][1] == "gr\xfc\xdf\ndich".encode("utf-8")


def test_empty_payload_is_a_noop():
    calls = []
    assert host_server._mirror_scrap_to_host_pasteboard(b"", runner=lambda *a, **k: calls.append(a)) is False
    assert calls == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
