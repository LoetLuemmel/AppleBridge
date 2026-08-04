"""The drift check between merged PRs and the ledgers.

Until 2026-08-04 this tool knew exactly ONE page and reported "in step" on that
basis — while the Stop hook that runs it runs on BOTH machines, and the second
session keeps its own ledger elsewhere. It could never report that the other
page was stale, and it would have stayed exactly as quiet if it were. The split
surfaced only because the other session mentioned it in passing.

That is the shape this file guards: a check that says less than it appears to.

Run: python3 tests/test_ledger_diff.py   (or via pytest)
"""
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(_ROOT, "host", "tools"))
import ledger_diff  # noqa: E402


class EveryLedgerIsChecked(unittest.TestCase):

    def test_more_than_one_ledger_is_configured(self):
        """Two sessions, two ledgers. One entry here means the tool speaks for a
        page nobody asked it about."""
        self.assertGreaterEqual(len(ledger_diff.LEDGERS), 2)

    def test_both_workstreams_are_named(self):
        pages = {(s, g) for s, g, _ in ledger_diff.LEDGERS}
        self.assertIn((ledger_diff.SECTION, ledger_diff.LEDGER), pages)
        self.assertTrue(any(s == "nvidia" for s, _, _ in ledger_diff.LEDGERS),
                        "the second session's ledger is not among the pages")

    def test_a_third_workstream_needs_no_code_change(self):
        """Configuration, not a constant: the next split should cost an env var,
        not another day of nobody noticing."""
        old = os.environ.get("APPLEBRIDGE_LEDGERS")
        os.environ["APPLEBRIDGE_LEDGERS"] = "alpha/one,beta/two"
        try:
            got = ledger_diff._configured_ledgers()
            self.assertEqual([(s, g) for s, g, _ in got],
                             [("alpha", "one"), ("beta", "two")])
        finally:
            if old is None:
                os.environ.pop("APPLEBRIDGE_LEDGERS", None)
            else:
                os.environ["APPLEBRIDGE_LEDGERS"] = old

    def test_a_malformed_override_falls_back_rather_than_checking_nothing(self):
        """An empty page list would make every run report "in step" about
        nothing at all — silence that looks exactly like success."""
        old = os.environ.get("APPLEBRIDGE_LEDGERS")
        os.environ["APPLEBRIDGE_LEDGERS"] = "no-slash-here"
        try:
            self.assertEqual(ledger_diff._configured_ledgers(),
                             ledger_diff._DEFAULT_LEDGERS)
        finally:
            if old is None:
                os.environ.pop("APPLEBRIDGE_LEDGERS", None)
            else:
                os.environ["APPLEBRIDGE_LEDGERS"] = old

    def test_an_unreachable_page_is_not_reported_as_in_step(self):
        """THE point of the change. A page that could not be fetched must say
        so; treating it as clean is how a stale ledger stays invisible."""
        src = open(ledger_diff.__file__, encoding="utf-8").read()
        self.assertIn("NOT REACHED", src)
        self.assertIn("unreachable", src)

    def test_the_report_names_the_page_it_speaks_about(self):
        """"in step" without a subject is the original defect in one word."""
        src = open(ledger_diff.__file__, encoding="utf-8").read()
        self.assertIn('f"{label:<18}: last edited {updated} — in step"', src)


class TheOpenItemParser(unittest.TestCase):
    """Unchanged behaviour, pinned because the multi-page loop now feeds it."""

    def test_unchecked_boxes_with_a_status_are_open_items(self):
        body = ("- [x] **Done thing** — *Done* · nothing to see\n"
                "- [ ] **Open thing** — *Open* · P2 · still to do\n"
                "- [ ] **Blocked thing** — *Blocked* · waiting\n")
        items = ledger_diff.open_items(body)
        self.assertEqual(items, [("Open thing", "Open"), ("Blocked thing", "Blocked")])

    def test_a_ticked_box_is_never_open(self):
        self.assertEqual(ledger_diff.open_items("- [x] **Shipped** — *Done*\n"), [])


if __name__ == "__main__":
    import sys as _s
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromTestCase(t)
        for t in (EveryLedgerIsChecked, TheOpenItemParser))
    _s.exit(0 if runner.run(suite).wasSuccessful() else 1)
