"""The session brief records that a session started, and cannot break trying.

Whether a session READ anything is not observable from here: the operating
notes live behind Authelia and the CMS audit log records only writes. When a
session started, on which branch and against which commit is observable, and
that is what "did another instance pick this up?" usually reduces to.

Two properties matter and neither is obvious from the code: the line has to be
greppable months later, and writing it must never be able to take the brief
down — a SessionStart hook that errors is a hook somebody switches off, which
is the same reasoning that makes every other section of the brief degrade
silently.
"""
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host", "tools"))
import session_brief  # noqa: E402


class TheLine(unittest.TestCase):

    def test_it_carries_the_facts_as_grepppable_key_values(self):
        line = session_brief.session_line(
            "2026-08-02T17:05:28", 13036, "main",
            "1337e7b 2026-08-02 Merge pull request #169", "0.8d33")
        self.assertEqual(
            line,
            "2026-08-02T17:05:28 hookpid=13036 branch=main "
            "commit=1337e7b version=0.8d33")

    def test_the_process_field_does_not_pose_as_a_session_id(self):
        """It was `pid=` for an hour. Two entries 28 seconds apart carried
        different numbers while ONE Claude Code process was running, because
        `os.getppid()` in a hook is the short-lived shell — so anyone
        cross-checking it against `ps` would have concluded a second instance
        had started. No reliable session id is available here, so none is
        claimed."""
        line = session_brief.session_line("T", 1, "b", "c", "v")
        self.assertIn("hookpid=", line)
        self.assertNotIn(" pid=", line)

    def test_the_commit_field_is_the_hash_and_not_the_subject(self):
        """`git log -1 --format=%h %ad %s` is what the brief already has; a
        subject line with spaces in the middle of a key=value record would make
        the log unparseable exactly when someone needs to parse it."""
        line = session_brief.session_line("T", 1, "b",
                                          "abc1234 2026-08-02 a subject", "v")
        self.assertIn("commit=abc1234 ", line)
        self.assertNotIn("subject", line)

    def test_missing_facts_become_a_question_mark_not_an_empty_field(self):
        line = session_brief.session_line("T", 1, "", "", "?")
        self.assertIn("branch=? ", line)
        self.assertIn("commit=? ", line)


class TheWrite(unittest.TestCase):

    def _tmp(self, name):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(prefix="ab-brief-"), name)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_each_start_appends_rather_than_replacing(self):
        """A log that keeps only the last start cannot answer "did another
        instance run?" — which is the whole reason it exists."""
        path = self._tmp("sessions.log")
        self.assertTrue(session_brief.record_session("first", path))
        self.assertTrue(session_brief.record_session("second", path))
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read().split("\n")[:2], ["first", "second"])

    def test_an_unwritable_path_is_reported_but_never_raised(self):
        self.assertFalse(session_brief.record_session(
            "x", "/nonexistent-directory-for-this-test/sessions.log"))

    def test_the_default_path_is_overridable_from_the_environment(self):
        """So a test — or a second machine — can point it somewhere else
        without editing the file the SessionStart hook runs."""
        source = open(session_brief.__file__, encoding="utf-8").read()
        self.assertIn("APPLEBRIDGE_SESSION_LOG", source)


class TheBriefItself(unittest.TestCase):

    def test_it_still_exits_zero_when_the_log_cannot_be_written(self):
        import subprocess
        env = dict(os.environ, APPLEBRIDGE_SESSION_LOG="/nonexistent-dir/x.log")
        run = subprocess.run(
            [sys.executable, os.path.join(_ROOT, "host", "tools", "session_brief.py")],
            capture_output=True, text=True, env=env, cwd=_ROOT, timeout=60)
        self.assertEqual(run.returncode, 0)
        self.assertIn("AppleBridge session brief", run.stdout)


if __name__ == "__main__":
    unittest.main()
