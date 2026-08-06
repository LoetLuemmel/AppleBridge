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


# --- cycles: the shape `consecutive` is blind to -----------------------------
def test_a_b_a_b_is_reported_as_a_cycle():
    """The case the outside comment named: alternation resets the consecutive
    chain every time, so the guard saw nothing at all."""
    c = Clock()
    w = lg.RepeatWatch(clock=c)
    out = None
    for name in ("a", "b", "a", "b"):
        c.advance(1)
        out = w.note(name, {})
    assert out["cycle_length"] == 2, out
    assert "consecutive" not in out


def test_a_longer_cycle_is_found_too():
    c = Clock()
    w = lg.RepeatWatch(clock=c)
    out = None
    for name in ("a", "b", "c", "a", "b", "c"):
        c.advance(1)
        out = w.note(name, {})
    assert out["cycle_length"] == 3, out


def test_ordinary_work_is_not_a_cycle():
    """A guard that fires on ordinary sequences is one that gets switched off,
    and then it is worth less than none."""
    c = Clock()
    w = lg.RepeatWatch(clock=c)
    for name in ("a", "b", "c", "d"):
        c.advance(1)
        out = w.note(name, {})
    assert out is None


def test_a_cycle_is_reported_and_not_blocked():
    """Legitimate alternation exists — list, read, list, read is a real pattern.
    So `cycle_length` is a REPORT, and the shell must weigh it against whether
    anything progressed. Blocking here would be the guard deciding that for it."""
    c = Clock()
    w = lg.RepeatWatch(clock=c)
    for name in ("a", "b", "a", "b"):
        c.advance(1)
        out = w.note(name, {})
    assert out["cycle_length"] == 2      # reported...
    c.advance(1)
    assert w.note("a", {}) is not None   # ...and the call still went through


# --- normalisation, and its deliberate limits -------------------------------
def test_an_omitted_optional_equals_a_null_one():
    """`{"path": "x"}` and `{"path": "x", "options": None}` are the same call,
    and a model re-sending one as the other is repeating itself."""
    w = lg.RepeatWatch(clock=Clock())
    w.note("t", {"path": "x"})
    assert w.note("t", {"path": "x", "options": None}) is not None


def test_an_empty_string_folds_like_an_absent_key():
    w = lg.RepeatWatch(clock=Clock())
    w.note("t", {"path": "x"})
    assert w.note("t", {"path": "x", "options": ""}) is not None


def test_whitespace_and_case_are_NOT_normalised():
    """Deliberate, and the reason is the guest: a classic-Mac filename may
    legitimately differ in exactly those ways. Folding two distinct calls into
    one costs a false alarm on real work — the way a signal earns being
    ignored."""
    w = lg.RepeatWatch(clock=Clock())
    w.note("t", {"path": "MeinMac:X"})
    assert w.note("t", {"path": "meinmac:x"}) is None
    assert w.note("t", {"path": "MeinMac:X "}) is None


# --- AttemptLog: what has been tried, not what happened last ---------------
def test_ten_steps_over_two_actions_are_two_lines():
    """The whole point against a longer history window: the register grows per
    distinct ACTION, not per step, so it stays affordable on a 2 GB node."""
    a = lg.AttemptLog()
    for _ in range(5):
        a.record("t1", {"p": "x"}, "failed")
        a.record("t2", {"p": "y"}, "ok")
    assert len(a) == 2
    assert len(a.lines()) == 2


def test_the_try_count_survives_where_a_window_would_have_forgotten():
    a = lg.AttemptLog()
    for _ in range(4):
        a.record("t", {"p": "x"}, "failed")
    assert a.tried("t", {"p": "x"})["tries"] == 4


def test_an_untried_action_is_not_claimed_as_tried():
    a = lg.AttemptLog()
    a.record("t", {"p": "x"}, "ok")
    assert a.tried("t", {"p": "y"}) is None


def test_outcomes_are_stored_verbatim():
    """A register that normalises outcomes into its own vocabulary hands the
    model a translation of its own history — and the translation is where the
    detail that mattered gets lost. "line 4" is that detail."""
    a = lg.AttemptLog()
    a.record("t", {}, "failed: line 4 #Error: ';' expected")
    assert "line 4" in a.lines()[0]


def test_a_repeated_outcome_is_not_repeated_in_the_line():
    """"failed, failed, failed" says no more than "failed" and costs three
    times the context."""
    a = lg.AttemptLog()
    for _ in range(3):
        a.record("t", {}, "failed")
    assert a.lines()[0].count("failed") == 1


def test_a_changed_outcome_is_kept_alongside_the_old_one():
    """That an action failed and then worked is the interesting shape; keeping
    only the last would erase it."""
    a = lg.AttemptLog()
    a.record("t", {}, "failed")
    a.record("t", {}, "ok")
    line = a.lines()[0]
    assert "failed" in line and "ok" in line


def test_the_newest_action_comes_first():
    a = lg.AttemptLog()
    a.record("old", {}, "ok")
    a.record("new", {}, "ok")
    assert a.lines()[0].startswith("new")


def test_an_omitted_row_is_reported_not_dropped():
    """A register that quietly drops rows tells the model it has tried less
    than it has — precisely the belief that produces another attempt."""
    a = lg.AttemptLog()
    for i in range(4):
        a.record(f"t{i}", {}, "ok")
    out = a.lines(limit=2)
    assert len(out) == 3 and "2 earlier action(s) not shown" in out[-1]


def test_a_long_argument_is_cut_at_the_end_with_a_marker():
    """A value cut in the middle reads as a DIFFERENT value; cut at the end
    with a marker it reads as a shortened one."""
    a = lg.AttemptLog()
    a.record("t", {"path": "MeinMac:" + "x" * 80}, "ok")
    line = a.lines()[0]
    assert "…" in line and line.index("MeinMac:") < line.index("…")


def test_the_register_shares_the_watch_s_notion_of_identity():
    """Two components disagreeing about what "the same call" means is how a
    loop ends up guarded against one thing and reported on another."""
    a = lg.AttemptLog()
    a.record("t", {"p": "x"}, "ok")
    assert a.tried("t", {"p": "x", "opt": None}) is not None


def test_brief_args_sorts_so_one_call_reads_the_same_every_time():
    """`_brief_args` renders what the model sees. Insertion order would make the
    same action look like two different lines across steps — the identity is
    already order-free, and the rendering has to agree with it."""
    assert lg._brief_args({"b": 2, "a": 1}) == "a=1, b=2"


def test_brief_args_leaves_a_short_value_alone():
    """No marker where nothing was cut: a "…" on an untouched value would make
    a reader look for a rest that does not exist."""
    assert lg._brief_args({"p": "x.c"}) == "p=x.c"


# --- TurnScope: the clock is the whole point --------------------------------
def test_a_name_that_could_only_come_from_this_turn_is_refused():
    """The measured case. A model called mac_list_files and mac_read_file in ONE
    turn and put the literal `<filename>` in the second — the listing did not
    exist yet."""
    t = lg.TurnScope()
    t.open_turn({})
    r = t.check("mac_read_file", {"path": "<filename>"})
    assert r["verdict"] == "refuse"


def test_a_guessed_but_later_valid_name_is_refused_too():
    """THE gap this closes. A check run at execution time would have had the
    listing by then and passed a guess that happens to be in it — resolved
    correctly, and still not taken from the result."""
    t = lg.TurnScope()
    t.open_turn({})                      # nothing known when the turn opened
    assert t.check("mac_read_file", {"path": "AppleBridge"})["verdict"] == "refuse"


def test_a_name_from_the_previous_turn_passes():
    """Two independent calls in one turn are fine; the guard must not tax them."""
    t = lg.TurnScope()
    t.open_turn({"dir": ["AppleBridge", "loadtest.txt"]})
    assert t.check("mac_read_file", {"path": "AppleBridge"})["verdict"] == "allow"


def test_a_refusal_hands_back_what_was_known():
    """Measured: a refusal WITH the list let the model correct itself; a refusal
    alone had it invent again."""
    t = lg.TurnScope()
    t.open_turn({"dir": ["AppleBridge", "loadtest.txt"]})
    r = t.check("mac_read_file", {"path": "Prefs.txt"})
    assert r["verdict"] == "refuse"
    assert "AppleBridge" in r["candidates"]


def test_an_empty_scope_refuses_with_its_own_reason():
    """"nothing was known" and "known, but not this" send a reader to different
    places: list first, versus you named the wrong one."""
    t = lg.TurnScope()
    t.open_turn({})
    assert "list first" in t.check("mac_read_file", {"path": "x"})["why"]


# --- reference versus new value ---------------------------------------------
def test_a_new_value_is_not_required_to_exist():
    """Writing to a path that does not exist yet is the normal case; refusing it
    would make the guard forbid creation."""
    t = lg.TurnScope()
    t.open_turn({})
    assert t.check("mac_write_file", {"path": "MeinMac:neu.txt"})["verdict"] == "allow"


def test_the_same_parameter_name_is_read_per_tool():
    """`path` is a reference for mac_read_file and a new value for
    mac_write_file. A rule guessing from the name would either refuse every
    write or wave through every read."""
    t = lg.TurnScope()
    t.open_turn({})
    assert t.check("mac_read_file", {"path": "x"})["verdict"] == "refuse"
    assert t.check("mac_write_file", {"path": "x"})["verdict"] == "allow"


def test_a_tool_with_no_rule_says_unchecked_not_allowed():
    """Waving it through silently is the hole the outside comment named: an
    unproven argument passes marked, with nobody named to act on the mark."""
    r = lg.TurnScope().check("mac_screenshot", {})
    assert r["verdict"] == "unchecked" and "nothing was verified" in r["why"]


# --- the snapshot ------------------------------------------------------------
def test_opening_a_turn_replaces_rather_than_accumulates():
    """A scope that kept every structure ever delivered slowly becomes "anything
    ever seen", and then it answers yes to everything."""
    t = lg.TurnScope()
    t.open_turn({"a": ["one"]})
    t.open_turn({"b": ["two"]})
    assert t.check("mac_read_file", {"path": "one"})["verdict"] == "refuse"
    assert t.check("mac_read_file", {"path": "two"})["verdict"] == "allow"


def test_the_candidate_list_reports_what_it_left_out():
    t = lg.TurnScope()
    t.open_turn({"dir": [f"f{i}" for i in range(20)]})
    c = t.candidates(limit=5)
    assert len(c) == 6 and "15 more" in c[-1]


def test_sources_for_names_where_it_was_seen():
    """Which listing a name came from is what lets a caller re-read the right
    one instead of guessing."""
    t = lg.TurnScope()
    t.open_turn({"dirA": ["x"], "dirB": ["x", "y"]})
    assert t.sources_for("x") == ["dirA", "dirB"]


def test_a_missing_optional_reference_is_not_invented():
    """An argument the caller did not pass is not a name that failed to
    resolve — checking it would refuse every call that omits an optional."""
    t = lg.TurnScope()
    t.open_turn({})
    assert t.check("mac_compile", {"source_path": None})["verdict"] == "allow"



# --- TerminationWatch: three endings, and the one that was silent ------------
def _ok(**kw):
    d = {"success": True, "verified": True}
    d.update(kw)
    return d


def test_a_repair_that_was_never_compiled_is_its_own_ending():
    """Run N, 2026-08-05: the model read the compiler, repaired the source
    correctly, and stopped. On disk the artefact was the OLD object, so the run
    looked successful; in the transcript it looked like a crash."""
    t = lg.TerminationWatch()
    t.note("mac_compile", _ok(success=False))
    t.note("mac_write_file", {"success": True})
    assert t.outcome() == t.NOT_RECOMPILED
    assert "never compiled" in t.message()


def test_a_repair_that_failed_again_is_a_loop_that_worked():
    """The distinction a boolean destroys: the loop closed, the repair did
    not. Named by the parallel session against a draft that had closed=True."""
    t = lg.TerminationWatch()
    t.note("mac_write_file", {"success": True})
    t.note("mac_compile", _ok(success=False))
    assert t.outcome() == t.RECOMPILED_FAILED
    assert "the loop closed" in t.message()


def test_a_repair_that_compiled_is_the_closed_ending():
    t = lg.TerminationWatch()
    t.note("mac_write_file", {"success": True})
    t.note("mac_compile", _ok())
    assert t.outcome() == t.RECOMPILED_OK


def test_an_unverified_compile_is_not_a_missing_one():
    """`verified: false` (the -o-inside-options branch) says nobody checked.
    Reporting that as not_recompiled would claim a compile never ran, and as
    recompiled_failed would claim it failed. Both are inventions."""
    t = lg.TerminationWatch()
    t.note("mac_write_file", {"success": True})
    t.note("mac_compile", {"success": None, "verified": False})
    assert t.outcome() == t.COMPILED_UNVERIFIED
    assert "unknown, not successful" in t.message()


def test_a_run_that_wrote_nothing_makes_no_claim():
    """A read-only run has no termination question, and inventing one would put
    every such run into a denominator it does not belong in."""
    t = lg.TerminationWatch()
    t.note("mac_list_files", {"success": True})
    assert t.outcome() == t.NOTHING_WRITTEN
    assert t.message() == ""


def test_only_the_LAST_write_decides():
    """write, compile, write again: the loop is open on the second write, and a
    counter that only asked "was there ever a compile" would call it closed."""
    t = lg.TerminationWatch()
    t.note("mac_write_file", {"success": True})
    t.note("mac_compile", _ok())
    t.note("mac_write_file", {"success": True})
    assert t.outcome() == t.NOT_RECOMPILED
    assert t.compiles == 1 and t.writes == 2


def test_a_refused_call_must_never_close_a_loop():
    """A refused call reached no tool, so it can neither change nor judge. The
    caller is supposed to filter — and the result carries the hull's own marker,
    so this reads it rather than trusting a docstring. Without it a conductor
    that forwards refusals closes loops on calls the guard stopped, and the
    symptom is a termination rate three weeks later, not an error."""
    t = lg.TerminationWatch()
    t.note("mac_write_file", {"success": True})
    t.note("mac_compile", {"success": False, "refused_by_hull": True,
                           "reason": "HULL REFUSED: no listing is known yet"})
    assert t.outcome() == t.NOT_RECOMPILED, "a refusal closed the loop"
    assert t.compiles == 0


def test_the_report_carries_the_sentence_and_not_only_the_verdict():
    """Same reason StepBudget returns a message: a verdict without its sentence
    is a field somebody has to remember to interpret."""
    t = lg.TerminationWatch()
    t.note("mac_write_file", {"success": True})
    r = t.report()
    assert set(r) == {"outcome", "writes", "compiles", "message"}
    assert r["message"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        # Exception, not AssertionError. A test that raises anything else --
        # KeyError on a field the code does not produce yet is the obvious one --
        # used to kill the whole run at that point, and a reader grepping for
        # "FAIL" then saw none. Measured while running THIS file against the
        # pre-cycle code: the negative control reported zero failures because
        # the runner had died on the first one.
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
