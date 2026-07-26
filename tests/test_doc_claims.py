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
DESIGN_DOCS_GLOB = "docs/*.md"
DESIGN_DOCS_EXTRA = ["RX_TX_LEDS.md"]
_STATUS_MARKER_RE = re.compile(r"✅|\bPR #\d+|\bSHIPPED\b|^Status:.*(?:[Ss]hipped|[Dd]one)")

# Whole files that already carry a progress journal — known debt, listed so it
# cannot grow silently. Stripping a file's journal removes it from this set.
STATUS_DEBT = {
    "RX_TX_LEDS.md",
    "docs/JOURNALING_MENU_BY_NAME.md",
    "docs/INPUT_MODIFIERS_AND_MENUS.md",
}

# Individual lines where a marker is NOT progress journaling: a rhetorical
# checkmark, or a PR number cited as *provenance* of a corrected belief (which
# is encouraged, not banned). Same rule as HISTORICAL_COUNTS: an exemption may
# not outlive the line it exempts.
STATUS_OK_LINES = {
    "docs/ARCHITECTURE_LAYERS.md": ["✅ **Both together:**"],
    "docs/SETUP.md": ["Fixed in PR #75"],
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
    rels = [os.path.relpath(p, _ROOT).replace(os.sep, "/")
            for p in glob.glob(os.path.join(_ROOT, DESIGN_DOCS_GLOB))]
    return sorted(rels + DESIGN_DOCS_EXTRA)


def test_design_docs_carry_no_status_markers():
    bad = []
    for rel in _design_docs():
        if rel in STATUS_DEBT:
            continue
        ok_lines = STATUS_OK_LINES.get(rel, [])
        for n, line in enumerate(_read(rel).split("\n"), 1):
            if any(marker in line for marker in ok_lines):
                continue
            if _STATUS_MARKER_RE.search(line):
                bad.append(f"{rel}:{n} journals progress in a design doc: {line.strip()[:90]}")
    assert not bad, ("status belongs on the ledger, not in design docs:\n  "
                     + "\n  ".join(bad))


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
