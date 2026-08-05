"""Two guards for a model-driven loop: repetition made visible, steps bounded.

Both come out of the first run of a local model through this bridge
(2026-08-05, `qwen2.5-coder:7b`). Two things happened that nobody had planned
for, and neither was a fault of the model:

**It called the same tool with identical arguments before answering** — three
times in one run, twice in the other. On `mac_compile` that is harmless; the
tool is idempotent. On `mac_key`, `mac_click`, `launch_app`, `SWAPSELF` or
`REBOOT` it is not, and the loop had no way to notice. So `RepeatWatch` reports
it.

**It stopped on its own after three or four steps.** That looked like a loop
that ends, and it was a model that happened to be finished. Nothing bounded it.
So `StepBudget` bounds it — and says so out loud when it does.

The design rule both follow is the one this project keeps paying for: **make it
visible, do not guess what was meant.** Suppressing a repeated call would be a
guard deciding that the driver did not want what it asked for twice — which it
cannot know. And a budget that stops the loop silently is indistinguishable from
a model that finished; the difference has to reach whoever reads the transcript,
which is why exhaustion produces a MESSAGE rather than just a `False`.

stdlib only, no framework: this is meant to be imported by a conductor that
lives outside this repository, or copied into one.
"""

import collections
import time

# How long a call stays "recent" for repetition purposes. A repeat three hours
# later is not a stuck loop, it is a second job — counting it would turn a
# useful signal into noise that gets ignored, which is how signals die.
DEFAULT_WINDOW_S = 120.0

# How far back a cycle may reach. A-B-A-B is the shape small models fall into;
# consecutive repetition is only its shortest case. Six covers a three-step
# cycle seen twice, which is where a loop stops being a coincidence — longer
# would find "cycles" in ordinary work, and a signal that fires on ordinary work
# is one that gets switched off.
DEFAULT_HISTORY = 6


class RepeatWatch:
    """Notices that a call is identical to a recent one. Reports; never blocks.

    `note(name, args)` returns None for an ordinary call, or a small dict for a
    repeat. The dict is meant to travel INSIDE the tool result, so the loop sees
    it in the same place it sees everything else — a signal on a side channel is
    a signal somebody has to remember to look at.
    """

    def __init__(self, window_s=DEFAULT_WINDOW_S, clock=time.monotonic,
                 history=DEFAULT_HISTORY):
        self.window_s = window_s
        self._clock = clock
        self._last_key = None
        self._consecutive = 0
        self._seen = {}          # key -> (count, last_time)
        self._recent = collections.deque(maxlen=history)

    @staticmethod
    def key(name, args):
        """A stable identity for (tool, arguments).

        Sorted items, not `repr(dict)`: two dicts with the same content and a
        different insertion order are the same call, and treating them as
        different would silently lose exactly the repeats worth reporting.

        Absent and empty are folded together — `{"path": "x"}` and
        `{"path": "x", "options": None}` are the same call, and a model that
        re-sends one as the other is repeating itself. This is deliberately the
        ONLY normalisation: stripping whitespace or case would look tidier and
        would be wrong, because a classic-Mac filename may legitimately differ
        in exactly those ways. Folding two distinct calls into one costs a
        false alarm on real work, which is how a signal earns its way to being
        ignored.
        """
        items = tuple(sorted(((k, v) for k, v in (args or {}).items()
                              if v is not None and v != ""),
                             key=lambda kv: kv[0]))
        return (name, repr(items))

    def note(self, name, args):
        now = self._clock()
        k = self.key(name, args)
        count, last_t = self._seen.get(k, (0, None))
        recent = last_t is not None and (now - last_t) <= self.window_s
        if not recent:
            count = 0                       # outside the window: a fresh start
        count += 1
        self._seen[k] = (count, now)

        # Only an UNBROKEN chain counts. Anything else — another tool in
        # between, or a gap wider than the window — resets it: a call that
        # happens twice with work in between is not a stuck loop, and reporting
        # it as one would make the field mean nothing.
        if recent and self._last_key == k:
            self._consecutive += 1
        else:
            self._consecutive = 0
        self._last_key = k
        self._recent.append((k, now))
        cycle = self._cycle_length(now)

        if count <= 1 and cycle is None:
            return None
        out = {}
        if count > 1:
            out["identical_calls"] = count
            out["seconds_since_previous"] = round(now - last_t, 2)
        if self._consecutive:
            # Back-to-back is the shape that means "stuck"; the same call twice
            # with other work in between usually means something else entirely.
            out["consecutive"] = self._consecutive + 1
        if cycle is not None:
            # A-B-A-B is the shape small models actually fall into, and the
            # consecutive counter is blind to it — B resets the chain every
            # time. Reported separately rather than folded in, because the two
            # need different answers: a repeated call may be idempotent, a cycle
            # never is progress.
            out["cycle_length"] = cycle
        return out or None

    def _cycle_length(self, now):
        """Shortest p for which the last 2p calls are two identical halves.

        Length 1 is the consecutive case and is reported anyway; it is included
        here so a caller that only reads `cycle_length` is not blind to the
        simplest cycle of all.

        The window applies here too, and it was not obvious: without it a call
        repeated two hours later reported `cycle_length: 1`, because the deque
        remembers what the clock has long forgotten. A cycle spread over hours
        is not a stuck loop. Caught by the window test that already existed.
        """
        seq = [key for key, t in self._recent if (now - t) <= self.window_s]
        for p in range(1, len(seq) // 2 + 1):
            if seq[-p:] == seq[-2 * p:-p]:
                return p
        return None


class StepBudget:
    """A hard bound on loop steps that says why it ended.

    `spend()` returns True while the budget holds and False once it is gone;
    `message()` is the sentence that has to reach the transcript. Both, because
    returning only a boolean is how a bounded loop comes to look like a finished
    one — the case this exists to prevent.
    """

    def __init__(self, max_steps=8):
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.max_steps = max_steps
        self.used = 0

    def spend(self):
        if self.used >= self.max_steps:
            return False
        self.used += 1
        return True

    @property
    def exhausted(self):
        return self.used >= self.max_steps

    def remaining(self):
        return max(0, self.max_steps - self.used)

    def message(self):
        """Why the loop stopped — never "" while exhausted."""
        if not self.exhausted:
            return ""
        return (f"step budget exhausted after {self.used} step(s): the loop was "
                f"stopped by its bound, not by the model deciding it was done")


class AttemptLog:
    """One line per distinct action, not one per step — what has been TRIED.

    The perception contract gives the model the last three actions, because a
    longer window costs context that a 2 GB node does not have. But three steps
    is exactly how a model comes to try the same thing a fourth time: the shell
    sees the cycle, the model does not, and the new rule *never argue from
    memory, perceive again* removes the only other place that knowledge lived.

    The way out is not a longer window. It is a register that grows per distinct
    ACTION rather than per step: ten steps that repeat two actions are two lines.
    Proposed by an outside comment on the loop draft, 2026-08-05.

    Monotone on purpose — entries are never removed, only their counts rise.
    A register that forgets is a window with extra machinery, and the whole
    point is to remember what a window cannot afford to.

    Fed from outside (a conductor calls `record`), so the same register serves a
    loop this repository does not contain.
    """

    def __init__(self):
        self._rows = {}          # key -> dict
        self._order = []         # first-seen order, so the summary is stable

    def record(self, name, args, outcome):
        """Note that an action was tried and how it came out.

        `outcome` is the caller's word for what happened — "ok", "failed",
        "not_read", a short reason. It is stored VERBATIM: a register that
        normalises outcomes into its own vocabulary hands the model a
        translation of its own history, and the translation is where the detail
        that mattered gets lost.
        """
        k = RepeatWatch.key(name, args)
        row = self._rows.get(k)
        if row is None:
            row = {"tool": name, "args": dict(args or {}), "tries": 0,
                   "outcomes": []}
            self._rows[k] = row
            self._order.append(k)
        row["tries"] += 1
        # Last outcome first, and only distinct ones: "failed, failed, failed"
        # says no more than "failed" and costs three times the context.
        if not row["outcomes"] or row["outcomes"][-1] != outcome:
            row["outcomes"].append(outcome)
        return row

    def tried(self, name, args):
        """Has this exact action been tried before? -> the row, or None."""
        return self._rows.get(RepeatWatch.key(name, args))

    def __len__(self):
        return len(self._rows)

    def lines(self, limit=None):
        """Compact lines for a prompt, newest-first, plus what was left out.

        The omission is REPORTED rather than silently applied. A register that
        quietly drops rows tells the model it has tried less than it has, which
        is precisely the belief that produces another attempt.
        """
        rows = [self._rows[k] for k in reversed(self._order)]
        shown = rows if limit is None else rows[:limit]
        out = [f"{r['tool']}({_brief_args(r['args'])}) x{r['tries']} "
               f"-> {', '.join(r['outcomes'])}" for r in shown]
        if limit is not None and len(rows) > limit:
            out.append(f"… and {len(rows) - limit} earlier action(s) not shown")
        return out


def _brief_args(args):
    """Arguments as `k=v`, values shortened but never in the middle.

    A value cut in the middle reads as a different value; cut at the end with a
    visible marker it reads as a shortened one. The distinction is the whole
    difference between a hint and a lie.
    """
    parts = []
    for k, v in sorted((args or {}).items()):
        s = str(v)
        parts.append(f"{k}={s[:40] + '…' if len(s) > 40 else s}")
    return ", ".join(parts)
