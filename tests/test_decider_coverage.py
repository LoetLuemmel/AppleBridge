"""A decision-making host function must be tested, or be listed as debt.

Four defects in two days, 2026-07-27/28, all in `host/`, all the same shape:

  * `timeout_for` classified a compound command line by its first token, so a
    240 s `Rez` got 15 s and a completed command was reported as a failure.
  * `probe_sockets` matched the daemon's peer with a pattern anchored on the
    configured host address, so on the slirp branch — which has none by design —
    it reported "no guest connected" about a live link.
  * `NoteErr` tagged three different verbs `"cmd fail"`, so the error counter
    named nothing.
  * the host discarded the response frame, so a failed command and a successful
    one left the same trace in the log.

Every one of them is a **decider**: give it data, it returns a verdict. No
network, no disk, nothing to stand up. A test costs three lines. And every one
of them had **no test at all** — not a weak test, none — which is why each
stayed wrong through every build that used it. They fail silently by
construction: a wrong verdict looks exactly like a right one from outside.

So this file asks one question of `host/*.py`: *is every decider reachable from
something a test names?* A new one that is neither tested nor explicitly
excused fails the suite.

What "tested" means here
------------------------
The proxy is: the function's name appears in some `tests/*.py`, OR it is
reachable through calls from a function whose name does. The second half
matters — `test_bridge_doctor.py` drives every probe through `collect()` and
names none of them, and counting those as untested would be false.

The corpus is searched as text, so the mention must not be part of a FILENAME:
`main.c` in an unrelated test used to satisfy a host function called `main`,
and with transitivity that covered eleven of `build.py`'s twelve functions —
including one whose oracle could never return True (repaired 2026-08-02, held
by `test_a_filename_is_not_a_test_mention`).

This cannot prove a test is any *good*. It can prove nobody so much as
referenced the function, which is the state all four defects were in.

Why `host/*.py` and not `host/tools/*.py`
-----------------------------------------
`host/*.py` is what the running bridge executes; a wrong verdict there is
silent and reaches the guest. `host/tools/*` are hand-run developer utilities
whose output a human reads on the spot, so a wrong answer argues with the
person looking at it. Worth bringing in later; excluded deliberately, not by
oversight.

Run: python3 tests/test_decider_coverage.py   (or via pytest)
"""

import ast
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
HOST = HERE.parent / "host"
SELF = pathlib.Path(__file__).resolve()

# Appears in this file and nowhere else in tests/ — see
# test_the_corpus_excludes_this_file for what it guards.
_SELF_MARKER = "decider~coverage~self~marker"

# Roots whose call means the function reaches outside the process, so a unit
# test would need something stood up first. `os.path` is pure and stays in.
_BLOCK_PREFIX = ("socket.", "subprocess.", "sys.", "shutil.", "urllib.",
                 "select.", "signal.", "threading.", "time.sleep")
_BLOCK_BARE = {"open", "input", "exit"}


def _dotted(node):
    """'os.path.join' for an Attribute/Name callee; '' for anything else."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def is_decider(fn):
    """True when `fn` returns a value and touches nothing outside the process.

    A call through one of the function's own PARAMETERS does not disqualify it:
    that is dependency injection, which is what makes `bridge_doctor`'s probes
    testable from canned command output in the first place. Rejecting them
    would exempt the very functions this ratchet exists for — `probe_sockets`
    is injected `run`, and it is one of the four.
    """
    if not any(isinstance(n, ast.Return) and n.value is not None
               for n in ast.walk(fn)):
        return False                      # a procedure, not a decision
    injected = ({a.arg for a in fn.args.args} |
                {a.arg for a in fn.args.kwonlyargs})
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        d = _dotted(n.func)
        if not d or d.split(".")[0] in injected:
            continue
        if d in _BLOCK_BARE:
            return False
        if d.startswith("os.") and not d.startswith("os.path."):
            return False
        if d.startswith(_BLOCK_PREFIX):
            return False
    return True


def _test_corpus():
    """Every test file EXCEPT this one.

    Excluding self is load-bearing: the baseline below spells out the names of
    the untested functions, so a corpus that included this file would find each
    of them "mentioned in a test" and the ratchet would pass by reading its own
    debt list. `test_the_corpus_excludes_this_file` holds that shut.
    """
    return "\n".join(p.read_text() for p in sorted(HERE.glob("test_*.py"))
                     if p.resolve() != SELF)


def _module_functions(path):
    tree = ast.parse(path.read_text())
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _covered_names(fns, corpus):
    """Names a test mentions, plus everything reachable from them by call."""
    calls = {}
    for name, node in fns.items():
        calls[name] = {_dotted(c.func).split(".")[-1] for c in ast.walk(node)
                       if isinstance(c, ast.Call) and _dotted(c.func)}
    reached = {n for n in fns if re.search(rf"\b{re.escape(n)}\b(?!\.[A-Za-z])", corpus)}
    stack = list(reached)
    while stack:
        for callee in calls.get(stack.pop(), ()):
            if callee in fns and callee not in reached:
                reached.add(callee)
                stack.append(callee)
    return reached


def untested_deciders():
    """[(module, function)] — deciders no test reaches, sorted."""
    corpus = _test_corpus()
    out = []
    for path in sorted(HOST.glob("*.py")):
        fns = _module_functions(path)
        covered = _covered_names(fns, corpus)
        for name, node in fns.items():
            if is_decider(node) and name not in covered:
                out.append((path.name, name))
    return sorted(out)


# --------------------------------------------------------------------------
# The debt. Entries may be REMOVED (write the test) but adding one is a
# deliberate act that needs a reason on the line — which is the whole ratchet:
# an untested decider is now a thing somebody chose, not a thing nobody noticed.
# --------------------------------------------------------------------------
BASELINE = {
    ("guest_input.py", "cmd_geometry"): "argparse entry point, not a decision",
    ("guest_input.py", "cmd_move"): "argparse entry point, not a decision",
    ("guest_input.py", "cmd_click"): "argparse entry point, not a decision",
    ("guest_input.py", "cmd_menu"): "argparse entry point, not a decision",
    ("guest_input.py", "cmd_shot"): "argparse entry point, not a decision",
    ("guest_input.py", "cmd_menushot"): "argparse entry point, not a decision",
    ("macbinary.py", "_selftest"): "is itself a test",
}
# A LITERAL, deliberately. Written first as `len(BASELINE)`, which makes
# `len(BASELINE) <= HIGH_WATER` true for every possible BASELINE — a check
# that examines nothing, in the file whose whole subject is checks that
# examine nothing. Caught 2026-07-28 while taking get_file_list off the list.
# Lower it when an entry leaves; raising it is the change that needs an argument.
HIGH_WATER = 7   # 6 -> 7 with cmd_menushot (an argparse entry point like
                 # its four siblings; hold_and_capture, the part that decides, IS tested).
                 # 9 -> 8 with get_file_list (PR #117); 8 -> 6 when the
                 # gesture-cost work put a recording stub in front of _run,
                 # which reaches _osascript and frontmost_app for real. Both
                 # entries claimed they needed "a live desktop"; they needed a
                 # stub. Written down because a debt entry's REASON can go
                 # stale as quietly as the debt itself.


# --- the ratchet ------------------------------------------------------------
def test_every_decider_is_tested_or_listed_as_debt():
    new = [f"{m}::{n}" for m, n in untested_deciders() if (m, n) not in BASELINE]
    assert not new, (
        "new decider function(s) with no test reaching them: "
        + ", ".join(new)
        + " — write a test, or add an entry to BASELINE with the reason why not")


def test_the_baseline_never_grows():
    assert len(BASELINE) <= HIGH_WATER, (
        f"BASELINE is {len(BASELINE)}, high-water mark is {HIGH_WATER}. "
        "This list is allowed to shrink, not grow.")


def test_the_baseline_has_no_stale_entries():
    # A name that IS covered now must leave the list, or the debt looks larger
    # than it is and the ratchet quietly loosens by that much.
    still = {(m, n) for m, n in untested_deciders()}
    stale = [f"{m}::{n}" for (m, n) in BASELINE if (m, n) not in still]
    assert not stale, ("BASELINE lists function(s) that are covered now: "
                       + ", ".join(stale) + " — delete those lines")


def test_every_baseline_entry_states_a_reason():
    for key, why in BASELINE.items():
        assert why and len(why) > 15, f"{key} needs a real reason, got {why!r}"


# --- guards on the classifier itself ---------------------------------------
# Without these, the cheapest way to a green run is to weaken is_decider until
# it recognises nothing — the same "a check that examines nothing" failure the
# ratchet exists to prevent.
def test_the_classifier_recognises_the_functions_that_actually_broke():
    sys.path.insert(0, str(HOST))
    import host_server, bridge_doctor  # noqa: E402

    for mod, name in ((host_server, "timeout_for"),
                      (host_server, "_framed_failure"),
                      (bridge_doctor, "probe_sockets")):
        path = HOST / (mod.__name__ + ".py")
        fn = _module_functions(path)[name]
        assert is_decider(fn), f"{name} is no longer classified as a decider"


def test_the_classifier_rejects_an_io_driver():
    src = ast.parse(
        "def serve(port):\n"
        "    s = socket.socket()\n"
        "    s.bind(('', port))\n"
        "    return s\n")
    assert not is_decider(src.body[0])


def test_a_procedure_that_returns_nothing_is_not_a_decider():
    src = ast.parse("def draw(x):\n    total = x + 1\n")
    assert not is_decider(src.body[0])


def test_an_injected_callable_does_not_disqualify():
    # probe_sockets in miniature: the reason bridge_doctor is testable at all.
    src = ast.parse(
        "def probe(run):\n"
        "    for line in run(['lsof']).splitlines():\n"
        "        return line\n"
        "    return None\n")
    assert is_decider(src.body[0])


def test_a_filename_is_not_a_test_mention():
    # The hole this ratchet had, found 2026-08-02 while auditing why a dead
    # function in build.py survived for months: the corpus is searched as TEXT,
    # so `main.c` — which appears in five unrelated tests — matched a host
    # function called `main`. Coverage is transitive, so ELEVEN of build.py's
    # twelve functions were covered by a filename, including `file_exists`,
    # whose oracle could never return True.
    #
    # The lookahead only rejects a following ".<letter>", so `build.main()`,
    # `probe.__name__` and every ordinary reference still count.
    fns = _module_functions_from_source(
        "def entry():\n"
        "    helper()\n"
        "def helper():\n"
        "    return 1\n")
    assert _covered_names(fns, "the guest compiled entry.c today") == set(), \
        "a filename still reads as a test mention"
    assert _covered_names(fns, "build.entry() is the entry point") == {"entry", "helper"}, \
        "a real reference stopped counting, or transitivity broke"


def _module_functions_from_source(src):
    tree = ast.parse(src)
    return {n.name: n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_the_corpus_excludes_this_file():
    # BASELINE names appear verbatim above. If this file joined the corpus,
    # every one of them would read as "mentioned in a test" and the ratchet
    # would pass by reading its own debt list.
    #
    # The canary is a marker that exists ONLY here. It was first a real
    # BASELINE name (`get_file_list`) — which broke the moment that function
    # was genuinely tested and the name legitimately appeared elsewhere, so the
    # canary reported "the ratchet is vacuous" about a repair. A canary that
    # fires on good news is one somebody eventually deletes.
    assert _SELF_MARKER not in _test_corpus(), (
        "this file is in its own test corpus — the ratchet is now vacuous")


def test_the_scan_actually_found_functions():
    # A wrong path, a renamed directory, an AST change: the scan returns an
    # empty set and everything above passes while examining nothing.
    total = sum(len(_module_functions(p)) for p in HOST.glob("*.py"))
    assert total > 50, f"only {total} host functions found — is the scan reaching host/?"


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
    if failed == 0:
        print(f"\n{len(BASELINE)} untested decider(s) on the books "
              f"(high-water {HIGH_WATER}):")
        for (m, n), why in sorted(BASELINE.items()):
            print(f"  {m}::{n} — {why}")
    sys.exit(1 if failed else 0)
