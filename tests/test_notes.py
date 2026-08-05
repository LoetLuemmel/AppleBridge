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
import notes_watch  # noqa: E402
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

    def test_a_multiline_question_is_still_exactly_one_line_in_the_file(self):
        """The invariant the whole file format rests on. Whatever the escaping
        does, a note that occupied two lines would make the line NUMBER of every
        note after it wrong — which is the clock `notes_watch.lost_since` reads
        to decide whether an unreadable line is new."""
        line = notes.format_note("T", "a", "b", None, "two\nlines   and  spaces")
        self.assertNotIn("\n", line)
        self.assertNotIn("\r", line)
        self.assertEqual(len(line.splitlines()), 1)

    def test_a_foreign_line_is_ignored_rather_than_crashing_the_reader(self):
        for junk in ("", "hello", "2026-08-02 not a note at all", "a b c d"):
            self.assertIsNone(notes.parse_note(junk))


class TheShapeTheAuthorTyped(unittest.TestCase):
    """A note is one line in the file and the author's paragraphs on screen.

    Before this, `format_note` collapsed every run of whitespace, so a
    five-paragraph review arrived as one unbroken block. Authors worked around
    it by typing a literal `\\n` into the shell string — which is not
    whitespace, survived the collapse, and reached the reader as two characters
    of noise mid-sentence. Both halves are pinned here: the file stays
    line-per-note, and what comes back out is what went in.
    """

    def test_a_paragraph_break_survives_the_round_trip(self):
        text = "first paragraph.\n\nsecond paragraph."
        line = notes.format_note("T", "a", "all", None, text)
        self.assertNotIn("\n", line)
        got = notes.unescape_text(notes.parse_note(line)["text"])
        self.assertEqual(got, text)

    def test_a_note_about_the_escape_is_not_read_as_a_line_break(self):
        """Why the backslash doubles. Without it, a note discussing the
        sequence would come back to the reader as a paragraph break — the same
        defect one level down, and the harder one to notice because the note
        still looks like prose."""
        text = r"grep for \n in the log"
        line = notes.format_note("T", "a", "all", None, text)
        got = notes.unescape_text(notes.parse_note(line)["text"])
        self.assertEqual(got, text)
        self.assertNotIn("\n", got)

    def test_a_carriage_return_cannot_reach_the_file_by_another_spelling(self):
        """MacRoman/CR is this project's daily traffic, so a note quoting guest
        output can carry CR or CRLF. Either would split the line just as well
        as LF."""
        for spelling in ("a\rb", "a\r\nb", "a\nb"):
            line = notes.format_note("T", "x", "all", None, spelling)
            self.assertEqual(len(line.splitlines()), 1, spelling)
            self.assertEqual(notes.unescape_text(notes.parse_note(line)["text"]),
                             "a\nb", spelling)

    def test_the_compact_readers_render_a_note_on_one_line(self):
        """The session brief and the watcher print a truncated preview into a
        fixed-shape block. A real newline there splits one note across what
        reads as two events, so those two callers ask for `inline`."""
        text = "line one\nline two"
        escaped = notes.escape_text(text)
        self.assertNotIn("\n", notes.unescape_text(escaped, inline=True))
        self.assertEqual(notes.unescape_text(escaped, inline=True),
                         "line one line two")

    def test_a_line_written_before_the_escaping_still_displays(self):
        """Backward compatibility, stated rather than hoped for: the channel
        already holds notes whose author typed a literal `\\n` to get a
        paragraph. Those now render as the break they meant."""
        old = "2026-08-04T08:48:30.773 from=A to=B re=- one\\n\\ntwo"
        got = notes.unescape_text(notes.parse_note(old)["text"])
        self.assertEqual(got, "one\n\ntwo")

    def test_list_indents_the_continuation_so_one_note_reads_as_one_block(self):
        """Without the indent, a multi-paragraph note in `list` is a wall of
        text in which the boundary between two senders is invisible."""
        note = notes.parse_note(
            notes.format_note("T", "sender", "all", None, "head\ntail"))
        out = notes.render(note, "question").split("\n")
        self.assertEqual(out[0], "question T  from=sender  head")
        self.assertEqual(out[1], "    tail")


class TwoSessionsCrossing(unittest.TestCase):
    """Writing tells you what arrived while you were composing.

    The real event, 2026-08-04: one side answered two questions at 10:45:41; the
    other re-asked the same two at 10:46:29, 48 seconds later, because its turn
    had already begun. Delivering into a turn already in flight is impossible
    here and always will be. But the answer was ALREADY IN THE FILE when the
    re-ask was written, and the tool that wrote it said nothing — that half is a
    defect, and this is the fix for it.
    """

    def _lines(self):
        return [
            notes.format_note("2026-08-04T10:09:07.000", "B", "A", None, "q1"),
            notes.format_note("2026-08-04T10:11:51.000", "B", "A", None, "q2"),
            notes.format_note("2026-08-04T10:45:41.570", "A", "B",
                              "2026-08-04T10:09:07.000", "answer to q1"),
            notes.format_note("2026-08-04T10:45:41.642", "A", "B",
                              "2026-08-04T10:11:51.000", "answer to q2"),
        ]

    def test_the_re_asker_is_shown_the_answers_that_crossed(self):
        got = notes.crossed(self._lines(), "B")
        self.assertEqual([n["text"] for n in got],
                         ["answer to q1", "answer to q2"])

    def test_your_own_messages_are_never_reported_back_to_you(self):
        """A channel that reports your own writing as news is noise."""
        for note in notes.crossed(self._lines(), "A"):
            self.assertNotEqual(note["from"], "A")

    def test_a_session_that_never_wrote_is_not_flooded_with_the_whole_inbox(self):
        """The cold start belongs to the session brief. This is a crossing
        signal, and a signal that fires on everything is not one."""
        self.assertEqual(notes.crossed(self._lines(), "C"), [])

    def test_nothing_older_than_your_last_message_is_reported(self):
        """Otherwise every post re-announces the whole conversation."""
        lines = self._lines() + [
            notes.format_note("2026-08-04T10:46:29.000", "B", "A", None, "re-ask"),
        ]
        self.assertEqual(notes.crossed(lines, "B"), [])


class TheWakePathCanBeBlind(unittest.TestCase):
    """Two defects that between them lost a message for two days, both measured
    on the live channel 2026-08-04.

    The other machine's Stop-hook watcher ran as `apfelpilot-live` while its
    session posted as `sess-64c74122`. Inbox 3 against 15; **zero** wake reasons
    in an hour against ten. Neither half can see the other, so neither could
    report it — and on top of that the watcher's baseline marked everything
    present at arming as old, which is the entire time the session was working.
    """

    def _split_identity(self):
        return [
            notes.format_note("2026-08-04T10:00:00.000", "poster", "other",
                              None, "a question from the posting name"),
            notes.format_note("2026-08-04T10:45:41.000", "other", "poster",
                              "2026-08-04T10:00:00.000", "the answer"),
        ]

    def test_a_watching_name_that_never_wrote_is_reported(self):
        """The watcher's name is not the posted name — the exact live case."""
        warnings = notes.identity_warnings(self._split_identity(), "watcher-name")
        self.assertTrue(any("never written" in w for w in warnings), warnings)

    def test_a_name_derived_from_the_session_id_is_reported_as_temporary(self):
        """It changes on restart, so an address recorded today reaches nobody
        tomorrow — true of both sides of this channel."""
        warnings = notes.identity_warnings(self._split_identity(), "sess-abc12345")
        self.assertTrue(any("CHANGES on restart" in w for w in warnings), warnings)

    def test_an_absent_channel_says_nothing_at_all(self):
        """Silence on an absent channel is a rule here, not an oversight: a
        brief that complains where there is no conversation is one that gets
        switched off, and then every warning it could have carried is lost too."""
        self.assertEqual(notes.identity_warnings([], "sess-abc12345"), [])
        self.assertEqual(notes.identity_warnings([], "agent"), [])

    def test_a_stable_name_that_takes_part_draws_no_warning(self):
        self.assertEqual(notes.identity_warnings(self._split_identity(), "poster"), [])

    def test_what_arrived_while_the_session_worked_is_still_reported(self):
        """THE defect. The watcher arms at the END of a turn; with the old
        `latest_ts` baseline, an answer written during that turn was already in
        the file and therefore never new. Measured: answers at 10:45:41, watcher
        armed 10:46:47 with baseline 10:46:29, nothing reported, the question
        re-asked."""
        lines = self._split_identity()
        base = notes_watch.baseline_for(lines, "poster", already_seen="")
        self.assertEqual(base, "2026-08-04T10:00:00.000")
        hits = notes_watch.relevant(lines, "poster", base)
        self.assertEqual([n["text"] for n in hits], ["the answer"])

    def test_the_old_baseline_would_have_lost_it(self):
        """Pins WHY the change was needed, so nobody reverts it as cosmetic."""
        lines = self._split_identity()
        self.assertEqual(notes_watch.relevant(lines, "poster",
                                              notes_watch.latest_ts(lines)), [])

    def test_the_same_message_is_not_reported_twice(self):
        """Without this the fix becomes a nag: an unanswered message stays newer
        than the last write at EVERY turn end, and a watcher that repeats itself
        gets switched off — which costs more than the defect it fixes."""
        lines = self._split_identity()
        base = notes_watch.baseline_for(lines, "poster",
                                        already_seen="2026-08-04T10:45:41.000")
        self.assertEqual(notes_watch.relevant(lines, "poster", base), [])

    def test_a_session_that_never_wrote_keeps_the_old_baseline(self):
        """There is no better marker for it, and inventing one would claim
        knowledge about reading that nothing here can observe."""
        lines = self._split_identity()
        self.assertEqual(notes_watch.baseline_for(lines, "newcomer", ""),
                         notes_watch.latest_ts(lines))


class TheViewIsNotTheChannel(unittest.TestCase):
    """`list` shows what is OUTSTANDING; the file still holds everything.

    Measured 2026-08-04 and the numbers are the whole argument: 70 notes,
    139 739 characters in the file, ZERO open questions — and `list` printed
    14 989 characters, every one already handled. The other machine's inbox was
    67 469. Both sides had stopped reading `list` and started grepping it, which
    is the point where a delivery mechanism has quietly failed: still
    delivering, nobody receiving.
    """

    def _lines(self):
        return [
            notes.format_note("2026-08-04T10:00:00.000", "B", "A", None, "alt, gefragt"),
            notes.format_note("2026-08-04T10:01:00.000", "A", "B",
                              "2026-08-04T10:00:00.000", "alt, beantwortet"),
            notes.format_note("2026-08-04T10:02:00.000", "A", "all", notes.NOTE_MARKER,
                              "As letzte eigene Nachricht"),
            notes.format_note("2026-08-04T10:03:00.000", "B", "A", None, "NEU und offen"),
        ]

    def test_the_default_view_is_what_is_outstanding(self):
        got = notes.actionable(self._lines(), "A")
        self.assertEqual([n["text"] for n in got], ["NEU und offen"])

    def test_handled_history_is_hidden_but_not_lost(self):
        """The noise was in the VIEW. `--all` still reaches everything, and the
        file is untouched — that distinction is the entire design."""
        lines = self._lines()
        # Against all_notes, not inbox_for: `inbox_for` carries answers and
        # statements, never questions, so the open question is not in it at all.
        # The first version of this test compared the wrong two sets and failed
        # on its own premise rather than on the code.
        self.assertEqual(len(notes.all_notes(lines)), 4)
        self.assertEqual(len(notes.actionable(lines, "A")), 1)
        self.assertGreater(len(notes.all_notes(lines)),
                           len(notes.actionable(lines, "A")))

    def test_nothing_outstanding_is_an_empty_view(self):
        """The measured case: zero open questions, and the old view still
        printed fifteen thousand characters."""
        lines = self._lines()[:3]
        self.assertEqual(notes.actionable(lines, "A"), [])

    def test_a_shortened_note_says_that_it_was_shortened(self):
        """A cut that does not announce itself is a silent cap: the reader
        cannot tell a short note from a truncated one."""
        short = notes.preview("kurz", 220)
        self.assertEqual(short, "kurz")
        long_text = "x" * 500
        cut = notes.preview(long_text, 220)
        self.assertIn("+280 Zeichen", cut)
        self.assertLess(len(cut), len(long_text))


class RotationKeepsEverything(unittest.TestCase):
    """Rotation is an archive, not a bin — and never a partial truncation.

    "Keep the last N" is the obvious idea and breaks three mechanisms silently:
    `lost_since` reads the LINE NUMBER as its clock, `answer` validates against
    the questions still present, and `crossed` measures from this session's last
    own message. A whole-file move breaks none of them.
    """

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "notes.log")

    def test_rotation_is_refused_while_a_question_is_open(self):
        """An archived question can never be closed by anyone. Refusing is the
        only honest option — the alternative makes a pending question silently
        unanswerable."""
        notes.append(notes.format_note("T1", "B", "A", None, "offen"), self.path)
        ok, msg = notes.rotate("2026-08-04T15:00:00.000", self.path)
        self.assertFalse(ok)
        self.assertIn("offene Frage", msg)

    def test_rotation_archives_and_leaves_a_readable_marker(self):
        """The marker must be a NORMAL note: an unparseable line would be
        reported by every reader as a lost delivery, which is precisely the
        alarm this project built to be trusted."""
        notes.append(notes.format_note("T1", "B", "all", notes.NOTE_MARKER, "eins"),
                     self.path)
        ok, msg = notes.rotate("2026-08-04T15:00:00.000", self.path)
        self.assertTrue(ok, msg)
        fresh = notes.read(self.path)
        self.assertEqual(notes.unreadable(fresh), [])
        self.assertEqual(len(notes.all_notes(fresh)), 1)
        self.assertIn("rotiert", notes.all_notes(fresh)[0]["text"])
        self.assertEqual(len(notes.archives(self.path)), 1)

    def test_the_archive_is_searchable_or_it_is_a_bin(self):
        """An archive nobody can search is material effectively deleted, with
        the added harm that everyone believes it was kept."""
        notes.append(notes.format_note("T1", "B", "all", notes.NOTE_MARKER,
                                       "GETHANDLESIZE zieht Glue"), self.path)
        notes.rotate("2026-08-04T15:00:00.000", self.path)
        notes.append(notes.format_note("T2", "B", "all", notes.NOTE_MARKER, "neu"),
                     self.path)
        hits = notes.find("gethandlesize", self.path)
        self.assertEqual(len(hits), 1)
        self.assertIn("Glue", hits[0][1]["text"])

    def test_an_empty_channel_is_not_rotated(self):
        ok, msg = notes.rotate("2026-08-04T15:00:00.000", self.path)
        self.assertFalse(ok)


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


class TheWatcher(unittest.TestCase):
    """The Stop-hook watcher: the only mechanism that reaches an IDLE session.

    Biased towards silence on purpose — a watcher that wakes a session for
    nothing is switched off within a day, and then it is worth less than none.
    """

    def _at(self, ts, who, to, answering, text):
        return notes.format_note(ts, who, to, answering, text)

    def test_it_never_wakes_a_session_for_its_own_note(self):
        """A channel that wakes you for your own message is noise with extra
        steps."""
        import notes_watch
        lines = [self._at("2026-08-02T10:00:00.000", "B", "all", None, "mine")]
        self.assertEqual(notes_watch.relevant(lines, "B", ""), [])

    def test_it_wakes_for_something_addressed_here(self):
        import notes_watch
        lines = [self._at("2026-08-02T10:00:00.000", "A", "B",
                          notes.NOTE_MARKER, "for you")]
        got = notes_watch.relevant(lines, "B", "")
        self.assertEqual([n["text"] for n in got], ["for you"])

    def test_it_wakes_for_an_open_question_from_the_other_side(self):
        import notes_watch
        lines = [self._at("2026-08-02T10:00:00.000", "A", "all", None, "which trap?")]
        self.assertTrue(notes_watch.relevant(lines, "B", ""))

    def test_anything_already_there_at_the_start_is_not_news(self):
        """The baseline is what makes it a watcher rather than a reporter."""
        import notes_watch
        lines = [self._at("2026-08-02T10:00:00.000", "A", "B",
                          notes.NOTE_MARKER, "old")]
        self.assertEqual(
            notes_watch.relevant(lines, "B", "2026-08-02T10:00:00.000"), [])

    def test_two_sessions_do_not_share_one_lock(self):
        """A single global lock made the watcher first-come-first-served:
        whichever session went idle first held it, the other one's watcher
        exited at once, and only one of the two could ever be woken. Measured
        minutes after the first deploy — this session held the lock, the other
        had none. The lock is there to stop ONE session stacking a watcher per
        turn, which is a per-session concern; global, it silently became a
        per-machine mutex on being reachable at all."""
        import notes_watch
        self.assertNotEqual(notes_watch.lock_path("sess-aaaa"),
                            notes_watch.lock_path("apfelpilot-live"))

    def test_a_session_name_cannot_steer_the_lock_out_of_tmp(self):
        """`who` comes from the environment and lands in a path."""
        import notes_watch
        path = notes_watch.lock_path("../../etc/passwd")
        self.assertTrue(path.startswith("/tmp/applebridge_watch."), path)
        self.assertNotIn("/", path[len("/tmp/applebridge_watch."):])

    def test_a_stale_lock_does_not_block_a_new_watcher(self):
        """A killed watcher leaves its pid behind; treating that as 'running'
        would silence the channel until somebody deleted a file in /tmp."""
        import tempfile, notes_watch
        path = os.path.join(tempfile.mkdtemp(prefix="ab-lock-"), "watch.lock")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("999999")          # no such process
        self.assertFalse(notes_watch.another_watcher_running(path))

    def test_the_hook_is_wired_so_the_wake_can_reach_the_session(self):
        """The wiring is the whole mechanism, and two mistakes in it are silent:
        `|| true` would swallow exit 2 — the wake signal itself — and without
        asyncRewake the exit code means nothing. Neither shows up as a failure
        anywhere; the channel just stays quiet forever."""
        import json
        with open(os.path.join(_ROOT, ".claude", "settings.json"),
                  encoding="utf-8") as handle:
            settings = json.load(handle)
        watchers = [h for entry in settings["hooks"]["Stop"] for h in entry["hooks"]
                    if "notes_watch.py" in h.get("command", "")]
        self.assertEqual(len(watchers), 1, "the watcher is not in the Stop hook")
        watcher = watchers[0]
        self.assertTrue(watcher.get("asyncRewake"), "exit 2 would not wake anything")
        self.assertNotIn("|| true", watcher["command"],
                         "the wake signal is being swallowed")
        self.assertNotIn("2>/dev/null", watcher["command"],
                         "the message would be discarded before it is shown")


class TheHelp(unittest.TestCase):

    def _run(self, *args):
        import subprocess
        return subprocess.run([sys.executable, notes.__file__, *args],
                              capture_output=True, text=True, timeout=30)

    def test_the_wrong_flag_is_refused_with_the_right_one(self):
        """Plain argparse answers `unrecognized arguments: --to x` plus a usage
        line — true and useless. The help text alone fixed it for whoever asks
        first, not for whoever stumbles, and stumbling is the case that produced
        it."""
        run = self._run("answer", "2026-08-02T10:00:00.000", "text",
                        "--to", "apfelpilot-live")
        self.assertEqual(run.returncode, 2)
        self.assertIn("takes no --to", run.stderr)
        self.assertIn("note --to apfelpilot-live", run.stderr,
                      "the refusal never names the verb that does take it")

    def test_the_refused_flag_stays_out_of_the_help(self):
        """It is accepted to be refused, not offered."""
        run = self._run("answer", "--help")
        self.assertNotIn("\n  --to", run.stdout)

    def test_the_answer_verb_says_it_needs_no_recipient(self):
        """`ask` and `note` take --to, `answer` does not — the question names
        the recipient. Argparse reported that only AFTER a long answer had been
        typed and rejected, which is how this help text came to exist."""
        import subprocess
        run = subprocess.run([sys.executable, notes.__file__, "answer", "--help"],
                             capture_output=True, text=True, timeout=30)
        self.assertIn("--to", run.stdout,
                      "the help never mentions the option it does not have")
        self.assertIn("asked it", run.stdout)


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
            # Was `assertFalse(append(...))`. A boolean is exactly what got
            # ignored on 2026-08-04, so the failure is now a raise.
            with self.assertRaises(notes.ChannelWriteError):
                notes.append("x")
            self.assertFalse(notes.append("x", raise_on_fail=False))
        finally:
            notes.NOTES = old


class TheShellNeverSeesTheText(unittest.TestCase):
    """`--stdin`, and the reason it had to exist.

    On 2026-08-04 BOTH sessions lost text to the shell within hours of each
    other, writing about the very defect class this project keeps re-finding:

      * one wrote `$0A1C` (the MenuList low-memory address) inside double
        quotes; the shell substituted an undefined variable and the address
        vanished from the sentence explaining it.
      * the other wrote a sentence containing a backquoted `nc`; the shell
        EXECUTED it and deleted the subjects of two sentences.

    Neither was noticed at the time, and neither COULD be: `list` reported zero
    unreadable lines, correctly — the line was syntactically perfect. The
    damage happens in the shell, before any byte reaches this tool, so the tool
    cannot detect it. It can only offer a path the shell does not touch.
    """

    def setUp(self):
        import tempfile
        self.path = os.path.join(tempfile.mkdtemp(prefix="ab-stdin-"), "notes.log")

    def run_cli(self, args, stdin=None):
        import subprocess
        env = dict(os.environ, APPLEBRIDGE_NOTES=self.path)
        return subprocess.run([sys.executable, notes.__file__, *args],
                              input=stdin, capture_output=True, text=True,
                              env=env, timeout=30)

    def test_the_two_constructs_that_ate_text_survive_verbatim(self):
        payload = "`nc` half-closes, and $0A1C is the MenuList."
        run = self.run_cli(["note", "--stdin", "--from", "t"], stdin=payload)
        self.assertEqual(run.returncode, 0, run.stderr)
        written = open(self.path, encoding="utf-8").read()
        self.assertIn("`nc`", written)
        self.assertIn("$0A1C", written)

    def test_line_breaks_survive_the_round_trip(self):
        """Multi-line is the whole reason to reach for a heredoc; if it arrived
        as one run-on line nobody would use it and the class would stay open."""
        run = self.run_cli(["note", "--stdin", "--from", "t"],
                           stdin="first line\nsecond line")
        self.assertEqual(run.returncode, 0, run.stderr)
        note = notes.all_notes(notes.read(self.path))[0]
        self.assertEqual(notes.unescape_text(note["text"]),
                         "first line\nsecond line")

    def test_the_advice_quotes_the_heredoc_delimiter(self):
        """An UNQUOTED delimiter (`<<EOF`) still expands `$` and backquotes
        inside the heredoc — the same trap, one layer down. The tool must not
        hand out advice that reopens the hole it is closing."""
        self.assertIn("<<'EOF'", notes.STDIN_IDIOM)

    def test_stdin_and_an_argument_together_are_refused(self):
        """Silently preferring one would be this project's signature failure:
        a report about text that is not the text that was written."""
        run = self.run_cli(["note", "--stdin", "also this", "--from", "t"],
                           stdin="piped")
        self.assertEqual(run.returncode, 2)
        self.assertFalse(os.path.exists(self.path) and
                         open(self.path, encoding="utf-8").read().strip())

    def test_empty_stdin_writes_nothing(self):
        run = self.run_cli(["note", "--stdin", "--from", "t"], stdin="")
        self.assertEqual(run.returncode, 2)
        self.assertIn("nothing was written", run.stderr.lower())

    def test_no_text_at_all_names_the_safe_idiom(self):
        """argparse alone would say "the following arguments are required:
        text", which is true and teaches nothing."""
        run = self.run_cli(["note", "--from", "t"], stdin="")
        self.assertEqual(run.returncode, 2)
        self.assertIn("<<'EOF'", run.stderr)

    def test_all_three_writing_verbs_offer_it(self):
        """`answer` is the one that carries the long technical replies, so
        leaving it out would miss the case that caused this."""
        import subprocess
        for verb in ("ask", "answer", "note"):
            run = subprocess.run([sys.executable, notes.__file__, verb, "--help"],
                                 capture_output=True, text=True, timeout=30)
            self.assertIn("--stdin", run.stdout, verb)

    def test_a_question_without_a_question_mark_is_flagged_as_maybe_a_note(self):
        """The mirror of the `note`-should-have-been-`answer` warning, and the
        one that was missing. On 2026-08-04 EIGHT status reports stood open as
        questions at once — every one substantively handled, every one still
        shown by every reader, because a report written with `ask` waits for an
        answer nobody will write while both sides consider it settled."""
        run = self.run_cli(["ask", "Branch steht, rebaset und gepusht",
                            "--from", "t"])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("note", run.stderr)
        self.assertIn("kein Fragezeichen", run.stderr)
        self.assertIn("x", "x")

    def test_a_real_question_is_not_flagged(self):
        run = self.run_cli(["ask", "which trap does it hook?", "--from", "t"])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn("kein Fragezeichen", run.stderr)

    def test_the_flagged_question_is_still_written(self):
        """A hint, never a refusal: a question without a question mark is still
        a question ("name the trap it hooks"). A false positive must cost a
        line of stderr and nothing else."""
        self.run_cli(["ask", "no question mark here", "--from", "t"])
        self.assertIn("no question mark here",
                      open(self.path, encoding="utf-8").read())

    def test_a_long_argv_text_is_mentioned_but_still_written(self):
        """A hint, not a refusal: a long argv text may be perfectly intact, and
        refusing it would break every caller that predates this. But the two
        constructs that eat text leave no trace once they have, so the only
        honest moment to raise it is before the NEXT long one."""
        run = self.run_cli(["note", "x" * 500, "--from", "t"])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("<<'EOF'", run.stderr)
        self.assertIn("x" * 500, open(self.path, encoding="utf-8").read())

    def test_a_long_stdin_text_does_not_trigger_the_argv_warning(self):
        """A regression from the fix above: reading stdin early cleared the
        `--stdin` flag, so the "the shell saw every character" hint fired on
        text the shell never saw. A warning that cries wolf teaches people to
        ignore the one that matters."""
        run = self.run_cli(["note", "--stdin", "--from", "t"], stdin="x" * 600)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn("Kommandozeile", run.stderr)

    def test_a_long_argv_text_still_does(self):
        run = self.run_cli(["note", "y" * 600, "--from", "t"])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Kommandozeile", run.stderr)


class NothingIsLostQuietly(unittest.TestCase):
    """A channel may fail to read a line. It may not fail SILENTLY.

    Measured 2026-08-03: the other session wrote three notes whose leading
    timestamp was missing. `parse_note` returned None for each, `all_notes`
    filtered them away, and so `list`, the session brief and the watcher all
    said nothing. One of the three asked for a technical review and stated that
    its author was HOLDING ITS WORK until the answer arrived. The answer was
    never given, because this side was never shown the question. It surfaced
    only because the human pasted the other session's transcript and asked
    whether it was true.

    The same failure struck in the other direction within the hour: `answer`
    was given a target that named no question, so it wrote a full review that
    closed nothing, was addressed to nobody, and that `list` does not print.
    """

    # Verbatim from the channel file, line 69 — the shape that was lost.
    LOST = ("from=apfelpilot-live to=sess-9e5cc132 re=note "
            "FERTIGSTELLUNG START: bridge_client.dialog_tree() gegen DLGTREE")

    def _channel(self, *lines):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(prefix="ab-notes-"), "notes.log")
        with open(path, "w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line + "\n")
        return path

    def test_a_line_without_a_timestamp_is_reported_not_dropped(self):
        good = notes.format_note("2026-08-02T22:35:47.877", "apfelpilot-live",
                                 "all", notes.NOTE_MARKER, "MESH bestaetigt")
        lines = [good + "\n", self.LOST + "\n"]
        self.assertEqual(len(notes.all_notes(lines)), 1)
        lost = notes.unreadable(lines)
        self.assertEqual(len(lost), 1)
        self.assertEqual(lost[0]["lineno"], 2)
        self.assertIn("FERTIGSTELLUNG", lost[0]["raw"])

    def test_a_well_formed_channel_reports_nothing_lost(self):
        """The check has to be quiet when there is nothing wrong, or it is a
        warning nobody reads. Blank lines are not losses either."""
        good = notes.format_note("2026-08-02T22:35:47.877", "a", "all",
                                 notes.NOTE_MARKER, "fine")
        self.assertEqual(notes.unreadable([good + "\n", "\n", "   \n"]), [])

    def test_the_brief_puts_a_lost_line_above_the_open_questions(self):
        """Mail that was sent and never arrived outranks mail that arrived and
        is merely unanswered."""
        path = self._channel(
            notes.format_note("2026-08-03T06:00:00.000", "other", "all", None,
                              "an open question"),
            self.LOST)
        old, notes.NOTES = notes.NOTES, path
        oldwho, notes.WHO = notes.WHO, "me"
        try:
            out = session_brief.note_lines()
        finally:
            notes.NOTES, notes.WHO = old, oldwho
        self.assertTrue(any("UNREADABLE" in l for l in out), out)
        first_bad = next(i for i, l in enumerate(out) if "UNREADABLE" in l)
        first_open = next(i for i, l in enumerate(out) if "open questions" in l)
        self.assertLess(first_bad, first_open, out)

    def test_the_watcher_wakes_for_a_lost_line_it_did_not_start_with(self):
        """The watcher was one of the three readers that stayed quiet."""
        import notes_watch
        good = notes.format_note("2026-08-03T06:00:00.000", "other", "all",
                                 notes.NOTE_MARKER, "hello")
        started_with = [good + "\n"]
        self.assertEqual(notes_watch.lost_since(started_with, 1), [])
        arrived = started_with + [self.LOST + "\n"]
        self.assertEqual(len(notes_watch.lost_since(arrived, 1)), 1)

    def test_a_lost_line_that_predates_the_watcher_does_not_wake_it(self):
        """Otherwise every turn of every session re-announces the same old
        breakage, and the wake-up gets switched off within a day."""
        import notes_watch
        lines = [self.LOST + "\n"]
        self.assertEqual(notes_watch.lost_since(lines, len(lines)), [])


class AnAnswerReachesSomebody(unittest.TestCase):

    def _run(self, argv, path):
        """notes.main() with the channel pointed at a temp file."""
        import contextlib
        import io
        old, notes.NOTES = notes.NOTES, path
        argv_old, sys.argv = sys.argv, ["notes.py"] + argv
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = notes.main()
        except SystemExit as exc:            # argparse
            code = exc.code
        finally:
            notes.NOTES, sys.argv = old, argv_old
        with open(path, encoding="utf-8") as handle:
            return code, err.getvalue(), handle.readlines()

    def test_answering_a_timestamp_that_names_no_question_writes_nothing(self):
        """`answer konsultation "…"` wrote a full technical review that closed
        nothing and reached nobody. Refusing costs one retry; accepting costs
        the message."""
        import tempfile
        path = os.path.join(tempfile.mkdtemp(prefix="ab-notes-"), "notes.log")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(notes.format_note("2026-08-03T07:08:24.547", "other",
                                           "me", None, "a real question") + "\n")
        code, err, lines = self._run(
            ["answer", "konsultation", "the review", "--from", "me"], path)
        self.assertEqual(code, 2)
        self.assertEqual(len(lines), 1, "the answer must NOT have been written")
        self.assertIn("konsultation", err)
        self.assertIn("2026-08-03T07:08:24.547", err, "it must name what IS open")

    def test_an_answer_is_addressed_to_whoever_asked(self):
        """`re=` alone routed it; an explicit recipient survives more."""
        import tempfile
        path = os.path.join(tempfile.mkdtemp(prefix="ab-notes-"), "notes.log")
        asked = "2026-08-03T07:08:24.547"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(notes.format_note(asked, "apfelpilot-live", "me",
                                           None, "a real question") + "\n")
        code, _, lines = self._run(["answer", asked, "measured", "--from", "me"], path)
        self.assertEqual(code, 0)
        answer = notes.parse_note(lines[-1])
        self.assertEqual(answer["kind"], "answer")
        self.assertEqual(answer["re"], asked)
        self.assertEqual(answer["to"], "apfelpilot-live")
        self.assertEqual(notes.open_notes(lines), [], "the question must close")


if __name__ == "__main__":
    unittest.main()


class ALostNoteCannotBeIgnored(unittest.TestCase):
    """`append` raises, because a return value can be ignored by omission.

    On 2026-08-04 the parallel session printed "note appended OK" twice without
    looking at what `append` returned. Both notes were lost. A third had already
    failed VISIBLY with "could not write / exit 1" — so the mechanism worked and
    the reporting did not, and the two silent losses stayed invisible for over
    an hour while both sides believed the channel was quiet.

    Confirmed from the other end before the sender said anything: the channel
    file's last line was the OTHER session's, and `unreadable()` found ZERO bad
    lines — so the notes were not mangled in transit, they were never written.
    Nothing downstream could have detected that, which is the argument for
    making it undetectable-proof upstream.
    """

    def setUp(self):
        self.dead = "/nonexistent-dir-for-tests/notes.log"

    def test_a_failed_local_write_raises(self):
        with self.assertRaises(notes.ChannelWriteError):
            notes.append("x", self.dead)

    def test_the_message_says_the_note_is_LOST_not_that_a_call_failed(self):
        """"could not write" reads like a retryable hiccup. The consequence is
        that a person will never see the text — which is what must be said."""
        try:
            notes.append("x", self.dead)
            self.fail("expected ChannelWriteError")
        except notes.ChannelWriteError as exc:
            self.assertIn("did NOT reach", str(exc))
            self.assertIn(self.dead, str(exc))

    def test_a_deliberate_caller_can_still_opt_out(self):
        """Requested by the session it happened to: a caller who has genuinely
        decided not to care keeps the boolean — but has to SAY so, so that
        going back to silence is a visible act in a diff."""
        self.assertFalse(notes.append("x", self.dead, raise_on_fail=False))

    def test_a_failed_ssh_write_raises_too(self):
        """The remote path is the one that actually lost the notes: the sender
        was on another machine writing over ssh."""
        with self.assertRaises(notes.ChannelWriteError):
            notes.append("x", "jetson:/tmp/notes.log",
                         run=lambda *a, **k: (False, "ssh: connect refused"))

    def test_the_ssh_reason_survives_into_the_message(self):
        """Without it the reader learns that something failed and not what, on
        the exact path where the cause is remote and unguessable."""
        try:
            notes.append("x", "jetson:/tmp/notes.log",
                         run=lambda *a, **k: (False, "Permission denied"))
            self.fail("expected ChannelWriteError")
        except notes.ChannelWriteError as exc:
            self.assertIn("Permission denied", str(exc))

    def test_a_successful_write_still_just_returns_true(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(prefix="ab-append-"), "notes.log")
        self.assertTrue(notes.append("2026-01-01T00:00:00.000 from=a to=b re=note x", path))

    def test_rotation_reports_a_missing_marker_instead_of_raising_past_it(self):
        """The move has already happened by then, so a failed marker is not a
        failed rotation — but it is not nothing either, and this call ignored
        its result entirely until now."""
        import tempfile
        d = tempfile.mkdtemp(prefix="ab-rot-")
        path = os.path.join(d, "notes.log")
        notes.append(notes.format_note("2026-01-01T00:00:00.000", "a", "all",
                                       notes.NOTE_MARKER, "x"), path)
        ok, msg = notes.rotate("2026-01-02T00:00:00.000", path,
                               run=lambda *a, **k: (False, "nope"))
        self.assertTrue(ok, msg)


class TheChannelSurvivesARestart(unittest.TestCase):
    """It lived in /tmp, and on 2026-08-05 a reboot took 130 notes.

    Two days of correspondence — both sides' measurements, the corrections, the
    findings — gone, and nothing said a word. Every guard this tool has was one
    level too high to help: `rotate` archives rather than deletes, `find`
    searches the archives, an open question blocks rotation. Then the operating
    system removed the file underneath all of it.

    What saved the content was the separation the process rests on: the channel
    is CORRESPONDENCE, not memory. The findings were already in the operating
    notes, the ledger and the pull requests, so a data loss became an
    inconvenience. That is an argument for keeping the separation, not for
    leaving the mailbox where it gets emptied.
    """

    def test_the_default_is_not_in_tmp(self):
        self.assertNotIn("/tmp", notes._DEFAULT_NOTES)
        self.assertTrue(notes._DEFAULT_NOTES.endswith("notes.log"))

    def test_the_directory_is_created_on_demand(self):
        import tempfile
        base = os.path.join(tempfile.mkdtemp(prefix="ab-chan-"), "deep", "notes.log")
        notes.ensure_channel_dir(base)
        self.assertTrue(os.path.isdir(os.path.dirname(base)))

    def test_a_remote_spec_is_left_alone(self):
        """`user@host:/path` is somebody else's filesystem. Creating a local
        directory called `jetson` would be a silent second channel that neither
        side reads."""
        spec = "pit@jetson:/home/pit/notes.log"
        self.assertEqual(notes.ensure_channel_dir(spec), spec)
        self.assertFalse(os.path.exists("pit@jetson"))

    def test_a_legacy_tmp_channel_is_carried_over_once(self):
        """Migration, not a flag day: the two sides are on different machines and
        cannot be updated in the same breath, so whichever moves first must not
        lose what the other has already written."""
        import tempfile
        legacy = os.path.join(tempfile.mkdtemp(prefix="ab-legacy-"), "old.log")
        with open(legacy, "w", encoding="utf-8") as fh:
            fh.write(notes.format_note("2026-01-01T00:00:00.000", "a", "all",
                                       notes.NOTE_MARKER, "vom alten Kanal") + "\n")
        new = os.path.join(tempfile.mkdtemp(prefix="ab-new-"), "notes.log")
        old_legacy, notes._LEGACY_NOTES = notes._LEGACY_NOTES, legacy
        old_default, notes._DEFAULT_NOTES = notes._DEFAULT_NOTES, new
        try:
            notes.ensure_channel_dir(new)
            self.assertIn("vom alten Kanal", open(new, encoding="utf-8").read())
            # ...and only once: a second call must not append it again.
            notes.ensure_channel_dir(new)
            self.assertEqual(open(new, encoding="utf-8").read().count("alten Kanal"), 1)
        finally:
            notes._LEGACY_NOTES = old_legacy
            notes._DEFAULT_NOTES = old_default

    def test_a_custom_path_gets_no_uninvited_migration(self):
        """The first version migrated into ANY new path and pulled the live
        channel into every test's temp file. A caller who names a path wants
        that channel, not a copy of another one."""
        import tempfile
        legacy = os.path.join(tempfile.mkdtemp(prefix="ab-legacy2-"), "old.log")
        with open(legacy, "w", encoding="utf-8") as fh:
            fh.write("2026-01-01T00:00:00.000 from=a to=all re=note alt\n")
        mine = os.path.join(tempfile.mkdtemp(prefix="ab-mine-"), "notes.log")
        old_legacy, notes._LEGACY_NOTES = notes._LEGACY_NOTES, legacy
        try:
            notes.ensure_channel_dir(mine)
            self.assertFalse(os.path.exists(mine), "an uninvited copy appeared")
        finally:
            notes._LEGACY_NOTES = old_legacy

    def test_an_append_creates_the_directory_it_needs(self):
        """The failure this closes is not hypothetical: `append` now RAISES, so a
        missing directory would turn every note into a visible crash instead of
        a working channel."""
        import tempfile
        path = os.path.join(tempfile.mkdtemp(prefix="ab-mk-"), "sub", "notes.log")
        old_legacy, notes._LEGACY_NOTES = notes._LEGACY_NOTES, "/nonexistent-legacy"
        try:
            self.assertTrue(notes.append(
                notes.format_note("2026-01-01T00:00:00.000", "a", "all",
                                  notes.NOTE_MARKER, "x"), path))
        finally:
            notes._LEGACY_NOTES = old_legacy


class TwoCopiesOfOneFile(unittest.TestCase):
    """`repariert` on one side is not `repariert` on the other.

    Both sessions run notes.py — one from the repo, one from a copy on the
    machine at the far end of the ssh channel. On 2026-08-05 a fix landed in the
    repo and the other side kept failing **with a word-for-word identical error
    message**. Nothing in the setup said which copy was speaking, so the obvious
    reading — "the fix does not work" — was available and wrong.

    The session it happened to had written, in the same message, that two copies
    of one thing are one that will eventually diverge — and had not applied it to
    itself. That is why this is a command rather than a habit: run it on both
    sides, compare one line.

    Content, not a version number: a number is a claim somebody must remember to
    update, and the whole point is to stop trusting claims about copies.
    """

    def test_this_copy_reports_the_fixes_it_has(self):
        fp = notes.fingerprint()
        self.assertIn("ssh-devnull", fp["has"])
        self.assertIn("stdin-first", fp["has"])
        self.assertEqual(fp["missing"], [])

    def test_a_stale_copy_names_exactly_what_it_lacks(self):
        """Verified against the real predecessor: it reported ssh-devnull and
        stdin-first missing — the two fixes the other side was running without."""
        import tempfile
        src = open(notes.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
        stale = src.replace("subprocess.DEVNULL", "None")
        path = os.path.join(tempfile.mkdtemp(prefix="ab-stale-"), "notes.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(stale)
        fp = notes.fingerprint(path)
        self.assertIn("ssh-devnull", fp["missing"])
        self.assertNotIn("ssh-devnull", fp["has"])

    def test_the_hash_differs_when_the_file_does(self):
        """A capability list can only see the fixes somebody thought to mark.
        The hash catches the rest — including a copy that is merely OLDER."""
        import tempfile
        src = open(notes.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
        path = os.path.join(tempfile.mkdtemp(prefix="ab-hash-"), "notes.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src + "\n# a harmless edit\n")
        self.assertNotEqual(notes.fingerprint(path)["sha"], notes.fingerprint()["sha"])

    def test_the_command_is_reachable(self):
        import subprocess
        run = subprocess.run([sys.executable, notes.__file__, "version"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("notes.py", run.stdout)



class SshMustNotDrinkTheCallersStdin(unittest.TestCase):
    """`answer --stdin` lost its text, and the cause was one line away.

    Found by the session on the other end, 2026-08-05, with 6905 bytes at stdin
    and the message "stdin was empty; nothing was written" — twice, once through
    a shell redirect and once through subprocess `input=`. **ssh reads stdin by
    default.** `answer` looks its recipient up IN THE CHANNEL before parsing the
    text; on a host:path channel that lookup runs ssh, and ssh emptied stdin on
    the way past. `note --stdin` was unaffected because it reads the text first
    — both were run side by side in one setup and only `answer` lost its payload.

    The bitter part: `--stdin` exists precisely so that text stops disappearing
    silently, and the one path the shell cannot touch was the one ssh drank.
    A guard is only as good as the layer it guards.
    """

    def test_a_read_gives_ssh_no_stdin_to_drink(self):
        seen = {}

        def fake_run(argv, **kw):
            seen.update(kw)
            class R:
                returncode, stdout = 0, ""
            return R()
        real, notes.subprocess.run = notes.subprocess.run, fake_run
        try:
            notes._ssh_run("host", "cat /x")
        finally:
            notes.subprocess.run = real
        self.assertIs(seen.get("stdin"), notes.subprocess.DEVNULL,
                      "ssh was left free to read the caller's stdin")

    def test_a_write_still_gets_its_payload(self):
        """The fix must not close the channel it was built for."""
        seen = {}

        def fake_run(argv, **kw):
            seen.update(kw)
            class R:
                returncode, stdout = 0, ""
            return R()
        real, notes.subprocess.run = notes.subprocess.run, fake_run
        try:
            notes._ssh_run("host", "cat > /x", stdin="the note\n")
        finally:
            notes.subprocess.run = real
        self.assertEqual(seen.get("input"), "the note\n")
        self.assertIsNone(seen.get("stdin"), "input= and a real stdin= collide")

    def test_the_text_is_taken_before_any_channel_access(self):
        """Removes the SHAPE as well as the cause: any future channel read
        placed before the text parse would drink stdin the same way."""
        src = open(notes.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
        body = src[src.index("def main("):]
        take = body.index('piped = None if sys.stdin.isatty()')
        lookup = body.index("known = {n[\"ts\"]: n for n in all_notes(read())")
        self.assertLess(take, lookup,
                        "stdin must be read before the recipient is looked up")
