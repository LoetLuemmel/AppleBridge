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
    (re.compile(r"-31001|Not a text file", re.I),
     "The file has no TEXT type, so MPW will not open it — this is the usual "
     "result of Duplicate out of Unix:. Fix with: SetFile -t TEXT -c 'MPS ' <file>"),
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

    _send(f"Delete -i {artifact} {err_file}")
    _send(f"{command} {STDERR_REDIRECT} {err_file}", timeout)
    captured = _send(f"Catenate {err_file}")
    found = artifact_exists(_send(f"Exists {artifact}"))

    result = classify_diagnostics(captured)
    result.update({"success": found, "artifact": artifact, "commands": sent})
    if not found:
        result["toolserver_alive"] = toolserver_alive(_send)
    return result
