#!/usr/bin/env python3
"""A question-and-answer channel between agent sessions — one append-only file.

Why this is a file and not a tool. Two sessions on this machine already share a
filesystem, so a file is visible to both IMMEDIATELY: no MCP server restart, no
tool-count sweep through four documents, no protocol change. An MCP tool would
be first-class and would also be invisible to the other side until its server
restarts, which is exactly the delay a channel must not have.

What it cannot do, stated up front because no amount of building changes it: a
Claude Code session has no event loop. It acts when its user prompts it and at
no other time. So a question can be deposited, but nobody is woken by it — the
answer arrives at the other session's next turn, not on demand. This channel
removes the human as a RELAY; it does not remove them as the fastest path when
an answer is needed now.

Delivery therefore rides the one push that already exists: `session_brief.py`
prints the open notes at every session start.

"Open" means: a question nobody has answered. An answer references the question
by its timestamp (`re=`), and answering makes the question drop off the brief.
No read-state is tracked, deliberately — a "seen" flag would claim to know
something about who read what, and this project has spent a day on a check that
claimed more than it could see.

    notes.py ask    "does the DPAT block carry a non-zero Real field?"
    notes.py answer 2026-08-02T17:40:11 "no — it is zero when adopted"
    notes.py list
"""
import argparse
import datetime
import os
import shlex
import subprocess
import sys

NOTES = os.environ.get("APPLEBRIDGE_NOTES", "/tmp/applebridge_notes.log")

# Remote channel support (Jetson-side). The channel file lives on the Mac; a
# session driving the Mac by ssh from another host points APPLEBRIDGE_NOTES at
# `user@host:/path` and this module's TWO i/o points -- read() and append() --
# transparently go over ssh. Everything downstream (list, brief, the watcher)
# is unchanged. The price, taken deliberately: a network dependency in a tool
# that could not fail before, so every ssh error degrades to "nothing" (read ->
# [], append -> False) rather than raising -- a channel that crashes the hook
# is worse than one that is briefly silent. Poll slower when remote (the
# watcher sets APPLEBRIDGE_WATCH_POLL=20).
_SSH_KEY = os.environ.get("APPLEBRIDGE_SSH_KEY", "")
_SSH_TIMEOUT = float(os.environ.get("APPLEBRIDGE_SSH_TIMEOUT", "20"))


def _remote(spec):
    """('user@host', '/path') if spec is an ssh target, else None.

    A local path (/tmp/...) has no colon before its first slash; an ssh target
    is `[user@]host:/path`. The host part must contain no slash and the path
    must be ABSOLUTE, so none of these are mistaken for a remote target and none
    triggers an ssh call: a plain local path (no colon at all), a colon inside a
    filename (`/tmp/a:b` -> host `/tmp/a` has a slash), a Windows-style drive
    (`C:\\x` -> path `\\x` is not absolute-posix). Only `host:/abs/path` matches."""
    if not spec or ":" not in spec:
        return None
    host, _, path = spec.partition(":")
    if not host or "/" in host or not path.startswith("/"):
        return None
    return host, path


def _ssh_run(host, remote_cmd, stdin=None):
    """Default channel executor: run one ssh command, return (ok, stdout).

    Never raises -- any failure (no network, bad key, timeout, an undecodable
    byte) is (False, ''). read()/append() take this as an injectable `run` so
    the remote path is testable without a network, the same shape as
    run_step(send, ...) in host/mpw.py: the executor is a parameter, and a test
    passes a fake.

    `encoding='utf-8'` is NOT optional: text=True alone decodes with the
    locale's preferred encoding, and the local read/append fix utf-8 explicitly
    (see read()/append()), so a bare text=True would read the same channel file
    as utf-8 locally and as `whatever LANG says` remotely. On a headless Jetson
    LANG is often C/POSIX -> ASCII, and one em-dash / umlaut / a stray ≥ then
    raises UnicodeDecodeError -- which is neither OSError nor SubprocessError, so
    a narrow except would let it out of read() and tear down the watcher, the
    brief and the Stop hook. errors='replace' + a broad except keep the promise
    that a channel can only ever be briefly silent, never crash the hook."""
    argv = ["ssh"]
    if _SSH_KEY:
        argv += ["-i", _SSH_KEY]
    argv += ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, remote_cmd]
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=_SSH_TIMEOUT)
        return p.returncode == 0, p.stdout
    except Exception:          # a channel that kills the hook is worse than one
        return False, ""       # briefly silent -- so nothing here ever propagates


# The remote append should be as atomic as the local one. The local path opens
# O_APPEND and writes once; `cat >>` over ssh could split a large payload across
# write()s, so instead run python on the far side doing the SAME open("a").write
# -- the identical mechanism to the local case, and no `flock` (which macOS
# lacks). Honest bound: this is one write() only up to the io buffer; a payload
# past it can still split on either side. A channel note is far below that, so
# the guarantee holds for the actual traffic -- but the promise is "same as
# local", not "atomic at any size".
_REMOTE_APPEND = ("/usr/bin/python3 -c "
                  "'import sys; open(sys.argv[1],\"a\").write(sys.stdin.read())' ")

def _default_who():
    """This session's name, without anybody having to configure one.

    `APPLEBRIDGE_WHO` started out as a required setting, and requiring it was a
    mistake: the return path cannot address anything while both sides answer to
    the same name, so a channel silently routed nothing until a human
    remembered. Claude Code already exports `CLAUDE_CODE_SESSION_ID` into the
    environment of everything it runs, which is distinct per session and costs
    nothing — the identity was there the whole time.

    Shortened to eight characters: long enough to be unique in a file two
    sessions write to, short enough that a line stays readable. The env var
    remains an override, for when a name says more than an id.
    """
    named = os.environ.get("APPLEBRIDGE_WHO")
    if named:
        return named
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    return f"sess-{session[:8]}" if session else "agent"


WHO = _default_who()


# A note is ONE line — that is what makes the file greppable, append-safe and
# parseable by position, and it is not negotiable. But "one line" used to be
# achieved by `" ".join(text.split())`, which collapses every run of whitespace
# and therefore DESTROYS the author's paragraph breaks. For a one-sentence
# question that is invisible; for the five-paragraph reviews this channel
# actually carries it is a silent loss of structure, and the workaround it
# provoked was worse: an author types a literal `\n` into the shell string, it
# is not whitespace, so it survives — and arrives at the reader as two
# characters of noise in the middle of a sentence.
#
# So the line stays one line and the shape is kept as an ESCAPE: written
# escaped, displayed unescaped. `\` doubles on the way in, without which the
# mapping is not reversible — a note ABOUT the sequence (`grep for \n`) would
# come back to the reader as a line break, which is the same class of defect one
# level down.
#
# One asymmetry, deliberate and bounded: lines written BEFORE this carry an
# unescaped `\n`, so they now display as a real break. For those lines that is
# what the author meant (they typed it to get a paragraph), so the one direction
# this can be wrong in is the harmless one.
def escape_text(text):
    """Text as one line, with the author's line breaks preserved as `\\n`."""
    doubled = (text or "").replace("\\", "\\\\")
    # splitlines() also catches CR and CRLF, so nothing that would break the
    # line-per-note rule can reach the file by another spelling.
    return "\\n".join(" ".join(part.split()) for part in doubled.splitlines())


def unescape_text(text, inline=False):
    """The author's text back.

    `inline=True` renders a break as a space — for the session brief and the
    watcher, where a note appears as a truncated one-line preview and a real
    newline would break the surrounding layout.

    Scanned left to right rather than by two `str.replace` calls: replacing
    `\\\\`->`\\` first and `\\n`->newline second turns the escaped sequence
    `\\\\n` into a line break, which is precisely the case the doubling exists
    to protect.
    """
    break_as = " " if inline else "\n"
    out, i, text = [], 0, text or ""
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in "n\\":
            out.append(break_as if text[i + 1] == "n" else "\\")
            i += 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def format_note(stamp, who, to, answering, text):
    """One line: the fields first so they can be grepped, the text last."""
    return (f"{stamp} from={who or '?'} to={to or 'all'} "
            f"re={answering or '-'} {escape_text(text)}")


# The `re=` field carries the kind as well as the target, so the line format
# did not have to change and every line written before this stays parseable:
#   re=-      a question — stays open until answered
#   re=note   a statement — nothing to answer, so it is never open
#   re=<ts>   an answer — closes that question
#
# The third kind exists because its absence showed up in twenty minutes of real
# use: the other session sent a status report, had nothing to point `re=` at,
# and it registered as a question that would have stayed open forever. A format
# with only ask and answer forces every message into one of the two.
NOTE_MARKER = "note"


def parse_note(line):
    """-> dict with a `kind`, or None for a line this channel did not write."""
    parts = (line or "").strip().split(" ", 4)
    if len(parts) < 5:
        return None
    stamp, who, to, answering, text = parts
    fields = {}
    for token in (who, to, answering):
        key, _, value = token.partition("=")
        if not _:
            return None
        fields[key] = value
    if set(fields) != {"from", "to", "re"}:
        return None
    marker = fields["re"]
    if marker == "-":
        kind, target = "question", None
    elif marker == NOTE_MARKER:
        kind, target = "note", None
    else:
        kind, target = "answer", marker
    return {"ts": stamp, "from": fields["from"], "to": fields["to"],
            "re": target, "kind": kind, "text": text}


def all_notes(lines):
    """Every line this channel wrote, in order; foreign lines dropped."""
    return [n for n in (parse_note(l) for l in lines) if n]


def unreadable(lines):
    """Lines this channel could not parse — SURFACED, never dropped.

    `all_notes` filters unparseable lines away silently. That is right for a
    stray line that wandered into the file and catastrophically wrong for what
    actually happened: the other session wrote three notes whose leading
    timestamp was missing, so `parse_note` returned None for each and every one
    fell out of `list`, out of the inbox and out of the watcher. Nobody was
    told. The sender believed it had asked for a review, said in the note that
    it was HOLDING ITS WORK until the answer came, and waited on a question this
    side was never shown. It surfaced only because the human happened to paste
    the other session's transcript and ask "is this true?".

    A channel is allowed to fail to understand a line. It is not allowed to
    fail QUIETLY — the whole point of the thing is delivery. So every reader
    (`list`, the session brief, the watcher) reports these instead of dropping
    them, and the raw text is kept so the content is recoverable by hand.
    """
    out = []
    for number, line in enumerate(lines, 1):
        if line.strip() and parse_note(line) is None:
            out.append({"lineno": number, "raw": line.strip()})
    return out


def open_notes(lines):
    """Questions nobody has answered yet, oldest first.

    A question is closed by any note whose `re=` names its timestamp. Nothing
    tracks who has READ what: answering is the only signal, and it is one the
    file can actually carry.
    """
    notes = all_notes(lines)
    answered = {n["re"] for n in notes if n["re"]}
    return [n for n in notes
            if n["kind"] == "question" and n["ts"] not in answered]


def inbox_for(lines, who):
    """What this session should be told about — the RETURN PATH.

    `open_notes` was the whole delivery rule at first, and it carries questions
    only: an answer sets `re=`, which closes the question and removes it from
    the list. The asker therefore learned nothing on either surface — the
    channel delivered outward and was silent coming back. Found by asking
    "did the other side get a trigger for the answer?" and reading the code.

    Three things land here: an answer to a question this session asked, an
    answer sent `to=` it, and a statement addressed to it or broadcast. Its own
    messages never come back — a channel that echoes you is a channel you stop
    reading.

    All of it needs `APPLEBRIDGE_WHO` set per session: with the default both
    sides are called "agent", so nothing can be addressed. That is a
    precondition, not a detail, so `session_brief` says so rather than quietly
    routing nothing.
    """
    notes = all_notes(lines)
    asked_by_me = {n["ts"] for n in notes if n["from"] == who}
    out = []
    for note in notes:
        if note["from"] == who:
            continue
        if note["kind"] == "answer" and (note["to"] == who or note["re"] in asked_by_me):
            out.append(note)
        elif note["kind"] == "note" and note["to"] in (who, "all"):
            out.append(note)
    return out


def recent(notes_, now, seconds):
    """Of the open notes, those deposited within the last `seconds`.

    The PostToolUse hook fires on EVERY tool call, so announcing all open notes
    there would repeat the same question after every step until somebody
    answered it — noise that gets a hook switched off. A time window is the
    stateless way to say it once and then be quiet: the note announces itself
    for a while, and the session brief still lists every open one at the next
    start.

    Deliberately not a "seen" marker. One marker cannot mean two sessions, and
    a shared one would silence a note for the side that never saw it.
    """
    if seconds is None:
        return list(notes_)
    cutoff = (now - datetime.timedelta(seconds=seconds)).isoformat(timespec="milliseconds")
    return [n for n in notes_ if n["ts"] >= cutoff]


def crossed(lines, who):
    """What arrived for `who` since `who` last wrote — the CROSSING check.

    Measured 2026-08-04: one side answered two questions at 10:45:41 and the
    other re-asked the same two at 10:46:29, 48 seconds later, because its turn
    had already begun. Nothing can deliver into a turn already in flight — that
    part is inherent to a channel between sessions with no event loop.

    But at the moment it wrote, the answer was ALREADY IN THE FILE, and the tool
    that wrote said nothing about it. This closes that half: posting reads the
    channel anyway (`answer` validates its timestamp against it), so it can also
    report what came in meanwhile. The writer learns it in its OWN turn, with no
    watcher and no waiting.

    "Since I last wrote" rather than "since I last read", because reading is not
    observable here and never will be — the same reason `open_notes` tracks
    answers instead of a `seen` flag. A session that has never written gets
    nothing: the session brief already covers the cold start, and dumping the
    whole inbox on a first post would be noise where this is meant to be a
    signal.
    """
    mine = [n["ts"] for n in all_notes(lines) if n["from"] == who]
    if not mine:
        return []
    return [n for n in inbox_for(lines, who) if n["ts"] > max(mine)]


def identity_warnings(lines, who):
    """Reasons this name may not be reachable — measured, not guessed.

    Found 2026-08-04 on the other machine and it had been true for two days: its
    Stop-hook watcher ran as `apfelpilot-live` (set in the hook) while its
    session posted as `sess-64c74122` (auto-derived from the session id). Two
    identities for one participant, so `inbox_for` for the watching name matched
    nothing addressed to the posting name — a `note` counts only on `to == who`
    or `to == all`, and an `answer` only on `to == who` or a `re=` pointing at a
    question that name asked. Measured on the live channel: inbox 3 versus 15,
    and **zero** wake reasons in an hour against ten.

    Neither half can see the other, so neither can report it. This is the check
    that can: it reads the channel and asks what is true of THIS name.
    """
    everyone = all_notes(lines)
    if not everyone:
        # No conversation yet, so nothing about reachability is actionable —
        # and a brief that complains where there is no channel at all is the
        # kind of noise that gets a brief switched off. Silence on an absent
        # channel is a rule here, not an oversight.
        return []
    out = []
    if who == "agent":
        out.append("no session identity (neither APPLEBRIDGE_WHO nor "
                   "CLAUDE_CODE_SESSION_ID) — nothing can be addressed back")
        return out
    if who.startswith("sess-"):
        out.append(f"'{who}' is derived from the session id, so it CHANGES on "
                   "restart: whoever addresses it today addresses nobody "
                   "tomorrow. Set APPLEBRIDGE_WHO for a name that lasts.")
    if not any(n["from"] == who for n in everyone):
        out.append(f"'{who}' has never written to this channel, so nothing can "
                   "reach it by `re=` and only messages explicitly addressed to "
                   "it — or broadcast — arrive. If a watcher runs under this "
                   "name while the session posts under another, the wake path "
                   "is blind (measured 2026-08-04).")
    return out


def actionable(lines, who):
    """What this session still has to DO — the default view of `list`.

    Measured 2026-08-04, and the numbers are the argument: 70 notes, 140 000
    characters in the file, ZERO open questions — and `list` printed 15 000
    characters, every one of them already handled. On the other machine the
    inbox was 67 000. Both of us had stopped reading `list` and started
    grepping it, which is the point at which a delivery mechanism has failed:
    it was still delivering, and nobody was receiving.

    So the default answers "what is outstanding", not "what was ever said":
    open questions, plus whatever arrived since this session last wrote. The
    full history is still there — `--all` prints it, and the FILE never loses
    anything. The noise was in the view, not in the channel.
    """
    # `emitted`, not `seen`: a ratchet in tests/test_notes.py forbids the word
    # in this file, because a "seen" flag would claim to know who READ what —
    # which nothing here can observe. This is a dedup set for one print run and
    # has nothing to do with read state, but the guard cannot tell the two
    # apart, and it guards something worth more than my variable name.
    emitted = set()
    out = []
    for note in open_notes(lines) + crossed(lines, who):
        if note["ts"] not in emitted:
            emitted.add(note["ts"])
            out.append(note)
    return sorted(out, key=lambda n: n["ts"])


def preview(text, width=220):
    """One note, shortened, with the omission STATED rather than silent.

    A cut that does not announce itself is the same defect as a silent cap: the
    reader cannot tell a short note from a truncated one, so they cannot know
    whether they have the whole thing.
    """
    flat = unescape_text(text)
    if len(flat) <= width:
        return flat
    return flat[:width] + f"… [+{len(flat) - width} Zeichen — notes.py list --full]"


def read(path=None, run=None):
    """Lines of the channel. Local file, or over ssh when the spec is host:/path.
    `run` is the ssh executor (default _ssh_run); tests inject a fake."""
    spec = path or NOTES
    remote = _remote(spec)
    if remote:
        host, rpath = remote
        ok, out = (run or _ssh_run)(host, "cat " + shlex.quote(rpath))
        return out.splitlines(keepends=True) if ok else []
    try:
        with open(spec, encoding="utf-8") as handle:
            return handle.readlines()
    except OSError:
        return []


def append(line, path=None, run=None):
    """Append one line. Local O_APPEND, or the identical single-write over ssh.
    `run` is the ssh executor (default _ssh_run); tests inject a fake."""
    spec = path or NOTES
    remote = _remote(spec)
    if remote:
        host, rpath = remote
        ok, _ = (run or _ssh_run)(host, _REMOTE_APPEND + shlex.quote(rpath),
                                  stdin=line + "\n")
        return ok
    try:
        with open(spec, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return True
    except OSError:
        return False


def render(note, label):
    """A note as the author wrote it: header line, continuation lines indented.

    The indent is what keeps a multi-paragraph note from reading as several
    notes — without it, `list` on a channel carrying reviews is a wall of text
    in which the boundary between two senders is invisible.
    """
    head, *rest = unescape_text(note["text"]).split("\n")
    lines = [f"{label} {note['ts']}  from={note['from']}  {head}"]
    lines += [("    " + line) if line else "" for line in rest]
    return "\n".join(lines)


def rotate(stamp, path=None, run=None):
    """Archive the channel and start an empty one. -> (ok, message).

    Why not "keep the last N notes", which is the obvious idea and the dangerous
    one: three mechanisms here read the file as a WHOLE, and truncating it in
    place breaks all three silently.

      - `notes_watch.lost_since` uses the LINE NUMBER as its clock, because an
        unreadable line has no timestamp to compare. Renumbering the file makes
        a watcher's baseline point at the wrong place: it either reports lines
        that are not new, or misses ones that are.
      - `answer <ts>` validates against the questions still in the file. Drop an
        old question and answering it is refused — the answer would close
        nothing and reach nobody, which is exactly the defect that check exists
        to prevent.
      - `crossed` measures from this session's last own message. Cut it away and
        the crossing notice goes quiet.

    Rotation avoids all three by being a WHOLE-FILE event, not a partial edit:
    the old file is kept intact under a dated name, the new one starts with a
    marker note saying where it went. Nothing is lost; the live file is small
    again; and the marker is a normal parseable note, so no reader reports it as
    an unreadable line.

    Refused while any question is open. That is not politeness — an open
    question in the archive cannot be answered any more, and a channel that
    quietly makes a pending question unanswerable is worse than a big file.
    """
    spec = path or NOTES
    lines = read(spec, run)
    still_open = open_notes(lines)
    if still_open:
        return False, (f"{len(still_open)} offene Frage(n) — erst beantworten. "
                       "Eine archivierte Frage kann niemand mehr schliessen.")
    if not lines:
        return False, "der Kanal ist leer, nichts zu rotieren"

    count = len(all_notes(lines))
    remote = _remote(spec)
    archive = f"{remote[1] if remote else spec}.{stamp[:19].replace(':', '')}"
    if remote:
        host, rpath = remote
        ok, _ = (run or _ssh_run)(host, f"mv {shlex.quote(rpath)} {shlex.quote(archive)}")
        if not ok:
            return False, f"konnte nicht nach {archive} verschieben"
    else:
        try:
            os.rename(spec, archive)
        except OSError as exc:
            return False, f"konnte nicht nach {archive} verschieben: {exc}"

    marker = format_note(stamp, WHO, "all", NOTE_MARKER,
                         f"Kanal rotiert: {count} Notizen liegen in {archive}. "
                         "Nichts geloescht — die Historie ist dort vollstaendig.")
    append(marker, spec, run)
    return True, f"{count} Notizen nach {archive}, Kanal neu begonnen"


def archives(path=None):
    """The rotated files beside the live channel, oldest first.

    Only the LOCAL side: an archive is searched, not polled, and a session that
    reaches the channel over ssh can search the machine that holds it. Guessing
    remote filenames would be a second protocol for no gain.
    """
    spec = path or NOTES
    if _remote(spec):
        return []
    folder = os.path.dirname(spec) or "."
    base = os.path.basename(spec) + "."
    try:
        found = [os.path.join(folder, n) for n in os.listdir(folder)
                 if n.startswith(base)]
    except OSError:
        return []
    return sorted(found)


def find(needle, path=None, run=None):
    """Every note containing `needle`, live channel AND archives. -> [(src, note)].

    This is what makes rotation an ARCHIVE rather than a dump. Rotating without
    it would trade one problem for a worse one: today's noise is at least
    readable, whereas material moved to a dated file nobody can search is
    material effectively deleted — with the added harm that everyone believes
    it was kept.

    Case-insensitive, because the thing one remembers about a note six weeks on
    is a word, not its capitalisation.
    """
    want = (needle or "").lower()
    out = []
    for src in archives(path) + [path or NOTES]:
        for note in all_notes(read(src, run)):
            if want in unescape_text(note["text"]).lower():
                out.append((os.path.basename(src), note))
    return out


def _now():
    """Milliseconds, because the timestamp IS the identifier.

    At second resolution two notes deposited in the same second shared an id,
    and answering one closed both — measured on the first end-to-end run of
    this file. An identifier that collides is worse than none: it silently
    marks somebody else's question as handled.
    """
    return datetime.datetime.now().isoformat(timespec="milliseconds")


STDIN_HELP = ("read the text from stdin instead of the command line "
              "(use for anything long or technical)")

# The heredoc that is actually safe. The quotes around EOF are the whole point
# and are the part people leave off.
STDIN_IDIOM = """  notes.py {verb} --stdin <<'EOF'
  … your text, verbatim …
  EOF"""


def text_from_stdin_or_argv(args, verb):
    """The text to write, or (None, complaint) — never a silently wrong one.

    Why this exists. On 2026-08-04 both sessions lost text to the shell on the
    same day and neither noticed: one wrote `$0A1C` in double quotes and the
    shell substituted an empty variable; the other wrote a sentence containing
    `\x60nc\x60` and the shell EXECUTED it, deleting the subjects of two
    sentences. The channel reported zero unreadable lines both times, correctly
    — the damage happens before the tool is reached, so the tool cannot see it.
    It can only offer a path the shell does not touch.
    """
    from_stdin = getattr(args, "stdin", False)
    if from_stdin and args.text is not None:
        return None, ("--stdin and a text argument both given; "
                      "which one did you mean? Nothing was written.")
    if from_stdin:
        if sys.stdin.isatty():
            return None, ("--stdin with nothing piped in would hang waiting "
                          "for a terminal. Nothing was written.\n"
                          + STDIN_IDIOM.format(verb=verb))
        text = sys.stdin.read().rstrip("\n")
        if not text.strip():
            return None, "stdin was empty; nothing was written."
        return text, None
    if args.text is None:
        return None, ("no text given. For anything long or technical prefer "
                      "stdin, which the shell cannot touch:\n"
                      + STDIN_IDIOM.format(verb=verb))
    return args.text, None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="verb", required=True)

    q = sub.add_parser("ask", help="deposit a question (--to someone, or all)")
    q.add_argument("text", nargs="?")
    q.add_argument("--stdin", action="store_true", help=STDIN_HELP)
    q.add_argument("--to", default="all")
    q.add_argument("--from", dest="who", default=WHO)

    # No --to here, deliberately: an answer is addressed by the question it
    # names, not by a name. Said in the help because the alternative is
    # discovering it from argparse AFTER typing the answer, which is how this
    # line came to exist.
    a = sub.add_parser(
        "answer", help="answer a question by its timestamp (no --to needed: "
                       "the question's asker is the recipient)",
        description="Close a question and route the answer back to whoever "
                    "asked it. The recipient follows from the timestamp, so "
                    "there is no --to; use `note --to <who>` to tell somebody "
                    "something that answers nothing.")
    a.add_argument("ts")
    a.add_argument("text", nargs="?")
    a.add_argument("--stdin", action="store_true", help=STDIN_HELP)
    a.add_argument("--from", dest="who", default=WHO)
    # Accepted only so it can be REFUSED with a sentence. Plain argparse answers
    # `unrecognized arguments: --to x` plus a usage line, which is true and
    # useless: it says the option does not exist, not what to reach for instead.
    # Hidden from --help, because it is not an option — it is a better error.
    a.add_argument("--to", help=argparse.SUPPRESS)

    n = sub.add_parser("note", help="state something; nothing to answer "
                                    "(--to someone, or all)")
    n.add_argument("text", nargs="?")
    n.add_argument("--stdin", action="store_true", help=STDIN_HELP)
    n.add_argument("--to", default="all")
    n.add_argument("--from", dest="who", default=WHO)

    f = sub.add_parser("find", help="search the live channel AND the archives")
    f.add_argument("text")

    sub.add_parser("rotate", help="archive the channel and start a fresh one "
                                  "(refused while a question is open)")

    lst = sub.add_parser("list", help="what is outstanding (--all for everything)")
    lst.add_argument("--all", action="store_true",
                     help="the whole inbox, not only what is outstanding")
    lst.add_argument("--full", action="store_true",
                     help="do not shorten note texts")
    lst.add_argument("--since", type=int, default=None, metavar="SECONDS",
                     help="only those deposited within the last N seconds "
                          "(what the PostToolUse hook uses, so it announces a "
                          "note once instead of after every tool call)")
    args = parser.parse_args()

    if args.verb == "find":
        hits = find(args.text)
        for src, note in hits:
            print(f"{note['ts']}  from={note['from']}  [{src}]")
            print(f"    {preview(note['text'], 300)}")
        print(f"{len(hits)} Treffer in {len(archives()) + 1} Datei(en)")
        return 0

    if args.verb == "rotate":
        ok, msg = rotate(_now())
        print(msg, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 2

    if args.verb == "list":
        now = datetime.datetime.now()
        lines = read()
        # First, and unconditionally: a line nobody can read is the one thing
        # here that has already cost a delivery, so it is never below the fold
        # and never filtered by --since.
        broken = unreadable(lines)
        if broken:
            print(f"!! {len(broken)} UNREADABLE line(s) — written to the "
                  "channel, delivered to nobody:")
            for bad in broken[-5:]:
                print(f"   line {bad['lineno']}: {bad['raw'][:160]}")
            print("   format is:  <timestamp> from=X to=Y re=Z <text>   "
                  "— write with notes.py; do not hand-build the line")
        shown = (inbox_for(lines, WHO) + open_notes(lines)) if args.all \
            else actionable(lines, WHO)
        shown = recent(shown, now, args.since)
        emitted = set()
        for note in sorted(shown, key=lambda n: n["ts"]):
            if note["ts"] in emitted:
                continue
            emitted.add(note["ts"])
            text = unescape_text(note["text"]) if args.full else preview(note["text"])
            head, *rest = text.split("\n")
            print(f"{note['kind']} {note['ts']}  from={note['from']}  {head}")
            for line in rest:
                print(("    " + line) if line else "")
        if not shown:
            # Silence would be ambiguous: "nothing outstanding" and "the tool is
            # broken" must not look the same, which is the whole complaint this
            # view was built to answer.
            total = len(all_notes(lines))
            print(f"nichts offen — {total} Notizen im Kanal (notes.py list --all)")
        elif not args.all:
            hidden = len(inbox_for(lines, WHO)) - len(shown)
            if hidden > 0:
                print(f"({hidden} bereits erledigte Nachricht(en) verborgen — --all)")
        return 0

    if args.verb == "answer" and getattr(args, "to", None) is not None:
        print("`answer` takes no --to: the answer goes back to whoever asked "
              f"{args.ts}, which the timestamp already says.\n"
              "  To tell somebody something that answers nothing, use:  "
              f"notes.py note --to {args.to} \"…\"", file=sys.stderr)
        return 2

    recipient = getattr(args, "to", "all")
    if args.verb == "answer":
        # An answer is delivered by naming a question, so a name that matches
        # NO question is not a typo to write down — it is an answer that closes
        # nothing and lands in nobody's inbox. That happened here: `answer
        # konsultation "…"` wrote a full technical review that `list` never
        # showed and the recipient never received, while the question it meant
        # to close stayed open. Refuse instead, and say which timestamps exist.
        known = {n["ts"]: n for n in all_notes(read()) if n["kind"] == "question"}
        if args.ts not in known:
            print(f"no question has the timestamp {args.ts!r}, so this answer "
                  "would close nothing and reach nobody. Nothing was written.",
                  file=sys.stderr)
            still_open = [n for n in open_notes(read())][-5:]
            if still_open:
                print("open questions you could answer:", file=sys.stderr)
                for note in still_open:
                    print(f"  {note['ts']}  from={note['from']}  "
                          f"{unescape_text(note['text'], inline=True)[:70]}",
                          file=sys.stderr)
            else:
                print("no open questions; use `notes.py note` to say something "
                      "that answers nothing.", file=sys.stderr)
            return 2
        # Route it by name as well as by `re=`: the inbox rule that matches on
        # `re=` alone only works while the asker's own line is still in the
        # file, and an explicit recipient survives anything.
        recipient = known[args.ts]["from"]

    text, complaint = text_from_stdin_or_argv(args, args.verb)
    if complaint:
        print(complaint, file=sys.stderr)
        return 2
    if not getattr(args, "stdin", False) and len(text) > 400:
        # Not a refusal: a long argv text is legal and may be perfectly intact.
        # But every character of it passed through the shell, and the two
        # constructs that eat text leave NO trace once they have — so the only
        # honest moment to mention it is before the next long one.
        print(f">> {len(text)} Zeichen über die Kommandozeile — die Shell hat "
              "jedes davon gesehen. Für den nächsten langen Text:\n"
              + STDIN_IDIOM.format(verb=args.verb) + "\n", file=sys.stderr)

    # Read the channel BEFORE appending, so "since I last wrote" does not have to
    # exclude the line this call is about to add.
    waiting = crossed(read(), args.who)

    stamp = _now()
    answering = args.ts if args.verb == "answer" else (
        NOTE_MARKER if args.verb == "note" else None)
    line = format_note(stamp, args.who, recipient, answering, text)
    if not append(line):
        print(f"could not write {NOTES}", file=sys.stderr)
        return 1
    print(line)

    # A `note --to X` while X has an open question is almost always an `answer`
    # that forgot its timestamp — the question then stays open for ever while
    # both sides consider it handled. THREE stood open that way on 2026-08-04,
    # all substantively answered hours earlier, all still shown by every reader.
    if args.verb == "note" and recipient != "all":
        theirs = [n for n in open_notes(read()) if n["from"] == recipient]
        if theirs:
            print(f"\n>> {recipient} hat {len(theirs)} OFFENE Frage(n) an dich. "
                  "War das eine Antwort? Dann schliesst sie nur:", file=sys.stderr)
            for note in theirs[-2:]:
                print(f"   notes.py answer {note['ts']} \"…\"   "
                      f"({preview(note['text'], 70)})", file=sys.stderr)

    if waiting:
        # stderr, so anything parsing the written line on stdout is unaffected.
        # Loudest first when it is an ANSWER to something this session asked:
        # that is the case where writing again may mean re-asking what is
        # already answered, which is what this exists to stop.
        replies = [n for n in waiting if n["kind"] == "answer"]
        print(f"\n>> {len(waiting)} message(s) arrived since you last wrote"
              + (f", {len(replies)} of them ANSWERS to you" if replies else "")
              + " — you may have crossed:", file=sys.stderr)
        for note in waiting[-3:]:
            body = unescape_text(note["text"], inline=True)
            print(f"   [{note['ts']}] {note['from']} ({note['kind']}): "
                  f"{body[:220]}", file=sys.stderr)
        print("   read them in full with: notes.py list", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
