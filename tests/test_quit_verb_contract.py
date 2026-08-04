"""The QUIT verb must report what happened, not what it attempted.

`AESend(..., kAENoReply, ...)` returns noErr as soon as the event is DELIVERED.
It says nothing about the target. An application with no Apple Event handler at
all — `AppleBridgeConfig` is exactly that — leaves the quit sitting in its queue
for ever, and the verb answered `Quit OK` while the app kept running.

That is this project's named failure class arriving in the daemon: a report on
evidence that is not the thing it claims. The repair is the same one as
everywhere else — verify by the artefact (the process is gone), not by the
status of the attempt.

Read from the C source rather than from a running guest, so it holds in CI where
there is no emulator. What it can check is exactly what the source states, which
for a length-framed protocol is more than it sounds: a wrong STDERR length is
not a cosmetic error, it desynchronises the reader for the rest of the frame.

Run: python3 tests/test_quit_verb_contract.py   (or via pytest)
"""

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = open(os.path.join(_ROOT, "mac", "src", "main.c"), encoding="utf-8",
           errors="replace").read()
QUIT = SRC[SRC.index('if (strncmp(request, "QUIT:", 5) == 0)'):][:4000]


def test_success_is_reported_only_after_a_check():
    """The defect in one line: `Quit OK` must not be reachable straight from
    the send returning noErr."""
    send = QUIT.index("qerr = QuitAppBySignature(sig)")
    # The REPLY, not the words. The first version searched for "Quit OK" and
    # found it in the comment that explains the defect — a test satisfied by
    # prose about the bug is the bug's own failure class, and it is the second
    # time today: the DEADLINE test found its subject in a docstring saying why
    # there wasn't one.
    ok = QUIT.index(r'"STATUS:0\rSTDOUT:7\rQuit OK')
    between = QUIT[send:ok]
    assert "IsAppRunning" in between, \
        "nothing checks whether the app actually quit before reporting success"


def test_the_wait_yields():
    """A target starved of CPU can never process the quit, so a busy loop would
    report "still running" every time and disprove itself. The daemon is
    cooperative — the wait has to hand the machine over."""
    loop = QUIT[QUIT.index("IsAppRunning") - 400:QUIT.index("IsAppRunning") + 200]
    assert "WaitNextEvent" in loop, "the verification loop does not yield"


def test_the_wait_is_bounded():
    """An app that puts up "Save changes?" never goes away. Without a guard the
    daemon would sit in that loop, taking the bridge with it."""
    m = re.search(r"for \(vguard = 0; vguard < (\d+) && !vgone", QUIT)
    assert m, "the verification loop has no guard"
    assert 0 < int(m.group(1)) <= 300, f"guard of {m.group(1)} iterations"


def test_every_declared_length_matches_its_payload():
    """The wire is length-framed: the reader takes STDOUT/STDERR by the DECLARED
    length, not by a terminator. A number that disagrees with its string does
    not truncate one field, it desynchronises everything after it — and nothing
    in the daemon would report that."""
    checked = 0
    for decl, payload in re.findall(r'STDERR:(\d+)\\r([^"\\]*)\\r\\r"', QUIT):
        assert int(decl) == len(payload), \
            f"declared STDERR:{decl} but the message is {len(payload)}: {payload!r}"
        checked += 1
    for m in re.finditer(r'STDOUT:(\d+)\\r', QUIT):
        rest = QUIT[m.end():]
        end = rest.find(r"\r")
        payload = rest[:end] if end >= 0 else rest
        # `STDOUT:0\rSTDERR:...` has an EMPTY payload; reading the next field
        # as the payload is how the first version of this test convinced itself
        # that a correct frame declared 0 and carried 9 characters.
        if payload.startswith("STDERR:"):
            payload = ""
        assert int(m.group(1)) == len(payload), \
            f"declared STDOUT:{m.group(1)} but the payload is {len(payload)}: {payload!r}"
        checked += 1
    assert checked >= 3, f"only {checked} framed fields found — did the verb move?"


def test_the_failure_says_which_failure_it_was():
    """"No such app" and "the app ignored the quit" send the reader to opposite
    places. They shared a message once; they must not share one again."""
    assert "Quit failed" in QUIT
    assert "STILL RUNNING" in QUIT


def test_the_stuck_case_is_counted_as_an_error():
    """err= is how the operator sees that something went wrong at all. A verb
    that reports a failure in its reply but not in the counter is invisible to
    anybody watching the daemon rather than the client."""
    stuck = QUIT[QUIT.index("STILL RUNNING"):][:400]
    assert "NoteErr" in stuck


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
