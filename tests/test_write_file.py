"""What `mac_write_file` puts on the wire.

The tool used to be `Echo '<content>' > '<path>'` executed by ToolServer, and
that cannot write a file with more than one line in it. A CR inside the quoted
argument ends the ToolServer script: the redirect never runs, every command
chained after it is dropped, and ToolServer still answers STATUS:0. Since the
tool derived `success` from that status and reported `bytes_written` as
`len(content)` — the length of what was ASKED, never what landed — a write that
did nothing at all was indistinguishable from one that worked.

Measured 2026-07-29 on the live guest: a two-line `Echo` vanished, and so did
the `Echo done` chained behind it, at STATUS:0. It cost a build cycle — an icon
resource was Rez'd from a file that had never been updated, and the binary came
back with the old artwork looking entirely healthy.

This is the project's named failure class (reports success, does nothing), so
the replacement is pinned by behaviour rather than trusted: the frame that goes
out, the line endings in it, and where the reported byte count comes from.

Run: python3 tests/test_write_file.py   (or via pytest)
"""

import base64
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "host"))

from mcp import tools  # noqa: E402

TOOLS_SRC = open(os.path.join(ROOT, "mcp", "tools.py"), encoding="utf-8").read()


class FakeConn:
    """Stands in for the daemon link; records the command it was handed."""

    def __init__(self, status=0, stdout="", stderr="", connected=True):
        self.status, self.stdout, self.stderr = status, stdout, stderr
        self.connected = connected
        self.sent = []

    def is_connected(self):
        return self.connected

    def send_command(self, cmd, timeout=None):
        self.sent.append(cmd)
        return self.status, self.stdout, self.stderr


def _write(content, path="MeinMac:t.txt", **kw):
    conn = FakeConn(**kw.pop("conn_kw", {}))
    tools.get_connection = lambda: conn
    result = tools.mac_write_file(path, content, **kw)
    return conn, result


def _fields(cmd):
    """WRITEFILE:<pathB64>:<typeHex>:<creatorHex>:<dataB64>:<rsrcB64>"""
    assert cmd.startswith("WRITEFILE:"), cmd[:40]
    return cmd[len("WRITEFILE:"):].split(":")


def _payload(cmd):
    return base64.b64decode(_fields(cmd)[3])


# --- the regression itself ---------------------------------------------------
def test_a_multi_line_write_goes_out_as_one_writefile_frame():
    conn, res = _write("line one\nline two\nline three\n")
    assert len(conn.sent) == 1, "one frame, not a chain of shell commands"
    assert conn.sent[0].startswith("WRITEFILE:")
    assert res["success"] is True


def test_the_content_never_becomes_a_shell_command():
    # The whole defect was that the content was command SYNTAX. Anything that
    # would be interpreted -- quotes, redirects, MPW's special braces -- has to
    # survive as bytes.
    nasty = "Echo 'x' > y\n∂{ return 0; ∂}\n\"quoted\"\n'single'\n"
    conn, res = _write(nasty)
    assert res["success"] is True
    body = _payload(conn.sent[0]).decode("mac_roman")
    for fragment in ("Echo 'x' > y", "return 0;", '"quoted"', "'single'"):
        assert fragment in body, f"{fragment!r} did not survive"


def test_no_echo_redirect_remains_in_the_implementation():
    # Scan the CODE only: the docstring quotes the old redirect on purpose, so
    # a naive search for it matches the explanation of why it is gone.
    fn = TOOLS_SRC[TOOLS_SRC.index("def mac_write_file("):]
    fn = fn[:fn.index("\ndef ")]
    body = fn[fn.index('"""', fn.index('"""') + 3) + 3:]
    assert "Echo" not in body, "the shell redirect is back"
    assert "WRITEFILE:" in body


# --- line endings ------------------------------------------------------------
def test_lf_becomes_cr():
    conn, _ = _write("a\nb\n")
    assert _payload(conn.sent[0]) == b"a\rb\r"


def test_crlf_does_not_become_a_doubled_ending():
    conn, _ = _write("a\r\nb\r\n")
    assert _payload(conn.sent[0]) == b"a\rb\r"


def test_content_that_already_uses_cr_is_left_with_single_crs():
    # Round-tripped off the guest and written back: must not gain endings.
    conn, _ = _write("a\rb\r")
    assert _payload(conn.sent[0]) == b"a\rb\r"


def test_no_trailing_newline_is_added():
    conn, _ = _write("no ending")
    assert _payload(conn.sent[0]) == b"no ending"


# --- encoding ----------------------------------------------------------------
def test_text_is_encoded_to_macroman():
    conn, _ = _write("Grüße ∂\n")
    body = _payload(conn.sent[0])
    assert b"\x9f" in body, "u-umlaut should be MacRoman 0x9F"
    assert b"\xb6" in body, "MPW continuation char should be MacRoman 0xB6"
    assert b"\xc3\xbc" not in body, "still UTF-8"


def test_the_path_is_encoded_too():
    conn, _ = _write("x", path="MeinMac:Grüße:f.txt")
    assert base64.b64decode(_fields(conn.sent[0])[0]) == \
        "MeinMac:Grüße:f.txt".encode("mac_roman")


# --- what gets reported ------------------------------------------------------
def test_bytes_written_is_the_payload_length_not_len_content():
    # The old value was len(content), which agrees with the truth right up
    # until it matters. Tie it to the bytes actually sent.
    conn, res = _write("a\r\nb\r\nc\r\n")
    assert res["bytes_written"] == len(_payload(conn.sent[0])) == 6
    assert res["bytes_written"] != len("a\r\nb\r\nc\r\n")


def test_a_daemon_failure_is_reported_as_failure():
    conn, res = _write("x", conn_kw={"status": -43, "stderr": "dirNFErr"})
    assert res["success"] is False
    assert res["error"] == "dirNFErr"


def test_a_down_daemon_is_reported_rather_than_sent_into():
    conn, res = _write("x", conn_kw={"connected": False})
    assert res["success"] is False
    assert conn.sent == []


# --- type and creator --------------------------------------------------------
def test_the_default_is_a_text_file_mpw_can_open():
    conn, res = _write("x")
    typ, crt = _fields(conn.sent[0])[1], _fields(conn.sent[0])[2]
    assert bytes.fromhex(typ) == b"TEXT"
    assert bytes.fromhex(crt) == b"MPS "
    assert res["type"] == "TEXT"


def test_type_and_creator_can_be_overridden():
    conn, _ = _write("x", type="ttro", creator="ttxt")
    assert bytes.fromhex(_fields(conn.sent[0])[1]) == b"ttro"
    assert bytes.fromhex(_fields(conn.sent[0])[2]) == b"ttxt"


def test_a_short_code_is_space_padded_to_four():
    conn, _ = _write("x", type="TX", creator="A")
    assert bytes.fromhex(_fields(conn.sent[0])[1]) == b"TX  "
    assert bytes.fromhex(_fields(conn.sent[0])[2]) == b"A   "


def test_no_resource_fork_is_sent():
    conn, _ = _write("x")
    assert base64.b64decode(_fields(conn.sent[0])[4]) == b""


# --- the schema has to match the handler ------------------------------------
def test_the_declared_schema_offers_type_and_creator():
    spec = next(t for t in tools.TOOLS if t["name"] == "mac_write_file")
    props = spec["inputSchema"]["properties"]
    assert set(props) == {"path", "content", "type", "creator"}
    assert spec["inputSchema"]["required"] == ["path", "content"]


def test_the_description_no_longer_promises_a_toolserver_path():
    spec = next(t for t in tools.TOOLS if t["name"] == "mac_write_file")
    assert "WRITEFILE" in spec["description"]


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
