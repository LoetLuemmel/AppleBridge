"""An Apple Event the target REFUSED must not report success.

`AESend` returning `noErr` means the event was delivered and a reply came back.
It says nothing about whether the handler accepted it. A target that refuses puts
its reason in `keyErrorNumber` — and `SendGenericAE` never read that field. It
harvested `keyDirectObject`, fell back to `'----'`, and on finding neither wrote
`err = noErr; /* event sent, no reply parameter */`.

So every refused event answered **STATUS:0**, byte-identical to a successful one.
Measured 2026-08-05 by the parallel session, which sent class/ID `'ZZZZ'` — an
event no application handles — and got `STATUS:0` back in 0.34 s. The Apple Event
Manager had put `errAEEventNotHandled` (-1708) in that very reply.

That is this project's signature failure class in its purest form: the answer was
already in hand and simply not read. Same as `menuHeight` at offset 6, same as the
last column of `PROCLIST`.

These tests read the daemon source, because the daemon is 68K C that cannot run
here. That is a weaker instrument than a behavioural test and it is chosen
knowingly: the alternative was no test at all, and a field that goes back to
being silent looks exactly like a field with nothing to say. The live check is
the ZZZZ probe in both states — see docs/OPERATING_NOTES.md.

Run: python3 tests/test_ae_handler_error.py   (or via pytest)
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
COMMAND_C = os.path.join(HERE, "..", "mac", "src", "command.c")
HEADER = os.path.join(HERE, "..", "mac", "include", "applebridge.h")


def source(path=COMMAND_C):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def code_only(text):
    """The source with /* */ comments removed.

    Every assertion below is about what the daemon DOES. A test that matched a
    comment would have passed against the defect itself — the old behaviour was
    documented in a comment that read like a decision ("event sent, no reply
    parameter") right next to the line that dropped the error on the floor.
    """
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def send_generic_ae(text=None):
    """The body of SendGenericAE, comments stripped."""
    src = code_only(text or source())
    start = src.index("SendGenericAE(ProcessSerialNumber")
    end = src.index("ExecuteAppleEvent(", start)
    return src[start:end]


def execute_apple_event(text=None):
    src = code_only(text or source())
    start = src.index("BridgeResult ExecuteAppleEvent(")
    return src[start:]


# --- the field is read at all ------------------------------------------------
def test_the_reply_error_number_is_read():
    """The whole defect in one line: nobody asked the reply why it failed."""
    assert "keyErrorNumber" in send_generic_ae()


def test_the_reply_error_string_is_read_too():
    """A number sends the reader to a table; the target's own words send them to
    the cause. Both are in the reply, so taking only one is a choice to discard."""
    assert "keyErrorString" in send_generic_ae()


def test_the_error_number_is_asked_for_as_a_long():
    """typeShortInteger would make an app that returns a short come back as
    errAECoercionFail — inventing a failure where the target reported none. The
    AEM coerces, so ask for the wide one."""
    body = send_generic_ae()
    m = re.search(r"keyErrorNumber\s*,\s*(\w+)", body)
    assert m and m.group(1) == "typeLongInteger", m.group(1) if m else "not found"


# --- and it actually changes the outcome ------------------------------------
def test_a_refusal_becomes_the_exit_code():
    """Reading the field and then not acting on it would be the same bug with
    more source. The caller branches on exitCode; that is where it must land."""
    body = execute_apple_event()
    assert re.search(r"exitCode\s*=\s*handlerErr", body), body[-1200:]


def test_a_refusal_is_not_reported_as_success():
    """kBridgeNoErr on a refused event is what STATUS:0 was made of."""
    body = execute_apple_event()
    m = re.search(r"if\s*\(handlerErr\s*!=\s*0\)\s*\{(.*?)\n\t\}", body, re.S)
    assert m, "no handlerErr branch in ExecuteAppleEvent"
    assert "kBridgeCommandErr" in m.group(1)
    assert "kBridgeNoErr" not in m.group(1)


def test_the_reply_text_survives_a_refusal():
    """A handler may return an error AND output; the output is often the only
    description of what went wrong. Clearing it would trade one silence for
    another. So outData must be assigned BEFORE the refusal branch."""
    body = execute_apple_event()
    assert body.index("outData = h") < body.index("handlerErr != 0")


def test_the_numeric_code_is_never_the_truncated_part():
    """errData is 256 bytes and the target's message is arbitrary. The code is
    the part a caller can act on, so the message is what gets bounded."""
    header = source(HEADER)
    m = re.search(r"#define\s+AE_HANDLER_MSG_MAX\s*\((\d+)\)", header)
    assert m, "AE_HANDLER_MSG_MAX not defined"
    e = re.search(r"errData\[(\d+)\]", header)
    assert e and int(m.group(1)) < int(e.group(1)) - 40, (m.group(1), e.group(1))


def test_the_message_is_terminated_by_the_length_the_manager_reported():
    """AEGetParamPtr does not NUL-terminate. Printing the buffer without using
    actualSize would append whatever was on the stack — and this buffer goes
    straight into a response the host parses."""
    body = send_generic_ae()
    assert re.search(r"handlerMsg\[\s*gotSize\s*\]\s*=\s*'\\0'", body), body[-900:]


# --- the branch that used to swallow it -------------------------------------
def test_the_no_reply_parameter_branch_still_exists_and_is_now_narrow():
    """It was never wrong on its own: an event CAN legitimately answer with no
    direct object. It became wrong only because it was the sole verdict. It stays
    — but it now runs after keyErrorNumber has had its say."""
    body = send_generic_ae()
    assert "errAEDescNotFound" in body
    assert body.index("keyErrorNumber") < body.index("errAEDescNotFound")


def test_the_probe_signal_is_documented_where_it_is_used():
    """The discriminator was measured, and it is not the one that was predicted:
    without the fix it is latency (0.3 s vs 2.4 s), with it a real code. A reader
    who runs the probe and sees 0 must be able to tell "no fix" from "no error"."""
    notes = os.path.join(HERE, "..", "docs", "OPERATING_NOTES.md")
    text = source(notes)
    assert "keyErrorNumber" in text and "ZZZZ" in text


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
