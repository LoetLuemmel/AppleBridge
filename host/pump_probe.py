"""Ask whether the target is reading, before sending something it cannot answer.

An Apple Event runs when the target READS it, not when you send it. Measured
2026-08-05: an event lay in a busy application's queue for **905 seconds** and
fired four seconds after the modal in front of it was cleared — against whatever
was open by then. Delivery order is FIFO, and a merely *busy* application defers
exactly like a modal one.

Where that matters is narrow and worth stating, because a guard that runs
everywhere is a tax: it matters where we send **without waiting for a reply**.
A send that waits already learns the truth — the reply comes back or the bound
expires. A `kAENoReply` send learns nothing, and `status == 0` from the daemon
means the Apple Event Manager accepted it for delivery. That is the one place
where "delivered" gets mistaken for "done".

The probe is the one found the same day: send the target an event it does not
know, WITH a reply requested. An answer of any kind proves the target called
`AEProcessAppleEvent`; a timeout proves it did not. It says nothing about modal
versus busy, and that is correct — both mean the same thing to a caller.

**The probe event must stay unknown.** `'ZZZZ'` does nothing anywhere, which is
what makes measuring free. Substituting a "harmless" real verb would turn the
measurement into an action, and the measurement would then be the thing that
changes what it measures. If a later reader is tempted, this is the paragraph
that was written for them.

stdlib only; every function takes its `send` so it is testable without a guest.
"""

# An event class and ID no application implements. Not a placeholder — see the
# docstring: the whole method rests on this doing nothing.
PROBE_CLASS = "5a5a5a5a"
PROBE_ID = "5a5a5a5a"

# How long the probe may wait, in ticks. Measured: a target that pumps answers
# in 0.17-0.42 s (10-25 ticks), one that does not burns the whole bound. 120
# ticks (2 s) is comfortably past the observed answers and short enough that
# paying it before a send is cheaper than an effect landing somewhere unknown.
PROBE_TICKS = 120

# Verbs that send an Apple Event and do NOT wait for a reply. Written out rather
# than derived: a heuristic over tool names would silently acquire new members,
# and this list decides who pays 0.3 s and who does not.
NO_REPLY_SENDERS = ("mac_send_apple_event",)


def probe_verb(target_hex, ticks=PROBE_TICKS):
    """The control-port AESEND that asks whether `target_hex` is reading."""
    return f"AESEND:{target_hex}:{PROBE_CLASS}:{PROBE_ID}::{ticks}"


# The daemon's answer when no process carries that creator. NOT a timeout, and
# not evidence of pumping either — the first version of read_probe treated
# "anything but -1712" as pumping and therefore reported `pumping: true` for an
# application that was not running at all. Found on the first live run, against
# a case the table did not have.
TARGET_NOT_RUNNING = -600


def read_probe(status, seconds):
    """Interpret a probe reply. -> dict, always carrying its own evidence.

    Three states, not two:

    - **timed out** (`-1712`) — the target is running and is NOT reading its
      queue. This is the case the third exit exists for.
    - **not running** (`-600`) — there is nothing to read it. A different
      problem, and one the send itself reports loudly, so it is not blocked
      here; but the probe must not claim the target pumped.
    - **anything else** — the target answered, which it can only do from inside
      `AEProcessAppleEvent`. `-1708` (errAEEventNotHandled) is the usual one.

    `pumping` is the verdict; `status` and `seconds` are what it was made from,
    so a later reader can check the decision instead of believing it. After a
    day of fields that asserted without saying why, a verdict without its
    grounds is not worth adding.
    """
    timed_out = (status == -1712)
    absent = (status == TARGET_NOT_RUNNING)
    if timed_out:
        why = ("the target did not answer within the bound — it is running and "
               "not reading its Apple Event queue")
    elif absent:
        why = "no process carries that creator — there is nothing to read it"
    else:
        why = "the target answered, so it called AEProcessAppleEvent"
    return {
        "pumping": not (timed_out or absent),
        "target_running": not absent,
        "status": status,
        "seconds": round(seconds, 2),
        "why": why,
    }


def should_probe(tool_name, args):
    """-> (probe?, target_hex_or_None, why).

    Three outcomes, and the third is the point: where the target cannot be
    derived with certainty, the honest answer is NOT to probe and to say so —
    never to probe against a guessed target. A probe aimed at the wrong
    application answers a question nobody asked.
    """
    if tool_name not in NO_REPLY_SENDERS:
        return False, None, "not a no-reply sender; a waited send learns the truth itself"
    if (args or {}).get("expect_reply", True):
        return False, None, "this send waits for a reply, so it finds out by itself"
    if (args or {}).get("skip_pump_probe"):
        return False, None, "caller asked to skip the probe"
    target = (args or {}).get("target_creator")
    if not isinstance(target, str) or not (1 <= len(target) <= 4):
        return False, None, "target creator not readable from the call"
    return True, target, "no-reply send to a named target"


def pending_result(tool_name, args, probe):
    """The third exit: neither success nor failure.

    A failure is *the target refused it*. This is *the target has not even read
    it*, and the two need different answers — folding them together hands the
    caller an error path for something that is not an error, which is precisely
    where the false alarm comes from.

    `success` is None on purpose. Any boolean here would be read as one of the
    two outcomes this exists to distinguish.
    """
    return {
        "success": None,
        "outcome": "not_read",
        "sent": False,
        "tool": tool_name,
        "target": (args or {}).get("target_creator"),
        "probe": probe,
        "note": ("the event was NOT sent: the target is not reading its Apple "
                 "Event queue, so the effect would have landed at an unknown "
                 "later time, against unknown state. Retry once it answers, or "
                 "pass skip_pump_probe=True to send regardless."),
    }
