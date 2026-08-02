#!/usr/bin/env python3
"""Wake this session when the other one writes to the channel.

Everything else built for `notes.py` delivers when the session DOES something:
the brief at start, the NOTES field on a reply it asked for, `notes.py list` on
demand. None of that reaches a session sitting idle, so the human stayed the
messenger — walking between two windows to say "there is a note".

This is the one mechanism the platform documents for that: a hook declared
`async: true, asyncRewake: true` runs in the background and **wakes the model
when it exits with code 2**, showing its output as a system reminder. Run from
the Stop hook it starts exactly when a session goes idle, which is precisely
when nothing else can reach it.

    exit 2  -> something arrived for this session; the text goes to stderr
    exit 0  -> nothing (timeout, another watcher already running, no channel)

What it cannot do, and no amount of building changes: wake a session that is
not running. A closed window stays closed.

Deliberately biased towards silence. A watcher that wakes a session for nothing
is one that gets switched off within a day, and then the mechanism is worth
less than no mechanism at all — so every uncertain case exits 0.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notes  # noqa: E402

# How long a single watcher lives. It is replaced by the next Stop hook anyway,
# so this only bounds a session that is abandoned mid-turn.
MAX_LIFETIME = float(os.environ.get("APPLEBRIDGE_WATCH_SECONDS", "1800"))
POLL = float(os.environ.get("APPLEBRIDGE_WATCH_POLL", "5"))
LOCK = os.environ.get("APPLEBRIDGE_WATCH_LOCK", "/tmp/applebridge_watch.lock")
LOG = os.environ.get("APPLEBRIDGE_WATCH_LOG", "/tmp/applebridge_watch.log")


def relevant(lines, who, since):
    """Notes newer than `since` that this session should be woken for.

    Its own messages never count — a channel that wakes you for your own note
    is noise with extra steps. Anything addressed here, any answer to what this
    session asked, and any open question from the other side do.
    """
    fresh = [n for n in notes.all_notes(lines) if n["ts"] > since and n["from"] != who]
    if not fresh:
        return []
    addressed = {n["ts"] for n in notes.inbox_for(lines, who)}
    open_q = {n["ts"] for n in notes.open_notes(lines)}
    return [n for n in fresh if n["ts"] in addressed or n["ts"] in open_q]


def latest_ts(lines):
    stamps = [n["ts"] for n in notes.all_notes(lines)]
    return max(stamps) if stamps else ""


def another_watcher_running(path=None):
    """True when a live watcher already holds the lock.

    First one wins rather than newest: an older watcher has an EARLIER baseline,
    so it still fires for anything new. Replacing it would only reset the clock.
    """
    path = path or LOCK
    try:
        with open(path, encoding="utf-8") as handle:
            pid = int(handle.read().strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)          # signal 0: existence check, no signal sent
        return True
    except OSError:
        return False             # stale lock from a killed watcher


def _note(message):
    try:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n")
    except OSError:
        pass


def main():
    who = notes.WHO
    if another_watcher_running():
        return 0
    try:
        with open(LOCK, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except OSError:
        return 0                 # cannot lock -> do not risk a second watcher

    baseline = latest_ts(notes.read())
    _note(f"watch start who={who} baseline={baseline or '-'} pid={os.getpid()}")
    deadline = time.monotonic() + MAX_LIFETIME
    try:
        while time.monotonic() < deadline:
            time.sleep(POLL)
            hits = relevant(notes.read(), who, baseline)
            if hits:
                newest = hits[-1]
                _note(f"wake who={who} from={newest['from']} ts={newest['ts']}")
                print(f"Session channel: {len(hits)} new from {newest['from']} — "
                      f"{newest['text'][:300]}\n"
                      f"Read all with: host/tools/notes.py list",
                      file=sys.stderr)
                return 2
        _note(f"watch timeout who={who}")
        return 0
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
