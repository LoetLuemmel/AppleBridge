"""A command whose caller has already given up must not be executed.

The control port is ONE accept loop: a command is served to completion before
the next connection is accepted. Anything behind it waits in the kernel backlog,
where the server cannot see it at all — not even to notice the client has gone.

Measured 2026-08-04 with a harmless verb. Client B gave up after 1.0 s and
CLOSED its socket; the server logged `verb: 'PROCLIST'` about four seconds
later, while a screenshot ahead of it finished:

    B: nach 1.00s aufgegeben=True, Socket GESCHLOSSEN
    [16:29:05] screenshot 1024x768 ... data=786432B
    [16:29:07] screenshot -> 19985B PNG
    [16:29:07] verb: 'PROCLIST'          <- executed, caller long gone

Reported failure, real effect. For `LAUNCH`, `KEY`, `CLICK` or `SWAPSELF` that
means the guest changes while the caller has already read an error — and the
exposure is not academic: an `mpw_execute` running a link holds the port for
minutes (AE_SCRIPT_TIMEOUT is five) while the MCP client's default timeout is
30 s.

Why a deadline and not a liveness check — this is the part worth keeping:
there IS no liveness check to be had. `nc` half-closes as soon as its stdin
ends, so every well-behaved `printf ... | nc` sends FIN while still waiting for
its reply. FIN cannot mean "gave up". Only the caller knows when it stops
caring, so the caller says so.

Run: python3 tests/test_ctrl_deadline.py   (or via pytest)
"""
import os
import sys
import time
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host"))
sys.path.insert(0, os.path.join(_ROOT, "mcp"))
import host_server  # noqa: E402


class TheDeadlineLine(unittest.TestCase):

    def test_a_deadline_is_stripped_and_returned(self):
        cmd, deadline = host_server.split_ctrl_deadline("DEADLINE:1234.5\nPROCLIST")
        self.assertEqual(cmd, "PROCLIST")
        self.assertEqual(deadline, 1234.5)

    def test_a_request_without_one_is_untouched(self):
        """Backwards compatibility is the whole reason this is a leading line and
        not a new framing: a client that never heard of it keeps working."""
        cmd, deadline = host_server.split_ctrl_deadline("PROCLIST")
        self.assertEqual(cmd, "PROCLIST")
        self.assertIsNone(deadline)

    def test_an_unreadable_deadline_behaves_as_if_absent(self):
        """A malformed line must not turn into "expired" — refusing on garbage
        would make a typo look exactly like the defect this guards against."""
        cmd, deadline = host_server.split_ctrl_deadline("DEADLINE:soon\nPROCLIST")
        self.assertEqual(cmd, "PROCLIST")
        self.assertIsNone(deadline)

    def test_it_composes_with_the_auth_line(self):
        """Both are leading lines and a caller may send both. AUTH is stripped
        first by the server, so the deadline parser sees what is left."""
        after_auth, token = host_server.split_ctrl_auth(
            "AUTH:secret\nDEADLINE:99.5\nPROCLIST")
        self.assertEqual(token, "secret")
        cmd, deadline = host_server.split_ctrl_deadline(after_auth)
        self.assertEqual(cmd, "PROCLIST")
        self.assertEqual(deadline, 99.5)

    def test_a_deadline_with_no_command_yields_no_command(self):
        cmd, deadline = host_server.split_ctrl_deadline("DEADLINE:5")
        self.assertEqual(cmd, "")
        self.assertEqual(deadline, 5.0)


class TheRefusalHappensBeforeAnythingRuns(unittest.TestCase):
    """Where the check sits is the entire point, so it is pinned by position."""

    def setUp(self):
        self.src = open(host_server.__file__.replace(".pyc", ".py"),
                        encoding="utf-8").read()

    def test_the_deadline_is_checked_before_the_command_is_dispatched(self):
        """After execution it would be worthless: the guest has already changed.
        Pinned by source position because that is what makes the guarantee."""
        check = self.src.index("if ctrl_deadline is not None and time.time()")
        dispatch = self.src.index('log(f"verb: {cmd[:60]!r}")')
        self.assertLess(check, dispatch,
                        "the expiry check must precede the verb dispatch")

    def test_it_also_precedes_the_mpw_fall_through(self):
        check = self.src.index("if ctrl_deadline is not None and time.time()")
        fallthrough = self.src.index('log(f"cmd: {redact_secrets(cmd)')
        self.assertLess(check, fallthrough)

    def test_the_refusal_says_why_rather_than_just_failing(self):
        """A bare error would send the caller looking at the daemon. The reply
        names the queue, which is where the cause actually is."""
        window = self.src[self.src.index("if ctrl_deadline is not None"):][:1200]
        self.assertIn("NOT executed", window)
        self.assertIn("one command at a time", window)

    def test_read_only_verbs_are_refused_too(self):
        """One rule is easier to trust than two, and a caller that is gone cannot
        use the answer either. Pinned so nobody 'optimises' it into a whitelist
        of safe verbs — the moment that list is wrong, the guarantee is gone."""
        window = self.src[self.src.index("if ctrl_deadline is not None"):][:1200]
        for verb in ("PROCLIST", "DISKINFO", "LISTDIR"):
            self.assertNotIn(f'"{verb}"', window,
                             "the expiry check must not carve out exceptions")


class TheClientSaysWhenItStopsCaring(unittest.TestCase):

    def test_the_mcp_client_sends_its_own_timeout_as_the_deadline(self):
        src = open(os.path.join(_ROOT, "mcp", "mac_connection.py"),
                   encoding="utf-8").read()
        self.assertIn("DEADLINE:{time.time() + timeout}", src)

    def test_the_deadline_is_absolute_not_a_duration(self):
        """The server cannot infer when a request was SENT — it learns of it
        only when it accepts it, which is exactly the interval in question. Both
        ends share a clock (the control port is loopback), so an absolute
        instant is exact where a duration would be a guess."""
        src = open(os.path.join(_ROOT, "mcp", "mac_connection.py"),
                   encoding="utf-8").read()
        self.assertIn("time.time() + timeout", src)
        self.assertNotIn("DEADLINE:{timeout}", src)

    def test_a_client_that_cannot_give_up_sends_no_deadline(self):
        """`send_command.py` sets no socket timeout — it waits as long as the
        work takes. A deadline there could only refuse work nobody abandoned, so
        its absence is a decision, not an oversight, and is pinned as one."""
        src = open(os.path.join(_ROOT, "host", "send_command.py"),
                   encoding="utf-8").read()
        # Check what is SENT, not what the file mentions: the docstring there
        # explains at length why no deadline is sent, so a whole-file search
        # finds the word and proves nothing. The first version of this test did
        # exactly that — evidence that was not the thing it claimed, which is
        # the failure this whole suite exists to catch.
        sent = [line for line in src.splitlines() if "sendall(" in line]
        self.assertTrue(sent, "send_command.py sends nothing?")
        for line in sent:
            self.assertNotIn("DEADLINE", line,
                             "send_command.py has no socket timeout; a deadline "
                             "there could only refuse work nobody gave up on")
        self.assertNotIn("settimeout", src,
                         "if this client grows a timeout it CAN give up, and "
                         "then it should send a deadline after all")

    def test_the_rationale_does_not_claim_a_shared_clock(self):
        """The first version justified the absolute deadline with "both ends
        share a clock — the control port is loopback-only". The socket is
        loopback; the CALLER need not be. The parallel session drives this port
        by ssh from another host, and pointed that out after implementing its
        side: it computes the deadline Mac-side inside its relay so the server
        reads its own clock. A justification that is false for a real caller is
        worse than none — it stops the next person from asking."""
        src = open(host_server.__file__.replace(".pyc", ".py"),
                   encoding="utf-8").read()
        window = src[src.index("def split_ctrl_deadline"):][:2600]
        self.assertNotIn("Both ends share a clock", window)
        self.assertIn("the clock the SERVER reads", window)

    def test_the_client_imports_time(self):
        """It did not, at first — and that would have raised only at the moment
        a command was actually sent."""
        src = open(os.path.join(_ROOT, "mcp", "mac_connection.py"),
                   encoding="utf-8").read()
        self.assertIn("import time", src)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromTestCase(t)
        for t in (TheDeadlineLine, TheRefusalHappensBeforeAnythingRuns,
                  TheClientSaysWhenItStopsCaring))
    sys.exit(0 if runner.run(suite).wasSuccessful() else 1)
