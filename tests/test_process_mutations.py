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
# test below exists precisely to catch that class of mistake — it caught it on
# this harness's first run, and three more times on 2026-08-01 alone.
#
# That is why there is no hand-kept list any more. It named "only what
# test_doc_claims.py reads", and every new guard that DERIVED a fact from a new
# file — mac/config/config.c, mac/src/prefs.c, .mcp.json — walked straight into
# a FileNotFoundError here. Three failures in one day is a broken design, not
# three oversights: the list could only ever be correct about the past.
#
# Everything git tracks is 3.2 MB across 190 files, so copying all of it costs
# nothing measurable and cannot go stale.


def _scratch():
    """A throwaway copy of the files under test. Caller removes it."""
    tmp = tempfile.mkdtemp(prefix="ab-mutation-")
    tracked = subprocess.run(["git", "ls-files"], cwd=_ROOT,
                             capture_output=True, text=True).stdout.split("\n")
    for rel in filter(None, tracked):
        src = os.path.join(_ROOT, rel)
        if not os.path.exists(src):      # deleted but still indexed
            continue
        dst = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return tmp


DOC_GUARDS = "test_doc_claims.py"
HARDWARE_GUARDS = "test_hardware_findings.py"


def _guards_pass(root, guards=DOC_GUARDS):
    """Run one guard file inside `root`. -> (passed, output)."""
    proc = subprocess.run([sys.executable, os.path.join(root, "tests", guards)],
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


# --- mutants against the hardware-finding guards ----------------------------
# These aim at fixes that only a real Macintosh ever proved necessary. The point
# is the same as above: a guard standing watch over a defect nobody can still
# reproduce is worth exactly as much as its ability to fail.

def _m_serial_buffer_back_to_the_default(root):
    return _edit(root, "mac/src/transport_serial.c",
                 lambda s: s.replace("#define kSerInBufSize   16384",
                                     "#define kSerInBufSize   64"))


def _m_serial_buffer_freed_before_the_driver_lets_go(root):
    """Restore AFTER the close — the ordering that hands the Serial Manager a
    dangling pointer. Textually a two-line swap; on-device it is a use of freed
    memory that nothing on the host would ever notice."""
    return _edit(root, "mac/src/transport_serial.c",
                 lambda s: s.replace(
                     "        if (gInBuf != NULL) SerSetBuf(c->inRef, NULL, 0);\n"
                     "        CloseDriver(c->inRef);",
                     "        CloseDriver(c->inRef);\n"
                     "        if (gInBuf != NULL) SerSetBuf(c->inRef, NULL, 0);"))


def _m_monitor_window_regrown(root):
    return _edit(root, "mac/src/main.c",
                 lambda s: s.replace("#define COMPACT_MON_W    440",
                                     "#define COMPACT_MON_W    480"))


def _m_clamp_dropped_from_the_restored_path(root):
    """Keep the clamp for a fresh window, lose it for a rect restored from
    prefs — the half of the fix that is easy to delete because it looks
    redundant, and the half that mattered on a screen the rect was never
    saved on."""
    return _edit(root, "mac/src/main.c",
                 lambda s: s.replace("        ClampForCompactScreen(r, &scr);\n"
                                     "        return;",
                                     "        return;"))


def _m_atalkd_checker_blind_to_the_range(root):
    return _edit(root, "host/tools/check_atalkd_conf.py",
                 lambda s: s.replace("STARTUP_FIRST = 65280",
                                     "STARTUP_FIRST = 65535"))


MUTANTS = [
    ("tool count off by one",          _m_tool_count_off_by_one,          DOC_GUARDS),
    ("tool renamed in code only",      _m_tool_renamed_in_code,           DOC_GUARDS),
    ("version bumped in code only",    _m_version_bumped_only_in_code,    DOC_GUARDS),
    ("status marker in a design doc",  _m_status_marker_in_design_doc,    DOC_GUARDS),
    ("decision without a falsifier",   _m_decision_without_falsifier,     DOC_GUARDS),
    ("duplicate decision id",          _m_duplicate_decision_id,          DOC_GUARDS),
    ("superseded naming no successor", _m_superseded_without_successor,   DOC_GUARDS),
    ("hard rule without provenance",   _m_hard_rule_without_provenance,   DOC_GUARDS),
    ("serial buffer back to 64 bytes", _m_serial_buffer_back_to_the_default,        HARDWARE_GUARDS),
    ("serial buffer freed too early",  _m_serial_buffer_freed_before_the_driver_lets_go, HARDWARE_GUARDS),
    ("monitor window regrown",         _m_monitor_window_regrown,         HARDWARE_GUARDS),
    ("clamp dropped on restore path",  _m_clamp_dropped_from_the_restored_path,     HARDWARE_GUARDS),
    ("atalkd checker blinded",         _m_atalkd_checker_blind_to_the_range,        HARDWARE_GUARDS),
]


# --- the control ------------------------------------------------------------

def test_the_unmutated_copy_passes():
    """Without this, a 'killed' mutant could just be a pre-existing failure."""
    root = _scratch()
    try:
        for guards in (DOC_GUARDS, HARDWARE_GUARDS):
            ok, out = _guards_pass(root, guards)
            assert ok, (f"{guards} fails on an UNMUTATED copy, so no kill below "
                        "is meaningful:\n" + out[-800:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- the suite that never ran ------------------------------------------------

def test_every_test_file_is_registered_in_the_runner():
    """A suite absent from run_all.sh is not run by CI, and nothing says so.

    `test_host_ip_config.py` was written alongside the R1/R2 repair and never
    added to the list: 23 ratchets — including "no host-address literal in the
    runtime files", the exact defect they exist to hold — sat unexecuted while
    every run reported ALL SUITES PASSED. They passed; nobody was running them.
    That is the same shape as the defects this project keeps finding: it reports
    success and does nothing.

    smoke_e2e.py is the one deliberate exclusion — it drives the live stack and
    needs an emulator, so it is a manual pre-release gate.
    """
    runner = open(os.path.join(_ROOT, "tests", "run_all.sh")).read()
    listed = set(re.findall(r"test_[a-z_0-9]+\.py", runner))
    present = {f for f in os.listdir(os.path.join(_ROOT, "tests"))
               if f.startswith("test_") and f.endswith(".py")}
    missing = sorted(present - listed)
    assert not missing, ("test files CI never runs: " + ", ".join(missing)
                         + " — add them to tests/run_all.sh")


# --- the mutation run -------------------------------------------------------

def test_every_seeded_defect_is_caught():
    survived, unapplied = [], []
    for name, mutate, guards in MUTANTS:
        root = _scratch()
        try:
            if not mutate(root):
                unapplied.append(name)
                continue
            ok, _ = _guards_pass(root, guards)
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
