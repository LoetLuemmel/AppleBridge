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
LOG = os.environ.get("APPLEBRIDGE_WATCH_LOG", "/tmp/applebridge_watch.log")


def lock_path(who):
    """One lock PER SESSION, not one for the machine.

    A single global lock made the watcher first-come-first-served: whichever
    session went idle first held it, and the other one's watcher exited
    immediately — so only one of the two could ever be woken. Measured within
    minutes of the first deploy, with this session holding the lock while the
    other had none.

    The lock exists to stop ONE session stacking a watcher per turn, which is a
    per-session concern; making it global quietly turned it into a per-machine
    mutex on being reachable at all.

    `who` is sanitised because it comes from the environment and ends up in a
    path — a session named `../../etc/x` must not point the lock elsewhere.
    """
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (who or "anon"))
    return os.environ.get("APPLEBRIDGE_WATCH_LOCK",
                          f"/tmp/applebridge_watch.{safe}.lock")


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


def lost_since(lines, baseline_lines):
    """Unreadable lines that appeared after this watcher started.

    Kept as its own decider because it is the one rule here with no timestamp
    to lean on: a line the parser cannot read has no `ts`, so "newer than the
    baseline" can only mean "further down the file". Append-only makes the line
    number a usable clock.
    """
    return [b for b in notes.unreadable(lines) if b["lineno"] > baseline_lines]


def latest_ts(lines):
    stamps = [n["ts"] for n in notes.all_notes(lines)]
    return max(stamps) if stamps else ""


def another_watcher_running(path=None):
    """True when a live watcher already holds the lock.

    First one wins rather than newest: an older watcher has an EARLIER baseline,
    so it still fires for anything new. Replacing it would only reset the clock.
    """
    if path is None:
        raise TypeError("pass the session's own lock path")
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
    lock = lock_path(who)
    if another_watcher_running(lock):
        return 0
    try:
        with open(lock, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except OSError:
        return 0                 # cannot lock -> do not risk a second watcher

    first = notes.read()
    baseline = latest_ts(first)
    # A second baseline, by line NUMBER, because an unreadable line has no
    # timestamp to compare — and an unreadable line is exactly the case that
    # must not stay quiet: three notes were written to this channel and
    # delivered to nobody, and the watcher was one of the three readers that
    # said nothing. Counting lines is the only handle a broken line offers.
    baseline_lines = len(first)
    _note(f"watch start who={who} baseline={baseline or '-'} "
          f"lines={baseline_lines} pid={os.getpid()}")
    deadline = time.monotonic() + MAX_LIFETIME
    try:
        while time.monotonic() < deadline:
            time.sleep(POLL)
            lines = notes.read()
            hits = relevant(lines, who, baseline)
            broken = lost_since(lines, baseline_lines)
            if broken:
                # Loudest case, so it is checked first: somebody wrote to the
                # channel and the channel could not read it. Nobody is being
                # delivered to right now, and the sender does not know.
                _note(f"wake who={who} unreadable={len(broken)} "
                      f"first_line={broken[0]['lineno']}")
                print(f"Session channel: {len(broken)} UNREADABLE line(s) — "
                      "somebody wrote to the channel and it was delivered to "
                      f"nobody. First at line {broken[0]['lineno']}: "
                      f"{broken[0]['raw'][:200]}\n"
                      "Read all with: host/tools/notes.py list",
                      file=sys.stderr)
                return 2
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
            os.remove(lock)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
