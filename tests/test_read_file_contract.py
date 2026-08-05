"""`mac_read_file` must not report a missing file as an empty one.

It ran `Catenate '<path>'` and trusted `status == 0`, which says only that the
Apple Event was DELIVERED. `Catenate` on a file that does not exist complains to
stderr — which stays inside ToolServer — and answers with nothing. So a missing
file came back as `{"success": true, "content": ""}`.

A false POSITIVE, and worse than the false negative found in `mac_compile` the
same day: "I read it, it is empty" for something that is not there. The
distinction it destroys is the one a caller needs — *nothing to do* versus
*wrong path*.

Measured 2026-08-05 by the parallel session, whose local model then invented a
filename that its OWN previous tool result had ruled out, read the invention and
reported on it. No prompt prevents that. Only a tool that says "not there".

Run: python3 tests/test_read_file_contract.py   (or via pytest)
"""

import base64
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "host"))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import macbinary  # noqa: E402
from mcp import tools  # noqa: E402


class FakeConn:
    """Answers one scripted (status, stdout, stderr); records what was sent."""

    def __init__(self, reply):
        self.reply = reply
        self.sent = []

    def is_connected(self):
        return True

    def send_command(self, command, timeout=30.0):
        self.sent.append(command)
        return self.reply


def read_with(reply, path="MeinMac:X:file.txt"):
    conn = FakeConn(reply)
    tools.get_connection = lambda: conn
    return tools.mac_read_file(path=path), conn


def macbinary_reply(data: bytes):
    """What the host returns for a successful READFILE: base64 MacBinary."""
    return (0, base64.b64encode(macbinary.encode(data)).decode("ascii"), "")


# --- the defect -------------------------------------------------------------
def test_a_missing_file_is_not_reported_as_an_empty_one():
    """The one that matters. This is the whole bug."""
    result, _ = read_with((-1, "", "READFILE failed"))
    assert result["success"] is False
    assert result["content"] is None


def test_the_error_points_the_caller_somewhere_useful():
    """"READFILE failed" alone leaves a caller guessing between a wrong path and
    a broken bridge. The message names the cheaper check."""
    result, _ = read_with((-1, "", "READFILE failed"))
    assert "mac_list_files" in result["error"]


def test_a_genuinely_empty_file_is_still_a_success():
    """The other half, and it is why "not there" could not simply be inferred
    from empty content: an empty file is a legitimate, readable file."""
    result, _ = read_with(macbinary_reply(b""))
    assert result["success"] is True
    assert result["content"] == ""
    assert result["bytes"] == 0


# --- the mechanism ----------------------------------------------------------
def test_the_native_verb_is_used_not_catenate():
    """Catenate needs ToolServer and cannot fail visibly across the bridge;
    READFILE needs neither and does."""
    _, conn = read_with(macbinary_reply(b"hi"))
    assert conn.sent == ["READFILE:MeinMac:X:file.txt"]
    assert not any("Catenate" in c for c in conn.sent)


def test_carriage_returns_become_line_feeds():
    """Mirrors what mac_write_file does on the way in. Without it every guest
    text file arrives as one line, which reads like a corrupt file."""
    result, _ = read_with(macbinary_reply(b"a\rb\rc"))
    assert result["content"] == "a\nb\nc"


def test_macroman_is_decoded_rather_than_mangled():
    """0xC4 is `ƒ`, which is in half the interesting paths on a classic Mac."""
    result, _ = read_with(macbinary_reply(b"System \xc4 Folder"))
    assert "ƒ" in result["content"]


def test_the_byte_count_is_of_the_bytes_not_the_text():
    """len(str) after decoding is a different number, and a caller comparing it
    against a directory listing would see a mismatch that is not there."""
    result, _ = read_with(macbinary_reply(b"System \xc4 Folder"))
    assert result["bytes"] == len(b"System \xc4 Folder")


def test_a_disconnected_bridge_is_not_an_empty_file_either():
    conn = FakeConn(None)
    conn.is_connected = lambda: False
    tools.get_connection = lambda: conn
    result = tools.mac_read_file(path="MeinMac:X:file.txt")
    assert result["success"] is False and conn.sent == []


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
