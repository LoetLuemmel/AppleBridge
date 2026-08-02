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
import sys

NOTES = os.environ.get("APPLEBRIDGE_NOTES", "/tmp/applebridge_notes.log")

# Free-form, short, no spaces. Two sessions both calling themselves "agent"
# still pair up correctly — questions and answers are matched by timestamp, not
# by name — but setting it makes the file readable by a human.
WHO = os.environ.get("APPLEBRIDGE_WHO", "agent")


def format_note(stamp, who, to, answering, text):
    """One line: the fields first so they can be grepped, the text last."""
    flat = " ".join((text or "").split())
    return f"{stamp} from={who or '?'} to={to or 'all'} re={answering or '-'} {flat}"


def parse_note(line):
    """-> dict, or None for a line this channel did not write."""
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
    return {"ts": stamp, "from": fields["from"], "to": fields["to"],
            "re": None if fields["re"] == "-" else fields["re"], "text": text}


def open_notes(lines):
    """Questions nobody has answered yet, oldest first.

    A question is closed by any note whose `re=` names its timestamp. Nothing
    tracks who has READ what: answering is the only signal, and it is one the
    file can actually carry.
    """
    notes = [n for n in (parse_note(l) for l in lines) if n]
    answered = {n["re"] for n in notes if n["re"]}
    return [n for n in notes if not n["re"] and n["ts"] not in answered]


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


def read(path=None):
    try:
        with open(path or NOTES, encoding="utf-8") as handle:
            return handle.readlines()
    except OSError:
        return []


def append(line, path=None):
    try:
        with open(path or NOTES, "a", encoding="utf-8") as handle:
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

    q = sub.add_parser("ask", help="deposit a question")
    q.add_argument("text")
    q.add_argument("--to", default="all")
    q.add_argument("--from", dest="who", default=WHO)

    a = sub.add_parser("answer", help="answer a question by its timestamp")
    a.add_argument("ts")
    a.add_argument("text")
    a.add_argument("--from", dest="who", default=WHO)

    lst = sub.add_parser("list", help="open questions")
    lst.add_argument("--since", type=int, default=None, metavar="SECONDS",
                     help="only those deposited within the last N seconds "
                          "(what the PostToolUse hook uses, so it announces a "
                          "note once instead of after every tool call)")
    args = parser.parse_args()

    if args.verb == "list":
        pending = recent(open_notes(read()), datetime.datetime.now(), args.since)
        for note in pending:
            print(f"note {note['ts']}  from={note['from']}  {note['text']}")
        return 0

    stamp = _now()
    answering = args.ts if args.verb == "answer" else None
    line = format_note(stamp, args.who, getattr(args, "to", "all"), answering, args.text)
    if not append(line):
        print(f"could not write {NOTES}", file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
