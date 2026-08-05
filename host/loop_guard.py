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

import time

# How long a call stays "recent" for repetition purposes. A repeat three hours
# later is not a stuck loop, it is a second job — counting it would turn a
# useful signal into noise that gets ignored, which is how signals die.
DEFAULT_WINDOW_S = 120.0


class RepeatWatch:
    """Notices that a call is identical to a recent one. Reports; never blocks.

    `note(name, args)` returns None for an ordinary call, or a small dict for a
    repeat. The dict is meant to travel INSIDE the tool result, so the loop sees
    it in the same place it sees everything else — a signal on a side channel is
    a signal somebody has to remember to look at.
    """

    def __init__(self, window_s=DEFAULT_WINDOW_S, clock=time.monotonic):
        self.window_s = window_s
        self._clock = clock
        self._last_key = None
        self._consecutive = 0
        self._seen = {}          # key -> (count, last_time)

    @staticmethod
    def key(name, args):
        """A stable identity for (tool, arguments).

        Sorted items, not `repr(dict)`: two dicts with the same content and a
        different insertion order are the same call, and treating them as
        different would silently lose exactly the repeats worth reporting.
        """
        items = tuple(sorted((args or {}).items(), key=lambda kv: kv[0]))
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

        if count <= 1:
            return None
        out = {"identical_calls": count,
               "seconds_since_previous": round(now - last_t, 2)}
        if self._consecutive:
            # Back-to-back is the shape that means "stuck"; the same call twice
            # with other work in between usually means something else entirely.
            out["consecutive"] = self._consecutive + 1
        return out


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
