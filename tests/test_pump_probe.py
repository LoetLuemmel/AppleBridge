"""The third exit: neither sent-and-worked nor sent-and-refused, but not read.

An Apple Event runs when the target READS it. Measured 2026-08-05: one lay in a
busy application's queue for 905 seconds and fired four seconds after the modal
in front of it was cleared — against whatever was open by then.

Two outcomes describe a world in which sending and executing are the same thing.
This one is not. Without a third outcome the most likely failure of a
model-driven loop is a FALSE ALARM of its own guard: the tool reports success,
the next step perceives the old state, the model concludes failure and retries,
and the repetition guard aborts a run in which nothing was ever wrong.

Scope is deliberately narrow — only a send that does NOT wait for a reply. One
that waits finds out by itself.

Run: python3 tests/test_pump_probe.py   (or via pytest)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "host"))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
import pump_probe as pp  # noqa: E402
from mcp import tools  # noqa: E402


class FakeConn:
    """Answers per command fragment; records everything sent."""

    def __init__(self, script):
        self.script = script
        self.sent = []

    def is_connected(self):
        return True

    def send_command(self, command, timeout=30.0):
        self.sent.append(command)
        for fragment, reply in self.script:
            if fragment in command:
                return reply
        return (0, "", "")


def send_event(script, **kw):
    conn = FakeConn(script)
    tools.get_connection = lambda: conn
    kw.setdefault("target_creator", "KAHL")
    kw.setdefault("event_class", "KAHL")
    kw.setdefault("event_id", "MAKE")
    return tools.mac_send_apple_event(**kw), conn


PROBE = f"{pp.PROBE_CLASS}:{pp.PROBE_ID}"
NOT_READING = [(PROBE, (-1712, "", "AESend timed out"))]
READING = [(PROBE, (-1708, "", "target refused the event: -1708"))]


# --- the decision -----------------------------------------------------------
def test_a_send_that_waits_is_not_probed():
    """It finds out by itself, and the probe would be a tax on every call."""
    want, _, why = pp.should_probe("mac_send_apple_event", {"expect_reply": True})
    assert want is False and "waits" in why


def test_a_no_reply_send_is_probed():
    want, target, _ = pp.should_probe(
        "mac_send_apple_event", {"expect_reply": False, "target_creator": "KAHL"})
    assert want is True and target == "KAHL"


def test_a_tool_that_is_not_a_no_reply_sender_is_left_alone():
    want, _, _ = pp.should_probe("mac_list_files", {"expect_reply": False})
    assert want is False


def test_an_underivable_target_is_not_probed_against_a_guess():
    """The rule that matters most: a probe aimed at the wrong application
    answers a question nobody asked. Not probing and saying so beats guessing."""
    want, target, why = pp.should_probe(
        "mac_send_apple_event", {"expect_reply": False, "target_creator": None})
    assert want is False and target is None
    assert "not readable" in why


# --- reading the probe ------------------------------------------------------
def test_a_timeout_means_the_target_is_not_reading():
    r = pp.read_probe(-1712, 2.55)
    assert r["pumping"] is False and r["seconds"] == 2.55


def test_any_answer_means_it_pumped():
    """-1708 is produced by the target's OWN Apple Event Manager inside
    AEProcessAppleEvent — it cannot exist unless the target pumped."""
    assert pp.read_probe(-1708, 0.42)["pumping"] is True


def test_the_verdict_carries_its_own_evidence():
    """A field that asserts without saying why is what this whole day was
    about. Status and latency travel with the verdict so it can be checked."""
    r = pp.read_probe(-1712, 2.4)
    assert set(("pumping", "status", "seconds", "why")) <= set(r)


def test_the_probe_event_is_one_nobody_implements():
    """The method rests on the probe doing NOTHING. A 'harmless' real verb
    would turn the measurement into an action."""
    assert pp.PROBE_CLASS == pp.PROBE_ID == "5a5a5a5a"


# --- end to end through the tool -------------------------------------------
def test_a_target_that_is_not_reading_gets_nothing_sent():
    """The point of the exit: the event is NOT queued, because its effect would
    land at an unknown later time against unknown state."""
    result, conn = send_event(NOT_READING, expect_reply=False)
    assert result["success"] is None
    assert result["outcome"] == "not_read"
    assert result["sent"] is False
    assert not any("4d414b45" in c for c in conn.sent), conn.sent


def test_not_read_is_neither_true_nor_false():
    """A refusal is 'the target said no'. This is 'the target has not read it'.
    Folding them together hands the caller an error path for something that is
    not an error — which is exactly where the false alarm comes from."""
    result, _ = send_event(NOT_READING, expect_reply=False)
    assert result["success"] is not False and result["success"] is not True


def test_a_reading_target_is_sent_to_and_keeps_its_probe():
    result, conn = send_event(READING, expect_reply=False)
    assert any("4d414b45" in c for c in conn.sent), conn.sent
    assert result["probe"]["pumping"] is True


def test_a_waited_send_pays_for_no_probe():
    _, conn = send_event(READING, expect_reply=True, wait_seconds=1.0)
    assert not any(PROBE in c for c in conn.sent), conn.sent


def test_a_skipped_probe_leaves_a_trace():
    """A skip nobody can see is a check nobody can tell was not made."""
    result, conn = send_event(READING, expect_reply=False, skip_pump_probe=True)
    assert result["probe"]["skipped"] is True
    assert not any(PROBE in c for c in conn.sent)


def test_the_skip_still_sends():
    _, conn = send_event(NOT_READING, expect_reply=False, skip_pump_probe=True)
    assert any("4d414b45" in c for c in conn.sent), conn.sent


# --- the state the table did not have --------------------------------------
def test_a_target_that_is_not_running_did_not_pump():
    """Found on the FIRST live run. The first version read "anything but a
    timeout" as pumping, so an application that was not running at all came back
    as `pumping: true` — a brand-new field asserting something false, on the day
    that was about exactly that."""
    r = pp.read_probe(pp.TARGET_NOT_RUNNING, 0.08)
    assert r["pumping"] is False
    assert r["target_running"] is False


def test_a_missing_target_is_not_swallowed_into_not_read():
    """`-600` fails loudly on the send itself. Turning it into the subtle
    outcome would hide a plain error behind a hard one."""
    result, conn = send_event([(PROBE, (-600, "", "target app not running"))],
                              expect_reply=False)
    assert result.get("outcome") != "not_read"
    assert any("4d414b45" in c for c in conn.sent), conn.sent


def test_pumping_and_target_running_are_separate_answers():
    """Not reading and not existing are different problems for a caller: one is
    "wait", the other is "launch it"."""
    busy = pp.read_probe(-1712, 2.5)
    assert busy["pumping"] is False and busy["target_running"] is True


# --- the two builders, reached by name -------------------------------------
def test_probe_verb_asks_the_target_and_waits():
    """A probe with no wait would answer nothing at all — the bound IS the
    measurement."""
    v = pp.probe_verb("4b41484c")
    assert v.startswith("AESEND:4b41484c:")
    assert v.endswith(f":{pp.PROBE_TICKS}")
    assert pp.PROBE_CLASS in v and pp.PROBE_ID in v


def test_probe_verb_carries_no_direct_object():
    """An empty direct object keeps the probe inert. Anything in that field
    would be data an application might act on."""
    assert "::" in pp.probe_verb("4b41484c")


def test_pending_result_says_the_event_was_not_sent():
    """The caller has to be able to tell 'nothing happened' from 'something
    happened and failed' without reading this module."""
    r = pp.pending_result("mac_send_apple_event", {"target_creator": "KAHL"},
                          pp.read_probe(-1712, 2.5))
    assert r["sent"] is False
    assert r["success"] is None and r["outcome"] == "not_read"
    assert "NOT sent" in r["note"]


def test_pending_result_names_the_way_out():
    """A refusal with no stated remedy is a wall. It names both: wait, or skip
    the probe deliberately."""
    r = pp.pending_result("mac_send_apple_event", {"target_creator": "KAHL"},
                          pp.read_probe(-1712, 2.5))
    assert "skip_pump_probe" in r["note"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
