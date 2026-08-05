"""Tests for host/loop_guard.py — repetition made visible, steps bounded.

Both guards come from the first run of a local model through this bridge
(2026-08-05): it called one tool with identical arguments three times before
answering, and it stopped after four steps because it happened to be finished,
not because anything bounded it.

The design rule under test is the project's: **make it visible, do not guess
what was meant.** So there is a test that the watch does NOT block, and a test
that an exhausted budget produces a sentence rather than only a `False` — a
loop stopped silently is indistinguishable from a model that was done.

Run: python3 tests/test_loop_guard.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import loop_guard as lg  # noqa: E402


class Clock:
    """A hand-wound clock: the window is a real behaviour and needs testing
    without sleeping through it."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


# --- RepeatWatch ------------------------------------------------------------
def test_a_first_call_is_not_a_repeat():
    w = lg.RepeatWatch(clock=Clock())
    assert w.note("mac_compile", {"source_path": "x.c"}) is None


def test_the_same_call_again_is_reported():
    """The measured case: three identical mac_compile calls before an answer."""
    c = Clock()
    w = lg.RepeatWatch(clock=c)
    w.note("mac_compile", {"source_path": "x.c"})
    c.advance(1.0)
    r = w.note("mac_compile", {"source_path": "x.c"})
    assert r["identical_calls"] == 2
    assert r["consecutive"] == 2
    assert r["seconds_since_previous"] == 1.0


def test_argument_order_does_not_make_two_calls_different():
    """`repr(dict)` would have: same content, different insertion order, and the
    repeats worth reporting would be exactly the ones that got away."""
    w = lg.RepeatWatch(clock=Clock())
    w.note("t", {"a": 1, "b": 2})
    assert w.note("t", {"b": 2, "a": 1}) is not None


def test_different_arguments_are_a_different_call():
    w = lg.RepeatWatch(clock=Clock())
    w.note("mac_compile", {"source_path": "x.c"})
    assert w.note("mac_compile", {"source_path": "y.c"}) is None


def test_back_to_back_is_distinguished_from_merely_seen_again():
    """Consecutive is the shape that means "stuck"; the same call twice with
    other work in between usually means something else."""
    c = Clock()
    w = lg.RepeatWatch(clock=c)
    w.note("a", {})
    c.advance(1)
    w.note("b", {})
    c.advance(1)
    r = w.note("a", {})
    assert r["identical_calls"] == 2
    assert "consecutive" not in r


def test_a_repeat_after_the_window_is_a_fresh_call():
    """A repeat three hours later is a second job, not a stuck loop. Counting it
    would turn a useful signal into noise, and noisy signals get ignored."""
    c = Clock()
    w = lg.RepeatWatch(window_s=120.0, clock=c)
    w.note("t", {})
    c.advance(121.0)
    assert w.note("t", {}) is None


def test_the_watch_never_blocks():
    """It reports. Refusing would be a guard deciding the caller did not mean
    what it asked for twice — which it cannot know."""
    w = lg.RepeatWatch(clock=Clock())
    for _ in range(5):
        w.note("t", {})            # no exception, no falsy "denied" result
    assert w.note("t", {})["identical_calls"] == 6


# --- StepBudget -------------------------------------------------------------
def test_the_budget_allows_exactly_what_it_says():
    b = lg.StepBudget(3)
    assert [b.spend() for _ in range(4)] == [True, True, True, False]


def test_an_exhausted_budget_says_why_it_stopped():
    """The whole point. A loop stopped silently looks like a model that was
    finished — which is what actually happened in the measured run."""
    b = lg.StepBudget(1)
    b.spend()
    assert b.exhausted
    assert "step budget exhausted" in b.message()


def test_a_budget_with_room_left_makes_no_claim():
    b = lg.StepBudget(2)
    b.spend()
    assert b.message() == ""
    assert b.remaining() == 1


def test_a_budget_of_zero_is_refused_rather_than_accepted():
    """A loop that can never take a step is a configuration error, and it would
    surface as "the model did nothing"."""
    try:
        lg.StepBudget(0)
    except ValueError:
        return
    raise AssertionError("StepBudget(0) was accepted")


# --- the wiring into call_tool ---------------------------------------------
def test_the_dispatcher_reports_a_repeat_in_the_result():
    """The signal travels INSIDE the tool result: a side channel is one somebody
    has to remember to look at."""
    root = os.path.join(os.path.dirname(__file__), "..")
    sys.path.insert(0, os.path.abspath(root))
    from mcp import tools

    calls = []
    tools.TOOL_HANDLERS["__probe__"] = lambda **kw: calls.append(kw) or {"ok": True}
    try:
        tools._REPEATS = lg.RepeatWatch(clock=Clock())
        first = tools.call_tool("__probe__", {"a": 1})
        second = tools.call_tool("__probe__", {"a": 1})
    finally:
        del tools.TOOL_HANDLERS["__probe__"]
    assert "repeated_call" not in first
    assert second["repeated_call"]["identical_calls"] == 2
    assert len(calls) == 2, "the repeat must still have been EXECUTED"


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
