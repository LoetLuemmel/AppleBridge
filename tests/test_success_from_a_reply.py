"""`success: true` must come from the lower layer's answer, not from silence.

An outside comment on the loop draft (2026-08-05) asked one searchable question
of the whole tool surface:

    Where does a `success: true` arise from the ABSENCE of an exception rather
    than from an answer of the layer below?

Three instances of that class had turned up that day by accident — a discarded
`keyErrorNumber`, `STATUS:0` mistaken for a successful build, a missing file
read as an empty one. This is the fourth, and the first found by *looking*.

`mac_reboot` and `mac_shutdown` read `status` and threw it away. A dropped
connection is genuine evidence there — the guest is going down, so the socket
must die — but a REPLY with a non-zero status is the opposite: the daemon
answered, and it said no.

The second half of this file is the audit itself, kept as a test so the question
gets asked again on every run instead of once in a comment.

Run: python3 tests/test_success_from_a_reply.py   (or via pytest)
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.join(HERE, "..", "host"))
from mcp import tools  # noqa: E402

TOOLS_SRC = os.path.join(HERE, "..", "mcp", "tools.py")

# Verbs whose reply the caller cannot second-guess, so their handler MUST branch
# on the status it was given. Extend this list when a verb joins them; that is
# cheaper than rediscovering the class a fifth time.
STATUS_BEARING = ("mac_reboot", "mac_shutdown")


class FakeConn:
    def __init__(self, reply):
        self.reply = reply
        self.sent = []

    def is_connected(self):
        return True

    def send_command(self, command, timeout=30.0):
        self.sent.append(command)
        return self.reply


def call(fn, reply):
    tools.get_connection = lambda: FakeConn(reply)
    return fn()


# --- the defect -------------------------------------------------------------
def test_a_refused_reboot_is_not_a_success():
    r = call(tools.mac_reboot, (-1, "", "REBOOT refused"))
    assert r["success"] is False, r
    assert "refused" in (r.get("error") or "")


def test_a_refused_shutdown_is_not_a_success():
    r = call(tools.mac_shutdown, (-1, "", ""))
    assert r["success"] is False, r
    assert "SHUTDOWN" in (r.get("error") or "")


def test_an_accepted_reboot_is_still_a_success():
    """The other half. Reporting failure for a reboot that worked would be the
    same mistake pointing the other way."""
    assert call(tools.mac_reboot, (0, "Reboot", ""))["success"] is True


def test_a_dropped_connection_is_still_evidence_of_a_reboot():
    """A dead socket is exactly what a restart looks like from here — this is
    the one place where the absence of an answer IS the answer, and it must not
    be swept away with the rest."""
    class Dying(FakeConn):
        def send_command(self, command, timeout=30.0):
            raise OSError("connection reset")
    tools.get_connection = lambda: Dying(None)
    r = tools.mac_reboot()
    assert r["success"] is True and "dropped" in r["note"]


# --- the audit, kept as a test so it is asked again -------------------------
def _functions_returning_success_true(src):
    """-> {name: body} for every tool handler that can answer success: True."""
    out = {}
    for chunk in re.split(r"\ndef ", src)[1:]:
        name = chunk.split("(")[0]
        if '"success": True' in chunk:
            out[name] = chunk
    return out


def test_every_verb_that_gets_a_status_branches_on_it():
    """The audit question, asked mechanically.

    Deliberately narrow: it checks the handlers that send a verb AND are known
    to receive a meaningful status. A blanket rule over every tool would be
    noise — `mac_build` verifies by `Exists` rather than by status, and calling
    that a defect is how a guard earns its way to being switched off.
    """
    src = open(TOOLS_SRC, encoding="utf-8").read()
    bodies = _functions_returning_success_true(src)
    missing = []
    for name in STATUS_BEARING:
        body = bodies.get(name, "")
        if not re.search(r"if\s+status\s*!=\s*0", body):
            missing.append(name)
    assert not missing, f"answer a status and ignore it: {missing}"


def test_the_audit_can_fail():
    """A guard only ever seen passing is untested. This proves the matcher does
    catch a handler that ignores its status."""
    fake = {"mac_reboot": 'def mac_reboot():\n    return {"success": True}\n'}
    assert not re.search(r"if\s+status\s*!=\s*0", fake["mac_reboot"])


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
