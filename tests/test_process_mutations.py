"""Mutation testing for the documentation guards — a test of the tests.

`test_doc_claims.py` asserts that the documentation's checkable claims match the
code. But a guard that never fails is indistinguishable from no guard at all, and
this project has already published a case study of a verification that could not
fail (the key-code defect: Command-A passed for three weeks because `a` happens
to sit at virtual key code zero, the very value being sent wrongly).

So: seed a known defect, run the guards, and require that they notice. A mutant
that SURVIVES is a hole in the guards and a bug report against the process.

Everything happens in a scratch copy of the relevant files, so a run is free —
the working tree is never touched, and a crash leaves nothing behind.

    python3 tests/test_process_mutations.py     (or via pytest)
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Only what test_doc_claims.py reads — a targeted copy keeps a run fast.
# `host` is needed because mcp/tools.py adds it to sys.path and imports
# macbinary / bridge_doctor / guest_input from it; without it the guards die on
# an ImportError in every copy, and every mutant would look killed. The control
# test below exists precisely to catch that class of mistake — it did, on the
# first run of this harness.
_NEEDED = ["mcp", "host", "docs", "README.md", "ARCHITECTURE.md", "CLAUDE.md",
           "TROUBLESHOOTING.md", "RX_TX_LEDS.md", "DECISIONS.md"]


def _scratch():
    """A throwaway copy of the files under test. Caller removes it."""
    tmp = tempfile.mkdtemp(prefix="ab-mutation-")
    for rel in _NEEDED:
        src = os.path.join(_ROOT, rel)
        if not os.path.exists(src):
            continue
        dst = os.path.join(tmp, rel)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    os.makedirs(os.path.join(tmp, "mac"), exist_ok=True)
    shutil.copy2(os.path.join(_ROOT, "mac", "vers.r"), os.path.join(tmp, "mac", "vers.r"))
    os.makedirs(os.path.join(tmp, "tests"), exist_ok=True)
    shutil.copy2(os.path.join(_ROOT, "tests", "test_doc_claims.py"),
                 os.path.join(tmp, "tests", "test_doc_claims.py"))
    return tmp


def _guards_pass(root):
    """Run the guards inside `root`. -> (passed, output)."""
    proc = subprocess.run([sys.executable, os.path.join(root, "tests", "test_doc_claims.py")],
                          capture_output=True, text=True, timeout=120, cwd=root)
    return proc.returncode == 0, (proc.stdout + proc.stderr)


def _edit(root, rel, fn):
    """Rewrite a file through `fn`; -> False when the mutation did not apply."""
    path = os.path.join(root, rel)
    with open(path, encoding="utf-8") as fh:
        before = fh.read()
    after = fn(before)
    if after is None or after == before:
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(after)
    return True


# --- the mutants ------------------------------------------------------------
# Each returns True when it managed to plant its defect. A mutant that cannot be
# applied is reported separately from one that survives: it means the thing it
# was aiming at has moved, and the guard may now be pointing at nothing.

def _m_tool_count_off_by_one(root):
    def bump(text):
        m = re.search(r"\b(\d+)(\s+(?:MCP\s+)?tools\b)", text)
        return text[:m.start(1)] + str(int(m.group(1)) + 1) + text[m.end(1):] if m else None
    return _edit(root, "README.md", bump)


def _m_tool_renamed_in_code(root):
    return _edit(root, os.path.join("mcp", "tools.py"),
                 lambda t: t.replace('"name": "mac_status"', '"name": "mac_status_renamed"', 1)
                 if '"name": "mac_status"' in t else None)


def _m_version_bumped_only_in_code(root):
    def bump(text):
        m = re.search(r'"(\d+\.\d+)([a-z])(\d+)"', text)
        if not m:
            return None
        new = '"{}{}{}"'.format(m.group(1), m.group(2), int(m.group(3)) + 1)
        return text.replace(m.group(0), new)
    return _edit(root, os.path.join("mac", "vers.r"), bump)


def _m_status_marker_in_design_doc(root):
    target = os.path.join("docs", "SERIAL_TRANSPORT.md")
    if not os.path.exists(os.path.join(root, target)):
        return False
    return _edit(root, target, lambda t: t + "\n- Phase 2 SHIPPED in PR #999.\n")


def _m_decision_without_falsifier(root):
    # Must strike a real entry: the file's header explains the format with the
    # same field names, and an early version of this mutant deleted the
    # explanation instead — planting no defect, then reporting the guard as
    # holed. Everything from the first '## D-' heading onward is a real entry.
    def drop(text):
        i = text.find("\n## D-")
        if i < 0:
            return None
        head, entries = text[:i], text[i:]
        cut = re.sub(r"\n- \*\*Revisit if:\*\*[^\n]*", "", entries, count=1)
        return head + cut if cut != entries else None
    return _edit(root, "DECISIONS.md", drop)


def _m_duplicate_decision_id(root):
    def dup(text):
        ids = re.findall(r"^## (D-\d{3}) — ", text, re.M)
        if len(ids) < 2:
            return None
        return text.replace(f"## {ids[1]} — ", f"## {ids[0]} — ", 1)
    return _edit(root, "DECISIONS.md", dup)


def _m_superseded_without_successor(root):
    return _edit(root, "DECISIONS.md",
                 lambda t: t.replace("**Status:** active", "**Status:** superseded", 1)
                 if "**Status:** active" in t else None)


def _m_hard_rule_without_provenance(root):
    return _edit(root, "CLAUDE.md",
                 lambda t: t.replace("## Hard rules (learned the hard way)\n",
                                     "## Hard rules (learned the hard way)\n"
                                     "- **Never do the bad thing** because it is bad.\n", 1)
                 if "## Hard rules (learned the hard way)\n" in t else None)


MUTANTS = [
    ("tool count off by one",          _m_tool_count_off_by_one),
    ("tool renamed in code only",      _m_tool_renamed_in_code),
    ("version bumped in code only",    _m_version_bumped_only_in_code),
    ("status marker in a design doc",  _m_status_marker_in_design_doc),
    ("decision without a falsifier",   _m_decision_without_falsifier),
    ("duplicate decision id",          _m_duplicate_decision_id),
    ("superseded naming no successor", _m_superseded_without_successor),
    ("hard rule without provenance",   _m_hard_rule_without_provenance),
]


# --- the control ------------------------------------------------------------

def test_the_unmutated_copy_passes():
    """Without this, a 'killed' mutant could just be a pre-existing failure."""
    root = _scratch()
    try:
        ok, out = _guards_pass(root)
        assert ok, "the guards fail on an UNMUTATED copy, so no kill below is meaningful:\n" + out[-800:]
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- the mutation run -------------------------------------------------------

def test_every_seeded_defect_is_caught():
    survived, unapplied = [], []
    for name, mutate in MUTANTS:
        root = _scratch()
        try:
            if not mutate(root):
                unapplied.append(name)
                continue
            ok, _ = _guards_pass(root)
            if ok:
                survived.append(name)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    problems = []
    if survived:
        problems.append("SURVIVED (a hole in the guards): " + ", ".join(survived))
    if unapplied:
        # Not a pass: the defect could not be planted, so the guard aimed at it
        # is now unexercised — usually because the text it targets moved.
        problems.append("COULD NOT BE APPLIED (guard may aim at nothing): " + ", ".join(unapplied))
    assert not problems, "mutation run:\n  " + "\n  ".join(problems)


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
