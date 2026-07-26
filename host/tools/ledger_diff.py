#!/usr/bin/env python3
"""Report what has shipped since the roadmap ledger was last edited.

The ledger is prose on a website; the work is merged PRs in git. Nothing keeps
the two in step, and on 2026-07-25 they missed each other by eleven minutes: PR
#85 (the MONITOR toggle and the DISKINFO verb) merged at 18:41, the ledger was
edited at 18:52, and both items stayed unticked for a day until someone read
them side by side.

This is that side-by-side, as one command: the ledger's own `updated` stamp
versus `gh pr list`. It reconciles nothing by itself — it says what to look at.

    host/tools/ledger_diff.py                # fetch the stamp, list newer PRs
    host/tools/ledger_diff.py --open         # ...and the ledger's open items
    host/tools/ledger_diff.py --quiet        # print only when something is out of step
    host/tools/ledger_diff.py --since 2026-07-01   # no API key needed

Needs `CMS_API_KEY` in the environment for the fetch (the key bypasses Authelia
on /api/v1/**); `--since` skips the fetch entirely. Stdlib + `gh` only.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request


def _moment(stamp):
    """ISO stamp -> aware datetime. `gh` answers in UTC ('…Z'), the CMS in local
    time ('…+02:00'); comparing the strings makes 08:47Z look older than
    10:43+02:00 when it is four minutes NEWER. Compare instants, not text."""
    s = stamp.strip().replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:                       # a bare --since date
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt

SITE = os.environ.get("CMS_SITE", "https://pit.390er.de")
SECTION = "applebridge"
LEDGER = "applebridge-roadmap-ledger-progress-and-status-tracker"
REPO = os.environ.get("APPLEBRIDGE_REPO", "LoetLuemmel/AppleBridge")


def fetch_ledger():
    """-> (updated_iso, body). Raises SystemExit with a usable message."""
    key = os.environ.get("CMS_API_KEY") or os.environ.get("GENERIC_CMS_KEY")
    if not key:
        sys.exit("CMS_API_KEY is not set — export it, or pass --since <date> to skip the fetch.")
    url = f"{SITE}/api/v1/articles/{SECTION}/{LEDGER}.md"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as fh:
            doc = json.load(fh)
    except urllib.error.HTTPError as e:
        sys.exit(f"ledger fetch failed: HTTP {e.code} {e.reason} ({url})")
    except urllib.error.URLError as e:
        sys.exit(f"ledger fetch failed: {e.reason} ({url})")
    fm = doc.get("frontmatter") or {}
    updated = fm.get("updated") or fm.get("date")
    if not updated:
        sys.exit("ledger has no `updated` stamp — pass --since instead.")
    return updated, doc.get("body") or ""


def merged_since(date):
    """PRs merged on or after `date` (YYYY-MM-DD), newest first."""
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "--repo", REPO, "--state", "merged",
             "--search", f"merged:>={date}", "--limit", "50",
             "--json", "number,title,mergedAt,url"],
            capture_output=True, text=True, timeout=60, check=True).stdout
    except FileNotFoundError:
        sys.exit("`gh` not found — install the GitHub CLI or pass --since and read the list yourself.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"gh failed: {e.stderr.strip() or e}")
    return sorted(json.loads(out), key=lambda p: p["mergedAt"], reverse=True)


def open_items(body):
    """Unchecked ledger checkboxes -> [(bold title, status word)]."""
    items = []
    for line in body.split("\n"):
        if not re.match(r"\s*[-*]\s*\[\s\]", line):
            continue
        title = re.search(r"\*\*(.+?)\*\*", line)
        status = re.search(r"\*(Open|In progress|Blocked|Deferred)\*", line)
        items.append((title.group(1) if title else line.strip()[:60],
                      status.group(1) if status else "?"))
    return items


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="YYYY-MM-DD; skips the ledger fetch")
    ap.add_argument("--open", action="store_true", dest="show_open",
                    help="also list the ledger's open items")
    ap.add_argument("--quiet", action="store_true",
                    help="say nothing when nothing merged since the ledger was edited")
    args = ap.parse_args()

    body = ""
    if args.since:
        updated, day = args.since, args.since[:10]
    else:
        updated, body = fetch_ledger()
        day = updated[:10]

    prs = merged_since(day)
    # A same-day PR may still predate the edit, so compare instants — and the two
    # sources speak different time zones (see _moment).
    if args.since:
        newer = prs
    else:
        cutoff = _moment(updated)
        newer = [p for p in prs if _moment(p["mergedAt"]) > cutoff]

    if not newer and args.quiet:
        return 0

    print(f"ledger last edited : {updated}")
    print(f"PRs merged since   : {len(newer)}")
    if newer:
        print()
        for p in newer:
            print(f"  #{p['number']:<4} {p['mergedAt'][:16].replace('T', ' ')}  {p['title']}")
        print("\n  ^ each of these should be reflected on the ledger, or deliberately not.")
    else:
        print("\n  nothing merged since the last edit — in step.")

    if args.show_open and body:
        items = open_items(body)
        print(f"\nopen on the ledger : {len(items)}")
        for title, status in items:
            print(f"  [{status:<11}] {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
