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


# Which arguments NAME something that must already exist, and which name
# something being created. Written out per tool rather than guessed from the
# parameter name: `path` is a reference for mac_read_file and a new value for
# mac_write_file, and a rule that could not tell those apart would either refuse
# every write or wave through every read.
#
# A tool that appears in neither is UNCHECKED, and says so. Waving it through
# silently is the hole the outside comment named: an unproven argument that
# passes marked, with nobody named to act on the mark.
REFERENCE_ARGS = {
    "mac_read_file": ("path",),
    "mac_list_files": ("path",),
    "mac_compile": ("source_path",),
    "launch_app": ("path",),
    "mac_get_file": ("mac_path",),
}
NEW_VALUE_ARGS = {
    "mac_write_file": ("path",),
    "mac_put_file": ("mac_path",),
    "mac_compile": ("output_path",),
}


class TurnScope:
    """What the shell knew when the model formed its request.

    A naming argument must resolve against a structure the shell HOLDS — and the
    clock matters more than it looks. Measured 2026-08-05: a model called
    `mac_list_files` and `mac_read_file` in ONE turn and put the literal
    `<filename>` in the second, because the listing did not exist yet. A check
    run at execution time would have had the listing by then and could have
    passed a *guessed* name that happens to be in it — resolved correctly, and
    still not taken from the result.

    So the snapshot is taken when the turn OPENS, and results produced during
    the turn deliberately do not enter it. Anything a model can only have known
    from this turn's results is, at turn start, unknown — which is exactly the
    property worth checking, and it catches an invented name and a placeholder
    without having to tell them apart.

    Fed from outside, like the rest of this module: the shell calls
    `open_turn` with whatever it just showed the model.
    """

    def __init__(self):
        self._known = {}         # source -> frozenset of names
        self._turn = 0

    def open_turn(self, structures=None):
        """Begin a turn with the structures the model was shown. -> turn number.

        Replaces rather than accumulates. A scope that kept every structure ever
        delivered would slowly become "anything ever seen", and then it answers
        yes to everything — the way a check stops being one.
        """
        self._turn += 1
        self._known = {src: frozenset(str(n) for n in names)
                       for src, names in (structures or {}).items()}
        return self._turn

    def sources_for(self, value):
        """Which structures contain this exact name."""
        v = str(value)
        return sorted(src for src, names in self._known.items() if v in names)

    def candidates(self, limit=12):
        """Everything known at turn start — the list a refusal hands back.

        Measured: a refusal WITH the list let the model correct itself in the
        next step; a refusal alone had it invent again. The limit is reported
        when it bites, for the same reason as everywhere else here.
        """
        names = sorted({n for names in self._known.values() for n in names})
        if len(names) <= limit:
            return names
        return names[:limit] + [f"… and {len(names) - limit} more"]

    def check(self, tool, args):
        """-> {"verdict": allow|refuse|unchecked, "why": str, ...}.

        Three verdicts, and `unchecked` is a real answer rather than a quiet
        pass: it says the tool has no rule here, which a reader can act on.
        """
        refs = REFERENCE_ARGS.get(tool)
        news = NEW_VALUE_ARGS.get(tool, ())
        if refs is None and not news:
            return {"verdict": "unchecked", "tool": tool,
                    "why": f"no naming rule for {tool}; nothing was verified"}
        for key in (refs or ()):
            value = (args or {}).get(key)
            if value is None:
                continue
            if not self._known:
                return {"verdict": "refuse", "tool": tool, "argument": key,
                        "value": value, "candidates": [],
                        "why": ("nothing was known when this turn opened, so a "
                                "name that must already exist cannot have come "
                                "from anywhere — list first, then name")}
            if not self.sources_for(value):
                return {"verdict": "refuse", "tool": tool, "argument": key,
                        "value": value, "candidates": self.candidates(),
                        "why": (f"{value!r} was not in anything known when this "
                                f"turn opened; if it appeared in a result of "
                                f"THIS turn, it was not read from there")}
        return {"verdict": "allow", "tool": tool,
                "why": ("every naming argument resolved against what was known "
                        "when the turn opened")}


# Tools that CHANGE the source, and tools that JUDGE it. The split is the whole
# of TerminationWatch: a loop is closed when the last change was followed by a
# judgement, and open when it was not.
#
# `mac_build` is in both — it writes nothing but it compiles, so it can close a
# loop; `mpw_execute` is in neither, because what an arbitrary MPW line does is
# exactly what this module refuses to guess.
WRITE_TOOLS = ("mac_write_file", "mac_put_file")
COMPILE_TOOLS = ("mac_compile", "mac_build")


class TerminationWatch:
    """Did the loop close — and if it did, on which of the two endings?

    The case this exists for, measured 2026-08-05 (run N): the model read the
    compiler's complaint, repaired the source correctly, and then stopped. No
    answer, no second compile. On disk the artefact was the OLD object file, so
    the run looked successful; in the transcript it looked like a crash. The
    only honest reading — *the source was changed and never judged again* — was
    available to nobody, because nothing was watching for it.

    **Three outcomes, not two.** The obvious design returns a boolean, and a
    boolean merges exactly the two cases the measurement is about:

        recompiled_ok      changed, judged, the artefact appeared
        recompiled_failed  changed, judged, it failed again — the loop WORKED
        not_recompiled     changed and never judged — the silent ending

    A repair that fails again is a loop doing its job; a repair nobody compiled
    is a loop that stopped mid-sentence. Named by the parallel session, 2026-08-06,
    against a draft of this class that had `closed: True|False`.

    Fed from outside, like the other guards here: a conductor calls `note` with
    the tool name and its RESULT, and the result is the tool's own dict — the
    verdict must come from the artefact check the tool already did, never from
    anything the model said about it.

    Reports; never blocks. By the time this can speak, the loop is over.
    """

    NOTHING_WRITTEN = "nothing_written"
    RECOMPILED_OK = "recompiled_ok"
    RECOMPILED_FAILED = "recompiled_failed"
    NOT_RECOMPILED = "not_recompiled"
    # A FOURTH, beyond the three agreed: the compile happened and could not be
    # verified (`verified: false`). Calling that `not_recompiled` would be the
    # same merge this class was built to stop, one level down — a compile that
    # ran is not a compile that never ran, and "nobody knows" is not "it
    # failed". Rare by construction: it needs -o hidden inside `options`.
    COMPILED_UNVERIFIED = "compiled_unverified"

    def __init__(self, write_tools=WRITE_TOOLS, compile_tools=COMPILE_TOOLS):
        self.write_tools = tuple(write_tools)
        self.compile_tools = tuple(compile_tools)
        self.writes = 0
        self.compiles = 0
        self._pending_write = False      # a write not yet followed by a compile
        self._last_verdict = None        # success of the last compile, if known

    def note(self, name, result=None):
        """Record one executed tool call. Refused calls must NOT be passed here.

        A refused call never reached a tool, so it changed nothing and judged
        nothing — counting it would let a loop close on a call the guard
        stopped. The caller filters, because only the caller sees the record's
        `refused` field. But a refused RESULT carries the hull's own marker, and
        reading it here costs one line: a contract that only a docstring
        enforces is one an outside conductor breaks silently, and the symptom
        would be a termination rate three weeks later, not an error.
        """
        if isinstance(result, dict) and (result.get("refused_by_hull")
                                         or result.get("refused")):
            return
        if name in self.write_tools:
            self.writes += 1
            self._pending_write = True
            self._last_verdict = None
        elif name in self.compile_tools:
            self.compiles += 1
            self._pending_write = False
            self._last_verdict = self._compile_succeeded(result)

    @staticmethod
    def _compile_succeeded(result):
        """-> True / False / None, from the tool's OWN verified verdict.

        `None` where the tool says it could not check (`verified: false`, the
        -o-inside-options branch). Folding that into False would report a
        failed repair where the truth is an unmade measurement — the same
        collapse this class exists to prevent, one level down.
        """
        if not isinstance(result, dict):
            return None
        if result.get("verified") is False:
            return None
        success = result.get("success")
        return None if success is None else bool(success)

    def outcome(self):
        """One of the four constants above. Call it when the loop has ended."""
        if not self.writes:
            return self.NOTHING_WRITTEN
        if self._pending_write:
            return self.NOT_RECOMPILED
        if self._last_verdict is None:
            return self.COMPILED_UNVERIFIED
        return self.RECOMPILED_OK if self._last_verdict else self.RECOMPILED_FAILED

    def message(self):
        """The sentence for the transcript — never "" once anything was written."""
        out = self.outcome()
        if out == self.NOTHING_WRITTEN:
            return ""
        if out == self.NOT_RECOMPILED:
            return (f"the source was written {self.writes} time(s) and the last "
                    f"change was never compiled: the loop stopped mid-repair, "
                    f"and the artefact on disk is older than the source")
        if out == self.COMPILED_UNVERIFIED:
            return (f"the source was compiled after the last change, but the "
                    f"compile could not be verified after {self.compiles} "
                    f"compile(s) — the ending is unknown, not successful")
        if out == self.RECOMPILED_FAILED:
            return (f"the source was compiled after the last change and failed "
                    f"again after {self.compiles} compile(s): the loop closed, "
                    f"the repair did not")
        return (f"the source was compiled after the last change and the "
                f"artefact appeared: the loop closed after {self.compiles} "
                f"compile(s)")

    def report(self):
        """Everything a trace should carry about termination, in one dict."""
        return {"outcome": self.outcome(), "writes": self.writes,
                "compiles": self.compiles, "message": self.message()}


class CompileBudget:
    """A bound on COMPILE attempts, deliberately separate from StepBudget.

    Why a second class and not a second counter: a budget that counts two
    different things merges exactly the cases that need telling apart, which is
    the mistake `TerminationWatch` was redesigned to avoid one screen up.
    `StepBudget` bounds model turns and should go on bounding only those.

    Why it is needed at all — measured 2026-08-06, and it is a bias in the
    measurement rather than a nuisance: a run under the HARDER condition uses
    more turns and therefore hits a turn bound sooner. The 08:20 run (old remedy
    text) ran into the bound at 8 steps; the two runs with the new text were
    finished after 5. A turn budget is not neutral between the arms — it stops
    the arm that struggles, and the arm that struggles is the one being measured.

    Counting compiles instead gives both arms the same number of ATTEMPTS at the
    thing the experiment is about, however many turns each of them spends
    getting there. Named by the operator in a third proofread of the article.

    Like the other guards: reports, never blocks, and says who ended the run.
    """

    def __init__(self, max_compiles=4):
        if max_compiles < 1:
            raise ValueError("max_compiles must be at least 1")
        self.max_compiles = max_compiles
        self.used = 0

    def spend(self):
        """True while an attempt remains. Call it before each compile."""
        if self.used >= self.max_compiles:
            return False
        self.used += 1
        return True

    @property
    def exhausted(self):
        return self.used >= self.max_compiles

    def remaining(self):
        return max(0, self.max_compiles - self.used)

    def message(self):
        """Why the loop stopped — never "" while exhausted.

        Names the unit out loud. A transcript that says only "budget exhausted"
        leaves the reader to guess which of the two bounds ended the run, and
        the two mean different things about the model.
        """
        if not self.exhausted:
            return ""
        return (f"compile budget exhausted after {self.used} compile "
                f"attempt(s): the loop was stopped by its bound, not by the "
                f"model deciding it was done")


class UncompiledWrite:
    """Says, IN the tool result, that the last write was never compiled.

    `TerminationWatch` already works this out — but only afterwards, for whoever
    reads the transcript. This says it at the moment its case is true, which is
    the one delivery route measured to work: 2026-08-05, the same sentence was
    ignored in a prompt and obeyed in a tool result.

    **Why it exists at all.** The strategy's own rule for what deserves training
    is: *train only what a tool can DETECT but not ENFORCE.* Detection was proved
    on 2026-08-06 — sixteen of eighty runs never compiled at all, and nineteen
    did not compile after their last change. The other half of the rule was never
    tested: nobody had tried a tool that says so while the loop is still running.
    Until that has been tried and has failed, the case is an open stage-1 job and
    not a training candidate. Named by the third party, and neither session had
    seen it, because both had reasoned from the result backwards.

    It REPORTS. It does not block, and the hint carries no instruction to stop —
    a guard that refuses here would be deciding that a caller who wrote a file
    did not mean to write it. Whether a conductor turns this into a refusal is
    the conductor's decision, and it can, because the flag is in the result.
    """

    def __init__(self, write_tools=WRITE_TOOLS, compile_tools=COMPILE_TOOLS):
        self.write_tools = tuple(write_tools)
        self.compile_tools = tuple(compile_tools)
        self._pending = None          # path of the write not yet compiled

    def note(self, name, args=None, result=None):
        """Record an executed call. -> a hint dict for the NEXT result, or None.

        Refused calls are ignored on the same reasoning as `TerminationWatch`:
        a call that never reached a tool neither wrote nor compiled.
        """
        if isinstance(result, dict) and (result.get("refused_by_hull")
                                         or result.get("refused")):
            return None
        if name in self.write_tools:
            self._pending = (args or {}).get("path") or "the source"
        elif name in self.compile_tools:
            self._pending = None
        return self.hint()

    def hint(self):
        """The sentence to travel in the result, or None when nothing is due.

        Phrased as a STATE and not as an order. "You have not compiled" is a
        reproach and invites agreement; naming what is true of the file is the
        form that produced a repair when it arrived in a tool result.
        """
        if not self._pending:
            return None
        return {
            "uncompiled_write": self._pending,
            "note": (f"{self._pending} has been written since the last compile. "
                     f"Nothing on disk reflects that change yet — an artefact "
                     f"found now is the one from before it."),
        }

    @property
    def pending(self):
        return self._pending
