"""Golden-input tests for host/encoding_convert.py — UTF-8/LF <-> MacRoman/CR.

These pin the pure byte/text transforms the bridge relies on so a regression
can't silently mangle source going to (or coming from) the classic Mac: line
endings in both directions and the MPW-critical MacRoman characters.

Run: python3 tests/test_encoding_convert.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import encoding_convert as ec  # noqa: E402


# --- line endings: host LF -> Mac CR --------------------------------------

def test_to_mac_lf_becomes_cr():
    assert ec.convert_line_endings_to_mac(b"a\nb\nc") == b"a\rb\rc"


def test_to_mac_crlf_becomes_single_cr():
    assert ec.convert_line_endings_to_mac(b"a\r\nb") == b"a\rb"


def test_to_mac_existing_cr_preserved():
    assert ec.convert_line_endings_to_mac(b"a\rb") == b"a\rb"


# --- line endings: Mac CR -> host LF --------------------------------------

def test_from_mac_cr_becomes_lf():
    assert ec.convert_line_endings_from_mac(b"a\rb\rc") == b"a\nb\nc"


def test_from_mac_crlf_becomes_single_lf():
    assert ec.convert_line_endings_from_mac(b"a\r\nb") == b"a\nb"


def test_line_ending_roundtrip():
    original = b"line1\nline2\nline3"
    to_mac = ec.convert_line_endings_to_mac(original)
    assert b"\n" not in to_mac and to_mac.count(b"\r") == 2
    assert ec.convert_line_endings_from_mac(to_mac) == original


# --- MacRoman encoding: MPW-critical characters ---------------------------

def test_macroman_mpw_special_chars():
    # From the project encoding table: these MUST map to these exact bytes.
    assert ec.utf8_to_macroman("∂") == b"\xb6"   # ∂ line continuation
    assert ec.utf8_to_macroman("ƒ") == b"\xc4"   # ƒ folder
    assert ec.utf8_to_macroman("≈") == b"\xc5"   # ≈ wildcard (0xC5; NOT 0xC7 = «)


def test_macroman_common_symbols():
    assert ec.utf8_to_macroman("•") == b"\xa5"   # • bullet
    assert ec.utf8_to_macroman("©") == b"\xa9"   # ©
    assert ec.utf8_to_macroman("®") == b"\xa8"   # ®
    assert ec.utf8_to_macroman("™") == b"\xaa"   # ™
    assert ec.utf8_to_macroman("π") == b"\xb9"   # π


def test_macroman_ascii_is_identity():
    assert ec.utf8_to_macroman("Hello, World!\t42") == b"Hello, World!\t42"


def test_macroman_decode_roundtrip():
    text = "café ∂x ≈ ™ © folderƒ"
    encoded = ec.utf8_to_macroman(text)
    assert ec.macroman_to_utf8(encoded) == text


def test_unencodable_char_becomes_question_mark():
    # A char with no MacRoman equivalent falls back to b"?" (not a crash).
    assert ec.utf8_to_macroman("A你B") == b"A?B"   # 你 -> ?


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
