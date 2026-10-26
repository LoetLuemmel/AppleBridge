#!/usr/bin/python3
"""install_guests.py — one host-server instance, one hang watchdog and one dialog watchdog per row of the guest table.

    ./install_guests.py --dry-run     what would be written, started and removed
    ./install_guests.py               write the agents for every row, start them, remove agents of rows that are gone
    ./install_guests.py --remove      boot out and delete every guest agent (host, hangwatch, dialogwatch)
    ./install_guests.py --guest NAME  only that row (repeatable): the other guests' agents are not restarted

Reads `host/guests.conf` (see guests.conf.example and docs/MULTI_GUEST.md, D-028). Replaces the per-guest logic of
install_host_service.sh, install_remote_guest_service.sh, install_hang_watch.sh and install_dialog_watch.sh: those
knew exactly two guests, "local" and "remote", from keys in local.env.

Every agent carries its configuration in its own plist (launchd runs a deployed copy that cannot read the repo), so
re-run this after editing the table. deploy_host.sh syncs code, not configuration.

A full run bootstraps every row's agents again, so every live link drops and its in-flight command fails. Adding the
third guest on 2026-10-26 had to be done by hand for that reason, while a session was starting work on the second:
`--guest` writes and starts only the named rows, and retires nothing (it does not know which rows are gone).
"""

import argparse
import glob
import os
import plistlib
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guests as G          # noqa: E402
import host_config          # noqa: E402

AGENTS_DIR = os.path.expanduser("~/Library/LaunchAgents")
SUPPORT = os.path.expanduser("~/Library/Application Support/AppleBridge")
WATCH_FILES = {"hangwatch": ["hang_watch.py", "guest_boot.py", "fb_export.py", "guest_remote.py", "screenshot_decode.py"],
               "dialogwatch": ["dialog_watch.py", "guest_boot.py", "fb_export.py", "guest_remote.py",
                               "screenshot_decode.py"]}
WATCH_SCRIPT = {"hangwatch": "hang_watch.py", "dialogwatch": "dialog_watch.py"}


def _run(argv):
    p = subprocess.run(argv, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def agent_plist(guest, kind, base_env):
    """-> the plist dict of one guest's agent."""
    common = {"Label": guest.label(kind), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
              "ProcessType": "Background"}
    if kind == "host":
        log = "/tmp/applebridge_host_launchd.log" if guest.primary else f"/tmp/applebridge_host_{guest.name}_launchd.log"
        return dict(common, ProgramArguments=[os.path.join(SUPPORT, "run_server.sh")], WorkingDirectory=SUPPORT,
                    EnvironmentVariables=guest.server_env(base_env), StandardOutPath=log, StandardErrorPath=log)
    dest = os.path.join(SUPPORT, kind)
    log = f"/tmp/applebridge_{kind}_launchd.log"
    return dict(common, ProgramArguments=["/usr/bin/python3", "-u", os.path.join(dest, WATCH_SCRIPT[kind])],
                WorkingDirectory=dest, EnvironmentVariables=guest.watch_env(kind),
                StandardOutPath=log, StandardErrorPath=log)


def installed_guest_labels(agents_dir=AGENTS_DIR):
    """-> labels of every installed guest agent: `<prefix><kind>` and `<prefix><kind>-<anything>` for the three kinds.

    Matched on the kinds this installer writes, so another AppleBridge agent (none today) is never touched; the old
    `…-remote` agents of the two-guest setup match too, which is how they get retired."""
    out = []
    for kind in G.KINDS:
        base = G.LABEL_PREFIX + kind
        for path in glob.glob(os.path.join(agents_dir, base + "*.plist")):
            label = os.path.basename(path)[:-len(".plist")]
            if label == base or label.startswith(base + "-"):
                out.append(label)
    return sorted(set(out))


def plan(table, base_env, agents_dir=AGENTS_DIR, only=None):
    """-> (write: [(guest, kind, plist dict)], remove: [label])

    `only`: names of the rows to install; the rest of the table is left alone and nothing is removed. A name that is
    not in the table raises GuestTableError rather than installing nothing and reporting success."""
    if only:
        unknown = sorted(set(only) - {g.name for g in table})
        if unknown:
            raise G.GuestTableError(f"not in the table: {', '.join(unknown)}")
        table = [g for g in table if g.name in only]
    write = [(g, k, agent_plist(g, k, base_env)) for g in table for k in G.KINDS]
    if only:
        return write, []
    wanted = {p["Label"] for _, _, p in write}
    remove = [lbl for lbl in installed_guest_labels(agents_dir) if lbl not in wanted]
    return write, remove


def sync_watch_files():
    for kind, files in WATCH_FILES.items():
        dest = os.path.join(SUPPORT, kind)
        os.makedirs(os.path.join(dest, "refs"), exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(HERE, f), os.path.join(dest, f))
        for ref in glob.glob(os.path.join(HERE, "refs", "*.json")):
            shutil.copy2(ref, os.path.join(dest, "refs"))
        env = os.path.join(HERE, "local.env")
        if os.path.exists(env):
            shutil.copy2(env, os.path.join(dest, "local.env"))
            os.chmod(os.path.join(dest, "local.env"), 0o600)


def boot_out(label, run=_run):
    run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"])
    path = os.path.join(AGENTS_DIR, label + ".plist")
    if os.path.exists(path):
        os.remove(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove", action="store_true", help="remove every guest agent")
    ap.add_argument("--table", help="path of the guest table (default host/guests.conf)")
    ap.add_argument("--guest", action="append", metavar="NAME",
                    help="install only this row (repeatable); other guests keep running, nothing is removed")
    args = ap.parse_args(argv)

    if args.remove and args.guest:
        print("--remove takes every guest agent; --guest does not narrow it", file=sys.stderr)
        return 2
    if args.remove:
        labels = installed_guest_labels()
        for lbl in labels:
            print(("would remove " if args.dry_run else "removed ") + lbl)
            if not args.dry_run:
                boot_out(lbl)
        return 0

    try:
        table = G.load(args.table)
    except G.GuestTableError as e:
        print(f"guests.conf: {e}", file=sys.stderr)
        return 2
    if not table:
        print(f"no guest table at {args.table or G.CONF} — copy guests.conf.example and fill it in", file=sys.stderr)
        return 2
    base_env = host_config.launchd_environment()
    try:
        write, remove = plan(table, base_env, only=args.guest)
    except G.GuestTableError as e:
        print(f"--guest: {e}", file=sys.stderr)
        return 2

    for g in [g for g in table if not args.guest or g.name in args.guest]:
        print(f"{g.name}: daemon {base_env.get('APPLEBRIDGE_HOST_IP', '*')}:{g.daemon_port}, control 127.0.0.1:"
              f"{g.ctrl_port}, {'this Mac' if g.local else g.ssh}")
        for kind in G.KINDS:
            print(f"    {g.label(kind)}")
    for lbl in remove:
        print(f"remove (no longer in the table): {lbl}")
    if args.dry_run:
        return 0

    rc, out = _run([os.path.join(HERE, "deploy_host.sh"), "--no-restart"])
    if rc != 0:
        print(out, file=sys.stderr)
        return 1
    sync_watch_files()
    for lbl in remove:
        boot_out(lbl)
    os.makedirs(AGENTS_DIR, exist_ok=True)
    failed = []
    for g, kind, pl in write:
        path = os.path.join(AGENTS_DIR, pl["Label"] + ".plist")
        with open(path, "wb") as fh:
            plistlib.dump(pl, fh)
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{pl['Label']}"])
        rc, out = _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", path])
        if rc != 0:   # a bootstrap right after a bootout can race it ("Input/output error"); once more, after a second
            import time
            time.sleep(1.5)
            rc, out = _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", path])
        if rc != 0:
            failed.append(f"{pl['Label']}: {out.strip()}")
    for f in failed:
        print("FAILED " + f, file=sys.stderr)
    n = len(args.guest) if args.guest else len(table)
    print(f"{len(write) - len(failed)} agents started for {n} guests, {len(remove)} removed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
