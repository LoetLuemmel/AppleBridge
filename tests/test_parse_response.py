"""Tests for MacConnection response parsing — the "no false success" contract.

The bridge must never report success for a reply that did not actually carry a
STATUS line: daemon-down sentinels, errors, empty replies, and truncated frames
all have to surface as a non-zero status. Regression cover for the silent
false-success bug fixed in fix/p0-no-false-success.

Run: python3 tests/test_parse_response.py   (or via pytest)
"""

import os
import sys

# Import mac_connection.py directly (it has no relative imports) so we avoid the
# name clash between this repo's ./mcp package and the installed `mcp` SDK.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))
from mac_connection import MacConnection  # noqa: E402


def _parse(raw: str):
    return MacConnection()._parse_response(raw.encode("utf-8"))


# --- success cases: a real STATUS:0 frame ---------------------------------

def test_clean_success_with_output():
    status, out, err = _parse("STATUS:0\rSTDOUT:5\rHELLO\rSTDERR:0\r\r")
    assert status == 0, status
    assert out == "HELLO", repr(out)
    assert err == "", repr(err)


def test_nonzero_status_is_preserved():
    status, out, err = _parse("STATUS:1\rSTDOUT:0\rSTDERR:9\r### oops\r\r")
    assert status == 1, status


def test_status_zero_with_empty_output_is_success():
    # A genuine STATUS:0 with no output (e.g. a silent SetFile) stays success.
    status, out, err = _parse("STATUS:0\rSTDOUT:0\rSTDERR:0\r\r")
    assert status == 0, status
    assert out == "", repr(out)


# --- the false-success cases that used to slip through ---------------------

def test_no_response_sentinel_is_error():
    status, out, err = _parse("No response")
    assert status != 0, "daemon-down sentinel must not be success"
    assert "not connected" in err.lower(), err


def test_error_sentinel_is_error():
    status, out, err = _parse("ERROR: something broke")
    assert status != 0, "ERROR sentinel must not be success"
    assert "something broke" in err


def test_empty_reply_is_error():
    status, out, err = _parse("")
    assert status != 0, "empty reply must not be success"


def test_frame_without_status_line_is_error():
    # Truncated/malformed: STDOUT arrived but the STATUS line never did.
    status, out, err = _parse("STDOUT:5\rHELLO\r")
    assert status != 0, "a frame with no STATUS line must not be success"


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
