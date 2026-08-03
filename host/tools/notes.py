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


def format_note(stamp, who, to, answering, text):
    """One line: the fields first so they can be grepped, the text last."""
    flat = " ".join((text or "").split())
    return f"{stamp} from={who or '?'} to={to or 'all'} re={answering or '-'} {flat}"


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


def _now():
    """Milliseconds, because the timestamp IS the identifier.

    At second resolution two notes deposited in the same second shared an id,
    and answering one closed both — measured on the first end-to-end run of
    this file. An identifier that collides is worse than none: it silently
    marks somebody else's question as handled.
    """
    return datetime.datetime.now().isoformat(timespec="milliseconds")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="verb", required=True)

    q = sub.add_parser("ask", help="deposit a question (--to someone, or all)")
    q.add_argument("text")
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
    a.add_argument("text")
    a.add_argument("--from", dest="who", default=WHO)
    # Accepted only so it can be REFUSED with a sentence. Plain argparse answers
    # `unrecognized arguments: --to x` plus a usage line, which is true and
    # useless: it says the option does not exist, not what to reach for instead.
    # Hidden from --help, because it is not an option — it is a better error.
    a.add_argument("--to", help=argparse.SUPPRESS)

    n = sub.add_parser("note", help="state something; nothing to answer "
                                    "(--to someone, or all)")
    n.add_argument("text")
    n.add_argument("--to", default="all")
    n.add_argument("--from", dest="who", default=WHO)

    lst = sub.add_parser("list", help="open questions")
    lst.add_argument("--since", type=int, default=None, metavar="SECONDS",
                     help="only those deposited within the last N seconds "
                          "(what the PostToolUse hook uses, so it announces a "
                          "note once instead of after every tool call)")
    args = parser.parse_args()

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
        for note in recent(open_notes(lines), now, args.since):
            print(f"question {note['ts']}  from={note['from']}  {note['text']}")
        for note in recent(inbox_for(lines, WHO), now, args.since):
            print(f"{note['kind']} {note['ts']}  from={note['from']}  {note['text']}")
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
                          f"{note['text'][:70]}", file=sys.stderr)
            else:
                print("no open questions; use `notes.py note` to say something "
                      "that answers nothing.", file=sys.stderr)
            return 2
        # Route it by name as well as by `re=`: the inbox rule that matches on
        # `re=` alone only works while the asker's own line is still in the
        # file, and an explicit recipient survives anything.
        recipient = known[args.ts]["from"]

    stamp = _now()
    answering = args.ts if args.verb == "answer" else (
        NOTE_MARKER if args.verb == "note" else None)
    line = format_note(stamp, args.who, recipient, answering, args.text)
    if not append(line):
        print(f"could not write {NOTES}", file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
