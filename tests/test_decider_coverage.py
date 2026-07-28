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
    reached = {n for n in fns if re.search(rf"\b{re.escape(n)}\b", corpus)}
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
    ("build.py", "get_file_list"):
        "genuine debt — parses a ToolServer listing, exactly the shape that broke",
    ("guest_input.py", "_osascript"):
        "wraps the host's own osascript; the wrappers around it are tested",
    ("guest_input.py", "frontmost_app"):
        "reads host window state; meaningful only against a live desktop",
    ("guest_input.py", "cmd_geometry"): "argparse entry point, not a decision",
    ("guest_input.py", "cmd_move"): "argparse entry point, not a decision",
    ("guest_input.py", "cmd_click"): "argparse entry point, not a decision",
    ("guest_input.py", "cmd_menu"): "argparse entry point, not a decision",
    ("guest_input.py", "cmd_shot"): "argparse entry point, not a decision",
    ("macbinary.py", "_selftest"): "is itself a test",
}
HIGH_WATER = len(BASELINE)   # 9 on 2026-07-28. This number may go DOWN.


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


def test_the_corpus_excludes_this_file():
    # BASELINE names appear verbatim above. If this file joined the corpus,
    # every one of them would read as "mentioned in a test" and the ratchet
    # would pass by reading its own debt list.
    assert "get_file_list" not in _test_corpus(), (
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
