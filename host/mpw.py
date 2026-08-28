"""Verification helpers for MPW/ToolServer build steps — stdlib only, no sockets.

A `STATUS:0` from an MPW tool means *the Apple Event was delivered*. It does not
mean the tool succeeded: `ExecuteCommand` (`mac/src/command.c`) sets `exitCode`
to zero and only ever overwrites it with the Apple Event's own error, so the
tool's exit status never crosses the bridge and its stderr never leaves
ToolServer. `SC`, `Asm`, `Link`, `Rez` and `SetFile` are all silent on success —
and equally silent on failure.

So a build step has exactly one honest oracle: **did the artefact appear**. This
module holds that oracle once, together with the mapping from a captured
diagnostic to the rule that applies to it. Both used to exist only inside
`mac_build`, where nothing else could reach them.

The split is deliberate:
  - the *deciders* take text and return a verdict — no I/O, testable from the
    transcripts the guest actually prints;
  - the *drivers* take a `send(command, timeout) -> str` callable, so the
    caller owns the socket and the tests own the answers.

Provenance for the remedy table is in `TROUBLESHOOTING.md` and `CLAUDE.md`'s
hard rules; each entry names its source.
"""

import re

# MPW's stderr redirect (option->). NOT ">=", which is a different thing and
# writes to a file called "=" — a mistake that hides a failing build.
STDERR_REDIRECT = "≥"

# MPW's escape character (option-D). It survives the bridge because
# host_server encodes every command to MacRoman before sending, where this is
# 0xB6 — the same path that carries STDERR_REDIRECT above.
ESCAPE = "∂"


def quote(path):
    """Wrap a Mac path so MPW receives it as ONE literal argument.

    Inside single quotes MPW treats everything literally — spaces, `ƒ`, `•`,
    `{}`, even ESCAPE itself — so a path needs no other preparation. The single
    exception is the quote character, which cannot appear inside its own
    quoting at all: the string simply ends there. The fix is to close, escape
    the quote outside, and reopen.

    Measured 2026-08-08, on 49 real Developer-CD sources: naive `f"SC '{p}'"`
    split the command at the apostrophe of the folder `What's New?` — a
    STANDARD folder on every Developer CD, not an exotic name. SC then never
    received a filename, so it emitted no diagnostics, produced no object, and
    the result was a failure with `errors: []`. Twenty-seven of the
    forty-nine, all of them, and none of the other twenty-two. A failure that
    reports nothing is worse than one that reports the wrong thing, because
    there is nothing to search for: the reader looks for a defect in a source
    file that was never opened.
    """
    return "'" + str(path).replace("'", "'" + ESCAPE + "''") + "'"


# Tools that print nothing when they succeed. Kept in step with the timeout
# budget in host_server.LONG_CMDS by tests; `SetFile`/`Delete` are silent too
# but fast, so they belong here and not there.
SILENT_TOOLS = {
    "sc", "scpp", "asm", "link", "ilink", "rez", "derez",
    "setfile", "delete", "duplicate", "make", "lib",
}

# The same segment splitter host_server.timeout_for uses, so "one command line"
# means the same thing on both sides of the bridge.
_SEGMENTS = re.compile(r"[;\r\n]|&&|\|\|")

# (pattern, remedy). Scanned over captured diagnostics; the remedy is the rule
# that applies, delivered at the moment it is true rather than left in a file.
_REMEDIES = (
    # SC's own wording for the same defect is "Command line error: unable to open
    # input file" — measured 2026-08-08 (RUECKMELDUNG_2026-08-08_xavier.md): 120
    # failures went by without this remedy firing because only the OS-error spelling
    # was matched. Also true of an ExtFS (Unix:) source, where SetFile does not stick.
    (re.compile(r"-31001|Not a text file|unable to open input file", re.I),
     "The file has no TEXT type, so MPW will not open it — this is the usual "
     "result of Duplicate out of Unix:. Fix with: SetFile -t TEXT -c 'MPS ' <file>. "
     "If the file is still on Unix: that will not stick: copy it to a local volume "
     "first (mac_read_file -> mac_write_file), then compile."),
    (re.compile(r"\bError 48\b"),
     "One segment is over the 32 KB PC-relative reach. Link with ILink -model far "
     "(D-011 in DECISIONS.md); plain Link cannot do it."),
    (re.compile(r"-903\b"),
     "The SIZE resource (isHighLevelEventAware) is missing — re-run "
     "Rez <app>_res.r -a -o <app> after every link, or every command fails."),
    (re.compile(r"-1712\b"),
     "-1712 is an Apple Event timeout: a long link often returns it and still "
     "completes. Judge by the artefact, not by this status."),
)

# Remedies keyed on a DIAGNOSTIC GROUP: the compiler's message plus the source
# line it points at. Only possible since the group travels together (see
# `_SEPARATOR`) — before that, the source line was dropped and there was nothing
# to key on.
#
# **Why this is not a lint rule.** `c89_lint`'s docstring has said since day one
# that "a declaration appearing after a statement inside a block" is deliberately
# NOT detected: finding it reliably needs a parser, and a wrong flag is worse
# than a missing one. That reasoning still holds for a rule that runs BEFORE the
# compiler — it would fire on correct code.
#
# It does not hold here. This fires only on a line the compiler has ALREADY
# rejected, so a false positive on correct code is impossible by construction.
# The trade-off the docstring names disappears rather than being re-decided.
#
# Measured 2026-08-06 over eighty runs: `expression expected` is SC's message for
# several distinct C89 violations. Of 135 such lines, 84 were a declaration in a
# for-head (which the lint does catch) and 46 were a declaration after a
# statement — the second-largest cause in the whole measurement, and until now
# the model was told only "expression expected", which names nothing.
#
# **What keeps the two classes apart is the `^` anchor**, and it is worth naming
# because the other 84 lines look like declarations too: `for (int i = 0; …)`
# contains `int i` and would match an unanchored pattern. It begins with `for`,
# so it cannot match an anchored one — the for-head belongs to `c89_lint`, this
# belongs to what the lint deliberately does not detect, and the anchor is the
# whole border between them. Pinned by a test, because widening the pattern
# without the anchor would trade a gap for a MISDIRECTION: the remedy would point
# at the loop line and say "move it above the first statement", which is exactly
# the wrong instruction and exactly how one of two applications of our own remedy
# failed on 2026-08-06. Named by the parallel session before it could bite twice.
_GROUP_REMEDIES = (
    (re.compile(r"expression expected", re.I),
     re.compile(r"^\s*(?:const\s+|static\s+|register\s+|volatile\s+|"
                r"unsigned\s+|signed\s+)*"
                r"(?:int|char|long|short|float|double|size_t|FILE)\b"
                r"[\s*]+[A-Za-z_]"),
     "C89 wants every declaration at the START of its block, before the first "
     "statement. This line declares something after a statement — move it up, "
     "above the first statement of the enclosing block, and keep the "
     "declarations that are already there."),
)


def group_remedies(errors):
    """Remedies that need the source line, not just the message.

    Reads the grouped entries `classify_diagnostics` produces: the last line is
    the compiler's message, the ones before it are the source and the caret.
    """
    out = []
    for entry in errors:
        lines = [l for l in entry.split("\n") if l.strip()]
        if len(lines) < 2:
            continue
        message, source = lines[-1], lines[0]
        for msg_pat, src_pat, remedy in _GROUP_REMEDIES:
            if msg_pat.search(message) and src_pat.match(source) \
                    and remedy not in out:
                out.append(remedy)
    return out


# Benign: the linker reporting that a library it was handed was not needed.
_BENIGN = re.compile(r"\bError 52\b")

# The guest is not consistent about case: SC says "Fatal error: unable to open
# file '…' (not a TEXT file)" while Asm says "# Not a text file (OS Error
# -31001)" — measured 2026-08-02, both for the same defect. A case-sensitive
# match for "Error" therefore reported the first as having no errors at all,
# while the remedy (matched case-insensitively) fired: the two halves of one
# result disagreed. Hence one case-insensitive marker set for both.
_ERROR_MARKERS = re.compile(r"error|fatal|cannot open|unable to open|^###|^#", re.I)
_WARNING_MARKERS = re.compile(r"warning", re.I)

# SC writes FOUR lines per diagnostic and puts the message LAST:
#
#     printf("%d\n", a);          <- the offending source line
#     ^                           <- the column marker
#     File "…err.c"; line 7 #Error: ';' expected
#     #-----------------------
#
# Classifying line by line therefore dropped the two informative lines (neither
# carries a marker) and KEPT the empty one (`#---` matches `^#`). Measured
# 2026-08-06 by the parallel session against a probe whose defect was
# deliberately NOT a lint rule, and reproduced host-side against this function.
#
# Why it matters more than it looks: for the four habits `c89_lint` knows, its
# own `text` field carries the source line — so the loss is invisible exactly
# until the model hits an error the lint does NOT know, which is where a repair
# rate is measured. The back channel was blind where the measurement looks.
#
# The group has to be bound BACKWARDS — message found, then take the lines
# before it. Reading forwards attaches a source line to the *following*
# diagnostic, which is worse than dropping it: it is confidently wrong.
_SEPARATOR = re.compile(r"^#-{3,}$")

# The banner SC prints once per invocation. Not a diagnostic — and, left in the
# stream, it would be picked up as the prefix of the FIRST error.
_BANNER = re.compile(r"^Copyright\b|^\S+ (?:C|C\+\+) Compiler\b", re.I)

# How many preceding markerless lines may join a diagnostic. Two, because SC
# writes exactly two (source line, caret). More would start collecting whatever
# an unrelated tool printed earlier in the same capture.
_PREFIX_LINES = 2


# --------------------------------------------------------------------------
# deciders — text in, verdict out
# --------------------------------------------------------------------------

def artifact_exists(exists_output):
    """Did MPW's `Exists` find the file?

    ToolServer echoes the path when the file is there and answers with an empty
    stdout plus NoDir:-1701 when it is not. `host/build.py` tests for a token
    this protocol does not emit at all, which makes its check answer False for
    every successful build — see the regression test in
    tests/test_build_verification.py, which pins that token to that one file.
    """
    text = exists_output or ""
    return bool(text.strip()) and "NoDir" not in text and "__SENDERR__" not in text


def classify_diagnostics(text):
    """-> {"errors": [...], "warnings": [...], "remedies": [...]}.

    An entry is a whole diagnostic, not a line: the offending source line and
    the column marker travel with the message that names them (see `_SEPARATOR`
    above for what SC actually writes and why the binding runs backwards). The
    entries stay strings, so `errors[0]` is still the first error — it is just
    no longer amputated.

    Indentation is preserved inside a group, and that is not cosmetic: strip the
    lines and the caret no longer points at the column it exists to mark.

    `Error 52` is a warning, not an error: it only says a library passed to the
    linker was not needed.
    """
    errors, warnings = [], []
    pending = []                    # markerless lines since the last diagnostic
    for raw in (text or "").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _SEPARATOR.match(line) or _BANNER.match(line):
            pending = []            # neither a diagnostic nor a prefix for one
            continue
        if _BENIGN.search(line) or _WARNING_MARKERS.search(line):
            warnings.append("\n".join(pending[-_PREFIX_LINES:] + [line]))
        elif _ERROR_MARKERS.search(line):
            errors.append("\n".join(pending[-_PREFIX_LINES:] + [line]))
        else:
            pending.append(raw.rstrip())
            continue
        pending = []                # a classified line ends the group
    remedies = [remedy for pattern, remedy in _REMEDIES if pattern.search(text or "")]
    remedies += group_remedies(errors)
    return {"errors": errors, "warnings": warnings, "remedies": remedies}


def silent_tool(command):
    """Name of the silent MPW tool this command invokes, or None.

    Looks at the first token of every segment, so a compound line is judged by
    what it actually runs rather than by how it starts.
    """
    for segment in _SEGMENTS.split(command or ""):
        token = segment.strip().lstrip("(").split(None, 1)
        if token and token[0].lower() in SILENT_TOOLS:
            return token[0]
    return None


def redirect_and_read_on_one_line(command):
    """True when a stderr redirect and its read-back share one command line.

    Measured 2026-08-02: `SC … ≥ f.err ; Catenate f.err` comes back empty, while
    the same two commands sent separately return the diagnostics. A session read
    that emptiness as proof that the redirect was destroyed in transit and spent
    the rest of the day working around a transport that was fine.
    """
    text = command or ""
    if STDERR_REDIRECT not in text:
        return False
    segments = _SEGMENTS.split(text)
    return len(segments) > 1 and any("catenate" in s.lower() for s in segments)


# --------------------------------------------------------------------------
# drivers — take send(command, timeout) -> str (stdout)
# --------------------------------------------------------------------------

_PROBE = "__ABPROBE__"


def toolserver_alive(send, timeout=20.0):
    """Is ToolServer answering *right now*?

    `Echo` is the probe because it is the one tool that prints on success — the
    silent ones cannot distinguish "ran fine" from "never ran". Call this only
    when something already failed: it turns "nothing was produced" into either
    "ToolServer is gone" or "ToolServer is alive and rejected the input", which
    is the fork worth one extra round trip.
    """
    out = send(f"Echo {_PROBE}", timeout) or ""
    return _PROBE in out


def parent_folder(path):
    """-> the containing folder of a Mac path, or None when there is none.

    `MeinMac:Bench:x.o` -> `MeinMac:Bench:`. A volume root (`MeinMac:`) and a
    bare name with no colon have no parent worth probing, and return None.
    """
    text = (path or "").rstrip(":")
    cut = text.rfind(":")
    if cut <= 0:
        return None
    return text[:cut + 1]


def output_folder_missing(send, artifact, timeout=20.0):
    """Does the artefact's folder exist? Call this only when a step FAILED.

    MPW creates no intermediate folders, so a step told to write into a folder
    that is not there produces no artefact AND no error file — and therefore no
    diagnostic at all. Measured 2026-08-08: a re-measurement of 27 sources
    reported `errors: []` for every one of them, indistinguishable from the
    quoting defect fixed in the same session, and from a compiler that simply
    said nothing. Three different causes, one signature.

    On the failure path only, for the same reason as `toolserver_alive`: on a
    successful step the folder demonstrably exists, so the round trip would buy
    nothing on the path that runs most often.
    """
    folder = parent_folder(artifact)
    if not folder:
        return False
    return not artifact_exists(send(f"Exists {quote(folder)}", timeout))


def run_step(send, command, artifact, err_file, timeout=250.0):
    """Run one build step and report what was actually verified.

    Order matters and each part earns its place:
      1. delete the artefact *and* the error file — a stale artefact hides a
         failure, and a stale error file gets attributed to this step when the
         tool never runs to truncate it;
      2. run the command with the stderr redirect appended;
      3. read the error file back as its OWN command (see
         `redirect_and_read_on_one_line`);
      4. decide by the artefact.
    """
    sent = []

    def _send(cmd, to=30.0):
        sent.append(cmd)
        return send(cmd, to) or ""

    # Every path is quoted here, not by the caller: these four lines are where
    # `artifact` and `err_file` meet the shell, and a caller that quoted them
    # itself would have to quote for a command line it cannot see.
    art, err = quote(artifact), quote(err_file)
    _send(f"Delete -i {art} {err}")
    _send(f"{command} {STDERR_REDIRECT} {err}", timeout)
    captured = _send(f"Catenate {err}")
    found = artifact_exists(_send(f"Exists {art}"))

    result = classify_diagnostics(captured)
    result.update({"success": found, "artifact": artifact, "commands": sent})
    if not found:
        # Liveness FIRST, and the folder question only after it — because an
        # empty answer to `Exists` means "not there" OR "nobody answered", and
        # those are not the same finding. Probing the folder first would report
        # a missing folder with full confidence whenever ToolServer is simply
        # down: a precise, actionable, wrong answer, which is the failure this
        # whole probe exists to prevent. Caught by
        # test_build_verification.py's toolserver probe, not by foresight.
        alive = toolserver_alive(_send)
        result["toolserver_alive"] = alive
        if alive and output_folder_missing(_send, artifact):
            result["output_folder_missing"] = True
            result["remedies"] = result["remedies"] + [
                f"the output folder {parent_folder(artifact)} does not exist — "
                f"MPW creates no intermediate folders, so nothing was written "
                f"and nothing was reported; create it (`NewFolder`) and re-run"]
    return result
