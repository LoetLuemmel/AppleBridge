"""`Files` output is one name per LINE, and classic-Mac names contain spaces.

The last entry on the decider-coverage debt list, and it was debt for a reason:
`get_file_list` split the listing on WHITESPACE. Real output from Basilisk II
on 2026-07-28, `Files "MeinMac:AppleBridge:"`:

    AB.old4
    ABSmoke.txt
    AppleBridge
    'AppleBridge old'
    AppleBridgeConfig

`Files` prints one name per line and QUOTES any name that needs it. Split on
whitespace, `'AppleBridge old'` becomes `'AppleBridge` and `old'` — the real
file gone, two entries naming nothing in its place. Everything downstream then
compiles, links or deletes against a wrong list, and no status code says so.

'System Folder', 'Apple Menu Items', 'AppleBridge new' — the guest is full of
these. `mac_list_files` parses by column precisely because of it; this function
never got the lesson. Same defect twice in one codebase is what an untested
decider buys you.

The reverse direction had the same hole: `Files MeinMac:My Folder:` is two
arguments to the MPW shell.

Run: python3 tests/test_build_file_list.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import build  # noqa: E402

# Verbatim from the guest, 2026-07-28 (Files "MeinMac:AppleBridge:").
LIVE_LISTING = ("AB.old4\nAB.old5\nAB.old6\nAB.old7\nABJournalDRVR\n"
                "ABMenuPatch\nABSmoke.txt\nAppleBridge\n'AppleBridge old'\n"
                "AppleBridgeConfig\nAppleBridgeWatchdog\nPrefs.good")


# --- the defect -------------------------------------------------------------
def test_a_name_with_a_space_survives_as_one_entry():
    got = build.parse_files_output(LIVE_LISTING)
    assert "AppleBridge old" in got, got
    assert "'AppleBridge" not in got and "old'" not in got


def test_the_live_listing_yields_exactly_its_twelve_names():
    got = build.parse_files_output(LIVE_LISTING)
    assert len(got) == 12, got
    assert got[0] == "AB.old4" and got[-1] == "Prefs.good"


def test_quotes_are_stripped_only_when_they_are_a_pair():
    # A lone apostrophe inside a name is not a quote to strip.
    assert build.parse_files_output("Bob's File") == ["Bob's File"]
    assert build.parse_files_output("'quoted name'") == ["quoted name"]


def test_an_escaped_quote_inside_a_quoted_name_is_restored():
    # MPW escapes an embedded quote with the partial-diff character.
    assert build.parse_files_output("'Bob∂'s File'") == ["Bob's File"]


# --- shapes the guest actually produces -------------------------------------
def test_classic_mac_line_endings_split_the_same_way():
    for eol in ("\r", "\n", "\r\n"):
        assert build.parse_files_output(f"System Folder{eol}Finder") == \
            ["System Folder", "Finder"]


def test_no_match_yields_an_empty_list_not_a_phantom_entry():
    # `Files "…≈.nosuch"` prints nothing; an empty string must not become [''].
    for empty in ("", "\n", "  \n \r\n"):
        assert build.parse_files_output(empty) == [], repr(empty)


def test_a_single_name_needs_no_separator():
    assert build.parse_files_output("AppleBridge") == ["AppleBridge"]


def test_trailing_whitespace_on_a_line_is_not_part_of_the_name():
    assert build.parse_files_output("Finder  \nSystem  ") == ["Finder", "System"]


# --- the outbound half ------------------------------------------------------
def test_a_path_with_a_space_is_quoted_for_the_shell():
    assert build.mpw_quote("MeinMac:My Folder:") == "'MeinMac:My Folder:'"


def test_a_path_without_a_space_is_left_bare():
    assert build.mpw_quote("MeinMac:AppleBridge:") == "MeinMac:AppleBridge:"


def test_an_already_quoted_path_is_not_quoted_twice():
    for already in ("'MeinMac:My Folder:'", '"MeinMac:My Folder:"'):
        assert build.mpw_quote(already) == already


def test_an_embedded_quote_is_escaped_the_way_mpw_expects():
    assert build.mpw_quote("MeinMac:Bob's Stuff:") == "'MeinMac:Bob∂'s Stuff:'"


def test_the_empty_path_does_not_crash():
    assert build.mpw_quote("") == ""


# --- the two halves together, without a live bridge -------------------------
def test_get_file_list_quotes_the_request_and_parses_the_reply():
    sent = []

    def fake_send(cmd, host="127.0.0.1", port=9001):
        sent.append(cmd)
        return f"STATUS:0\nSTDOUT:{len(LIVE_LISTING)}\n{LIVE_LISTING}\nSTDERR:0\n"

    real = build.send_command
    build.send_command = fake_send
    try:
        got = build.get_file_list("MeinMac:My Folder:")
        assert sent == ["Files 'MeinMac:My Folder:'"], sent
        assert "AppleBridge old" in got, got
    finally:
        build.send_command = real


def test_the_pattern_is_appended_inside_the_quoting():
    # Quoting the directory and then appending the pattern would put the
    # wildcard OUTSIDE the quotes, where the shell reads it as another argument.
    sent = []

    def fake_send(cmd, host="127.0.0.1", port=9001):
        sent.append(cmd)
        return "STATUS:0\nSTDOUT:0\n\nSTDERR:0\n"

    real = build.send_command
    build.send_command = fake_send
    try:
        build.get_file_list("MeinMac:My Folder:", "≈.c")
        assert sent == ["Files 'MeinMac:My Folder:≈.c'"], sent
    finally:
        build.send_command = real


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
