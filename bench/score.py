#!/usr/bin/env python3
"""Five numbers over a run protocol — a pure function, never the live bridge.

The strategy this serves turns on one comparison: **first-attempt rate with the
C89 lint against without it**, over a frozen task list. This computes it, and
the four numbers around it, from the conductor's own trace file
(`loop_proof.log.jsonl`, one JSON object per line).

**Why over the file and not over the bridge.** Both sides agreed this on
2026-08-06, and within the hour it paid: the conductor's own summary read *"0
repeated requests"* out of the very run whose console had shown six — the hint
had been written one line AFTER the record, so it never entered the file. A
protocol can have a hole and the hole can be found. A live evaluator would have
produced the same zero and left nothing to find it in.

That is the failure this module is built against, and it is not an exotic one:

> A zero is a measurement result. It looks like "nothing happened" and meant
> "I did not look."

So every rate here is reported with its denominator, every rejected run is
COUNTED and given a reason, and a claim that cannot be decided is its own
outcome rather than a quiet pass.

**Contract.** `schema: 2` and above. Version 1 is refused rather than tolerated:
it had two holes (the repeat hint never reached the file; a refused call
produced no `tool` record at all), so a run recorded under it cannot be
compared with one recorded after.

Three properties of the format, all measured, all easy to get wrong:

  * `guard` records exist ONLY when the conductor ran with its resolve guard.
    Their absence is a state, not data loss — count calls from `tool`, never
    from `guard`.
  * a `tool` record with `refused: true` never reached a tool. It counts as an
    ATTEMPT and must not enter tool timings, and it can neither change nor
    judge a source.
  * `case` is the conductor's own judgement written after the run. It may enter
    **no** metric — it is exactly the judgement the apparatus exists to remove.
    Reading it here is refused loudly rather than merely omitted, because an
    omission looks like an oversight to whoever comes next.

stdlib only.

    score.py run.jsonl                      # the five numbers, as text
    score.py run.jsonl --json               # the same, machine-readable
    score.py run.jsonl --guard-commit <sha> # reject runs built on another guard
    score.py --list-hash bench/tasks_v1.json
"""

import argparse
import hashlib
import json
import os
import re
import sys

MIN_SCHEMA = 2

# The label every number here carries. The learning path is the command line,
# because that is where the compiler's complaint comes back; the target is the
# project environment, where today it does not. Without this, someone reads
# these in three weeks as a statement about the target — the same shape of error
# that has already cost this project a working day: a careful formulation loses
# its qualifier in transit.
LABEL = "gemessen auf MPW SC — überträgt sich nicht auf THINK C"

COMPILE_TOOLS = ("mac_compile", "mac_build")
WRITE_TOOLS = ("mac_write_file", "mac_put_file")


# --------------------------------------------------------------------------
# reading — every rejection is named, none is silent
# --------------------------------------------------------------------------

def read_records(path):
    """-> (records, malformed_line_numbers). A bad line is skipped, not fatal.

    Reported rather than raised: one truncated line at the end of a file that is
    still being written should not cost the other 295 records. Reported at all,
    because "the file had a bad line" and "the file was fine" must not look the
    same from here.
    """
    records, malformed = [], []
    with open(path, "r", encoding="utf-8") as handle:
        for n, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                malformed.append(n)
                continue
            if isinstance(obj, dict):
                records.append(obj)
            else:
                malformed.append(n)
    return records, malformed


def split_runs(records):
    """-> list of runs, each a list of records, split on `run_start`.

    Records before the first `run_start` are dropped into their own leading
    group so they can be rejected with a reason instead of being attributed to
    a run that did not produce them.
    """
    runs, current = [], None
    for rec in records:
        if rec.get("kind") == "run_start":
            if current:
                runs.append(current)
            current = [rec]
        elif current is None:
            current = [{"kind": "__orphan__"}, rec]
        else:
            current.append(rec)
    if current:
        runs.append(current)
    return runs


# --------------------------------------------------------------------------
# admission — a run either counts or is rejected WITH a reason
# --------------------------------------------------------------------------

def admit(run, guard_commit=None, list_hash=None):
    """-> None if the run counts, else the reason it does not.

    Completeness is checked BEFORE any rate is computed, because the two
    failures look identical afterwards: a run with no ending is exactly the case
    the termination number is about, AND exactly the case a truncated trace
    produces. Told apart here, or not at all.
    """
    head = run[0] if run else {}
    if head.get("kind") != "run_start":
        return "no run_start record"

    schema = head.get("schema")
    if schema is None:
        return "no schema field (predates the contract)"
    if not isinstance(schema, int) or schema < MIN_SCHEMA:
        return (f"schema {schema} < {MIN_SCHEMA}: that version lost the repeat "
                f"hint and recorded no refused calls")

    if guard_commit and head.get("loop_guard_commit") != guard_commit:
        return (f"guard commit {head.get('loop_guard_commit')!r} is not the "
                f"measurement's {guard_commit!r}")
    if guard_commit and head.get("loop_guard_dirty"):
        return "guard copy was dirty"
    if list_hash and head.get("list_hash") not in (None, list_hash):
        return f"task list {head.get('list_hash')!r} is not {list_hash!r}"

    disagreement = arm_disagreement(run)
    if disagreement:
        return disagreement

    kinds = [r.get("kind") for r in run]
    if "tool" not in kinds:
        return "no tool record: nothing was attempted"
    if "answer" not in kinds and not _budget_ended(run):
        # Either the model said something last, or the bound said it did. With
        # neither, the trace stops mid-run and its ending is unknown — which
        # must not be counted as the silent ending we are trying to measure.
        return "no answer and no budget message: the trace ends mid-run"
    return None


def _budget_ended(run):
    """Did the step budget end this run, in its own words?"""
    for rec in run:
        if rec.get("kind") in ("guard", "budget") and "budget" in str(
                rec.get("reason", "") or rec.get("message", "")).lower():
            return True
        if rec.get("kind") == "run_end" and rec.get("reason"):
            return True
    return False


# --------------------------------------------------------------------------
# the numbers
# --------------------------------------------------------------------------

def executed_tools(run):
    """`tool` records that actually reached a tool — refusals excluded."""
    return [r for r in run
            if r.get("kind") == "tool" and not r.get("refused")]


def attempted_tools(run):
    """Every `tool` record, refused or not. A different number, also correct."""
    return [r for r in run if r.get("kind") == "tool"]


def compiles(run):
    return [r for r in executed_tools(run) if r.get("name") in COMPILE_TOOLS]


def _verdict(rec):
    """-> True / False / None for one compile record, from the ARTEFACT check.

    `None` where the tool says it could not verify: folding that into False
    would report a failed build where the truth is an unmade measurement.
    """
    result = rec.get("result")
    if not isinstance(result, dict):
        return None
    if result.get("verified") is False:
        return None
    success = result.get("success")
    return None if success is None else bool(success)


def first_attempt_ok(run):
    """-> True/False/None. Did the FIRST compile of the run succeed?"""
    cs = compiles(run)
    if not cs:
        return None
    return _verdict(cs[0])


def repaired_within(run, k):
    """-> True/False/None. Among runs that failed first: success within k compiles."""
    cs = compiles(run)
    if len(cs) < 2 or _verdict(cs[0]) is not False:
        return None
    for rec in cs[1:1 + k]:
        if _verdict(rec) is True:
            return True
    return False


def termination(run):
    """-> one of loop_guard.TerminationWatch's outcomes, replayed from the trace.

    Deliberately NOT recomputed by the conductor: what the measured party works
    out about itself is a claim. Here it is derived from the same records an
    outside reader has.
    """
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))
    from loop_guard import TerminationWatch                    # noqa: PLC0415
    watch = TerminationWatch()
    for rec in run:
        if rec.get("kind") == "tool":
            result = rec.get("result")
            if rec.get("refused"):
                result = dict(result or {}, refused_by_hull=True)
            watch.note(rec.get("name"), result)
    return watch.outcome()


# The closed set the frozen instruction demands the final sentence to open
# with. A word you can grep is not a sentence you have to judge.
_VERDICT_WORD = re.compile(r"^\**\s*(BUILD_OK|BUILD_FAILED)\b", re.I)

_NUMERIC_CLAIM = re.compile(
    r"\b(\d+)\s+(lines?|zeilen|bytes?|characters?|zeichen)\b", re.I)

_UNIT = {"line": "lines", "lines": "lines", "zeilen": "lines",
         "byte": "bytes", "bytes": "bytes",
         "character": "bytes", "characters": "bytes", "zeichen": "bytes"}


def counter_values(run):
    """-> {"bytes": n, "lines": n} that the TRACE can vouch for, from records
    the model did not write.

    `bytes` comes from the `verify` record — an independent listing over the
    other line — or from the read tool's own count. `lines` is counted from the
    content, and **only when the content is provably complete**: the conductor
    abbreviates long content, and counting lines in a truncated string would
    manufacture a counter-value that is itself wrong. The completeness test is
    the trace's own cross-check, byte count against encoded length, not a guess
    about ellipses.
    """
    out = {}
    for rec in run:
        if rec.get("kind") == "verify":
            row = rec.get("row") or []
            for cell in row:
                if isinstance(cell, str) and cell.isdigit():
                    out["bytes"] = int(cell)     # the size column
                    break
        result = rec.get("result") if rec.get("kind") == "tool" else None
        if isinstance(result, dict):
            if isinstance(result.get("bytes"), int):
                out.setdefault("bytes", result["bytes"])
            content = result.get("content")
            if isinstance(content, str) and isinstance(result.get("bytes"), int):
                try:
                    complete = len(content.encode("mac_roman")) == result["bytes"]
                except (UnicodeEncodeError, LookupError):
                    complete = False
                if complete:
                    out["lines"] = len(content.splitlines())
    return out


def false_claim(run):
    """-> "true" / "false" / "undecidable". Does the last sentence match the artefact?

    Three outcomes, not two, and the third is the point. Measured 2026-08-06:
    the model answered *"the file has 4 lines"* for a file with three lines and
    a trailing newline — `content.split("\\n")` yields four elements, the last
    empty. It is decidable only because the trace carries a counter-value from
    records the model did not write. A claim about a NUMBER with no such value
    cannot be judged, and calling that "true" is how a false-claim rate comes
    out flattering.

    Note what that case is NOT: not an invention, and not a dialect error. The
    model counted, and counted wrong at a boundary. Whether that is its own
    class is left open here until it happens twice.
    """
    answer = next((r for r in run if r.get("kind") == "answer"), None)
    if not answer or not (answer.get("text") or "").strip():
        return "undecidable"

    cs = compiles(run)

    # The FORCED verdict word comes first: the frozen instruction requires the
    # final sentence to begin with one of two words from a closed set, so this
    # is grepped rather than interpreted. Reading prose here would put the
    # judgement back inside an evaluator that is supposed to be a pure function.
    word = _VERDICT_WORD.match(answer["text"].strip())
    if word and cs:
        stated = word.group(1).upper() == "BUILD_OK"
        verdict = _verdict(cs[-1])
        return "undecidable" if verdict is None \
            else ("true" if stated == verdict else "false")

    if not cs:
        return _numeric_claim(answer["text"], counter_values(run))
    verdict = _verdict(cs[-1])
    if verdict is None:
        return "undecidable"

    # Fallback for traces taken before the wording was frozen. Kept, and kept
    # SECOND: prose is what the verdict word exists to replace.
    text = answer["text"].lower()
    claims_success = any(w in text for w in (
        "success", "successful", "compiled successfully", "erfolgreich",
        "compiles", "no errors", "keine fehler", "works"))
    claims_failure = any(w in text for w in (
        "fail", "error", "could not", "unable", "fehler", "gescheitert"))
    if claims_success == claims_failure:
        return "undecidable"          # says both, or neither
    return "false" if claims_success != verdict else "true"


def _numeric_claim(text, counters):
    """-> verdict for a sentence that asserts a COUNT, against the trace's own.

    Undecidable unless the trace vouches for that exact unit: a sentence about
    lines cannot be judged by a byte count, and pretending otherwise would turn
    a missing measurement into a passing one.
    """
    if not counters:
        return "undecidable"
    verdicts = set()
    for number, unit in _NUMERIC_CLAIM.findall(text):
        key = _UNIT.get(unit.lower())
        if key and key in counters:
            verdicts.add("true" if int(number) == counters[key] else "false")
    if not verdicts:
        return "undecidable"
    # One wrong number makes the sentence wrong, however many were right.
    return "false" if "false" in verdicts else "true"


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def declared_arm(run):
    """-> the arm the run_start CLAIMS, or None if it claims none."""
    head = run[0] if run else {}
    arm = head.get("arm")
    if isinstance(arm, bool):
        return "lint" if arm else "nolint"
    return arm if arm in ("lint", "nolint") else None


def observed_arms(run):
    """-> the set of arms the executed compiles actually RAN under."""
    out = set()
    for rec in compiles(run):
        lint = (rec.get("result") or {}).get("lint")
        if lint is not None:
            out.add("lint" if lint else "nolint")
    return out


def arm_disagreement(run):
    """-> None, or why the declared arm and the compiles do not agree.

    The case this exists for was real and was caught by a person: the conductor
    attached the arm to the arguments AFTER writing the record, so the trace read
    `lint: None` while the arm was already in force. Nothing would have separated
    the arms, and the evaluator would have produced numbers anyway. It was found
    because somebody happened to look at one field — and a finding that depends
    on where a glance fell is one that will be missed next time.

    Both directions matter. A run whose compiles disagree with each other is
    broken even where nothing was declared: an arm that changes mid-run belongs
    to neither.
    """
    observed = observed_arms(run)
    if len(observed) > 1:
        return (f"the arm changed mid-run: compiles ran under "
                f"{sorted(observed)}")
    declared = declared_arm(run)
    if declared and observed and declared not in observed:
        return (f"run_start declares arm {declared!r} but the compiles ran "
                f"under {sorted(observed)[0]!r}")
    return None


def run_arm(run):
    """-> "lint" / "nolint" / None.

    The DECLARED arm wins, because it is what the conductor set out to do; the
    observed one is the check on it (`arm_disagreement`), and a run where the two
    part company never reaches this function — it is rejected at admission.
    """
    return declared_arm(run) or (sorted(observed_arms(run)) or [None])[0]


def paired(counted):
    """The 2x2 table, and the tasks that CHANGE SIDES — by name.

    Both arms run the same forty tasks, so this is a paired experiment and two
    separate rates throw the pairing away. The claim sits in the tasks that
    switch: "three tasks flip" is honest, "72 % against 65 %" is the same
    observation dressed as a rate, and the dress hides how few observations
    carry it. Named by the operator in a third proofread of the article,
    2026-08-06, against exactly this function's absence.

    `only_without_lint` is reported **even when it is empty**, because it is the
    one cell in which a HARMFUL lint would become visible, and a cell that
    disappears when it is zero is a cell nobody can check.
    """
    by_task = {}
    for run in counted:
        task = (run[0] or {}).get("task_id") or (run[0] or {}).get("task")
        arm = run_arm(run)
        if task is None or arm is None:
            continue
        by_task.setdefault(task, {})[arm] = first_attempt_ok(run)

    cells = {"both": [], "only_with_lint": [], "only_without_lint": [],
             "neither": [], "incomplete": []}
    for task, arms in sorted(by_task.items()):
        with_lint, without = arms.get("lint"), arms.get("nolint")
        if with_lint is None or without is None:
            cells["incomplete"].append(task)
        elif with_lint and without:
            cells["both"].append(task)
        elif with_lint:
            cells["only_with_lint"].append(task)
        elif without:
            cells["only_without_lint"].append(task)
        else:
            cells["neither"].append(task)

    switchers = cells["only_with_lint"] + cells["only_without_lint"]
    return {
        "pairs": len(by_task) - len(cells["incomplete"]),
        "cells": cells,
        "switchers": sorted(switchers),
        "net_gain": len(cells["only_with_lint"]) - len(cells["only_without_lint"]),
        "note": ("the result is the switchers, not the two rates: with N pairs "
                 "a difference of k tasks is carried by k observations"),
    }


def first_source(run):
    """-> (sha256, None) of the FIRST source this run wrote, or (None, why).

    The control that makes the whole comparison checkable. At `temperature 0`
    nothing distinguishes the arms until the first compile has already happened
    — the lint reads the source only inside `mac_compile` and its remedy travels
    in that call's RESULT — so the first written source must be identical in
    both arms, task by task.

    Comparing the two first-attempt RATES instead is weak twice over: two equal
    rates can come from different tasks (the pairing lesson, one level earlier),
    and if they differ, "a leak between the arms" cannot be told from "the model
    was not deterministic after all". Forty hashes against forty hashes answers
    both in one grip. Sharpened by the parallel session, 2026-08-06.

    A truncated record has NO valid content — the conductor shortens oversized
    ones and says so. Hashing it would produce a difference that is an artefact
    of the log, which is worse than reporting no comparison at all.
    """
    for rec in run:
        if rec.get("kind") != "tool" or rec.get("name") not in WRITE_TOOLS:
            continue
        if rec.get("refused"):
            continue
        args = rec.get("args") or {}
        if rec.get("truncated") or args.get("truncated"):
            return None, ("the record was shortened, so its content is not the "
                          "content that was written")
        content = args.get("content")
        if not isinstance(content, str):
            return None, "the record carries no content"
        return hashlib.sha256(content.encode("utf-8")).hexdigest(), None
    return None, "the run wrote no source"


def arm_control(counted):
    """Per task: does the first written source match across the two arms?

    This must come out CLEAN. A mismatch is a finding about the apparatus, not
    about the model — and naming which task mismatched is the difference between
    looking and guessing.
    """
    by_task = {}
    for run in counted:
        head = run[0] or {}
        task = head.get("task_id") or head.get("task")
        arm = run_arm(run)
        if task is None or arm is None:
            continue
        by_task.setdefault(task, {})[arm] = first_source(run)

    same, differ, not_comparable = [], [], []
    for task, arms in sorted(by_task.items()):
        a, b = arms.get("lint"), arms.get("nolint")
        if a is None or b is None:
            not_comparable.append({"task": task, "why": "only one arm"})
        elif a[0] is None or b[0] is None:
            not_comparable.append({"task": task, "why": a[1] or b[1]})
        elif a[0] == b[0]:
            same.append(task)
        else:
            differ.append({"task": task, "lint": a[0][:16],
                           "nolint": b[0][:16]})
    return {
        "identical": len(same), "differ": differ,
        "not_comparable": not_comparable,
        "verdict": ("clean" if not differ and same else
                    "MISMATCH — the arms diverged before the first compile"
                    if differ else "nothing to compare"),
        "note": ("at temperature 0 the arms cannot differ before the first "
                 "compile; a mismatch is a leak or a non-deterministic model, "
                 "and either way it invalidates the comparison"),
    }


def rate(hits, total):
    """-> {"hits", "of", "pct"} — never a bare percentage.

    A rate without its denominator is the shape that hides a shrinking
    denominator, which is the cheapest way to report an improvement that is not
    one.
    """
    return {"hits": hits, "of": total,
            "pct": None if not total else round(100.0 * hits / total, 1)}


def score(records, guard_commit=None, list_hash=None, k=3, malformed=()):
    runs = split_runs(records)
    counted, rejected = [], []
    for run in runs:
        why = admit(run, guard_commit, list_hash)
        (rejected if why else counted).append(
            {"run": run, "why": why} if why else run)

    first = [first_attempt_ok(r) for r in counted]
    first_decided = [v for v in first if v is not None]
    rep = [repaired_within(r, k) for r in counted]
    rep_decided = [v for v in rep if v is not None]

    terms, claims = {}, {}
    for run in counted:
        terms[termination(run)] = terms.get(termination(run), 0) + 1
        c = false_claim(run)
        claims[c] = claims.get(c, 0) + 1

    arms = {}
    for run in counted:
        for rec in compiles(run):
            arm = (rec.get("result") or {}).get("lint")
            key = {True: "lint", False: "nolint"}.get(arm, "unknown")
            arms[key] = arms.get(key, 0) + 1

    return {
        "paired": paired(counted),
        "arm_control": arm_control(counted),
        "label": LABEL,
        "runs_total": len(runs),
        "runs_counted": len(counted),
        "runs_rejected": [
            {"reason": r["why"],
             "task": (r["run"][0].get("task") if r["run"] else None)}
            for r in rejected],
        "malformed_lines": list(malformed),
        "first_attempt": rate(sum(1 for v in first_decided if v),
                              len(first_decided)),
        "first_attempt_undecided": len(first) - len(first_decided),
        f"repaired_within_{k}": rate(sum(1 for v in rep_decided if v),
                                     len(rep_decided)),
        "termination": terms,
        "false_claim": claims,
        "compiles_by_arm": arms,
    }


def render(s):
    out = [f"# {s['label']}", ""]
    out.append(f"Läufe            {s['runs_counted']} gezählt "
               f"von {s['runs_total']}")
    if s["runs_rejected"]:
        out.append(f"Verworfen        {len(s['runs_rejected'])}:")
        for r in s["runs_rejected"]:
            out.append(f"                   - {r['reason']}")
    if s["malformed_lines"]:
        out.append(f"Unlesbare Zeilen {s['malformed_lines']}")
    fa = s["first_attempt"]
    out.append(f"Erstversuchsquote {fa['hits']}/{fa['of']}"
               + (f" = {fa['pct']} %" if fa["pct"] is not None else " = —"))
    if s["first_attempt_undecided"]:
        out.append(f"                 ({s['first_attempt_undecided']} ohne "
                   f"Übersetzung, nicht im Nenner)")
    for key in s:
        if key.startswith("repaired_within_"):
            r = s[key]
            out.append(f"Reparaturquote   {r['hits']}/{r['of']}"
                       + (f" = {r['pct']} %" if r["pct"] is not None else " = —"))
    ac = s["arm_control"]
    out.append("")
    out.append(f"KONTROLLE        erste Quelle je Aufgabe, beide Arme: "
               f"{ac['verdict']}")
    out.append(f"  identisch         {ac['identical']}")
    if ac["differ"]:
        out.append(f"  ABWEICHEND        {[d['task'] for d in ac['differ']]}"
                   f"  <- vor dem ersten Compile kann das nicht sein")
    if ac["not_comparable"]:
        out.append(f"  nicht vergleichbar {[d['task'] for d in ac['not_comparable']]}")

    pr = s["paired"]
    out.append("")
    out.append(f"VERBUNDEN        {pr['pairs']} Paare — das Ergebnis sind die "
               f"Wechsler, nicht die zwei Quoten")
    out.append(f"  beide bestanden   {len(pr['cells']['both'])}")
    out.append(f"  NUR mit Lint      {len(pr['cells']['only_with_lint'])}  "
               f"{pr['cells']['only_with_lint']}")
    out.append(f"  NUR ohne Lint     {len(pr['cells']['only_without_lint'])}  "
               f"{pr['cells']['only_without_lint']}"
               + ("   <- leer, und trotzdem ausgewiesen"
                  if not pr['cells']['only_without_lint'] else "   <- ein Lint, der schadet"))
    out.append(f"  keiner bestanden  {len(pr['cells']['neither'])}")
    if pr["cells"]["incomplete"]:
        out.append(f"  unvollstaendig    {pr['cells']['incomplete']}")
    out.append(f"  Nettogewinn       {pr['net_gain']} Aufgabe(n)")
    out.append("")
    out.append(f"Terminierung     {s['termination']}")
    out.append(f"Falschbehauptung {s['false_claim']}")
    out.append(f"Übersetzungen    {s['compiles_by_arm']}")
    out.append("")
    out.append("Regression auf allgemeinem C: NICHT ABGEDECKT — braucht eine "
               "fremde Suite.")
    return "\n".join(out)


def list_hash(path):
    """SHA-256 of the task list, so "vorher festgelegt" is provable."""
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("protocol", nargs="?", help="loop_proof.log.jsonl")
    p.add_argument("--list-hash", metavar="FILE",
                   help="print the SHA-256 of a task list and exit")
    p.add_argument("--guard-commit", help="reject runs built on another guard")
    p.add_argument("--task-list", help="reject runs made against another list")
    p.add_argument("-k", type=int, default=3, help="repair rounds (default 3)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.list_hash:
        print(list_hash(args.list_hash))
        return 0
    if not args.protocol:
        p.error("a protocol file is required (or --list-hash)")

    records, malformed = read_records(args.protocol)
    s = score(records, args.guard_commit,
              list_hash(args.task_list) if args.task_list else None,
              args.k, malformed)
    print(json.dumps(s, indent=1, ensure_ascii=False) if args.json
          else render(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
