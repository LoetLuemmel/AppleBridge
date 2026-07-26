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
