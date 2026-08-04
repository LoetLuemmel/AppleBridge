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
            self.assertFalse(notes.append("x"))
        finally:
            notes.NOTES = old


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
