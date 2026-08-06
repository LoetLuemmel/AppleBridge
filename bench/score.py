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
    if not cs:
        return _numeric_claim(answer["text"], counter_values(run))
    verdict = _verdict(cs[-1])
    if verdict is None:
        return "undecidable"

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
