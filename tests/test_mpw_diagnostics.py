"""A diagnostic is a group of lines, and the informative ones carried no marker.

Measured 2026-08-06. The parallel session drove a probe through `mac_compile`
whose defect was deliberately NOT one of `c89_lint`'s four rules — a missing
semicolon — and then read the `.err` file back byte for byte. Two things came
out of it:

  * `READFILE` on the guest and `Catenate` over ToolServer return the SAME
    bytes, so neither the compiler nor the wire loses anything;
  * what reached the caller was the message line and a `#---` separator. The
    offending source line and the column marker were gone.

The loss was therefore ours, in `classify_diagnostics`, and it was invisible for
as long as we only tested it against errors the lint already explains: for those,
`c89[].text` carries the source line. Exactly where the lint is silent — which is
where a repair rate gets measured — the model received a bare line number.

The fixture below is that capture, verbatim.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host"))
import mpw  # noqa: E402

# Captured on the guest 2026-08-06 (MeinMac:ErrProbe:err.c, missing semicolon).
# Written with an explicit \n escape inside the C string so the fixture holds
# what SC wrote rather than what a here-doc made of it — the first attempt at
# this test lost the source line to its own shell quoting.
SC_MISSING_SEMICOLON = (
    "SC C Compiler 8.9.0d3e1\r"
    "Copyright (C) 1985-2000 by Apple Computer, Inc.\r"
    "\r"
    '    printf("%d\\n", a);\r'
    "    ^\r"
    "File \"MeinMac:ErrProbe:err.c\"; line 7 #Error: ';' expected\r"
    "#-----------------------\r"
)

NO_TYPE_ASM = ('### Cannot open ":src:dlgpatch.a"\n'
               '# Not a text file (OS Error -31001)\n\n'
               'Asm - Execution terminated!')


class TheGroupTravelsTogether(unittest.TestCase):

    def setUp(self):
        self.got = mpw.classify_diagnostics(SC_MISSING_SEMICOLON)

    def test_one_diagnostic_is_one_entry(self):
        self.assertEqual(len(self.got["errors"]), 1, self.got["errors"])

    def test_the_message_is_still_there(self):
        self.assertIn("line 7 #Error: ';' expected", self.got["errors"][0])

    def test_the_offending_source_line_travels_with_it(self):
        """The point of the whole change: without this the model gets a line
        number and has to guess what is on that line."""
        self.assertIn('printf("%d\\n", a);', self.got["errors"][0])

    def test_the_column_marker_keeps_its_column(self):
        """Stripping the group would leave a caret pointing at nothing. It is
        the one line in the capture whose meaning IS its indentation."""
        lines = self.got["errors"][0].split("\n")
        source = next(l for l in lines if "printf" in l)
        caret = next(l for l in lines if l.strip() == "^")
        self.assertEqual(caret.index("^"), source.index("printf"),
                         f"caret no longer aligns:\n{self.got['errors'][0]}")

    def test_the_separator_is_not_an_error(self):
        """`#---` matches `^#` and used to be sorted into errors, where it says
        nothing at all."""
        self.assertFalse(any(set(e.strip()) <= {"#", "-"} for e in self.got["errors"]),
                         self.got["errors"])

    def test_the_banner_is_neither_an_error_nor_a_prefix(self):
        joined = " ".join(self.got["errors"] + self.got["warnings"])
        self.assertNotIn("Copyright", joined)
        self.assertNotIn("C Compiler 8.9", joined)


class WhatMustNotChange(unittest.TestCase):
    """The 2026-08-02 findings this classifier exists for."""

    def test_both_wordings_of_the_missing_type_are_errors(self):
        got = mpw.classify_diagnostics(NO_TYPE_ASM)
        self.assertTrue(any("-31001" in e for e in got["errors"]), got["errors"])
        self.assertTrue(any("SetFile -t TEXT" in r for r in got["remedies"]))

    def test_error_52_is_still_a_warning(self):
        got = mpw.classify_diagnostics(
            "### Link: Warning: File was not needed for link: (Error 52) StdCLib.o")
        self.assertEqual(got["errors"], [])
        self.assertTrue(got["warnings"])

    def test_a_prefix_never_crosses_a_diagnostic(self):
        """Two diagnostics, one loose line between them. The loose line belongs
        to the SECOND — reading forwards would give it to the first, which is
        the failure mode that makes a wrong answer look authoritative."""
        got = mpw.classify_diagnostics(
            "File \"a.c\"; line 1 #Error: first\n"
            "    int x = ;\n"
            "File \"a.c\"; line 9 #Error: second\n")
        self.assertEqual(len(got["errors"]), 2, got["errors"])
        self.assertNotIn("int x", got["errors"][0])
        self.assertIn("int x", got["errors"][1])

    def test_a_bare_message_stays_a_bare_message(self):
        """Nothing invented when there is no prefix to attach."""
        got = mpw.classify_diagnostics("Fatal error: unable to open file 'x.c'")
        self.assertEqual(got["errors"], ["Fatal error: unable to open file 'x.c'"])


class EveryDiagnosticClassSurvivesTheFilter(unittest.TestCase):
    """`SC` writes more than `#Error`, and a filter that forgets that lies.

    Measured 2026-08-06 on the parallel session's data, and it cost seven hours:
    their count matched the marker `#Error`, so `#Lexical error` never appeared
    in it. 152 "error lines" were really 193 diagnosis lines — and the two lines
    it dropped were the ones naming the actual cause (`ILLEGAL CHARACTER, ASCII
    8 DECIMAL`, from a `'\\b'` that a shell had turned into a raw control
    character before the file ever reached the guest). The tally looked complete
    the whole time.

    `classify_diagnostics` does not have that defect — it matches on the WORD,
    not on `#Error`. That is currently true by way of one regex alternation, and
    a single edit narrowing it to the marker would reintroduce the exact failure
    without breaking anything else. Hence this test: the breadth is a decision,
    not an accident."""

    LEXICAL_AND_FATAL = (
        '        else if (c == \x08) printf("\\bbackspace");\r'
        "        ^\r"
        'File "t06.c"; line 7 #Lexical error: ILLEGAL CHARACTER, ASCII 8 DECIMAL\r'
        "#-----------------------\r"
        'File "t06.c"; line 8 #Lexical error: unrecognized token\r'
        "#-----------------------\r"
        "### SC - Fatal error: too many errors\r"
    )

    def test_a_lexical_error_is_an_error(self):
        got = mpw.classify_diagnostics(self.LEXICAL_AND_FATAL)
        self.assertTrue(any("ILLEGAL CHARACTER" in e for e in got["errors"]),
                        got["errors"])
        self.assertTrue(any("unrecognized token" in e for e in got["errors"]),
                        got["errors"])

    def test_a_fatal_error_is_an_error(self):
        got = mpw.classify_diagnostics(self.LEXICAL_AND_FATAL)
        self.assertTrue(any("Fatal error" in e for e in got["errors"]),
                        got["errors"])

    def test_none_of_the_three_is_silently_dropped(self):
        """The count is the point. A classifier that returns two of three
        diagnostics is worse than one that returns none: it looks like an
        answer."""
        got = mpw.classify_diagnostics(self.LEXICAL_AND_FATAL)
        self.assertEqual(len(got["errors"]), 3, got["errors"])


class TheRemedyThatNeedsTheSourceLine(unittest.TestCase):
    """`expression expected` names nothing. The source line names the defect.

    Measured 2026-08-06 over eighty runs: of 135 lines carrying that message, 84
    were a declaration in a for-head — which the lint catches — and 46 were a
    declaration after a statement, the second-largest cause in the measurement.
    Until the diagnostic group travelled together, there was nothing to tell them
    apart by, and the model was told only the message.

    This is NOT the lint rule c89_lint deliberately does not have. That one would
    run before the compiler and could fire on correct code. This fires only on a
    line the compiler has already rejected, so a false positive on correct code
    is impossible by construction."""

    DECL_AFTER_STATEMENT = (
        '    printf("Fahrenheit\\tCelsius\\n");\r'
        "    int i;\r"
        "    ^\r"
        'File "MeinMac:Bench:t01.c"; line 5 #Error: expression expected\r'
        "#-----------------------\r"
    )

    def test_the_declaration_after_a_statement_gets_named(self):
        got = mpw.classify_diagnostics(self.DECL_AFTER_STATEMENT)
        self.assertTrue(any("START of its block" in r for r in got["remedies"]),
                        got["remedies"])

    def test_it_says_where_and_not_only_what(self):
        """The whole lesson of the day: an instruction that names the addition
        and not the place sends the model to the wrong line."""
        r = next(r for r in mpw.classify_diagnostics(self.DECL_AFTER_STATEMENT)["remedies"]
                 if "START of its block" in r)
        self.assertIn("before the first statement", r)
        self.assertIn("keep the declarations that are already there", r)

    C99_FOR_HEAD = (
        '    for (int i = 2; i < 10; i++) {\r'
        "    ^\r"
        'File "MeinMac:Bench:t07.c"; line 5 #Error: expression expected\r'
        "#-----------------------\r"
    )

    def test_a_c99_for_head_is_NOT_called_a_declaration_after_a_statement(self):
        """The one that would have cost a session. Of the 135 `expression
        expected` lines measured over both arms, 84 are C99 for-heads and 46 are
        declarations after a statement — SC gives them the SAME message, and
        `for (int i = 0; …)` contains `int i`, so an unanchored pattern matches
        both.

        Firing here would not leave a gap, it would MISDIRECT: it would tell the
        model to move the loop line above the first statement. That is precisely
        the wrong instruction, and a wrong instruction from this remedy is what
        broke one of the two runs that followed it. The for-head is `c89_lint`'s
        rule; the `^` anchor is the entire border between the two."""
        got = mpw.classify_diagnostics(self.C99_FOR_HEAD)
        self.assertTrue(got["errors"], got)          # still reported as an error
        self.assertEqual(got["remedies"], [], got["remedies"])

    def test_the_same_message_without_a_declaration_says_nothing(self):
        """`expression expected` alone must not trigger it — that is the whole
        difference between this and a rule that guesses."""
        other = ('    x = = 3;\r'
                 '    ^\r'
                 'File "a.c"; line 4 #Error: expression expected\r')
        got = mpw.classify_diagnostics(other)
        self.assertFalse(any("START of its block" in r for r in got["remedies"]),
                         got["remedies"])

    def test_a_bare_message_with_no_group_says_nothing(self):
        """Traces from before the grouping repair have no source line. They must
        degrade to silence, not to a guess."""
        self.assertEqual(
            mpw.group_remedies(["File \"a.c\"; line 5 #Error: expression expected"]),
            [])

    def test_the_remedy_appears_once_however_often_the_error_does(self):
        """Ten identical violations are one lesson — the same rule c89_lint
        follows, for the same reason: repetition costs context and teaches
        nothing the first line did not."""
        got = mpw.classify_diagnostics(self.DECL_AFTER_STATEMENT * 3)
        self.assertEqual(sum("START of its block" in r for r in got["remedies"]), 1)


class QuotingPathsForMPW(unittest.TestCase):
    """The apostrophe that made SC fail without saying anything.

    Measured 2026-08-08 over 49 Developer-CD sources: naive `'{path}'` split
    the command at the apostrophe of `What's New?` — 27 of 49 silently, 0 of
    the remaining 22. The tell was a failure with no diagnostics at all, which
    is why it read as a compiler mystery for a whole measurement.
    """

    def test_an_ordinary_path_is_just_quoted(self):
        self.assertEqual(mpw.quote("MeinMac:MPW:main.c"), "'MeinMac:MPW:main.c'")

    def test_spaces_and_mac_characters_need_nothing_extra(self):
        """Inside single quotes MPW takes everything literally, so the
        characters people expect to be dangerous are already safe."""
        for path in ("a:b c:d.c", "a:WASTE Demo ƒ:x.c", "a:• Obsolete:y.c",
                     "a:{braces}:z.c", "a:" + mpw.ESCAPE + ":w.c"):
            self.assertEqual(mpw.quote(path), "'" + path + "'")

    def test_the_apostrophe_is_closed_escaped_and_reopened(self):
        self.assertEqual(mpw.quote("CD:What's New?:a.c"),
                         "'CD:What'" + mpw.ESCAPE + "''s New?:a.c'")

    def test_every_apostrophe_is_escaped_not_only_the_first(self):
        self.assertEqual(mpw.quote("o'n'e").count(mpw.ESCAPE), 2)

    @staticmethod
    def _delimiters(text):
        """Quote characters that still DELIMIT — an escaped one is a literal.

        Counting raw quotes was this test's own first version of the defect it
        guards: it called the correct output unbalanced, because `∂'` looks
        like a quote and is not one.
        """
        return text.replace(mpw.ESCAPE + "'", "").count("'")

    def test_the_quoted_form_is_balanced(self):
        """An odd number of DELIMITING quotes is exactly the defect: the
        command line ends somewhere inside the path."""
        for path in ("plain", "What's", "a'b'c'd", "'leading", "trailing'"):
            self.assertEqual(self._delimiters(mpw.quote(path)) % 2, 0, path)

    def test_run_step_quotes_the_artefact_and_the_error_file(self):
        """The four command lines run_step builds are where a path meets the
        shell, so the quoting belongs there and not in each caller."""
        sent = []

        def send(cmd, timeout=30.0):
            sent.append(cmd)
            return ""

        mpw.run_step(send, "SC 'x'", "Disk:What's New?:a.o",
                     "Disk:What's New?:a.o.err")
        self.assertTrue(sent, "run_step sent nothing")
        for cmd in sent:
            self.assertEqual(self._delimiters(cmd) % 2, 0, cmd)
            if "What" in cmd:
                self.assertIn(mpw.ESCAPE, cmd)


if __name__ == "__main__":
    unittest.main()
