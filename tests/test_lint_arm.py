"""The control arm of the measurement has to be a fact in the result.

The strategy agreed with the Jetson session (2026-08-06) turns on one number:
first-attempt rate WITH the C89 lint against WITHOUT it, over the same frozen
task list. Until now that experiment could not be run at all — the lint sat in
`mac_compile` unconditionally and there was no way to turn it off.

The alternative considered and rejected: let the caller strip `c89` and the c89
tail of `remedies` from the result before handing it to the model. That works,
and nobody can check afterwards that the cut was made correctly — it has to
remove exactly the lint's remedies and leave the BRIDGE remedies from `mpw.py`
standing. As a parameter, the arm travels in the trace instead.

So these tests pin three things: the lint runs by default, `lint=False` silences
it, and silencing it does not take the bridge's own remedies with it.
"""
import base64
import os
import sys
import types
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host"))
import macbinary  # noqa: E402

sys.path.insert(0, _ROOT)
from tests.test_build_verification import FakeConn, tools  # noqa: E402

# C99 in the for-head — the habit that started all of this, and one of the four
# the lint knows.
C99_SOURCE = (b'int main(void) {\r'
              b'    for (int i = 0; i < 3; i++) { ; }\r'
              b'    return 0;\r'
              b'}\r')


def _readfile_reply(source: bytes) -> str:
    return base64.b64encode(macbinary.encode(source)).decode("ascii")


def _compile(**kwargs):
    conn = FakeConn([
        ("READFILE:", _readfile_reply(C99_SOURCE)),
        # The compile fails with a defect the lint does NOT know, so the bridge
        # remedy is in play at the same time as the lint's.
        ("Catenate", 'Fatal error: unable to open file "x.c" (OS Error -31001)'),
        ("Exists", "NoDir:-1701"),
    ])
    tools.get_connection = lambda: conn
    kwargs.setdefault("source_path", "MeinMac:MPW:P:src:x.c")
    kwargs.setdefault("output_path", "MeinMac:MPW:P:src:x.o")
    return tools.mac_compile(**kwargs)


class TheArmIsInTheResult(unittest.TestCase):

    def test_the_lint_runs_by_default(self):
        got = _compile()
        self.assertTrue(got["lint"])
        self.assertTrue(got["c89"], "the C99 for-head was not reported")
        self.assertTrue(any("for-head" in r for r in got["remedies"]),
                        got["remedies"])

    def test_lint_false_silences_it(self):
        got = _compile(lint=False)
        self.assertFalse(got["lint"])
        self.assertIsNone(got["c89"])
        self.assertFalse(any("for-head" in r for r in got["remedies"]),
                         got["remedies"])

    def test_the_control_arm_keeps_the_bridge_remedies(self):
        """The cut that a caller would have to make by hand, and the one it
        would most easily get wrong: `remedies` carries BOTH the lint's rewrites
        and the project rules from mpw.py. Only the first half is the arm."""
        for lint in (True, False):
            got = _compile(lint=lint)
            self.assertTrue(any("SetFile -t TEXT" in r for r in got["remedies"]),
                            f"bridge remedy lost with lint={lint}: {got['remedies']}")

    def test_the_verdict_does_not_depend_on_the_arm(self):
        """`lint` changes what the caller is told, never what is built."""
        self.assertEqual(_compile(lint=True)["success"],
                         _compile(lint=False)["success"])

    def test_the_schema_offers_the_switch(self):
        """A parameter a model cannot see is a parameter only we can use."""
        entry = next(t for t in tools.TOOLS if t["name"] == "mac_compile")
        self.assertIn("lint", entry["inputSchema"]["properties"])


if __name__ == "__main__":
    unittest.main()
