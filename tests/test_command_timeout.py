"""`timeout_for` budgets a whole command line, not just its first word.

The bridge is driven with COMPOUND lines — `Directory "…"; Rez …; SetFile …` is
how a build step is sent — and the timeout was chosen from the first token only.
`Directory` is not a long command, so a `Rez` that needs the 240 s budget was
given 15 s. Twice on 2026-07-28, during the 0.8d31 and 0.8d32 builds:

    [07:03:47] command timeout after 15s: 'Directory "MeinMac:MPW:AppleBridge:"; Rez AppleB'
    [07:03:51] drained 384 stale bytes (anti-desync)

and the caller was told `Mac daemon not connected` about a command that had
already completed on the guest. The guest was fine; the host stopped listening
while the reply was still arriving, and the leftover bytes were swept up by the
next command's anti-desync drain — which is why it looked like a bridge fault
and left no failed artifact behind.

That direction of the error is the expensive one: told a successful command
failed, the obvious next move is to run it again. Two `Rez -a` runs append the
resources twice.

`timeout_for` had NO tests before this file, which is the whole reason a
one-line classifier stayed wrong.

Run: python3 tests/test_command_timeout.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import host_server  # noqa: E402

host_server._logf = open(os.devnull, "a")

LONG = host_server.LONG_TIMEOUT
SHORT = host_server.DEFAULT_TIMEOUT
tf = host_server.timeout_for


# --- the regression itself --------------------------------------------------
def test_a_long_verb_after_a_short_one_still_gets_the_long_budget():
    # The exact line from the log, truncated there at 'Rez AppleB'.
    assert tf('Directory "MeinMac:MPW:AppleBridge:"; Rez AppleBridge_res.r '
              '-a -o :bin:AppleBridge.new') == LONG


def test_the_full_build_step_that_failed_twice():
    cmd = ('Directory "MeinMac:MPW:AppleBridge:"; '
           'Rez AppleBridge_res.r -a -o :bin:AppleBridge.new ≥ r1.err; '
           'Rez vers.r -a -o :bin:AppleBridge.new ≥ r2.err; '
           "SetFile -t APPL -c 'ABrg' :bin:AppleBridge.new")
    assert tf(cmd) == LONG


def test_the_link_line_behind_a_set():
    # `Set LIBS '…'; ILink …` is how the daemon's own link is sent.
    assert tf("Set LIBS 'MeinMac:Interfaces&Libraries:Libraries:'; "
              "ILink -model far -o :bin:AppleBridge.new :obj:main.c.o") == LONG


def test_a_compile_after_a_directory_change():
    assert tf('Directory "MeinMac:MPW:AppleBridge:"; '
              'SC -i :include: -model far :src:main.c -o :obj:main.c.o') == LONG


# --- what must NOT change ---------------------------------------------------
def test_a_bare_long_command_is_unchanged():
    for cmd in ("Link -model far -o :bin:App :obj:a.o",
                "Catenate MeinMac:big.txt", "Files -l MeinMac:", "Rez x.r"):
        assert tf(cmd) == LONG, cmd


def test_a_bare_short_command_is_unchanged():
    for cmd in ("Echo hello", "Directory", "Exists :obj:main.c.o",
                'Directory "MeinMac:MPW:"; Echo done'):
        assert tf(cmd) == SHORT, cmd


def test_the_verb_is_matched_case_insensitively():
    assert tf("directory x; rez y") == LONG


def test_an_empty_or_blank_line_does_not_raise():
    for cmd in ("", "   ", ";", ";;"):
        assert tf(cmd) == SHORT, repr(cmd)


# --- the ways MPW separates statements --------------------------------------
def test_a_newline_separated_script_is_scanned_too():
    # Over the wire a classic-Mac line ends in CR, not LF; both must split.
    assert tf('Directory "MeinMac:"\rRez x.r') == LONG
    assert tf('Directory "MeinMac:"\nRez x.r') == LONG


def test_conditional_separators_are_scanned():
    assert tf("Exists :obj:a.o && Link :obj:a.o") == LONG
    assert tf("Exists :obj:a.o || SC :src:a.c") == LONG


# --- and the false positive the naive fix would introduce -------------------
def test_a_long_name_used_as_an_ARGUMENT_does_not_inflate_the_budget():
    # Only each segment's first token is a verb. Scanning every word would make
    # `Echo "rez"` a four-minute command, and worse, would hide a genuinely
    # hung short command behind a long wait.
    assert tf('Echo "rez"') == SHORT
    assert tf('Echo make; Echo link') == SHORT
    assert tf('Search /rez/ MeinMac:file.txt') == SHORT


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
