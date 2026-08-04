#!/usr/bin/env python3
"""Print the project's current state at session start — rules and state, not history.

An AI collaborator retains nothing between sessions; what it needs first is the
small set of things that are true *now*: which version this is, where the branch
stands, which decisions are of record, where the operating notes live, what is
open on the ledger. This brief is
the SessionStart counterpart to the Stop-hook `ledger_diff.py` — the pair
bracket a session with "here is the state" and "did the records drift".

Every section degrades silently: no CMS key → no ledger lines; no gh/git → skip.
A brief that errors at every session start is a brief that gets switched off,
so the exit code is always 0.

    host/tools/session_brief.py          # the brief
    host/tools/session_brief.py --full   # ...plus all open ledger items
"""
import datetime
import os
import re
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The cross-session record of what to VERIFY before believing the bridge, as
# opposed to what to know (CLAUDE.md) or what was decided (DECISIONS.md). It is
# printed rather than merely linked from CLAUDE.md because its entries are the
# ones a session needs *before* it has a reason to go looking: each was written
# after somebody spent hours on a layer that was not at fault.
# A repo-relative path rather than the CMS url it used to be (D-020): a session
# that needs these notes is mid-debug with the repo open, and a URL behind
# Authelia is exactly the thing it cannot grep at that moment.
OPERATING_NOTES = "docs/OPERATING_NOTES.md"

# One line per session start, so "did another instance run?" has an answer that
# does not depend on catching its process alive. Whether a session READ anything
# is not observable — the notes live behind Authelia and the CMS audit log
# records only writes — but when it started, on which branch, and against which
# commit is, and that is what the question usually reduces to.
#
# /tmp deliberately, next to /tmp/applebridge_server.log: a session log that
# outlives a reboot would be a retention decision nobody asked for.
SESSION_LOG = os.environ.get("APPLEBRIDGE_SESSION_LOG",
                             "/tmp/applebridge_sessions.log")


def _run(args, timeout=15):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, cwd=_ROOT).stdout.strip()
    except Exception:
        return ""


def daemon_version():
    """Short version from mac/vers.r — the project's single version source."""
    try:
        text = open(os.path.join(_ROOT, "mac", "vers.r"), encoding="utf-8").read()
        found = re.findall(r'"(\d+\.\d+[a-z]\d+|\d+\.\d+\.\d+)"', text)
        return found[0] if found else "?"
    except OSError:
        return "?"


def decisions():
    """-> (active_count, [three newest '## D-NNN — title' lines])."""
    try:
        text = open(os.path.join(_ROOT, "DECISIONS.md"), encoding="utf-8").read()
    except OSError:
        return 0, []
    heads = re.findall(r"^## (D-\d{3} — .+)$", text, re.M)
    active = len(re.findall(r"\*\*Status:\*\*\s*active", text))
    return active, heads[-3:]


def ledger_lines(show_all):
    """Open ledger items via ledger_diff's fetch; [] when no key / no network."""
    if not (os.environ.get("CMS_API_KEY") or os.environ.get("GENERIC_CMS_KEY")):
        return []
    try:
        import ledger_diff
        updated, body = ledger_diff.fetch_ledger(quiet=False)
    except SystemExit:
        return []
    except Exception:
        return []
    items = ledger_diff.open_items(body)
    lines = [f"ledger last edited : {updated}   open items: {len(items)}"]
    shown = items if show_all else [i for i in items if i[1] in ("In progress", "Blocked")]
    for title, status in shown:
        lines.append(f"  [{status:<11}] {title}")
    if not show_all and len(shown) < len(items):
        lines.append(f"  (+{len(items) - len(shown)} open — session_brief.py --full)")
    return lines


def note_lines():
    """Open questions from the session-to-session channel, or nothing.

    This is the delivery half of `notes.py`: a session has no event loop, so a
    question deposited there would sit unseen until somebody thought to look.
    The brief is the one push that already happens, so it carries them.
    """
    try:
        import datetime as _dt
        import notes
        lines = notes.read()
        pending = notes.open_notes(lines)
        # Answers are bounded by a day rather than shown forever: a session
        # start is a "what happened recently" moment, and an answer from last
        # week is history, not news.
        replies = notes.recent(notes.inbox_for(lines, notes.WHO),
                               _dt.datetime.now(), 86400)
        broken = notes.unreadable(lines)
    except Exception:
        return []

    out = []
    # Above the questions on purpose: an unreadable line is mail that was sent
    # and never arrived, which outranks mail that arrived and is unanswered.
    if broken:
        out.append(f"UNREADABLE         : {len(broken)} line(s) written to the "
                   "channel and delivered to nobody")
        for bad in broken[-2:]:
            out.append(f"  line {bad['lineno']}: {bad['raw'][:80]}")
    # `inline=True`: the brief is a fixed-shape overview, so a note's paragraph
    # breaks are rendered as spaces rather than as real newlines. The full shape
    # is one `notes.py list` away; a brief whose height depends on how long the
    # other session's last review was is a brief that gets skimmed past.
    def preview(note, width=88):
        return f"  [{note['ts']}] {note['from']}: " \
               f"{notes.unescape_text(note['text'], inline=True)[:width]}"

    if pending:
        out.append(f"open questions     : {len(pending)} (notes.py answer <ts> \"…\")")
        out += [preview(note) for note in pending[-5:]]
    if replies:
        out.append(f"for you            : {len(replies)}")
        out += [preview(note) for note in replies[-3:]]
    if out and notes.WHO == "agent":
        # Only reachable when neither APPLEBRIDGE_WHO nor CLAUDE_CODE_SESSION_ID
        # is in the environment. It used to fire whenever nobody had configured
        # a name, which was most of the time — until the session id turned out
        # to be exported already. If this line ever appears at a real session
        # start, the hook's environment lacks that variable and the automatic
        # naming needs another source: that is what it is here to report.
        out.append("                     no session identity (neither "
                   "APPLEBRIDGE_WHO nor CLAUDE_CODE_SESSION_ID) — nothing can "
                   "be addressed back")
    return out


def session_line(stamp, hookpid, branch, commit, version):
    """The record of one session start — one greppable line, fields as key=value.

    `hookpid` is the process that ran the hook, NOT the session. It was called
    `pid` for about an hour, which was worse than omitting it: two entries 28
    seconds apart carried different numbers while a single Claude Code process
    was running, because `os.getppid()` here is the short-lived shell the hook
    is spawned in. A field that looks like a session identifier and is not one
    sends whoever cross-checks it against `ps` to the wrong conclusion. There is
    no reliable session id available from a hook, so none is claimed.

    The fields that DO carry weight are the timestamp, the branch and the
    commit. A restart of the same session simply adds another line.

    Kept separate from writing it so the format can be tested without a file:
    a log nobody can parse answers the question no better than no log at all.
    """
    return (f"{stamp} hookpid={hookpid} branch={branch or '?'} "
            f"commit={(commit or '?').split()[0]} version={version}")


def record_session(line, path=None):
    """Append the line; never let it matter if that fails.

    The brief runs as a SessionStart hook, and a hook that errors is a hook that
    gets switched off — the same reasoning that makes every section here degrade
    silently. An unwritable /tmp costs the log, not the brief.
    """
    try:
        with open(path or SESSION_LOG, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return True
    except Exception:
        return False


def main():
    full = "--full" in sys.argv
    branch = _run(["git", "branch", "--show-current"]) or "?"
    last = _run(["git", "log", "-1", "--format=%h %ad %s", "--date=short"])
    n_active, newest = decisions()

    print("=== AppleBridge session brief ===")
    print(f"daemon version     : {daemon_version()}  (mac/vers.r)")
    print(f"branch             : {branch}")
    if last:
        print(f"last commit        : {last[:100]}")
    print(f"decisions of record: {n_active} active (DECISIONS.md)"
          + (f" — newest: {newest[-1]}" if newest else ""))
    print(f"operating notes    : {OPERATING_NOTES}")
    print( "                     read it when a command reports success "
           "and nothing happened")
    for line in ledger_lines(full):
        print(line)
    for line in note_lines():
        print(line)

    record_session(session_line(
        datetime.datetime.now().replace(microsecond=0).isoformat(),
        os.getppid(), branch, last, daemon_version()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
