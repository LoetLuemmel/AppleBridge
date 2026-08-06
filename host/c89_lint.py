"""Name the C99 habits that MPW's 1994 compiler will not take.

Measured 2026-08-05, and it is the first defect of the day that lies in the
MODEL rather than in a tool. Asked for a C program, `qwen2.5-coder:7b` wrote

    for (int i = 2; i < 10; i++) {

and MPW `SC` answered `line 5 #Error: expression expected`. A declaration in the
`for` initialiser is C99; `SC` is C89. The chain was honest end to end — the
model read the compiler's complaint and reported it correctly — it simply
produced a program this guest does not accept.

What makes it worth a tool rather than a sentence: **one sentence did not fix
it.** The next run's prompt named the construct explicitly — *declare at the
start of a block, never in the for-head* — and the model moved the variable
declarations to the top and left `for (int i = 0; ...)` standing. Partly obeyed,
and the named rule broken. That is the day's rule arriving in a new place: what
the model will not remember, a tool has to enforce.

**This reports, it does not block.** The compiler remains the verdict; what a
lint adds is the *reason*. `expression expected` does not tell anyone to move a
declaration, and the rewrite is what the caller needs. Blocking would also make
a false positive fatal, and a regex over C has them.

What it deliberately does NOT detect: a declaration appearing after a statement
inside a block. That is a genuine C89 rule and the model may well hit it — but
finding it reliably needs a parser, and a wrong flag here is worse than a
missing one. Named so nobody reads silence as coverage.

stdlib only.
"""

import re

# Findings, in the order they are checked. Each carries the RULE and the
# rewrite, because "this is C99" leaves the reader exactly where the compiler
# already left them.
RULES = (
    ("decl_in_for",
     re.compile(r"\bfor\s*\(\s*(?:const\s+|unsigned\s+|signed\s+|static\s+)*"
                r"(?:int|long|short|char|float|double|size_t)\b"),
     "declaration in the for initialiser is C99",
     # No template and no placeholder in the fix text. An earlier version read
     # "`int i; for (i = 0; …)`" -- and a model that copies a suggestion
     # literally would then write an ellipsis into its source. The remedy is
     # read by the thing that has to act on it, so it must be copy-safe.
     #
     # "as an additional line" is not padding. Measured 2026-08-06, proof run,
     # task A: the model followed this remedy CORRECTLY and destroyed the
     # program doing it -- it put `int i;` at the top of the block by REPLACING
     # `int sum = 0;` instead of adding a line. Next error: undefined identifier
     # 'sum'. A remedy that says what to write and not what to keep leaves the
     # keeping to be guessed, and the thing guessing has no memory of the file
     # it is editing.
     #
     # "at the start of the block" replaces "before the loop", measured
     # 2026-08-06 over forty tasks. Two runs followed this remedy; ONE of them
     # failed on it. t01 put `int i;` after a `printf(...)` — before the loop,
     # exactly as the text said, and C89 wants declarations before the first
     # STATEMENT. t35 put it among the other declarations and compiled.
     #
     # The morning's version had already been widened once, from what to write
     # to what to keep. The place stayed wrong, and this is the same defect one
     # step further along in the same sentence: an instruction that says what to
     # add and not where it belongs.
     "put `int i;` at the start of the block, before the first statement, as an "
     "additional line, keeping the declarations that are already there, and "
     "drop the type from the for-head"),
    ("line_comment",
     re.compile(r"//"),
     "`//` line comments are C99",
     "replace it with a block comment: `/*` before the text, `*/` after"),
    ("bool_type",
     re.compile(r"\b(?:bool|stdbool\.h|true|false)\b"),
     "`bool`/`true`/`false` need <stdbool.h>, which is C99",
     "use `short` with 0 and 1, or `Boolean` from the Mac headers"),
    ("c99_keyword",
     re.compile(r"\b(?:inline|restrict|_Bool)\b"),
     "`inline`/`restrict`/`_Bool` are C99 keywords",
     "remove it; C89 has no equivalent and none is needed here"),
)


def strip_literals(line):
    """Blank out string and char literals, keeping the line's LENGTH.

    Without this, `printf("http://x")` reports a `//` comment — a false positive
    on a line that is perfectly good C89, and the fastest way to teach a reader
    to ignore this tool. Length is preserved so a column, if ever wanted, still
    points at the right place.
    """
    out, i, n = [], 0, len(line)
    while i < n:
        ch = line[i]
        if ch in "\"'":
            quote, j = ch, i + 1
            out.append(" ")
            while j < n:
                if line[j] == "\\" and j + 1 < n:
                    out.append("  ")
                    j += 2
                    continue
                if line[j] == quote:
                    break
                out.append(" ")
                j += 1
            out.append(" ")
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def check(source):
    """-> list of findings, each with line, rule and the rewrite.

    Block comments are honoured across lines: a `/* … */` that spans three lines
    hides whatever is inside it, and a lint that flagged commented-out code
    would be reporting on text the compiler never reads.
    """
    findings = []
    in_block = False
    for no, raw in enumerate((source or "").replace("\r\n", "\n")
                             .replace("\r", "\n").split("\n"), 1):
        line = raw
        if in_block:
            end = line.find("*/")
            if end < 0:
                continue
            line = " " * (end + 2) + line[end + 2:]
            in_block = False
        line = strip_literals(line)
        # Remove complete block comments, then notice an unterminated one.
        line = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), line)
        start = line.find("/*")
        if start >= 0:
            in_block = True
            line = line[:start]
        for name, pattern, why, fix in RULES:
            if pattern.search(line):
                findings.append({"line": no, "rule": name, "why": why,
                                 "fix": fix, "text": raw.strip()[:120]})
    return findings


def remedies(findings):
    """One line per DISTINCT rule, with the lines it was seen on.

    Ten `//` comments are one lesson, not ten. Repeating it ten times costs
    context on a node that has 2 GB free and teaches nothing the first line did
    not.
    """
    by_rule = {}
    for f in findings:
        by_rule.setdefault(f["rule"], {"why": f["why"], "fix": f["fix"],
                                       "lines": []})["lines"].append(f["line"])
    out = []
    for info in by_rule.values():
        where = ", ".join(str(n) for n in info["lines"][:6])
        if len(info["lines"]) > 6:
            where += f" … ({len(info['lines'])} lines)"
        out.append(f"line {where}: {info['why']} — {info['fix']}")
    return out
