"""Name the C99 habits MPW's 1994 compiler will not take.

Measured 2026-08-05, and the first defect of the day that lies in the MODEL
rather than in a tool: asked for a C program, `qwen2.5-coder:7b` wrote
`for (int i = 2; i < 10; i++)` and `SC` answered `line 5 #Error: expression
expected`. A declaration in the for initialiser is C99; `SC` is C89.

What makes it a tool rather than a sentence: the next run's prompt named the
construct explicitly, and the model moved the variable declarations to the top
of the block and left the for-head standing. Partly obeyed, named rule broken.

The tests that matter most here are the FALSE-POSITIVE ones. A lint over C by
regex will produce them, and each one teaches a reader to ignore the tool —
which costs more than the finding was worth.

Run: python3 tests/test_c89_lint.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import c89_lint as L  # noqa: E402

# Verbatim from the run, reduced to the shape that failed.
MODEL_SOURCE = """#include <stdio.h>
int main(void)
{
    int a = 0, b = 1, c;
    for (int i = 2; i < 10; i++) {
        c = a + b; a = b; b = c;
    }
    return 0;
}
"""


def rules(src):
    return [f["rule"] for f in L.check(src)]


# --- the measured case ------------------------------------------------------
def test_the_construct_the_model_actually_wrote_is_found():
    found = L.check(MODEL_SOURCE)
    assert [f["rule"] for f in found] == ["decl_in_for"]
    assert found[0]["line"] == 5


def test_the_finding_says_what_to_write_instead():
    """`expression expected` does not tell anyone to move a declaration. The
    rewrite is the part a caller can act on — and the run that failed showed a
    model that read the compiler correctly and still could not fix it."""
    r = L.remedies(L.check(MODEL_SOURCE))[0]
    assert "int i;" in r and "C99" in r


def test_the_fix_text_is_copy_safe():
    """The remedy is read by the thing that has to act on it. An earlier
    version suggested "`int i; for (i = 0; …)`" — and a model that copies a
    suggestion literally would write an ellipsis into its source. No
    placeholders, no templates."""
    for _, _, _, fix in L.RULES:
        assert "…" not in fix and "..." not in fix, fix


def test_two_loops_are_one_lesson():
    """Ten `//` comments are one lesson, not ten — repeating it costs context
    on a node with 2 GB free and teaches nothing the first line did not."""
    src = MODEL_SOURCE + "int f(void){ for (int j = 0; j < 2; j++) {} return 0; }\n"
    assert len(L.remedies(L.check(src))) == 1


# --- the false positives, which matter more ---------------------------------
def test_a_url_in_a_string_is_not_a_line_comment():
    """`printf("http://x")` is perfectly good C89. Flagging it is the fastest
    way to teach a reader to ignore this tool."""
    assert rules('printf("see http://example\\n");') == []


def test_a_slash_slash_in_a_char_literal_is_not_a_comment():
    assert rules("char c = '/';\nchar d = '/';") == []


def test_commented_out_code_is_not_linted():
    """The compiler never reads it, so neither does this."""
    assert rules("/* for (int i = 0; i < 3; i++) {} */\nint x;") == []


def test_a_block_comment_across_lines_stays_closed():
    src = "/* a\n   for (int i = 0; i < 3; i++)\n   b */\nint x;"
    assert rules(src) == []


def test_the_word_boolean_is_not_the_type_bool():
    """`Boolean` is the Mac Toolbox's own type and is C89-legal here — a
    substring match would have refused the correct answer."""
    assert rules("Boolean ok = true;") == ["bool_type"]      # `true`, not `Boolean`
    assert rules("Boolean ok = 1;") == []


def test_a_variable_named_inline_is_not_the_keyword():
    assert rules("int inline_count = 0;") == []


# --- the other rules --------------------------------------------------------
def test_a_line_comment_is_found():
    assert rules("int x; // set it") == ["line_comment"]


def test_stdbool_is_found_by_its_header_too():
    assert "bool_type" in rules("#include <stdbool.h>")


def test_c99_keywords_are_found():
    assert rules("inline int f(void) { return 1; }") == ["c99_keyword"]


def test_line_numbers_survive_crlf():
    """Guest files are CR-terminated; a lint that counted them as one line
    would put every finding on line 1 and be useless for a caller."""
    src = "int a;\rfor (int i = 0; i < 2; i++) {}\r"
    assert L.check(src)[0]["line"] == 2


def test_clean_c89_produces_nothing():
    """The other half. A lint that fires on correct code is worse than none."""
    src = ("#include <stdio.h>\nint main(void)\n{\n    int i;\n"
           "    for (i = 0; i < 10; i++) {\n        printf(\"%d\\n\", i);\n"
           "    }\n    return 0;\n}\n")
    assert L.check(src) == []


# --- the limits, stated -----------------------------------------------------
def test_a_declaration_after_a_statement_is_NOT_detected():
    """A genuine C89 rule, deliberately not checked: finding it reliably needs a
    parser, and a wrong flag is worse than a missing one. Pinned as a test so
    nobody reads the silence as coverage."""
    src = "int main(void){ int a = 1; a++; int b = 2; return a + b; }"
    assert L.check(src) == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
