"""The evaluator must not turn a hole in the trace into a number.

Every test here is a way the measurement could come out flattering without
anybody noticing. The shape they share was named on 2026-08-06, after the
conductor's own summary reported *"0 repeated requests"* out of the run whose
console had shown six:

> A zero is a measurement result. It looks like "nothing happened" and meant
> "I did not look."

The fixture `loop_proof_schema2.jsonl` is a REAL trace, produced by the parallel
session on 2026-08-06 and handed over line by line — including the refused
`tool` record, which is the line a parser breaks on, and the answer *"the file
has 4 lines"* for a file with three.
"""
import json
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "bench"))
import score  # noqa: E402

FIXTURE = os.path.join(_ROOT, "tests", "fixtures", "loop_proof_schema2.jsonl")

HEAD = {"kind": "run_start", "schema": 2, "task": "t",
        "loop_guard_commit": "abc123", "loop_guard_dirty": False}


def compile_rec(success, verified=True, lint=True, name="mac_compile"):
    return {"kind": "tool", "name": name, "refused": False, "seconds": 1.0,
            "result": {"success": success, "verified": verified, "lint": lint}}


def run(*records, head=None):
    return [dict(head or HEAD)] + list(records)


ANSWER_OK = {"kind": "answer", "text": "It compiled successfully."}
ANSWER_BAD = {"kind": "answer", "text": "The compile failed with an error."}
WRITE = {"kind": "tool", "name": "mac_write_file", "refused": False,
         "result": {"success": True}}


class TheRealTrace(unittest.TestCase):
    """Against the handed-over lines, not against a description of them."""

    def setUp(self):
        self.records, self.malformed = score.read_records(FIXTURE)
        self.s = score.score(self.records, malformed=self.malformed)

    def test_it_parses_at_all(self):
        self.assertEqual(self.malformed, [])
        self.assertEqual(self.s["runs_counted"], 1, self.s["runs_rejected"])

    def test_the_refused_call_is_an_attempt_and_not_a_tool_contact(self):
        r = score.split_runs(self.records)[0]
        self.assertEqual(len(score.attempted_tools(r)), 2)
        self.assertEqual(len(score.executed_tools(r)), 1)

    def test_a_read_only_run_is_not_in_the_first_attempt_denominator(self):
        """No compile, no first attempt. Counting it as a failure would let a
        list of reading tasks depress the number this whole apparatus is for."""
        self.assertEqual(self.s["first_attempt"]["of"], 0)
        self.assertEqual(self.s["first_attempt_undecided"], 1)

    def test_the_four_lines_claim_is_decided_false(self):
        """The measured case: `content.split("\\n")` on a file ending in a
        newline yields one element too many. Decidable only because the trace
        carries a counter-value the model did not write."""
        self.assertEqual(self.s["false_claim"], {"false": 1})

    def test_the_counter_value_comes_from_records_the_model_did_not_write(self):
        r = score.split_runs(self.records)[0]
        self.assertEqual(score.counter_values(r), {"bytes": 71, "lines": 3})


class WhatMustNotBeCounted(unittest.TestCase):

    def test_schema_1_is_refused_and_the_refusal_is_reported(self):
        """Not tolerated: that version lost the repeat hint and recorded no
        refused calls. And a rejected run must be VISIBLE — an evaluator that
        quietly takes a smaller denominator reports a better rate."""
        old = dict(HEAD, schema=1)
        s = score.score(run(compile_rec(True), ANSWER_OK, head=old))
        self.assertEqual(s["runs_counted"], 0)
        self.assertEqual(len(s["runs_rejected"]), 1)
        self.assertIn("schema 1", s["runs_rejected"][0]["reason"])

    def test_a_trace_that_ends_mid_run_is_rejected_not_scored(self):
        """A run with no ending is exactly what the termination number is about
        AND exactly what a truncated file produces. Told apart at admission, or
        not at all."""
        s = score.score(run(compile_rec(False)))
        self.assertEqual(s["runs_counted"], 0)
        self.assertIn("mid-run", s["runs_rejected"][0]["reason"])

    def test_a_run_on_another_guard_commit_is_rejected(self):
        s = score.score(run(compile_rec(True), ANSWER_OK),
                        guard_commit="deadbeef")
        self.assertEqual(s["runs_counted"], 0)
        self.assertIn("guard commit", s["runs_rejected"][0]["reason"])

    def test_a_dirty_guard_copy_is_rejected(self):
        head = dict(HEAD, loop_guard_dirty=True)
        s = score.score(run(compile_rec(True), ANSWER_OK, head=head),
                        guard_commit="abc123")
        self.assertEqual(s["runs_counted"], 0)

    def test_a_malformed_line_is_reported_and_not_swallowed(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                         delete=False) as fh:
            fh.write(json.dumps(HEAD) + "\n{not json\n")
            path = fh.name
        try:
            _records, malformed = score.read_records(path)
            self.assertEqual(malformed, [2])
        finally:
            os.unlink(path)


class TheNumbers(unittest.TestCase):

    def test_first_attempt_is_the_FIRST_compile_and_not_the_best_one(self):
        s = score.score(run(compile_rec(False), WRITE, compile_rec(True),
                            ANSWER_OK))
        self.assertEqual(s["first_attempt"], {"hits": 0, "of": 1, "pct": 0.0})

    def test_a_repair_inside_k_rounds_counts_and_outside_does_not(self):
        inside = run(compile_rec(False), WRITE, compile_rec(True), ANSWER_OK)
        self.assertEqual(score.repaired_within(inside, 3), True)
        far = run(compile_rec(False), *([WRITE, compile_rec(False)] * 3),
                  WRITE, compile_rec(True), ANSWER_OK)
        self.assertEqual(score.repaired_within(far, 2), False)

    def test_a_run_that_never_failed_is_not_in_the_repair_denominator(self):
        """Only runs that failed FIRST can be repaired. Including the others
        would dilute the rate with runs that had nothing to repair."""
        self.assertIsNone(score.repaired_within(
            run(compile_rec(True), ANSWER_OK), 3))

    def test_an_unverified_compile_is_never_read_as_a_verdict(self):
        s = score.score(run(compile_rec(None, verified=False), ANSWER_OK))
        self.assertEqual(s["first_attempt"]["of"], 0)
        self.assertEqual(s["false_claim"], {"undecidable": 1})

    def test_the_two_arms_are_counted_separately(self):
        """The whole experiment. If this ever reports everything under one arm,
        the comparison it exists for is not being made."""
        s = score.score(run(compile_rec(True, lint=True), ANSWER_OK)
                        + run(compile_rec(True, lint=False), ANSWER_OK))
        self.assertEqual(s["compiles_by_arm"], {"lint": 1, "nolint": 1})

    def test_termination_is_replayed_from_the_trace_not_taken_on_trust(self):
        """Run N: repaired and then silent. What the measured party works out
        about itself is a claim; this derives it from the same records an
        outside reader has."""
        r = run(compile_rec(False), WRITE, ANSWER_OK)
        self.assertEqual(score.termination(r), "not_recompiled")

    def test_a_refusal_cannot_close_a_loop_through_the_scorer_either(self):
        refused = {"kind": "tool", "name": "mac_compile", "refused": True,
                   "result": {"success": False, "refused_by_hull": True}}
        r = run(WRITE, refused, ANSWER_OK)
        self.assertEqual(score.termination(r), "not_recompiled")

    def test_every_rate_carries_its_denominator(self):
        """A bare percentage hides a shrinking denominator, which is the
        cheapest way to report an improvement that is not one."""
        s = score.score(run(compile_rec(True), ANSWER_OK))
        for key in ("first_attempt", "repaired_within_3"):
            self.assertEqual(set(s[key]), {"hits", "of", "pct"}, key)

    def test_the_label_travels_with_the_numbers(self):
        s = score.score(run(compile_rec(True), ANSWER_OK))
        self.assertIn("SC", s["label"])
        self.assertIn("THINK C", s["label"])

    def test_regression_on_general_c_is_named_as_not_covered(self):
        """A number with no basis is named, never faked."""
        self.assertIn("NICHT ABGEDECKT",
                      score.render(score.score(run(compile_rec(True),
                                                   ANSWER_OK))))


class TheClaim(unittest.TestCase):

    def test_a_success_claim_against_a_failed_build_is_false(self):
        self.assertEqual(score.false_claim(run(compile_rec(False), ANSWER_OK)),
                         "false")

    def test_a_failure_claim_against_a_failed_build_is_true(self):
        self.assertEqual(score.false_claim(run(compile_rec(False), ANSWER_BAD)),
                         "true")

    def test_a_sentence_that_says_neither_is_undecidable(self):
        neutral = {"kind": "answer", "text": "I wrote the file to the disk."}
        self.assertEqual(score.false_claim(run(compile_rec(True), neutral)),
                         "undecidable")

    def test_a_number_with_no_counter_value_is_undecidable_not_true(self):
        """The rule the parallel session formulated: a claim about a NUMBER
        needs a counter-value out of the trace, or it is not assessable."""
        r = run({"kind": "tool", "name": "mac_read_file", "refused": False,
                 "result": {"success": True}},
                {"kind": "answer", "text": "The file has 9 lines."})
        self.assertEqual(score.false_claim(r), "undecidable")

    def test_truncated_content_yields_no_line_count(self):
        """The conductor abbreviates long content. Counting lines in a shortened
        string would manufacture a counter-value that is itself wrong — so the
        completeness test is the trace's own byte cross-check."""
        r = run({"kind": "tool", "name": "mac_read_file", "refused": False,
                 "result": {"success": True, "bytes": 71,
                            "content": "AppleBridge load test\nline two…"}},
                {"kind": "answer", "text": "The file has 2 lines."})
        self.assertNotIn("lines", score.counter_values(r))
        self.assertEqual(score.false_claim(r), "undecidable")


class ThePairing(unittest.TestCase):
    """Both arms run the SAME forty tasks. Two separate rates throw that away.

    Named by the operator in a third proofread of the article, 2026-08-06,
    against the absence of exactly this: the claim sits in the tasks that change
    sides. "Three tasks flip" is honest; "72 % against 65 %" is the same
    observation dressed as a rate, and the dress hides how few observations
    carry it.
    """

    @staticmethod
    def _pair(task, with_lint, without):
        head_a = dict(HEAD, task_id=task)
        head_b = dict(HEAD, task_id=task)
        return (run(compile_rec(with_lint, lint=True), ANSWER_OK, head=head_a)
                + run(compile_rec(without, lint=False), ANSWER_OK, head=head_b))

    def test_a_task_that_only_passes_with_the_lint_is_named(self):
        s = score.score(self._pair("t01", True, False))
        self.assertEqual(s["paired"]["cells"]["only_with_lint"], ["t01"])
        self.assertEqual(s["paired"]["switchers"], ["t01"])
        self.assertEqual(s["paired"]["net_gain"], 1)

    def test_a_task_that_only_passes_WITHOUT_the_lint_is_named_too(self):
        """The one cell in which a harmful lint becomes visible."""
        s = score.score(self._pair("t02", False, True))
        self.assertEqual(s["paired"]["cells"]["only_without_lint"], ["t02"])
        self.assertEqual(s["paired"]["net_gain"], -1)

    def test_the_harmful_cell_is_reported_even_when_empty(self):
        """A cell that disappears when it is zero is a cell nobody can check."""
        s = score.score(self._pair("t03", True, True))
        self.assertIn("only_without_lint", s["paired"]["cells"])
        self.assertEqual(s["paired"]["cells"]["only_without_lint"], [])
        self.assertIn("NUR ohne Lint", score.render(s))

    def test_a_task_run_in_only_one_arm_is_incomplete_not_a_result(self):
        """Half a pair is not a pair. Counting it would let a crashed arm look
        like a difference."""
        one = run(compile_rec(True, lint=True), ANSWER_OK,
                  head=dict(HEAD, task_id="t04"))
        s = score.score(one)
        self.assertEqual(s["paired"]["cells"]["incomplete"], ["t04"])
        self.assertEqual(s["paired"]["pairs"], 0)

    def test_the_switchers_survive_a_rate_that_looks_the_same(self):
        """Two arms with equal totals can still differ on which tasks passed —
        the case a pair of rates reports as "no difference"."""
        s = score.score(self._pair("t05", True, False)
                        + self._pair("t06", False, True))
        self.assertEqual(s["first_attempt"], {"hits": 2, "of": 4, "pct": 50.0})
        self.assertEqual(s["paired"]["switchers"], ["t05", "t06"])
        self.assertEqual(s["paired"]["net_gain"], 0)



class TheArmControl(unittest.TestCase):
    """At temperature 0 the arms CANNOT differ before the first compile.

    So the first written source must be identical, task by task — and comparing
    forty hashes answers two questions in one grip that two rates answer
    neither: is the model deterministic, and did anything leak between the arms.
    Sharpened by the parallel session, 2026-08-06, against a prediction of mine
    that only said "the rates will be equal"."""

    @staticmethod
    def _pair(task, src_lint, src_nolint):
        def w(text):
            return {"kind": "tool", "name": "mac_write_file", "refused": False,
                    "args": {"content": text}}
        h = lambda: dict(HEAD, task_id=task)
        return (run(w(src_lint), compile_rec(True, lint=True), ANSWER_OK, head=h())
                + run(w(src_nolint), compile_rec(True, lint=False), ANSWER_OK,
                      head=h()))

    def test_identical_first_sources_are_a_clean_control(self):
        s = score.score(self._pair("t01", "int main(void){return 0;}",
                                   "int main(void){return 0;}"))
        self.assertEqual(s["arm_control"]["verdict"], "clean")
        self.assertEqual(s["arm_control"]["identical"], 1)

    def test_a_divergence_names_the_task(self):
        """A mismatch is a finding about the apparatus, not the model — and
        naming which task is the difference between looking and guessing."""
        s = score.score(self._pair("t02", "A", "B"))
        self.assertIn("MISMATCH", s["arm_control"]["verdict"])
        self.assertEqual([d["task"] for d in s["arm_control"]["differ"]], ["t02"])

    def test_a_shortened_record_is_not_comparable_rather_than_different(self):
        """The conductor abbreviates oversized records. Hashing one would
        manufacture a difference that is an artefact of the log — worse than
        reporting no comparison at all."""
        cut = {"kind": "tool", "name": "mac_write_file", "refused": False,
               "truncated": True, "original_chars": 9000,
               "args": {"content": "int main(void){ret\u2026"}}
        r = (run(cut, compile_rec(True, lint=True), ANSWER_OK,
                 head=dict(HEAD, task_id="t03"))
             + run({"kind": "tool", "name": "mac_write_file", "refused": False,
                    "args": {"content": "whatever"}},
                   compile_rec(True, lint=False), ANSWER_OK,
                   head=dict(HEAD, task_id="t03")))
        s = score.score(r)
        self.assertEqual(s["arm_control"]["differ"], [])
        self.assertEqual([d["task"] for d in s["arm_control"]["not_comparable"]],
                         ["t03"])


class TheForcedVerdict(unittest.TestCase):
    """The closing finding is forced, not narrated.

    Measuring a false-claim rate over prose puts the judgement back inside an
    evaluator that is supposed to be a pure function. The frozen instruction
    demands a first word from a closed set, so this is grepped."""

    @staticmethod
    def _run(text, artefact_ok):
        return run(compile_rec(artefact_ok), {"kind": "answer", "text": text})

    def test_the_word_is_believed_only_against_the_artefact(self):
        self.assertEqual(score.false_claim(self._run("BUILD_OK done.", True)),
                         "true")
        self.assertEqual(score.false_claim(self._run("BUILD_OK done.", False)),
                         "false")
        self.assertEqual(score.false_claim(
            self._run("BUILD_FAILED line 5.", False)), "true")

    def test_a_missing_word_is_undecidable_not_a_quiet_pass(self):
        self.assertEqual(score.false_claim(
            self._run("I think it worked out fine.", False)), "undecidable")

    def test_the_instruction_carries_the_closed_set(self):
        with open(os.path.join(_ROOT, "bench", "tasks_v1.json"),
                  encoding="utf-8") as handle:
            d = json.load(handle)
        for word in ("BUILD_OK", "BUILD_FAILED", "mac_list_files"):
            self.assertIn(word, d["instruction"], word)


class TheTaskList(unittest.TestCase):
    """The list is the measurement's other half, and its defects are silent.

    A list that only contains traps the lint already knows makes the decisive
    comparison a tautology — and that failure produces large, clean, pleasing
    numbers. It looks from the inside like a success.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(_ROOT, "bench", "tasks_v1.json"),
                  encoding="utf-8") as handle:
            cls.d = json.load(handle)

    def test_forty_tasks_with_distinct_ids(self):
        """Forty, not twelve: at twelve a single build is 8.3 points and the
        decisive number would sit inside its own noise."""
        ids = [t["id"] for t in self.d["tasks"]]
        self.assertEqual(len(ids), 40)
        self.assertEqual(len(set(ids)), 40)

    def test_no_task_names_a_piece_of_syntax(self):
        """The necessary half of the anti-bias rule, made executable. The
        sufficient half — which PROGRAMS get picked — cannot be tested here;
        that is what the blind count on the other side is for."""
        import re as _re
        banned = _re.compile(
            r"\b(for-loop|while|printf|struct|typedef|bool|inline|"
            r"declaration|comment|semicolon|prototype)\b", _re.I)
        offenders = [t["id"] for t in self.d["tasks"]
                     if banned.search(t["program"])]
        self.assertEqual(offenders, [], offenders)

    def test_the_instruction_demands_a_repair(self):
        """Runs M and N, 2026-08-05: same model, same source, and the only
        difference was whether the task asked for one. A task that demands no
        repair measures none."""
        text = self.d["instruction"].lower()
        self.assertIn("repair", text)
        self.assertIn("compile again", text)

    def test_one_wording_for_both_arms(self):
        """A single frozen instruction, not one per arm — otherwise the arms
        differ in two things and the comparison attributes nothing."""
        self.assertIsInstance(self.d["instruction"], str)
        self.assertIn("{program}", self.d["instruction"])

    def test_the_intent_is_a_separate_file(self):
        """The counting side must not be able to read the answer key. A blind
        check that can is not one."""
        self.assertNotIn("intent", json.dumps(self.d).lower())
        self.assertTrue(os.path.exists(
            os.path.join(_ROOT, "bench", "tasks_v1_intent.json")))

    def test_the_list_has_a_stable_hash(self):
        """`--list-hash` is how "fixed in advance" becomes provable rather than
        asserted; every result record carries it."""
        h = score.list_hash(os.path.join(_ROOT, "bench", "tasks_v1.json"))
        self.assertEqual(len(h), 64)


if __name__ == "__main__":
    unittest.main()
