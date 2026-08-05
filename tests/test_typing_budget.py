"""Three constants in three languages that must relate, and nothing checked them.

Measured live 2026-08-05 against the running bridge — a clean straight line:

     1 char   1.738 s        6 chars  10.105 s
     2 chars  3.394 s        8 chars  13.494 s
     3 chars  5.094 s        9 chars  15.003 s  <- the host gives up
     4 chars  6.757 s

**~1.69 s per keystroke**, and the ninth character is simply the first that
pushes a burst past the host's 15 s timeout. There is nothing special about
nine; the operator's "nine characters take fifteen seconds" was an exact
observation with the wrong subject.

Where the 1.69 s goes, from `mac/src/events.c`:

    InjectKey  -> ShortDelay(2) + ShortDelay(2)      = 4 ticks   deliberate
    InjectType -> ShortDelay(1) per character        = 1 tick    deliberate
    PPostEventRetry -> 48 attempts x ShortDelay(2)   = 96 ticks  worst case
                                                     ---------
                                                       101 ticks = 1.683 s

Measured 1.69 s. The arithmetic and the stopwatch agree to within half a
percent, so the per-character cost IS the retry loop very nearly exhausting its
budget — the guest's OS event queue has no room, and every keystroke waits for
one. Controlled: after 30 s of complete quiet a single character still costs
1.715 s, so the jam is the standing state and not an artefact of the burst that
found it. `KEY:` costs the same 1.735 s, so it is per-KEYSTROKE, not per-verb.

Two things this pins, neither of which is the jam itself (which is not yet
explained and needs a guest with a fresh event queue to test):

1. `mac_type` chunks at 12 characters. At the worst-case per-character cost a
   full chunk cannot fit inside the host timeout — 12 x 1.68 s = 20 s against
   15 s. The chunk size was chosen for losslessness and the timeout for
   round-trip sanity; nobody related them, so the default path is guaranteed to
   time out whenever the queue is jammed.
2. The daemon answers **STATUS:0** for a keystroke that needed 47 attempts, and
   `err=` does not move. Verified live: err stayed 1 (an unrelated older entry)
   across every one of these. A cost of this size that reports success is the
   failure class this project keeps finding.

Run: python3 tests/test_typing_budget.py   (or via pytest)
"""

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host"))
import host_server  # noqa: E402

EVENTS_C = open(os.path.join(_ROOT, "mac", "src", "events.c"),
                encoding="utf-8", errors="replace").read()
TOOLS_PY = open(os.path.join(_ROOT, "mcp", "tools.py"),
                encoding="utf-8", errors="replace").read()

TICK = 1.0 / 60.0


def retry_budget():
    """(attempts, ticks_per_attempt) from PPostEventRetry."""
    m = re.search(r"for \(tries = 0; tries < (\d+); tries\+\+\)", EVENTS_C)
    body = EVENTS_C[m.end():m.end() + 400]
    d = re.search(r"ShortDelay\((\d+)L\)", body)
    return int(m.group(1)), int(d.group(1))


def per_keystroke_ticks():
    """Worst-case ticks for ONE character, as the source actually spends them."""
    attempts, per_try = retry_budget()
    inject = EVENTS_C[EVENTS_C.index("OSErr InjectKeyMod"):]
    inject = inject[:inject.index("\n}")]
    deliberate = sum(int(n) for n in re.findall(r"ShortDelay\((\d+)L\)", inject))
    loop = EVENTS_C[EVENTS_C.index("OSErr InjectType"):]
    loop = loop[:loop.index("\n}")]
    deliberate += sum(int(n) for n in re.findall(r"ShortDelay\((\d+)L\)", loop))
    return attempts * per_try + deliberate


def chunk_size():
    return int(re.search(r"^    CHUNK = (\d+)", TOOLS_PY, re.M).group(1))


def test_the_retry_budget_is_still_the_shape_the_measurement_assumed():
    attempts, per_try = retry_budget()
    assert (attempts, per_try) == (48, 2), \
        f"the budget changed to {attempts}x{per_try} ticks — re-measure before trusting the note"


def test_the_arithmetic_matches_the_stopwatch():
    """101 ticks against a measured 1.69 s. If a future edit breaks this
    agreement the note above stops being evidence and becomes folklore."""
    predicted = per_keystroke_ticks() * TICK
    assert abs(predicted - 1.69) < 0.05, \
        f"source now predicts {predicted:.3f}s per keystroke, measured was 1.69s"


def test_a_full_chunk_cannot_fit_the_host_timeout():
    """THE defect, stated as arithmetic rather than as a war story. Both numbers
    are defensible alone; together they guarantee a timeout on the default path
    whenever the guest's event queue is jammed — which is its standing state on
    this guest, measured after 30 s of quiet."""
    worst = chunk_size() * per_keystroke_ticks() * TICK
    assert worst > host_server.DEFAULT_TIMEOUT, (
        "this test encodes a KNOWN mismatch; if it now fits, the fix landed — "
        "delete the test and say which constant moved")


def test_how_many_characters_actually_fit():
    """The measured 9 is not a magic number: it is floor(timeout / cost) + 1.
    Written down so the next person does not look for something special about
    nine, which is what cost this measurement its first hour."""
    cost = per_keystroke_ticks() * TICK
    fits = int(host_server.DEFAULT_TIMEOUT // cost)
    assert fits == 8, f"{fits} characters fit, so the {fits + 1}th is the one that times out"


def test_an_exhausted_retry_is_not_counted_anywhere():
    """The daemon answers STATUS:0 for a keystroke that needed 47 attempts, and
    err= does not move — verified live across every measurement above. Pinned as
    a KNOWN GAP so that closing it is a deliberate act: if a counter appears,
    this test fails and its docstring is the changelog entry."""
    retry = EVENTS_C[EVENTS_C.index("static OSErr PPostEventRetry"):]
    retry = retry[:retry.index("\n}")]
    assert "NoteErr" not in retry and "gErrCount" not in retry, \
        "a retry counter appeared — good; update this test and the operating note"


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
