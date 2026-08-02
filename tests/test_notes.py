"""The session-to-session question channel.

One append-only file, because two sessions on this machine already share a
filesystem: a file is visible to both immediately, where an MCP tool would be
invisible until the other side's server restarts. What no design here can fix
is that a session has no event loop — the answer arrives at the other side's
next turn, never on demand.

The properties worth pinning are the ones that were wrong on the first run.
"""
import datetime
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host", "tools"))
import notes  # noqa: E402
import session_brief  # noqa: E402


class TheLine(unittest.TestCase):

    def test_a_note_survives_the_round_trip(self):
        line = notes.format_note("2026-08-02T17:28:54.328", "sessionA", "all",
                                 None, "does the block carry a zero Real field?")
        got = notes.parse_note(line)
        self.assertEqual(got["ts"], "2026-08-02T17:28:54.328")
        self.assertEqual(got["from"], "sessionA")
        self.assertIsNone(got["re"])
        self.assertEqual(got["text"], "does the block carry a zero Real field?")

    def test_a_multiline_question_is_flattened_so_one_note_is_one_line(self):
        line = notes.format_note("T", "a", "b", None, "two\nlines   and  spaces")
        self.assertNotIn("\n", line)
        self.assertEqual(notes.parse_note(line)["text"], "two lines and spaces")

    def test_a_foreign_line_is_ignored_rather_than_crashing_the_reader(self):
        for junk in ("", "hello", "2026-08-02 not a note at all", "a b c d"):
            self.assertIsNone(notes.parse_note(junk))


class WhatIsOpen(unittest.TestCase):

    def _lines(self):
        return [
            notes.format_note("2026-08-02T10:00:00.100", "A", "all", None, "first"),
            notes.format_note("2026-08-02T10:00:00.200", "A", "all", None, "second"),
            notes.format_note("2026-08-02T10:00:01.000", "B", "A",
                              "2026-08-02T10:00:00.100", "answering the first"),
        ]

    def test_an_answered_question_drops_off_and_the_other_stays(self):
        still_open = notes.open_notes(self._lines())
        self.assertEqual([n["text"] for n in still_open], ["second"])

    def test_an_answer_is_not_itself_an_open_question(self):
        self.assertTrue(all(n["re"] is None for n in notes.open_notes(self._lines())))

    def test_nothing_tracks_who_has_read_what(self):
        """Answering is the only signal the file can honestly carry. A 'seen'
        flag would claim knowledge about reading that nothing here can observe —
        the failure this project spent a day on, one level up."""
        with open(notes.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for word in ("seen", "unread", "read_by"):
            self.assertNotIn(f"{word} =", source)


class TheIdentifier(unittest.TestCase):

    def test_the_stamp_is_finer_than_a_second(self):
        """It was second-resolution on the first end-to-end run: two questions
        deposited in the same second shared an id, and answering one closed
        BOTH. An identifier that collides is worse than none — it silently
        marks somebody else's question as handled."""
        stamp = notes._now()
        self.assertRegex(stamp, r"T\d\d:\d\d:\d\d\.\d{3}$")

    def test_two_questions_in_the_same_second_stay_separable(self):
        lines = [
            notes.format_note("2026-08-02T17:28:27.100", "A", "all", None, "one"),
            notes.format_note("2026-08-02T17:28:27.900", "A", "all", None, "two"),
            notes.format_note("2026-08-02T17:28:28.000", "B", "A",
                              "2026-08-02T17:28:27.100", "answer to one"),
        ]
        self.assertEqual([n["text"] for n in notes.open_notes(lines)], ["two"])


class TheAnnouncementWindow(unittest.TestCase):
    """The PostToolUse hook fires on every tool call. Announcing every open note
    there would repeat the same question after each step until somebody answered
    it — noise that gets a hook switched off."""

    def _at(self, now, minutes_ago, text):
        ts = (now - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds")
        return notes.parse_note(notes.format_note(ts, "A", "all", None, text))

    def test_only_the_fresh_ones_are_announced(self):
        now = datetime.datetime(2026, 8, 2, 17, 0, 0)
        pending = [self._at(now, 1, "fresh"), self._at(now, 120, "two hours old")]
        got = notes.recent(pending, now, 600)
        self.assertEqual([n["text"] for n in got], ["fresh"])

    def test_without_a_window_nothing_is_filtered(self):
        now = datetime.datetime(2026, 8, 2, 17, 0, 0)
        pending = [self._at(now, 1, "fresh"), self._at(now, 120, "old")]
        self.assertEqual(len(notes.recent(pending, now, None)), 2)

    def test_the_window_is_not_a_seen_marker(self):
        """A marker would have to mean one session, and one file cannot mean
        two — it would silence a note for the side that never saw it."""
        with open(notes.__file__, encoding="utf-8") as handle:
            self.assertNotIn("marker", handle.read().lower().split("deliberately")[0])


class TheReturnPath(unittest.TestCase):
    """`open_notes` was the whole delivery rule at first, and it carries
    questions only: an answer sets `re=`, which closes the question and removes
    it from the list. So the side that ASKED learned nothing on either surface —
    the channel delivered outward and was silent coming back."""

    def _conversation(self):
        return [
            notes.format_note("2026-08-02T10:00:00.000", "A", "B", None, "real field?"),
            notes.format_note("2026-08-02T10:01:00.000", "B", "all",
                              "2026-08-02T10:00:00.000", "zero, measured"),
            notes.format_note("2026-08-02T10:02:00.000", "C", "all", None, "unrelated"),
            notes.format_note("2026-08-02T10:03:00.000", "B", "all",
                              "2026-08-02T10:02:00.000", "answer to C"),
        ]

    def test_the_asker_is_told_the_answer(self):
        got = notes.inbox_for(self._conversation(), "A")
        self.assertEqual([n["text"] for n in got], ["zero, measured"])

    def test_an_answer_addressed_to_me_counts_even_if_i_did_not_ask(self):
        lines = self._conversation() + [
            notes.format_note("2026-08-02T10:04:00.000", "B", "A",
                              "2026-08-02T10:02:00.000", "you may want this too")]
        self.assertIn("you may want this too",
                      [n["text"] for n in notes.inbox_for(lines, "A")])

    def test_somebody_elses_answer_is_not_mine(self):
        got = notes.inbox_for(self._conversation(), "A")
        self.assertNotIn("answer to C", [n["text"] for n in got])

    def test_the_answerer_does_not_get_its_own_answer_back(self):
        self.assertEqual(notes.inbox_for(self._conversation(), "B"), [])

    def test_an_unanswered_question_is_not_an_answer(self):
        self.assertTrue(all(n["re"] for n in notes.inbox_for(self._conversation(), "A")))


class TheThirdKind(unittest.TestCase):
    """A format with only ask and answer forces every message into one of the
    two. Twenty minutes of real use showed what that costs: the other session
    sent a status report, had nothing to point `re=` at, and it registered as a
    question that would have stayed open forever."""

    def _note(self, ts, who, to, text):
        return notes.format_note(ts, who, to, notes.NOTE_MARKER, text)

    def test_a_statement_is_never_an_open_question(self):
        lines = [self._note("2026-08-02T10:00:00.000", "B", "all", "INIT built")]
        self.assertEqual(notes.open_notes(lines), [])

    def test_a_broadcast_statement_reaches_the_other_side(self):
        lines = [self._note("2026-08-02T10:00:00.000", "B", "all", "INIT built")]
        self.assertEqual([n["text"] for n in notes.inbox_for(lines, "A")], ["INIT built"])

    def test_a_statement_does_not_come_back_to_its_author(self):
        """A channel that echoes you is a channel you stop reading."""
        lines = [self._note("2026-08-02T10:00:00.000", "B", "all", "INIT built")]
        self.assertEqual(notes.inbox_for(lines, "B"), [])

    def test_a_statement_addressed_elsewhere_is_not_mine(self):
        lines = [self._note("2026-08-02T10:00:00.000", "B", "C", "for C only")]
        self.assertEqual(notes.inbox_for(lines, "A"), [])

    def test_the_three_kinds_are_told_apart(self):
        question = notes.format_note("T1", "A", "all", None, "q")
        answer = notes.format_note("T2", "B", "all", "T1", "a")
        statement = self._note("T3", "B", "all", "s")
        self.assertEqual(notes.parse_note(question)["kind"], "question")
        self.assertEqual(notes.parse_note(answer)["kind"], "answer")
        self.assertEqual(notes.parse_note(statement)["kind"], "note")

    def test_lines_written_before_the_third_kind_still_parse(self):
        """The kind rides in the existing `re=` field precisely so the format
        did not change and nothing already in the channel became unreadable."""
        old = "2026-08-02T10:00:00.000 from=A to=all re=- an older question"
        self.assertEqual(notes.parse_note(old)["kind"], "question")
        self.assertIsNone(notes.parse_note(old)["re"])


class TheSessionName(unittest.TestCase):
    """`APPLEBRIDGE_WHO` was a REQUIRED setting for one commit, and requiring it
    was the mistake: the return path cannot address anything while both sides
    answer to the same name, so the channel routed nothing until a human
    remembered. Claude Code already exports a distinct session id into the
    environment of everything it runs — the identity was there all along."""

    def _who(self, **env):
        keep = {k: os.environ.get(k) for k in ("APPLEBRIDGE_WHO", "CLAUDE_CODE_SESSION_ID")}
        try:
            for key, value in env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            return notes._default_who()
        finally:
            for key, value in keep.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_the_session_id_names_the_session_with_no_configuration(self):
        self.assertEqual(
            self._who(APPLEBRIDGE_WHO=None,
                      CLAUDE_CODE_SESSION_ID="9e5cc132-c4c8-428b-a145-92f1a24340ca"),
            "sess-9e5cc132")

    def test_an_explicit_name_still_wins(self):
        self.assertEqual(
            self._who(APPLEBRIDGE_WHO="apfelpilot-live",
                      CLAUDE_CODE_SESSION_ID="9e5cc132-c4c8"),
            "apfelpilot-live")

    def test_without_either_it_says_agent_so_the_brief_can_report_it(self):
        self.assertEqual(
            self._who(APPLEBRIDGE_WHO=None, CLAUDE_CODE_SESSION_ID=None), "agent")

    def test_two_sessions_get_different_names(self):
        a = self._who(APPLEBRIDGE_WHO=None, CLAUDE_CODE_SESSION_ID="aaaaaaaa-1111")
        b = self._who(APPLEBRIDGE_WHO=None, CLAUDE_CODE_SESSION_ID="bbbbbbbb-2222")
        self.assertNotEqual(a, b)


class Delivery(unittest.TestCase):

    def test_the_brief_carries_open_questions(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(prefix="ab-notes-"), "notes.log")
        notes.append(notes.format_note("2026-08-02T09:00:00.000", "A", "all",
                                       None, "which trap does it hook?"), path)
        os.environ["APPLEBRIDGE_NOTES"] = path
        try:
            # notes.NOTES is read at import; point the module at the temp file.
            old, notes.NOTES = notes.NOTES, path
            lines = session_brief.note_lines()
        finally:
            notes.NOTES = old
            os.environ.pop("APPLEBRIDGE_NOTES", None)
        self.assertTrue(any("open questions" in l for l in lines), lines)
        self.assertTrue(any("which trap does it hook?" in l for l in lines), lines)

    def test_the_brief_carries_the_answer_back_to_the_asker(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(prefix="ab-notes-"), "notes.log")
        now = datetime.datetime.now()
        asked = (now - datetime.timedelta(minutes=5)).isoformat(timespec="milliseconds")
        replied = (now - datetime.timedelta(minutes=4)).isoformat(timespec="milliseconds")
        notes.append(notes.format_note(asked, "sessionA", "sessionB", None, "real field?"), path)
        notes.append(notes.format_note(replied, "sessionB", "all", asked, "zero, measured"), path)
        old, notes.NOTES = notes.NOTES, path
        oldwho, notes.WHO = notes.WHO, "sessionA"
        try:
            lines = session_brief.note_lines()
        finally:
            notes.NOTES, notes.WHO = old, oldwho
        self.assertTrue(any("for you" in l for l in lines), lines)
        self.assertTrue(any("zero, measured" in l for l in lines), lines)

    def test_the_brief_names_the_precondition_instead_of_routing_blind(self):
        """With the default name both sides are called "agent", so neither
        `to=` nor "a question I asked" can tell them apart. Saying so beats
        addressing nothing quietly."""
        import tempfile
        path = os.path.join(tempfile.mkdtemp(prefix="ab-notes-"), "notes.log")
        notes.append(notes.format_note("2026-08-02T09:00:00.000", "agent", "all",
                                       None, "which trap?"), path)
        old, notes.NOTES = notes.NOTES, path
        oldwho, notes.WHO = notes.WHO, "agent"
        try:
            lines = session_brief.note_lines()
        finally:
            notes.NOTES, notes.WHO = old, oldwho
        self.assertTrue(any("APPLEBRIDGE_WHO" in l for l in lines), lines)

    def test_a_missing_channel_file_is_silence_not_an_error(self):
        old, notes.NOTES = notes.NOTES, "/nonexistent-dir/notes.log"
        try:
            self.assertEqual(session_brief.note_lines(), [])
            self.assertEqual(notes.read(), [])
            self.assertFalse(notes.append("x"))
        finally:
            notes.NOTES = old


if __name__ == "__main__":
    unittest.main()
