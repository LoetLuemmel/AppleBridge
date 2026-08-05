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
    body = EVENTS_C[m.end():m.end() + 900]
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


def test_a_refusal_stops_the_loop_instead_of_being_retried():
    """THE fix. `evtNotEnb` is a statement about configuration, not about
    congestion — 48 attempts at it convert a refusal into a 1.6 s delay and
    change nothing else. Measured after: 1.694 s -> 0.121 s per keystroke."""
    retry = EVENTS_C[EVENTS_C.index("static OSErr PPostEventRetry"):]
    retry = retry[:retry.index("\n}")]
    assert "if (e == evtNotEnb) break;" in retry, \
        "a disabled event type is being retried again"


def test_the_event_mask_is_restored():
    """It is a GLOBAL low-memory value. Enabling keyUp and leaving it enabled
    would change the behaviour of every application on the machine — a fix that
    quietly alters somebody else's world is not a fix."""
    retry = EVENTS_C[EVENTS_C.index("static OSErr PPostEventRetry"):]
    retry = retry[:retry.index("\n}")]
    sets = [l for l in retry.splitlines() if "SetEventMask" in l]
    assert len(sets) == 2, f"expected enable + restore, found {len(sets)}: {sets}"
    assert "savedMask)" in sets[1], "the second call must put the ORIGINAL mask back"


def test_a_full_chunk_now_fits_the_host_timeout():
    """The arithmetic that used to guarantee a timeout. It still describes the
    WORST case the budget allows, but that case is now unreachable for the error
    that actually occurred, so the measured cost is the deliberate delays alone:
    9 characters went from 15.003 s (timeout) to 0.825 s."""
    deliberate = per_keystroke_ticks() - retry_budget()[0] * retry_budget()[1]
    assert chunk_size() * deliberate * TICK < host_server.DEFAULT_TIMEOUT


def test_how_many_characters_actually_fit():
    """The measured 9 is not a magic number: it is floor(timeout / cost) + 1.
    Written down so the next person does not look for something special about
    nine, which is what cost this measurement its first hour."""
    cost = per_keystroke_ticks() * TICK
    fits = int(host_server.DEFAULT_TIMEOUT // cost)
    assert fits == 8, f"{fits} characters fit, so the {fits + 1}th is the one that times out"


def test_a_failed_injection_is_no_longer_reported_as_success():
    """`InjectType`'s return value was DISCARDED and the verb answered "Typed"
    unconditionally — so every keystroke that never reached the queue was
    reported as success, for as long as the feature existed. Third instance of
    one shape in two days, after QUIT's "Quit OK" and append()'s ignored False."""
    src = open(os.path.join(_ROOT, "mac", "src", "main.c"), encoding="utf-8",
               errors="replace").read()
    verb = src[src.index('if (strncmp(request, PROTO_TYPE'):][:1800]
    assert "OSErr tErr = InjectType(" in verb, "the result is being discarded again"
    assert "STATUS:-1" in verb, "a failed injection must not answer STATUS:0"
    assert "NoteErrCode" in verb, "and it must move the error counter"


def test_the_instrument_reports_what_it_measured_not_what_it_inferred():
    """KEYSTAT exists because three explanations from arithmetic alone were all
    wrong. It states attempts, the OSErr and SysEvtMask — the reading that
    settled it in one call: keyupTries=48, keyupErr=1, sysEvtMask=-17 (0xFFEF,
    every bit but keyUpMask)."""
    src = open(os.path.join(_ROOT, "mac", "src", "main.c"), encoding="utf-8",
               errors="replace").read()
    verb = src[src.index('PROTO_KEYSTAT'):][:1500]
    for field in ("keydownTries", "keyupTries", "keyupErr", "sysEvtMask",
                  "maskPatched"):
        assert field in verb, f"KEYSTAT no longer reports {field}"


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
