"""Tests that the documentation's checkable claims still match the code.

Every drift found on 2026-07-26 was the same shape: prose that was true when
written and silently stopped being true — four documents claiming "20 tools"
while `len(TOOLS)` had been 30 for weeks, ten tools missing from the
enumerations. Nobody was careless; the claims simply had nothing holding them
to the code.

These tests are that hold. They check only what is mechanically checkable:
counts and tool names. Prose stays prose.

A statement about the PAST is not drift — "the surface grew 7 -> 20 tools
(2026-06-29)" was accurate then and must stay readable. Such lines are listed
in HISTORICAL_COUNTS, so a *new* count claim has to match the code or be
deliberately declared historical.

Run: python3 tests/test_doc_claims.py   (or via pytest)
"""

import os
import re
import sys
import types

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MCP = os.path.join(_ROOT, "mcp")


def _load_tools():
    """Import mcp/tools.py flat, stubbing its relative import (the repo's ./mcp
    package would otherwise clash with the installed `mcp` SDK). Same approach
    as test_input_modifiers.py."""
    sys.path.insert(0, _MCP)
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


tools = _load_tools()
TOOL_NAMES = [t["name"] for t in tools.TOOLS]

# Documents that describe the CURRENT surface. Docs not listed here are free to
# tell the project's history without tripping these tests.
CURRENT_DOCS = ["README.md", "ARCHITECTURE.md", "CLAUDE.md", "docs/SETUP.md"]

# Count claims that are deliberately about a PAST state. Keyed by file, value is
# a substring identifying the line. Add to this list only when the sentence
# really is dated — otherwise fix the number.
# Currently empty: the one dated count claim ("the surface grew 7 -> 20 tools",
# 2026-06-29) lived in CLAUDE.md's status narrative, which moved to the ledger.
# The test below noticed the sentence had gone and required this entry to go with
# it — an exemption may not outlive the sentence it exempts.
HISTORICAL_COUNTS = {}

# The authoritative enumeration an agent reads first.
INVENTORY_DOC = "CLAUDE.md"

# Module and file names that share the tools' prefix and are not tools.
NOT_TOOLS = {"mac_connection"}

# The daemon's own 'vers' resource is the project's only version number: it is
# what the Finder shows in Get Info, and `'vers'(2)` is commented there as the
# shared/suite line. There are no git tags, so no second release track exists —
# which is why "v0.7.0" sat in three documents as the *current* version long
# after it had become a milestone inside the 0.8 line.
VERS_R = "mac/vers.r"

# Lines that declare what version the reader is looking at. Anything else
# mentioning a version (a dated milestone, "wire protocol v0.2") is prose.
VERSION_MARKERS = ("**Version:**", "**Current daemon:**", "**Target:**")
VERSION_DOCS = ["README.md", "TROUBLESHOOTING.md", "docs/SETUP.md", "CLAUDE.md"]

# "20 tools", "(30 tools)", "20 MCP tools" — the qualifier in the middle slipped
# past the first version of this pattern and left a stale count in SETUP.md.
_COUNT_RE = re.compile(r"\(?(\d+)\s+(?:MCP\s+)?tools\b")
_NAME_RE = re.compile(r"\b(mac_[a-z_]+|mpw_execute|launch_app|run_applescript|bridge_doctor)\b")

# --- design docs must not journal progress ----------------------------------
# Design documents explain mechanism and rationale; "is it done" belongs to the
# ledger. Three docs grew running progress journals (✅ PASSED per step, PR
# numbers, "Phase 1 shipped") and each had to be maintained at the tempo of its
# fastest-changing sentence. This is a RATCHET: the named debt below is frozen,
# new markers anywhere else fail.
DESIGN_DOCS_GLOB = "docs/**/*.md"          # recursive: a subdirectory is not an escape
DESIGN_DOCS_EXTRA = ["RX_TX_LEDS.md"]
_STATUS_MARKER_RE = re.compile(r"✅|\bPR #\d+|\bSHIPPED\b|^Status:.*(?:[Ss]hipped|[Dd]one)")

# Frozen historical snapshots. NOT debt — these are progress journals on purpose
# and will never be cleaned up, because their whole value is being a verbatim
# record of what a page said on one date. The rule they are exempt from exists to
# stop status DRIFTING between two live places, and a dated snapshot cannot
# drift: nothing will ever be updated here.
#
# Deliberately a separate set from STATUS_DEBT, which means "should shrink to
# empty" and has. Filing a permanent, correct exception as debt would either
# make that set look unfinished forever or invite somebody to "fix" the archive
# by deleting it.
#
# The glob became recursive in the same change. Putting the archive in a
# subdirectory would have silently exempted it — and every future file beside
# it — which is a hole rather than a decision.
ARCHIVES = {"docs/archive/LEDGER_ARCHIVE.md"}

# Whole files that already carry a progress journal — known debt, listed so it
# cannot grow silently. Stripping a file's journal removes it from this set, and
# the set may only ever shrink: the three files it was created with (RX_TX_LEDS,
# JOURNALING_MENU_BY_NAME, INPUT_MODIFIERS_AND_MENUS) were cleared on 2026-07-26,
# so every design doc is now held to the rule with no exceptions.
STATUS_DEBT = set()

# Individual lines where a marker is NOT progress journaling: a rhetorical
# checkmark, or a PR number cited as *provenance* of a corrected belief (which
# is encouraged, not banned). Same rule as HISTORICAL_COUNTS: an exemption may
# not outlive the line it exempts.
#
# The INSTALLER_REQUIREMENTS entry is an *upstream* PR (kanjitalk755/macemu),
# not ours. It is cited for the same reason as the others — it says where the
# arm64 finding went — and it is deliberately written so the requirement does
# not depend on its outcome: a preflight must still read the bundle's Mach-O
# slices whether or not that PR merges. If it ever turns into "fixed upstream,
# nothing to check", that is drift and the line should go, not the exemption.
STATUS_OK_LINES = {
    "docs/ARCHITECTURE_LAYERS.md": ["✅ **Both together:**"],
    "docs/SETUP.md": ["Fixed in PR #75"],
    "docs/INSTALLER_REQUIREMENTS.md": ["PR #314 on"],
}

# --- decisions of record -----------------------------------------------------
DECISIONS_DOC = "DECISIONS.md"
_DECISION_HEAD_RE = re.compile(r"^## (D-\d{3}) — .+", re.M)
DECISION_FIELDS = ("**Date:**", "**Status:**", "**Decision:**", "**Evidence:**", "**Revisit if:**")

# --- hard rules carry provenance ---------------------------------------------
# A rule with no evidence and no pointer is folklore in waiting: four such rules
# from the initial commit were false and survived 82–110 days because nothing
# made them cheap to doubt. Checkable half: every Hard-rules bullet names a year
# or points somewhere (a [[memory link]], a file, a doc section). The falsifier
# itself stays prose discipline.
HARD_RULES_DOC = "CLAUDE.md"
_PROVENANCE_RE = re.compile(r"20\d\d|\[\[|\.(?:md|py|sh|r|c)\b|/")


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _count_claims(rel):
    """-> [(line_no, claimed_count, line)] excluding declared-historical lines."""
    out = []
    for n, line in enumerate(_read(rel).split("\n"), 1):
        if any(marker in line for marker in HISTORICAL_COUNTS.get(rel, [])):
            continue
        for m in _COUNT_RE.finditer(line):
            out.append((n, int(m.group(1)), line.strip()))
    return out


# --- the count -------------------------------------------------------------

def test_every_current_tool_count_matches_the_code():
    expected = len(TOOL_NAMES)
    wrong = []
    for rel in CURRENT_DOCS:
        for n, claimed, line in _count_claims(rel):
            if claimed != expected:
                wrong.append(f"{rel}:{n} claims {claimed}, len(TOOLS) is {expected}: {line[:90]}")
    assert not wrong, "stale tool count:\n  " + "\n  ".join(wrong)


def test_the_count_is_actually_stated_somewhere():
    # Guards the guard: if every count claim were deleted, the test above would
    # pass vacuously and the next drift would go unnoticed.
    total = sum(len(_count_claims(rel)) for rel in CURRENT_DOCS)
    assert total >= 3, f"only {total} tool-count claims left across {CURRENT_DOCS}"


def test_historical_claims_are_still_present_and_still_historical():
    # If a dated line is reworded, its allowlist entry must be revisited rather
    # than silently exempting a line that no longer exists.
    for rel, markers in HISTORICAL_COUNTS.items():
        text = _read(rel)
        for marker in markers:
            assert marker in text, f"{rel}: allowlisted historical line vanished: {marker!r}"


# --- the names -------------------------------------------------------------

def test_every_tool_appears_in_the_inventory():
    text = _read(INVENTORY_DOC)
    missing = [name for name in TOOL_NAMES if name not in text]
    assert not missing, (f"{INVENTORY_DOC} does not mention: " + ", ".join(missing))


def test_a_doc_section_that_advertises_the_tools_lists_them_all():
    """The count was checked and the CLAUDE.md inventory was checked, and the
    README still shipped a section headed "Available MCP Tools" holding SIX of
    thirty — twenty lines above a tree comment that said thirty. Nothing looked
    at it, because the inventory test only ever read CLAUDE.md.

    A section that presents itself as the tool list has to be one. Reported by
    the operator 2026-08-01, reading the rendered README on GitHub.
    """
    heading = "## Available MCP Tools"
    missing = []
    for rel in CURRENT_DOCS:
        text = _read(rel)
        if heading not in text:
            continue
        section = text.split(heading, 1)[1].split("\n## ", 1)[0]
        absent = [n for n in TOOL_NAMES if n not in section]
        if absent:
            missing.append(f"{rel} advertises the tool list but omits "
                           f"{len(absent)} of {len(TOOL_NAMES)}: "
                           + ", ".join(absent))
    assert not missing, "incomplete tool section:\n  " + "\n  ".join(missing)


def test_no_required_quick_start_step_is_about_claude_code():
    """The prerequisites say in as many words that the bridge works without
    Claude Code. The quick start then made "Configure MCP" step 4 of 6 —
    numbered, unmarked, and AHEAD of the step that checks whether the bridge
    came up at all (which is a `nc localhost 9001` and needs no MCP). A reader
    following the numbers would configure an AI client before confirming the
    thing it is meant to talk to.

    Spotted by the operator 2026-08-01. The two statements have to move
    together, so this checks both halves.
    """
    text = _read("README.md")
    assert "The bridge itself works without it" in text, (
        "the prerequisites no longer say the bridge works without Claude Code — "
        "if that stopped being true, this test and the quick start move together")

    bad = []
    for line in text.split("\n"):
        m = re.match(r"### (\d+)\. (.+)", line)
        if not m:
            continue
        title = m.group(2)
        mentions = any(w in title.lower() for w in ("mcp", "claude code"))
        if mentions and "optional" not in title.lower():
            bad.append(f"step {m.group(1)} is about Claude Code but is not "
                       f"marked optional: {title!r}")
    assert not bad, "\n  ".join(bad)


CONFIG_SOURCE = os.path.join("mac", "config", "config.c")
CONFIG_DOCS = ["README.md", os.path.join("mac", "config", "README.md")]


def test_every_control_panel_button_is_documented():
    """The daemon is faceless, so AppleBridgeConfig is the ONLY place a human
    changes anything — which makes an undocumented control there invisible in
    both directions.

    That happened: the editable **Host IP** field and its **Set** button landed
    2026-07-31 17:42, and neither doc ever mentioned them. The README's config
    screenshot was captured at 16:46 — fifty-six minutes BEFORE the feature —
    so the picture showed the address as a static line, and nothing anywhere
    said it could be changed there.

    Derived from `NewControl` calls, so a new button cannot ship undocumented.
    A title counts as documented when a doc names it as a UI element, in bold
    or backticks — prose that happens to contain the word "Set" does not.
    """
    src = _read(CONFIG_SOURCE)
    titles = re.findall(r'NewControl\([^;]*?"\\p([^"]+)"', src)
    assert titles, f"no NewControl titles parsed from {CONFIG_SOURCE}"

    docs = " ".join(_read(rel) for rel in CONFIG_DOCS)
    undocumented = []
    for title in sorted(set(titles)):
        stem = re.sub(r"(\.\.\.|\u2026)\s*$", "", title).strip()
        pattern = re.escape(stem) + r"(\.\.\.|\u2026)?"
        if re.search(r"\*\*" + pattern + r"\*\*", docs):
            continue
        if re.search(r"`" + pattern + r"`", docs):
            continue
        undocumented.append(title)
    assert not undocumented, (
        "AppleBridgeConfig has controls no doc names as UI elements: "
        + ", ".join(repr(t) for t in undocumented)
        + f" (looked in {', '.join(CONFIG_DOCS)})")


def test_docs_do_not_name_tools_that_no_longer_exist():
    known = set(TOOL_NAMES)
    stale = []
    for rel in CURRENT_DOCS:
        for n, line in enumerate(_read(rel).split("\n"), 1):
            for name in _NAME_RE.findall(line):
                if name not in known and name not in NOT_TOOLS:
                    stale.append(f"{rel}:{n} names {name!r}, which is not in TOOLS")
    assert not stale, "documented tool does not exist:\n  " + "\n  ".join(sorted(set(stale)))


# --- the version ------------------------------------------------------------

def _daemon_version():
    """Short version string from mac/vers.r; both 'vers' resources must agree."""
    found = re.findall(r'"(\d+\.\d+[a-z]\d+|\d+\.\d+\.\d+)"', _read(VERS_R))
    assert found, f"no version string found in {VERS_R}"
    assert len(set(found)) == 1, f"{VERS_R} disagrees with itself: {sorted(set(found))}"
    return found[0]


def test_version_declarations_match_the_vers_resource():
    version = _daemon_version()
    wrong = []
    for rel in VERSION_DOCS:
        for n, line in enumerate(_read(rel).split("\n"), 1):
            if not any(m in line for m in VERSION_MARKERS):
                continue
            if version not in line:
                wrong.append(f"{rel}:{n} declares a version without {version}: {line.strip()[:100]}")
    assert not wrong, "stale version declaration:\n  " + "\n  ".join(wrong)


def test_a_version_is_declared_somewhere():
    version = _daemon_version()
    declared = sum(1 for rel in VERSION_DOCS for line in _read(rel).split("\n")
                   if any(m in line for m in VERSION_MARKERS) and version in line)
    assert declared >= 2, f"only {declared} document(s) state the current version {version}"


def test_tool_names_are_unique():
    dupes = {n for n in TOOL_NAMES if TOOL_NAMES.count(n) > 1}
    assert not dupes, f"duplicate tool names in TOOLS: {sorted(dupes)}"


# --- process organs (2026-07-26) --------------------------------------------

def _design_docs():
    import glob
    # recursive=True is load-bearing: without it `**` behaves as a single `*`,
    # which would match docs/<sub>/*.md and STOP matching docs/*.md — quietly
    # dropping every file the check was written for while still passing.
    rels = [os.path.relpath(p, _ROOT).replace(os.sep, "/")
            for p in glob.glob(os.path.join(_ROOT, DESIGN_DOCS_GLOB),
                               recursive=True)]
    return sorted(rels + DESIGN_DOCS_EXTRA)


def test_design_docs_carry_no_status_markers():
    bad = []
    for rel in _design_docs():
        if rel in STATUS_DEBT or rel in ARCHIVES:
            continue
        ok_lines = STATUS_OK_LINES.get(rel, [])
        for n, line in enumerate(_read(rel).split("\n"), 1):
            if any(marker in line for marker in ok_lines):
                continue
            if _STATUS_MARKER_RE.search(line):
                bad.append(f"{rel}:{n} journals progress in a design doc: {line.strip()[:90]}")
    assert not bad, ("status belongs on the ledger, not in design docs:\n  "
                     + "\n  ".join(bad))


def test_the_recursive_glob_still_sees_the_top_level_docs():
    # The guard on the guard. `docs/**/*.md` without recursive=True matches only
    # subdirectories, so this check would examine an empty-ish set and pass
    # while every real design doc went unread — the project's own named failure
    # mode, a check that reports success while looking at nothing.
    found = _design_docs()
    assert "docs/SETUP.md" in found or any(
        f.startswith("docs/") and f.count("/") == 1 for f in found), \
        "the glob stopped matching top-level docs/*.md"


def test_an_archive_must_declare_itself_frozen():
    # The exemption is for snapshots, and nothing stops someone dropping a LIVE
    # document into docs/archive/ to escape the status rule. Require the file to
    # say what it is and when it was taken, so the exemption cannot be borrowed.
    for rel in sorted(ARCHIVES):
        text = _read(rel)
        assert "snapshot" in text.lower(), \
            f"{rel} is exempt as an archive but never calls itself a snapshot"
        assert re.search(r"as of \d{4}-\d{2}-\d{2}", text), \
            f"{rel} is exempt as an archive but carries no 'as of <date>'"


def test_archives_that_no_longer_exist_are_not_still_exempt():
    for rel in sorted(ARCHIVES):
        assert os.path.exists(os.path.join(_ROOT, rel)), \
            f"{rel} is exempt as an archive but is gone — drop it from ARCHIVES"


def test_status_exemptions_have_not_outlived_their_lines():
    # Debt files must still contain a marker (else the debt entry is stale),
    # and every exempted line must still exist.
    for rel in sorted(STATUS_DEBT):
        assert _STATUS_MARKER_RE.search(_read(rel)), \
            f"{rel} is listed as status debt but carries no marker — remove it from STATUS_DEBT"
    for rel, lines in STATUS_OK_LINES.items():
        text = _read(rel)
        for marker in lines:
            assert marker in text, f"{rel}: exempted line vanished: {marker!r}"


def test_decisions_register_is_wellformed():
    text = _read(DECISIONS_DOC)
    ids = _DECISION_HEAD_RE.findall(text)
    assert ids, f"{DECISIONS_DOC} has no '## D-NNN — title' entries"
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate decision ids: {sorted(dupes)}"
    sections = _DECISION_HEAD_RE.split(text)[1:]  # [id, body, id, body, ...]
    for did, body in zip(sections[0::2], sections[1::2]):
        missing = [f for f in DECISION_FIELDS if f not in body]
        assert not missing, f"{did} is missing fields: {missing}"
        status = re.search(r"\*\*Status:\*\*\s*(.+)", body).group(1).strip()
        assert status == "active" or re.match(r"superseded\s*(→|->)\s*D-\d{3}", status), \
            f"{did} status must be 'active' or 'superseded → D-NNN', got: {status!r}"


def test_hard_rules_carry_provenance():
    text = _read(HARD_RULES_DOC)
    m = re.search(r"^## Hard rules.*?$(.*?)(?=^## )", text, re.M | re.S)
    assert m, f"{HARD_RULES_DOC} has no '## Hard rules' section"
    naked = []
    for n, line in enumerate(m.group(1).split("\n"), 1):
        if not line.startswith("- "):
            continue
        if not _PROVENANCE_RE.search(line):
            naked.append(f"rule bullet {n} has no year and no pointer: {line[:90]}")
    assert not naked, ("every hard rule carries provenance (a year, a [[link]], or a "
                       "file/doc pointer) — see DECISIONS.md header:\n  " + "\n  ".join(naked))


# --- the guest kit's contents ----------------------------------------------
# `KIT_APPS` in host/install_bridge.py is what actually goes onto the kit image,
# so it is the only honest source for prose describing what the kit holds.
#
# The drift this catches happened on 2026-08-01: PR #138 added ABJournalDRVR to
# the payload, and README, docs/SETUP.md and the public write-up all went on
# saying "four 68K applications and a prefs file" while the screenshot showed
# the five-item volume. One payload change dated four places and broke no test.
KIT_SOURCE = os.path.join("host", "install_bridge.py")
KIT_DOCS = ["README.md", "docs/SETUP.md"]
# A count says nothing about a payload entry that is not an application, so the
# driver has to be NAMED. Either its filename or the phrase satisfies that.
KIT_DRIVER_ALIASES = ("journaling driver",)
_KIT_COUNT_RE = re.compile(r"\b(\w+)\s+68K applications\b")
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _kit_payload():
    """-> (labels, applications, drivers) as declared by KIT_APPS."""
    block = re.search(r"KIT_APPS\s*=\s*\[(.*?)^\]", _read(KIT_SOURCE),
                      re.S | re.M)
    assert block, f"KIT_APPS not found in {KIT_SOURCE}"
    labels = re.findall(r'\(\s*"([^"]+)"', block.group(1))
    assert labels, f"KIT_APPS in {KIT_SOURCE} parsed as empty"
    drivers = [l for l in labels if l.endswith("DRVR")]
    return labels, [l for l in labels if l not in drivers], drivers


def test_the_readme_states_the_address_a_released_kit_actually_ships():
    """The README told readers for a month that a released kit ships `IP=`
    EMPTY and that they had to seed one in. Both published kits carried
    `IP=10.0.2.2` — the rule changed on 2026-07-31 when 10.0.2.2 was measured
    to work, and nothing re-read the prose. A claim about a PUBLISHED artifact
    is the worst kind to leave stale: the reader cannot check it without
    downloading the thing, and a wrong extra step reads as a broken tool.

    So derive it. Whatever `guest_prefs_text("")` writes into a release kit is
    the address the docs must state, and a doc that mentions `IP=` in that
    context must not contradict it.
    """
    sys.path.insert(0, os.path.join(_ROOT, "host"))
    import install_bridge as ib
    shipped = [l.split("=", 1)[1].strip()
               for l in ib.guest_prefs_text("").split("\n")
               if l.startswith("IP=")]
    assert len(shipped) == 1, f"a release kit must ship exactly one IP=, got {shipped}"
    address = shipped[0]
    assert address, ("a release kit now ships an ADDRESS, not an empty field — "
                     "if that changed back, this test and the README move together")

    wrong = []
    for rel in KIT_DOCS:
        text = re.sub(r"\s+", " ", _read(rel))
        if "AppleBridgeKit.dmg" not in text:      # not a doc about the kit
            continue
        if re.search(r"released kit ships with `IP=` \*\*empty\*\*", text) or \
           re.search(r"kit ships `IP=` empty", text):
            wrong.append(f"{rel} still says a released kit ships IP= empty; "
                         f"it ships IP={address}")
        for m in re.finditer(r"`IP=([0-9][0-9.]*)`", text):
            if m.group(1) != address:
                wrong.append(f"{rel} states IP={m.group(1)} but a release kit "
                             f"ships IP={address}")
    assert not wrong, "stale claim about the published kit:\n  " + "\n  ".join(wrong)


def test_the_stated_kit_application_count_matches_the_payload():
    _, apps, _ = _kit_payload()
    wrong = []
    for rel in KIT_DOCS:
        for n, line in enumerate(_read(rel).split("\n"), 1):
            m = _KIT_COUNT_RE.search(line)
            if not m:
                continue
            word = m.group(1).lower()
            stated = _NUMBER_WORDS.get(word,
                                       int(word) if word.isdigit() else None)
            if stated is None:      # "the 68K applications" — not a count claim
                continue
            if stated != len(apps):
                wrong.append(f"{rel}:{n} says {word} but KIT_APPS ships "
                             f"{len(apps)} applications: {line.strip()[:100]}")
    assert not wrong, "stale kit count:\n  " + "\n  ".join(wrong)


def test_a_kit_payload_entry_that_is_not_an_application_is_named():
    """A driver is invisible to "N applications", so prose that describes the
    kit must name it — otherwise the next non-application addition goes
    unmentioned exactly as ABJournalDRVR did."""
    _, _, drivers = _kit_payload()
    missing = []
    for rel in KIT_DOCS:
        # Prose wraps, so "the journaling\ndriver" must still count as a
        # mention: collapse whitespace before looking. Without this the check
        # silently leans on whichever line happens not to wrap.
        text = re.sub(r"\s+", " ", _read(rel))
        if "68K applications" not in text:   # this doc does not describe the kit
            continue
        for d in drivers:
            if d in text or any(a in text for a in KIT_DRIVER_ALIASES):
                continue
            missing.append(f"{rel} describes the kit but never mentions {d}")
    assert not missing, ("a non-application payload entry is unmentioned:\n  "
                         + "\n  ".join(missing))


def test_the_kit_screenshot_caption_lists_the_whole_volume():
    """The figure caption enumerates the volume, so it is a claim like any
    other — and it is the one a reader compares against their own screen."""
    labels, _, _ = _kit_payload()
    setup = _read("docs/SETUP.md")
    m = re.search(r"!\[([^\]]*AppleBridge Kit volume[^\]]*)\]", setup)
    assert m, "docs/SETUP.md has no AppleBridge Kit volume figure to check"
    caption = m.group(1)
    missing = [l for l in labels if l not in caption]
    assert not missing, ("the kit figure caption omits " + ", ".join(missing)
                         + f" — caption: {caption[:120]}")


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
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERR  {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
