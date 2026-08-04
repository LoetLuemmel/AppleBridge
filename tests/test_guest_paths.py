"""Tests for host/guest_paths.py — finding things in a guest image, not assuming them.

`:System Folder:Preferences:AppleBridge Prefs` sat in TWO files as a constant.
It is the English name. The SE/30 in this project runs German System 7.5 and the
second host's guest is German as well, so on two of three machines the folder is
`Systemordner`: every `hcopy` returned nothing, the loop moved on, and the
fall-through blamed the IMAGES. A reader sent looking for a corrupt disk when the
answer was a word in another language.

The `hls -l` shapes below were MEASURED, not taken from the manual page — an HFS
image was built with `hformat` for the purpose, a German System Folder created in
it, and the real output pasted here (2026-08-04). A parser tested only against
what its author imagined the tool prints is a test of the imagination.

Run: python3 tests/test_guest_paths.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import guest_paths as gp  # noqa: E402

# Verbatim from `hls -l :` on an image built for this test.
GERMAN_ROOT = "d          3 items               Aug  4 17:20 Systemordner"
GERMAN_SYS = (
    "d          0 items               Aug  4 17:20 Kontrollfelder\n"
    "d          1 item                Aug  4 17:20 Preferences\n"
    "f  ????/UNIX         0         8 Aug  4 17:20 System")
PREFS_DIR = "f  TEXT/ABrg        0        24 Aug  4 17:20 AppleBridge Prefs"


class Guest:
    """Answers `hls -l <path>` from a dict of canned listings."""

    def __init__(self, listings):
        self.listings = listings
        self.asked = []

    def __call__(self, argv):
        self.asked.append(argv)
        if argv[:2] == ["hls", "-l"]:
            return self.listings.get(argv[2], "")
        return ""


GERMAN = {
    ":": GERMAN_ROOT,
    ":Systemordner:": GERMAN_SYS,
    ":Systemordner:Preferences:": PREFS_DIR,
    ":Systemordner:Kontrollfelder:": "",
}


# --- the parser ------------------------------------------------------------
def test_a_directory_line_is_read_as_a_directory():
    assert gp.parse_hls_long(GERMAN_ROOT) == [{"kind": "d", "name": "Systemordner"}]


def test_a_name_with_spaces_survives():
    """`.split()[-1]` would have returned "Prefs". Every interesting name on a
    classic Mac has a space in it."""
    assert gp.parse_hls_long(PREFS_DIR)[0]["name"] == "AppleBridge Prefs"


def test_a_four_digit_year_parses_as_well_as_a_time():
    """hls prints the YEAR instead of the time once a file is old enough — and
    every file on a 1996 system image is."""
    line = "f  TEXT/ttxt        0       512 Jan 12  1996 AppleBridge Prefs"
    assert gp.parse_hls_long(line)[0]["name"] == "AppleBridge Prefs"


def test_a_locked_file_is_still_a_file():
    """hls prints `F` for a Macintosh-locked file. Reading that as "not a file"
    would make a locked prefs file invisible."""
    line = "F  TEXT/ABrg        0       120 Aug  4 17:20 AppleBridge Prefs"
    assert gp.parse_hls_long(line) == [{"kind": "f", "name": "AppleBridge Prefs"}]


def test_an_unparseable_line_is_dropped_not_guessed():
    """A wrong name becomes a path that silently does not exist — the failure
    this whole module exists to end."""
    assert gp.parse_hls_long("hls: no volume is current") == []


# --- finding the System Folder ---------------------------------------------
def test_the_german_system_folder_is_found():
    """The case that was broken: two of three guests in this project."""
    run = Guest(GERMAN)
    assert gp.find_system_folder(run)[0] == ":Systemordner:"


def test_it_is_found_by_its_contents_not_its_name():
    """A list of folder names can only cover the languages somebody thought of.
    `System` is untranslated on every localisation seen here, so the contents
    test answers for a language nobody listed."""
    run = Guest({":": "d          3 items               Aug  4 17:20 Rendszermappa",
                 ":Rendszermappa:": GERMAN_SYS})
    folder, why = gp.find_system_folder(run)
    assert folder == ":Rendszermappa:", why
    assert "System" in why


def test_a_folder_merely_named_like_one_does_not_win():
    """The name list is a fast path, never the answer. A decoy called
    "System Folder" with nothing in it must not end the search."""
    run = Guest({
        ":": ("d          0 items               Aug  4 17:20 System Folder\n"
              "d          3 items               Aug  4 17:20 Systemordner"),
        ":System Folder:": "",
        ":Systemordner:": GERMAN_SYS,
    })
    assert gp.find_system_folder(run)[0] == ":Systemordner:"


def test_no_system_folder_says_where_it_looked():
    """The defect being repaired was a message that blamed the image. A reason
    that names the directories searched sends the reader to the right place."""
    run = Guest({":": "d          1 item                Aug  4 17:20 Dokumente",
                 ":Dokumente:": ""})
    folder, why = gp.find_system_folder(run)
    assert folder is None
    assert "Dokumente" in why


# --- finding the prefs file -------------------------------------------------
def test_the_prefs_file_is_found_in_a_german_image():
    run = Guest(GERMAN)
    path, why = gp.find_guest_prefs(run)
    assert path == ":Systemordner:Preferences:AppleBridge Prefs", why


def test_a_kit_image_keeps_it_at_the_root():
    run = Guest({":": PREFS_DIR})
    path, why = gp.find_guest_prefs(run)
    assert path == gp.KIT_PREFS_HFS, why


def test_the_system_folder_beats_the_root_copy():
    """A volume with both must answer with the one the daemon actually reads."""
    listings = dict(GERMAN)
    listings[":"] = GERMAN_ROOT + "\n" + PREFS_DIR
    path, _ = gp.find_guest_prefs(Guest(listings))
    assert path == ":Systemordner:Preferences:AppleBridge Prefs"


def test_a_system_folder_without_the_file_is_a_different_answer():
    """"Wrong image" and "guest not installed yet" send the reader to opposite
    places, so they must not share a message."""
    listings = dict(GERMAN)
    listings[":Systemordner:Preferences:"] = ""
    path, why = gp.find_guest_prefs(Guest(listings))
    assert path is None
    assert "System Folder" in why and "Systemordner" in why


def test_nothing_at_all_names_both_things_it_tried():
    path, why = gp.find_guest_prefs(Guest({":": ""}))
    assert path is None
    assert "root" in why


def test_the_search_never_mounts_or_unmounts():
    """The caller holds the volume for its own reasons — bridge_doctor mounts
    inside a try/finally that unmounts. A helper that mounted too would leave a
    volume behind on every early return."""
    run = Guest(GERMAN)
    gp.find_guest_prefs(run)
    assert all(a[0] == "hls" for a in run.asked), run.asked


# --- the printed instruction ------------------------------------------------
def test_the_printed_path_admits_which_language_it_is():
    """It goes into an instruction where no image is open, so it cannot be
    checked — and an unhedged path there is a claim nobody verified."""
    text = gp.describe_prefs_location()
    assert "System Folder" in text and "Systemordner" in text


# --- the call sites ---------------------------------------------------------
def test_neither_tool_still_hardcodes_the_english_path():
    """The point of the repair: per call site, not per class. Both files had
    their own copy of the constant, so fixing one would have left the other."""
    here = os.path.join(os.path.dirname(__file__), "..", "host")
    for name in ("bridge_doctor.py", "install_bridge.py"):
        src = open(os.path.join(here, name), encoding="utf-8").read()
        code = [l for l in src.splitlines()
                if ":System Folder:Preferences:" in l and not l.strip().startswith("#")]
        assert not code, f"{name} still names the English path in code: {code}"


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
