#!/usr/bin/env python3
"""Measure the documentation process, so a later run can be compared to today's.

Efficiency cannot be proven on the day a process changes; a baseline can only be
captured on that day. This computes the numbers that would move if the 2026-07-26
process work is doing anything, and prints them as a flat, diffable report.

It is deliberately a SCRIPT, not a maintained file: re-run it whenever the
comparison is wanted. Maintaining a metrics page by hand would be one more record
to drift, which is the failure it exists to detect. (D-009: generate history, do
not maintain a copy of it.)

    host/tools/process_metrics.py              # the report
    host/tools/process_metrics.py --ledger     # ...also fetch the ledger (needs CMS_API_KEY)

Stdlib + git only. Every section degrades to "n/a" rather than failing.
"""
import os
import re
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=30, cwd=_ROOT).stdout.strip()
    except Exception:
        return ""


def _read(rel):
    try:
        with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _tool_names():
    src = _read("mcp/tools.py")
    block = src[src.index("TOOLS = ["):] if "TOOLS = [" in src else src
    return re.findall(r'"name"\s*:\s*"([a-z_0-9]+)"', block)


def duplication_index():
    """How many separate files assert the same checkable fact.

    One owner per fact is the design goal, so anything above 1 (the owner) plus
    a pointer is restatement — the shape that drifts. Counted for the two facts
    that have historically drifted: the tool count and the daemon version."""
    docs = [p for p in ("README.md", "ARCHITECTURE.md", "CLAUDE.md", "docs/SETUP.md",
                        "TROUBLESHOOTING.md") if _read(p)]
    version = (re.findall(r'"(\d+\.\d+[a-z]\d+)"', _read("mac/vers.r")) or ["?"])[0]
    count_files = [p for p in docs if re.search(r"\b\d+\s+(?:MCP\s+)?tools\b", _read(p))]
    vers_files = [p for p in docs if version in _read(p)]
    return version, count_files, vers_files


def guards():
    doc = _read("tests/test_doc_claims.py")
    mut = _read("tests/test_process_mutations.py")
    n_asserts = len(re.findall(r"^def test_", doc, re.M))
    n_mutants = len(re.findall(r'^\s{4}\("', mut, re.M))
    suite = _read("tests/run_all.sh")
    n_files = len(re.findall(r"test_[a-z_0-9]+\.py", suite))
    return n_asserts, n_mutants, n_files


def corrections():
    """Commits that overturn a previously documented belief — the folklore rate.

    A high number is not failure; it is the project noticing. What matters over
    time is the LATENCY between a belief being written and corrected."""
    log = _git("log", "--format=%h %ad %s", "--date=short", "--all",
               "--grep=misdiagnos", "--grep=disproven", "--grep=correct the",
               "--grep=retire the", "--regexp-ignore-case")
    return [l for l in log.split("\n") if l.strip()]


def known_folklore():
    """Rules that were false and how long they stood, from the record."""
    return [("ILink crashes Basilisk II", "2026-04-05", "2026-06-26", 82),
            ("start the server before the emulator", "2026-04-05", "2026-07-24", 110),
            ("GUI apps crash the emulator", "2026-04-05", "2026-06-27", 83),
            ("a 0-byte data fork means a broken link", "2026-04-05", "2026-06-26", 82)]


def ledger_size():
    if not (os.environ.get("CMS_API_KEY") or os.environ.get("GENERIC_CMS_KEY")):
        return None
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import ledger_diff
        _, body = ledger_diff.fetch_ledger(quiet=False)
    except Exception:
        return None
    return len(body), body.count("- [x]"), body.count("- [ ]")


def main():
    version, count_files, vers_files = duplication_index()
    n_asserts, n_mutants, n_files = guards()
    corr = corrections()

    print("=== AppleBridge process metrics ===")
    print("commit            :", _git("rev-parse", "--short", "HEAD"), _git("log", "-1", "--format=%ad", "--date=short"))
    print("repo age          :", _git("log", "--reverse", "--format=%ad", "--date=short").split("\n")[0],
          "->", _git("log", "-1", "--format=%ad", "--date=short"))
    print("merged PRs        :", len([l for l in _git("log", "--merges", "--oneline").split("\n") if "pull request" in l]))
    print()
    print("--- surface ---")
    print("MCP tools         :", len(_tool_names()))
    print("daemon version    :", version, "(mac/vers.r, single source)")
    print("decisions         :", len(re.findall(r"^## D-\d{3} — ", _read("DECISIONS.md"), re.M)),
          "of which active:", _read("DECISIONS.md").count("**Status:** active"))
    print()
    print("--- duplication index (lower is better; 1 owner + pointers) ---")
    print("files stating a tool count :", len(count_files), count_files)
    print("files stating the version  :", len(vers_files), vers_files)
    print()
    print("--- guards ---")
    print("doc-claim assertions :", n_asserts)
    print("seeded mutants       :", n_mutants)
    print("suite files in CI    :", n_files)
    print()
    print("--- folklore: false rules and how long they stood ---")
    for rule, born, died, days in known_folklore():
        print(f"  {days:>4}d  {born} -> {died}  {rule}")
    print("  (baseline for the falsifier requirement introduced 2026-07-26:")
    print("   a rule carrying a 'revisit if' should not be able to reach these numbers)")
    print()
    print("--- corrections in git history ---")
    print("commits overturning a documented belief:", len(corr))
    for line in corr[:8]:
        print("   ", line[:100])

    led = ledger_size()
    print()
    print("--- ledger ---")
    if led:
        print("chars: {:,}   done: {}   open: {}".format(*led))
    else:
        print("chars: n/a (no CMS_API_KEY; pass one to include)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
