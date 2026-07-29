#!/usr/bin/env python3
"""install_bridge — configure THIS host for AppleBridge, on the slirp branch.

Why this exists
---------------
Installing AppleBridge on a machine nobody had prepared (2026-07-27, one Wi-Fi
interface, guest 7.5.3, no MPW) produced the twenty requirements in
`docs/INSTALLER_REQUIREMENTS.md`. Their common theme is not installation but
**derivation**: every value this project hardcoded is correct on exactly one
machine, and the resulting failures do not look like configuration errors. A
guest that dials `192.168.3.154` on a foreign LAN where something answers
connects to the WRONG COMPUTER and reports full health — protocol negotiated,
heartbeat running, zero errors on either console (R2).

So this program's job is to work out what is true about the machine it is
running on, write only what it can justify, and say plainly what it cannot know.

Scope: the slirp branch and nothing else (D-018)
------------------------------------------------
It configures `ether slirp`. Where it finds an `etherhelpertool` in the emulator
bundle it NAMES that path as manual and stops, because:

  * `etherhelper` needs two interactive password prompts per launch (the bridge
    in the operator's launcher, and BasiliskII elevating its built-in helper
    through Authorization Services), neither of which a script can answer;
  * the helper is not part of a stock Basilisk II — it comes from the
    kanjitalk755 macemu fork, and the copy on the developer's machine was
    compiled by the operator;
  * on a single-interface host it cannot form a guest->host connection at all
    (D-015): the guest reaches the whole world except the machine it runs in.

An installer whose output cannot start without somebody at the keyboard has not
finished the job it exists for. **The cost is stated, not buried: the slirp
branch has no AppleTalk** — no Chooser, no AFP mounts, no `mac_appletalk_browse`.
TCP keeps working either way, which is exactly how that gap disguised itself.

Refusing is a feature, not a fallback
-------------------------------------
A host that already runs `etherhelper` successfully is not a machine to be
"fixed": on the developer's machine that setup is production (AppleTalk + AFP to
a netatalk server). Converting it silently would be the same class of harm as
the wrong-computer connection above. So the installer refuses, names the branch
it found, and requires `--force-slirp` from a human who means it.

Design
------
Same shape as `bridge_doctor.py`, for the same reason: `run`/`read`/`write`/
`exists` are injectable, so every branch — including the refusals — is driven by
the test suite from canned output, with no live stack and no host mutated. Plans
are declarative data; `apply_plan` is the only code that writes anything.

Stdlib only, so `/usr/bin/python3` remains sufficient (D-007).

    host/install_bridge.py --dry-run       # derive and print; change nothing
    host/install_bridge.py                 # apply
    host/install_bridge.py --no-agent      # configure, don't own the launch path
    host/install_bridge.py --force-slirp   # convert a working etherhelper host
    host/install_bridge.py --seed-guest-prefs <image.dmg>
    host/install_bridge.py --json
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bridge_doctor                                   # noqa: E402  (path first)
import host_config                                     # noqa: E402
import macbinary                                       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

SLIRP = "slirp"
PREFS_PATH = os.path.expanduser("~/.basilisk_ii_prefs")
NETMODE_PATH = PREFS_PATH + ".netmode"
LOCAL_ENV = os.path.join(HERE, "local.env")

# What slirp gives the guest. Fixed by the backend, not by this machine — which
# is why they may be literals here while an address of the HOST may not be.
# `10.0.2.3` is the one that gets forgotten: an empty resolver field surfaces as
# iCab error -23045 (`authNameErr`), a DNS failure that reads like a routing
# failure (R7).
GUEST_ADDR = "10.0.2.15"
GUEST_MASK = "255.255.255.0"
GUEST_ROUTER = "10.0.2.2"
GUEST_RESOLVER = "10.0.2.3"

# Where the guest's own configuration lives, so the installer can name it rather
# than let somebody look in the installation folder first (R3).
GUEST_PREFS_HFS = ":System Folder:Preferences:AppleBridge Prefs"

# Emulator bundles, most authoritative first. A running process beats any guess.
# Gatekeeper runs a quarantined app from a per-launch throwaway mount. Anything
# under this is a path with an expiry date, never configuration.
TRANSLOCATED = "/AppTranslocation/"

BUNDLE_CANDIDATES = (
    "/Applications/BasiliskII.app",
    "~/Documents/Basilisk/BasiliskII.app",
    "~/Applications/BasiliskII.app",
    "/Applications/SheepShaver.app",
)

# hfsutils is not part of macOS, and every path that touches a guest volume
# needs it: the kit export, the prefs seeder, and make_test_guest.py. It went
# undeclared until a machine that had never installed it tried to build a kit
# (2026-07-29) and got `hmount failed on <image>:` with an empty reason.
HFS_TOOLS = ("hmount", "humount", "hcopy", "hformat", "hls")

# What actually identifies an emulator bundle: the executable inside
# Contents/MacOS, not the bundle's name. Measured on one real machine's folder
# (2026-07-29), where every name-based rule is wrong in BOTH directions —
# `Kanji-2020-01-22.app` and `org_BasiliskII.app` are emulators matching no
# sensible prefix, while `BasiliskIIGUI.app` matches `BasiliskII*` and is a
# front-end, not the emulator. The executable is the same in all of them.
EMULATOR_EXECUTABLES = ("BasiliskII", "SheepShaver")

REFUSE, STEP, NOTE = "refuse", "step", "note"


# --------------------------------------------------------------------------
# plumbing — every probe degrades to empty rather than raising
# --------------------------------------------------------------------------
def _run(argv, timeout=8.0):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _read(path):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


# --------------------------------------------------------------------------
# probes
# --------------------------------------------------------------------------
def probe_hfsutils(which=None):
    """-> {"missing": [names], "found": {name: path}} for the hfsutils suite.

    Asked as a probe rather than discovered as a crash, because the failure it
    replaces was silent in the worst way: the runner degrades to empty output,
    so a missing binary produced an error message naming the *disk image* with
    no reason attached, sending the reader to inspect a file that was fine.
    """
    which = which or shutil.which
    found, missing = {}, []
    for tool in HFS_TOOLS:
        path = which(tool)
        if path:
            found[tool] = path
        else:
            missing.append(tool)
    return {"found": found, "missing": missing}


def is_emulator_bundle(path, exists=None):
    """Does this .app actually contain an emulator? Judged by the executable.

    Name-based tests were wrong in both directions on the first machine that had
    more than one build (see EMULATOR_EXECUTABLES), so this asks the only
    question that survives a rename: is there a `Contents/MacOS/BasiliskII` (or
    SheepShaver) inside?
    """
    exists = exists or os.path.exists
    return any(exists(os.path.join(path, "Contents", "MacOS", exe))
               for exe in EMULATOR_EXECUTABLES)


def hfsutils_advice(missing, action):
    """The one message for 'this machine cannot touch an HFS volume'."""
    return ("hfsutils is not installed, so nothing here can %s: missing %s. "
            "It is not part of macOS — `brew install hfsutils` (or MacPorts "
            "`port install hfsutils`), then run this again."
            % (action, ", ".join(missing)))


def bundle_dirs_from_prefs(emulator_prefs):
    """-> the folders an emulator's own prefs point into, most-used first.

    The bundle usually sits beside the disk images and ROM the prefs name, and
    those paths are *this machine's* rather than the author's. `BUNDLE_CANDIDATES`
    listing `~/Documents/Basilisk/` while a real machine used
    `~/Documents/BasiliskII/` is the same class of defect as R1's addresses: a
    literal that happens to be right where it was written.
    """
    seen, out = set(), []
    entries = list(emulator_prefs.get("disks") or [])
    for key in ("rom", "keycodefile"):
        value = emulator_prefs.get(key)
        if value:
            entries.append(value)
    for path in entries:
        folder = os.path.dirname(path)
        if folder and folder not in seen:
            seen.add(folder)
            out.append(folder)
    return out


def probe_emulator_bundle(run=None, exists=None, candidates=BUNDLE_CANDIDATES,
                          prefs_dirs=(), listdir=None, override=None):
    """-> {app, helper, source} — the emulator bundle and whether it can do etherhelper.

    R8 makes this the FIRST question, ahead of counting interfaces: a stock
    Basilisk II has no `etherhelpertool` at all, so for most people the
    `etherhelper` branch does not exist whatever their NICs look like. It is also
    the cheaper check.

    `app` is discovered rather than hardcoded because `start_stack.sh` carries one
    machine's Basilisk path today, which is the same defect as the addresses of R1.
    """
    run = run or _run
    exists = exists or os.path.exists

    found, source, note = None, None, None
    # An operator who knows beats every heuristic. Deliberately a FLAG and not a
    # prompt: this branch exists because its result must be able to run with
    # nobody at the keyboard (D-018), and a program that stops to ask a question
    # cannot be run from a script, a cron job or another machine.
    if override:
        path = os.path.expanduser(override)
        if exists(path):
            return {"app": path, "source": "--emulator-app",
                    "helper": exists(os.path.join(path, "Contents", "Resources",
                                                  "etherhelpertool"))}
        return {"app": None, "source": None, "helper": False,
                "override_missing": path}
    for line in run(["pgrep", "-fl", "BasiliskII|SheepShaver"]).splitlines():
        m = re.search(r"(/.*?\.app)/Contents/MacOS/", line)
        if not (m and exists(m.group(1))):
            continue
        # Gatekeeper TRANSLOCATION: a quarantined app is run from a randomly
        # named read-only mount under .../T/AppTranslocation/<uuid>/d/, created
        # per launch and gone afterwards. The path is real right now and
        # worthless tomorrow — recording it in local.env would have
        # start_stack.sh launch something that no longer exists. Observed on
        # this project's own machine 2026-07-27, where the uuid had already
        # changed between two launches an hour apart.
        if TRANSLOCATED in m.group(1):
            note = m.group(1)
            continue
        found, source = m.group(1), "running process"
        break
    if not found:
        for cand in candidates:
            path = os.path.expanduser(cand)
            if exists(path):
                found, source = path, "well-known location"
                break
    # Derived, not listed: the emulator's own prefs name this machine's disk
    # images and ROM, and the bundle is normally in one of those folders. This
    # is what finds a renamed bundle (`BasiliskII_letzter.app`) in a folder
    # nobody guessed (`~/Documents/BasiliskII/`) while the emulator is DOWN —
    # which is the state every install runs in, and the state in which the
    # running-process branch above can say nothing.
    #
    # Caveat, measured on that same machine: the folder held BOTH
    # `BasiliskII.app` and `BasiliskII_letzter.app`, and only the latter is
    # actually launched. Sorted order prefers the plain name, which is a
    # defensible guess and not knowledge — nothing offline distinguishes the
    # bundle somebody uses from one they keep. The running-process stage is
    # authoritative and runs first; this is a fallback that gets `start_stack.sh`
    # a working launch path, not a claim about which build is preferred.
    if not found:
        lister = listdir or os.listdir
        for folder in prefs_dirs:
            try:
                names = sorted(lister(folder))
            except OSError:
                continue
            for name in names:
                if not name.endswith(".app"):
                    continue
                path = os.path.join(folder, name)
                if is_emulator_bundle(path, exists):
                    found, source = path, "beside the emulator's disk images"
                    break
            if found:
                break
    if not found:
        # Ask Spotlight for the EXECUTABLE, not the bundle. `-name
        # BasiliskII.app` matched one spelling; even `Basilisk*.app` is wrong in
        # both directions (see EMULATOR_EXECUTABLES). The executable's name is
        # stable across every rename, and the bundle is three levels above it.
        for exe in EMULATOR_EXECUTABLES:
            hits = run(["mdfind", 'kMDItemFSName == "%s"' % exe])
            for line in hits.strip().splitlines():
                marker = "/Contents/MacOS/" + exe
                if not line.endswith(marker):
                    continue
                app = line[:-len(marker)]
                if app.endswith(".app") and exists(app):
                    found, source = app, "mdfind"
                    break
            if found:
                break

    probe_target = found or note      # the helper question is about the bundle,
    helper = bool(probe_target) and exists(   # and a translocated copy carries
        os.path.join(probe_target, "Contents", "Resources", "etherhelpertool"))
    out = {"app": found, "helper": helper, "source": source}
    if note:
        out["translocated"] = note
    return out


def probe(run=None, read=None, exists=None, addresses=None,
          prefs_path=PREFS_PATH, netmode_path=NETMODE_PATH,
          local_env_path=LOCAL_ENV, emulator_app=None):
    """Everything the decision needs, gathered read-only."""
    run = run or _run
    read = read or _read

    # Emulator prefs FIRST: the bundle search uses the folders they name, so a
    # machine whose layout nobody guessed is still discoverable.
    emulator_prefs = bridge_doctor.probe_emulator_prefs(
        read, prefs_path, netmode_path)

    return {
        "bundle": probe_emulator_bundle(
            run, exists, prefs_dirs=bundle_dirs_from_prefs(emulator_prefs),
            override=emulator_app),
        "hfsutils": probe_hfsutils(),
        "processes": bridge_doctor.probe_processes(run),
        "emulator_prefs": emulator_prefs,
        "addresses": (host_config.ipv4_addresses(
            lambda cmd: run(cmd)) if addresses is None else addresses),
        "host_ip": host_config.resolve_host_ip(local_env_path=local_env_path),
        # Which interface the guest's traffic actually reaches this host on.
        # Without it, "the address to dial" degrades to whichever ifconfig
        # listed first — a coin toss on a multi-homed host, and the R2 failure
        # when it lands wrong.
        "default_route_interface": bridge_doctor.probe_network(
            run, "0.0.0.0").get("default_route_interface"),
        "local_env_exists": (exists or os.path.exists)(local_env_path),
        "paths": {"prefs": prefs_path, "netmode": netmode_path,
                  "local_env": local_env_path},
    }


def emulator_running(probes):
    p = probes["processes"]
    return bool(p.get("basilisk") or p.get("sheepshaver"))


# --------------------------------------------------------------------------
# decide — declarative, so the tests read the plan instead of the side effects
# --------------------------------------------------------------------------
def _item(kind, key, message, detail=None, **extra):
    out = {"kind": kind, "key": key, "message": message}
    if detail:
        out["detail"] = detail
    out.update(extra)
    return out


def decide(probes, force_slirp=False, want_agent=True):
    """-> {refusals, steps, notes} — what would be done, and what will not be.

    Nothing here writes. `apply_plan` is the only writer, and it executes exactly
    the steps this returns, so a `--dry-run` is the same computation minus the
    last stage rather than a separate code path that can drift from it.
    """
    refusals, steps, notes = [], [], []
    emu = probes["emulator_prefs"]
    bundle = probes["bundle"]
    current = emu.get("ether") or ""
    ifaces = sorted({iface for iface, _ in probes["addresses"]})

    # --- the refusal D-018 is built on ------------------------------------
    if current.startswith("etherhelper") and bundle["helper"] and not force_slirp:
        refusals.append(_item(
            REFUSE, "etherhelper_in_use",
            f"this host is configured for the etherhelper branch "
            f"(`ether {current}`) and its bundle carries an etherhelpertool.",
            "That branch is fully supported and set up BY HAND (D-018): it keeps "
            "AppleTalk, and it needs two interactive password prompts per launch, "
            "so no script can bring it up. Converting a working etherhelper host "
            "to slirp would also cost it the Chooser and AFP mounts.\n"
            "  Nothing has been changed. Pass --force-slirp if you mean to "
            "convert this machine, and see docs/SETUP.md for the manual branch."))
        return {"refusals": refusals, "steps": steps, "notes": notes}

    if bundle["helper"] and current != SLIRP:
        notes.append(_item(
            NOTE, "etherhelper_available",
            "this bundle carries an etherhelpertool, so the etherhelper branch "
            "is available on this machine — set up by hand, and it keeps "
            "AppleTalk (D-018). Continuing with slirp."))
    elif not bundle["helper"]:
        notes.append(_item(
            NOTE, "no_etherhelper",
            "no etherhelpertool in the emulator bundle: the etherhelper branch "
            "does not exist on this machine at all (R8), which settles the "
            "backend before the interface count is consulted."))

    if bundle.get("translocated"):
        notes.append(_item(
            NOTE, "emulator_translocated",
            "the running emulator is Gatekeeper-TRANSLOCATED: macOS is running "
            "it from a throwaway copy under .../T/AppTranslocation/, a path that "
            "changes on every launch. That path is not written to local.env, "
            "because a launcher pointed at it would fail the moment the app "
            "quits. Clear the quarantine to make the bundle findable: "
            "`xattr -dr com.apple.quarantine <BasiliskII.app>`, then move it out "
            "of the folder it was unzipped into and relaunch."))

    if len(ifaces) < 2:
        notes.append(_item(
            NOTE, "single_interface",
            f"one usable interface ({', '.join(ifaces) or 'none found'}) — on a "
            "single-NIC host a bridged backend cannot reach the machine it runs "
            "in (D-015), so slirp is the only branch that can work here."))

    # --- the emulator backend ---------------------------------------------
    # Two different writes hide behind "set the backend", and only one of them
    # is dangerous while an emulator runs. Rewriting the PREFS file underneath a
    # live emulator is pointless (it read them at launch) and asks for a lost
    # edit; recording the INTENT in .netmode touches nothing the emulator reads.
    # Refusing both was too broad — it locked the installer out of the machine
    # it was built for, a host already sitting on slirp with its guest up.
    prefs_rewrite = current != SLIRP
    intent_stale = emu.get("intended") != SLIRP

    if prefs_rewrite and emulator_running(probes):
        refusals.append(_item(
            REFUSE, "emulator_running",
            "an emulator is running, and the backend must be rewritten in prefs "
            "it read at launch.",
            "Quit the emulator cleanly first — `mac_shutdown`, or Special -> Shut "
            "Down in the guest. Never hard-kill it (D-004)."))
        return {"refusals": refusals, "steps": steps, "notes": notes}

    if not prefs_rewrite and not intent_stale:
        notes.append(_item(NOTE, "backend_already_slirp",
                           "emulator backend is already `ether slirp`, and that "
                           "is what this stack records as intended."))
    elif not prefs_rewrite:
        # The trap this closes: prefs say slirp, .netmode still says something
        # else, so the NEXT start_stack.sh silently "repairs" a deliberate slirp
        # machine back to the other backend. Drift is judged against the record,
        # so the record has to be corrected — and nothing else here needs to be.
        steps.append(_item(
            STEP, "set_backend",
            f"record `{SLIRP}` as the intended backend",
            f"prefs already say `ether {SLIRP}`, but "
            f"{os.path.basename(probes['paths']['netmode'])} says "
            f"`{emu.get('intended') or '<nothing>'}` — so the next launcher run "
            "would repair this machine away from slirp without being asked.",
            current=emu.get("intended"), desired=SLIRP))
    else:
        steps.append(_item(
            STEP, "set_backend",
            f"set the emulator backend to `ether {SLIRP}`",
            f"currently `ether {current or '<none>'}`; the intent recorded in "
            f"{os.path.basename(probes['paths']['netmode'])} becomes `{SLIRP}`, "
            "and check_ether_backend.sh performs the rewrite (timestamped "
            "backup, duplicate `ether` keys collapsed).",
            current=current or None, desired=SLIRP))

    # --- host/local.env ----------------------------------------------------
    # Say which of the two files it will write. Announcing "the discovered
    # emulator" while the header two lines above reads `— not found —` is the
    # same defect this installer exists to remove: text asserting something it
    # did not check. Seen on the 2013 MacBook, 2026-07-28, where the emulator
    # was translocated and therefore deliberately NOT recorded.
    why = ("slirp needs the wildcard bind: the guest's connection arrives from "
           "127.0.0.1 or from this machine's LAN address depending on which "
           "destination it dialled (R7). A DERIVED address would bind successfully "
           "and wait forever, so none is written (R1, R2).")
    if bundle["app"]:
        what = "write host/local.env with no host address and the discovered emulator"
    else:
        what = "write host/local.env with no host address and NO emulator path"
        why += (" No emulator bundle was found, so APPLEBRIDGE_EMULATOR_APP is "
                "left unset and start_stack.sh cannot launch the emulator for "
                "you — launch it by hand, or make the bundle findable (see the "
                "note above) and re-run this installer.")
    steps.append(_item(STEP, "write_local_env", what, why,
                       emulator_app=bundle["app"]))

    # --- who starts the server (R15: one launch path per installation) -----
    if want_agent:
        steps.append(_item(
            STEP, "install_agent",
            "install the launchd agent so the host server starts at login",
            "install_host_service.sh writes the LaunchAgent and chains "
            "deploy_host.sh (the repo lives under TCC-protected ~/Documents, so "
            "launchd runs a deployed copy). This is what makes `installed` mean "
            "`running`, and it makes bridge_doctor's launchd advice true on this "
            "machine rather than describing another one (R13)."))
    else:
        notes.append(_item(
            NOTE, "manual_launch",
            "--no-agent: start the server by hand with "
            "`cd host && ./run_server.sh < /dev/null &`. The redirect is not "
            "decoration — a TTY gives you the interactive prompt and NO control "
            "port, so every MCP tool then fails against a server that looks "
            "perfect (R12)."))

    return {"refusals": refusals, "steps": steps, "notes": notes}


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
def apply_plan(plan, probes, run=None, write=None, read=None):
    """Execute the plan's steps in order. -> [(key, ok, message)].

    Stops at the first failure: later steps assume the earlier ones landed (the
    agent serves a configuration that write_local_env produced).
    """
    run, write, read = run or _run, write or _write, read or _read
    results = []

    for step in plan["steps"]:
        key = step["key"]
        try:
            if key == "set_backend":
                write(probes["paths"]["netmode"], SLIRP + "\n")
                out = run([os.path.join(HERE, "check_ether_backend.sh"),
                           probes["paths"]["prefs"], probes["paths"]["netmode"]])
                now = bridge_doctor.probe_emulator_prefs(
                    read, probes["paths"]["prefs"], probes["paths"]["netmode"])
                ok = now.get("ether") == SLIRP
                results.append((key, ok, out.strip() or f"backend now `{now.get('ether')}`"))
            elif key == "write_local_env":
                text = render_local_env(step.get("emulator_app"))
                path = probes["paths"]["local_env"]
                if read(path) and "APPLEBRIDGE" in read(path):
                    backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
                    shutil.copyfile(path, backup)
                    results.append((key + ":backup", True, f"kept {backup}"))
                write(path, text)
                results.append((key, True, f"wrote {path} (no host address — "
                                           "the server binds 0.0.0.0)"))
            elif key == "install_agent":
                out = run([os.path.join(HERE, "install_host_service.sh")])
                listening = _listening_ports(run)
                ok = 9001 in listening
                results.append((key, ok, (out.strip()[-400:] + "\n" if out else "")
                                + f"listening: {sorted(listening) or 'none'}"))
            else:
                results.append((key, False, "unknown step"))
        except OSError as exc:
            results.append((key, False, str(exc)))

        if results and not results[-1][1]:
            break

    return results


def _listening_ports(run):
    """-> {port} for the bridge's two ports, observed rather than assumed."""
    ports = set()
    for line in run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]).splitlines():
        m = re.search(r":(\d+)\s+\(LISTEN\)", line)
        if m and int(m.group(1)) in (9000, 9001):
            ports.add(int(m.group(1)))
    return ports


def render_local_env(emulator_app=None):
    """-> the text of a generated host/local.env for the slirp branch.

    Deliberately WITHOUT `APPLEBRIDGE_HOST_IP`. On this branch the server must
    bind 0.0.0.0 (R7), and an address invented here is the R2 failure — it binds,
    it waits, and it reports nothing wrong. `APPLEBRIDGE_WIRED_IF` and
    `APPLEBRIDGE_BRIDGE` are absent for a different reason: they are properties
    of the etherhelper backend, not of AppleBridge (R15).
    """
    lines = [
        "# host/local.env — GENERATED by host/install_bridge.py. Safe to edit.",
        "#",
        "# Branch: slirp (D-018). No bridge, no interface alias, no privileged",
        "# step, no AppleTalk. The emulator's own prefs carry `ether slirp`.",
        "#",
        "# APPLEBRIDGE_HOST_IP is deliberately NOT set: on slirp the guest's",
        "# connection arrives from 127.0.0.1 or from this machine's LAN address",
        "# depending on which destination it dialled, so the server binds",
        "# 0.0.0.0 (R7). Setting a specific address here narrows that and can",
        "# make the bridge unreachable without any error being reported.",
        "",
    ]
    if emulator_app:
        lines += [
            "# The emulator bundle, discovered at install time rather than",
            "# hardcoded to one machine's path (R1).",
            f"APPLEBRIDGE_EMULATOR_APP={emulator_app}",
            "",
        ]
    lines += [
        "# The control port stays on loopback. Opening it wider is a deliberate,",
        "# authenticated choice — APPLEBRIDGE_CTRL_TOKEN becomes mandatory and",
        "# the server refuses to start without it.",
        "#APPLEBRIDGE_CTRL_BIND=0.0.0.0",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# the guest half — what this program cannot do, said exactly
# --------------------------------------------------------------------------
def dialable_address(addresses, default_iface=None):
    """The address to tell the guest to dial — chosen, not stumbled into.

    This used to be `addresses[0]`, i.e. whichever interface ifconfig happened to
    list first. On a multi-homed host that is a coin toss, and picking the wrong
    one produces the R2 failure in its purest form: the daemon binds nothing,
    dials, and waits forever while every status field reads healthy.

    The guest reaches this machine the same way anything else on the segment
    does, so prefer the DEFAULT-ROUTE interface's address. Loopback is never a
    candidate — a guest cannot reach the host's 127.0.0.1, whatever the host
    thinks of it.
    """
    usable = [(i, a) for i, a in addresses if not a.startswith("127.")]
    if not usable:
        return None
    if default_iface:
        for iface, addr in usable:
            if iface == default_iface:
                return addr
    return usable[0][1]


def guest_checklist(addresses):
    """-> lines naming every guest-side value, labelled by WHOSE address it is.

    R5 is one word with two meanings: `IP Adresse` in the guest's TCP/IP control
    panel is the GUEST's own address, `IP=` in `AppleBridge Prefs` is the HOST's,
    and in a setup instruction the two stand three lines apart. Swapping them is
    silent — entering slirp's gateway as the guest's own address raises an
    address-conflict alert and disables the TCP/IP driver.
    """
    lines = [
        "GUEST-SIDE STEPS (this program cannot perform them: System 7 with Open",
        "Transport 1.1.1 has no scripting surface for the TCP/IP control panel)",
        "",
        "1. TCP/IP control panel — the GUEST's OWN values. Two ways; prefer the",
        "   first, and type nothing:",
        "       Connect via   Ethernet",
        "       Configure     Using DHCP Server",
        "   slirp answers BOOTP/DHCP itself and hands out ALL FOUR values,",
        "   INCLUDING the name server. Measured on a live guest 2026-07-28: the",
        "   panel showed " + GUEST_ADDR + " / " + GUEST_MASK + " / router " + GUEST_ROUTER + " /",
        "   name server " + GUEST_RESOLVER + ", and the daemon reconnected and completed the",
        "   v0.2 handshake on it. That matters more than the saved typing: the",
        "   name server is the field that gets left empty by hand, and without it",
        "   DNS fails as iCab -23045, which reads like a routing fault rather",
        "   than a name one. DHCP cannot forget it.",
        "",
        "   If your emulator build does not answer DHCP, enter them manually —",
        "   they are fixed by slirp, so they are the same on every machine:",
        "       Configure     Manually",
        f"       IP Address    {GUEST_ADDR}",
        f"       Subnet mask   {GUEST_MASK}",
        f"       Router        {GUEST_ROUTER}",
        f"       Name server   {GUEST_RESOLVER}     <- the one that gets forgotten",
        "",
        "2. AppleBridge Prefs — `IP=` here is THE HOST's address, NOT the guest's.",
        f"   The file lives in `{GUEST_PREFS_HFS}`,",
        "   not in the installation folder, which is where one looks first (R3).",
    ]
    if addresses:
        lines.append(f"       IP={addresses[0][1]}"
                     + f"        (this machine, on {addresses[0][0]})")
        for iface, addr in addresses[1:]:
            lines.append(f"       or  {addr}" + f"        (also this machine, {iface})")
    else:
        lines.append("       IP=<this machine's LAN address — none could be read>")
    lines += [
        f"   NEVER `{GUEST_ROUTER}`: that is slirp's router only, and the daemon's",
        "   connection to it is refused. The host's real LAN address works.",
        "",
        "3. Then launch the daemon. `host/install_bridge.py --seed-guest-prefs",
        "   <image.dmg>` can write step 2 into a POWERED-OFF disk image for you.",
    ]
    return lines


def tier_report():
    """-> lines stating what a machine without MPW still has (R11).

    A guest with no ToolServer is a SUPPORTED configuration, not a broken one.
    Reporting it as a failed install is what R11 exists to prevent.
    """
    return [
        "TIERS",
        "  Native surface (no toolchain needed): screenshots, fork-aware file",
        "    transfer, input injection, LISTDIR, DISKINFO, clipboard, launch,",
        "    shutdown. Measured on a ToolServer-less guest: 11/11 checks passed",
        "    (host/tools/q1_native_surface.py).",
        "  Command surface (needs MPW + ToolServer, OPTIONAL): mpw_execute,",
        "    mac_compile, mac_build. Absent MPW is a tier you do not have, not a",
        "    failed install.",
        "",
        "NOT ON THIS BRANCH: AppleTalk. No Chooser, no AFP mounts, no",
        "  mac_appletalk_browse. TCP is unaffected — which is exactly how this",
        "  gap disguises itself (D-018). Want them? Configure etherhelper by",
        "  hand; docs/SETUP.md describes that branch.",
    ]


def exposure_report(addresses):
    """-> lines on what the wildcard bind publishes, and the safe token order.

    The default stays NO token: a mismatched pair locks the bridge out, and the
    order that cannot do so is guest first, host second (reverted in reverse).
    """
    where = ", ".join(f"{a}" for _, a in addresses) or "this machine's addresses"
    return [
        "EXPOSURE (R10)",
        f"  The wildcard bind publishes :9000 on {where}. The control port",
        "  (:9001) stays on loopback, so command injection still needs local",
        "  access — but the daemon slot itself is reachable: another host on the",
        "  segment can occupy it or pose as a daemon.",
        "  Optional wire auth, in the ONE order that cannot lock you out:",
        "    1. guest first — add `TOKEN=<secret>` to AppleBridge Prefs, reboot it",
        "    2. host second — export APPLEBRIDGE_TOKEN=<secret>, restart the server",
        "  To turn it off, reverse that: host first, guest second.",
    ]


# --------------------------------------------------------------------------
# --seed-guest-prefs: the one guest-side write that is possible at all
# --------------------------------------------------------------------------
# The suite the guest installer expects as SIBLINGS in one folder
# (mac/installer/installer.c: "the binaries ship as siblings of this app").
# AppleBridge must come first — a kit without the daemon is not a kit.
# What the guest installer expects as SIBLINGS in one folder
# (mac/installer/installer.c: "the binaries ship as siblings of this app").
# Each entry is (label, [names to try]) — the installer is built as
# `AppleBridgeInstaller` but referred to with a space in the docs, and the
# binaries live in the deployed folder on one machine and the build output on
# another, so both are searched rather than assumed.
KIT_APPS = [
    ("AppleBridge", ["AppleBridge"]),
    ("AppleBridgeWatchdog", ["AppleBridgeWatchdog"]),
    ("AppleBridgeConfig", ["AppleBridgeConfig"]),
    ("AppleBridgeInstaller", ["AppleBridgeInstaller", "AppleBridge Installer"]),
]
# Folders to look in, deployed first. A kit without the INSTALLER is just a pile
# of binaries — it is required, not optional, because installing by hand is the
# thing this replaces.
KIT_DIRS = [":AppleBridge:", ":MPW:AppleBridge:bin:"]
KIT_REQUIRED = {"AppleBridge", "AppleBridgeInstaller"}

# The kit ships as an HFS DISK IMAGE, not as a folder in the shared directory.
# Measured 2026-07-28 on System 7.6.1: Basilisk's `extfs` does not present a
# 68K application to the guest AS an application. The four binaries appear as
# documents, cannot be launched, and the installer therefore cannot be started
# at all — so the folder-in-the-shared-directory kit was undeliverable in the
# one way that mattered. Confirmed twice, the second time on a folder the guest
# had never seen before, after a full restart, to rule out a stale desktop
# database. Sidecar `.finf`/`.rsrc` files did not change it.
#
# A mounted HFS volume has real forks and real Finder info, so the Finder shows
# applications and launches them. Verified end to end the same day: the guest
# ran `AppleBridgeInstaller` straight off the mounted volume — no copying to
# local storage first — and produced a complete install.
KIT_VOLUME = "AppleBridge Kit"          # what the operator sees on the desktop
KIT_IMAGE_NAME = "AppleBridgeKit.dmg"   # the file they add as a `disk` line
KIT_SLACK_BYTES = 512 * 1024
KIT_MIN_BYTES = 2 * 1024 * 1024


def guest_prefs_text(host_ip, net="OT"):
    """The prefs file that ships INSIDE the kit, already carrying the address.

    The host knows exactly one thing the guest cannot work out for itself: which
    address to dial. That is what this file is for, and the reason the operator
    types nothing while no disk image is written to deliver it.

    `HOME=` is deliberately ABSENT. It names the folder the suite was installed
    into, and only the guest installer knows that — it seeds the key itself
    (installer.c step 3), which is what makes the install relocatable. Shipping
    a value here meant shipping `MeinMac:AppleBridge:`, i.e. this developer's
    volume name, onto a stranger's machine: an R1 literal smuggled in through a
    template.

    `NET=` defaults to Open Transport, the common case. A machine with only
    classic MacTCP needs `NET=MacTCP`; AppleBridgeConfig on the guest changes it
    without a reinstall, and the daemon reports the active transport in its
    monitor footer so a wrong guess is visible rather than silent.

    LF endings on purpose: the guest's own file is LF-terminated because MPW C
    maps '\r' to 0x0A, and a helpful conversion to CR would corrupt the very
    configuration the bridge depends on (R20).
    """
    # ASCII only in the generated body. It is read by a 68K daemon through a
    # MacRoman path and displayed by tools with assorted opinions about
    # encoding; a decorative em-dash in a comment is a risk with no upside.
    return ("# AppleBridge preferences - generated by install_bridge.py\n"
            "# HOME= is set by the guest installer, which knows where it put things.\n"
            f"IP={host_ip}\n"
            f"NET={net}\n"
            "DEBUG=0\n")


def image_in_use(image, probes, run=None):
    """Does a running emulator have THIS disk image open? -> True/False.

    The guard this replaces refused whenever any emulator was running at all,
    which is right about the danger and wrong about the scope: reading a
    POWERED-OFF image is perfectly safe while a different guest runs on a
    different image, and that is the normal case on a machine with a working
    guest and a test guest. The broad version blocked building a kit from the
    idle image because the *other* one was up.

    Precise when it can be, conservative when it cannot: if `lsof` gives no
    usable answer — missing, permission-denied, an emulator whose pid we did
    not get — this returns True and the caller refuses, exactly as before. A
    torn read of somebody's System 7 volume is not a risk worth taking for the
    convenience of skipping one shutdown.
    """
    run = run or _run
    pids = [p["pid"] for p in (probes["processes"].get("basilisk"),
                               probes["processes"].get("sheepshaver"))
            if p and p.get("pid")]
    if not pids:
        return False
    target = os.path.realpath(image)
    for pid in pids:
        out = run(["lsof", "-p", str(pid)])
        # No output at all means lsof told us nothing — not that the file is
        # closed. Treat silence as "unknown", which is a refusal.
        if not out.strip():
            return True
        for line in out.splitlines():
            # The path is the remainder of the line, not the last field: these
            # filenames contain spaces ("System761 weiter.dmg"), and splitting
            # on whitespace silently compares the wrong string.
            idx = line.find("/")
            if idx >= 0 and os.path.realpath(line[idx:].strip()) == target:
                return True
    return False


_IPV4_RE = re.compile(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def payload_host_literals(blob):
    """IPv4 addresses compiled into a 68K binary. -> sorted list of strings.

    R1 and R2 exist because a *wrong* host address is worse than none: the
    daemon connects to whatever answers and reports full health — protocol
    negotiated, heartbeat running, zero errors — so nothing downstream can tell
    you it is talking to the wrong computer.

    The kit shipped exactly that on 2026-07-28. `AppleBridgeInstaller` had not
    been rebuilt since 2026-07-02, five days before `66470a5` emptied
    `DEFAULT_HOST_IP`, so it still carried `192.168.3.154` — and because nothing
    read the kit's prefs file, that literal *was* the address a fresh install
    received. It looked flawless on the LAN where the number happens to be
    right.

    Dates would be the obvious staleness check and are the wrong one: a rebuild
    from unchanged sources refreshes the date and fixes nothing, and a correct
    old binary would be rejected. This looks for the defect itself.
    """
    out = set()
    for m in _IPV4_RE.finditer(blob or b""):
        s = m.group().decode("ascii", "replace")
        octets = s.split(".")
        if all(o.isdigit() and int(o) <= 255 for o in octets) and not s.startswith("0."):
            out.add(s)
    return sorted(out)


def kit_image_size(payload_bytes):
    """How big to make the kit volume. -> byte count, a multiple of 512.

    Generous on purpose. The payload is a couple of hundred kilobytes and the
    floor is two megabytes, because a volume sized exactly to its contents has
    nowhere to put the desktop database the Finder writes the moment the volume
    is mounted — and a full volume fails in the guest, far from here, where the
    only symptom is a Finder complaint nobody will connect back to this line.
    Disk is free; a failure mode reported inside an emulator is not.
    """
    size = max(KIT_MIN_BYTES, int(payload_bytes) + KIT_SLACK_BYTES)
    return (size + 511) // 512 * 512


def export_guest_kit(dest, host_ip, probes, run=None, exists=None,
                     read_bytes=None, write_bytes=None, staging=None):
    """Build a mountable guest kit on the HOST. -> (ok, message, [placed]).

    Why a kit and not a write into the guest's system image: a program that
    edits other people's disk volumes is not one strangers should run, whatever
    hfsutils makes technically possible. The guest already HAS a real installer
    — Gestalt preflight, fork-aware copy, prefs seeding, Startup Items alias —
    and it refuses environments that cannot work, which is its whole value. What
    was missing was never installation; it was DISTRIBUTION.

    Why a DISK IMAGE and not a folder in the shared directory: see KIT_VOLUME
    above. The short version is that `extfs` hands the guest documents where
    applications should be, so the folder kit could not be launched at all. This
    writes one new file of its own and still touches nothing of theirs — the
    operator adds a `disk` line, and deleting the file undoes everything.

    The binaries come out of a POWERED-OFF image as MacBinary (`hcopy -m`),
    because the repository tracks source, not 68K artifacts — so this clones a
    working install rather than building one. MacBinary is the right container
    here precisely because it is a container in TRANSIT: it carries both forks
    and the Finder info between two HFS volumes, and is unwrapped again by
    `hcopy -m` on the way in. It is only wrong when left as the final artifact.
    """
    run = run or _run
    exists = exists or os.path.exists
    read_bytes = read_bytes or _read_bytes
    write_bytes = write_bytes or _write_bytes

    # Declared, not discovered by crashing. Every step below shells out to
    # hfsutils, which macOS does not ship.
    missing_hfs = probes.get("hfsutils", {}).get("missing")
    if missing_hfs:
        return (False, hfsutils_advice(missing_hfs, "build a kit"), [])

    # Skip a previously-built kit when choosing the SOURCE. Once the operator
    # adds the `disk` line this tool prints, the kit is itself in the emulator's
    # disk list — and a second run would otherwise read the kit as its own
    # source, find no binaries, and report a missing REQUIRED file about a
    # volume nobody meant to search.
    images = [d for d in probes["emulator_prefs"].get("disks", [])
              if exists(d) and os.path.basename(d) != KIT_IMAGE_NAME]
    if not images:
        return (False, "no readable disk image in the emulator prefs to take "
                       "the binaries from", [])

    # Per-image, not per-machine: a powered-off image is safe to read while a
    # different guest runs on a different image (see image_in_use).
    open_images = [d for d in images if image_in_use(d, probes, run=run)]
    images = [d for d in images if d not in open_images]
    if not images:
        return (False, "an emulator is running with "
                       + ", ".join(os.path.basename(d) for d in open_images)
                       + " open — reading a live image gives a torn "
                         "filesystem; quit it first (mac_shutdown, or "
                         "Special > Shut Down in the guest)", [])

    image_path = (dest if dest.lower().endswith((".dmg", ".img", ".hfs"))
                  else os.path.join(dest, KIT_IMAGE_NAME))
    own_staging = staging is None
    staging = staging or tempfile.mkdtemp(prefix="applebridge-kit-")

    try:
        # --- 1. take the binaries out of the working volume -----------------
        staged, missing = [], []
        src = images[0]
        out = run(["hmount", src])
        if "Volume" not in out:
            # The runner degrades to empty on any OSError, so an unexplained
            # failure is almost always the tool being absent or unrunnable --
            # say that instead of printing a colon and nothing, which points
            # the reader at the image, and the image is usually fine.
            why = out.strip()[:160] or ("no output at all — hmount could not be "
                                        "run (not installed, or not executable)")
            return (False, f"hmount failed on {src}: {why}", [])
        try:
            for label, names in KIT_APPS:
                blob = os.path.join(staging, label + ".macbin")
                for folder in KIT_DIRS:
                    for name in names:
                        run(["hcopy", "-m", folder + name, blob])
                        if exists(blob):
                            break
                    if exists(blob):
                        break
                if exists(blob):
                    staged.append((label, blob))
                else:
                    missing.append(label + (" — REQUIRED"
                                            if label in KIT_REQUIRED
                                            else " (optional)"))
        finally:
            run(["humount"])

        short = [m for m in missing if "REQUIRED" in m]
        if short:
            # Fail BEFORE creating the image. Half a kit is worse than none: it
            # mounts, it looks installable, and it is not.
            #
            # Name the bootstrap problem rather than only the symptom: a kit is
            # assembled FROM a guest that already runs AppleBridge, so a machine
            # that never has cannot build one, and no release currently carries
            # the 68K binaries. Somebody hitting this on a fresh machine has
            # done nothing wrong and needs the way out, not a file list.
            return (False, "cannot ship a kit without " + ", ".join(short)
                           + " — searched " + " and ".join(KIT_DIRS)
                           + ". A kit is built FROM a guest that already has "
                             "AppleBridge installed, so this machine cannot "
                             "make its own: build it on a machine that has one, "
                             "with APPLEBRIDGE_GUEST_DIALS=<this host's address> "
                             "so the kit's prefs name the host the guest should "
                             "dial, and copy the resulting .dmg here",
                    [lbl for lbl, _ in staged])

        # --- 1b. no baked-in host address may leave in an APPLICATION -------
        # Checked before the prefs are added on purpose: the prefs file is the
        # ONE place an address belongs, so scanning it would reject every kit.
        baked = {}
        for label, blob in staged:
            lits = payload_host_literals(read_bytes(blob))
            if lits:
                baked[label] = lits
        if baked:
            detail = "; ".join(f"{k} carries {', '.join(v)}" for k, v in
                               sorted(baked.items()))
            return (False,
                    "refusing to ship a kit with a hardcoded host address in a "
                    "binary — " + detail + ". A stale build does this: the "
                    "address ends up on a stranger's machine, the daemon dials "
                    "the wrong computer and reports full health (R1, R2). "
                    "Rebuild it from current sources.",
                    [lbl for lbl, _ in staged])

        # --- 2. the prefs, as a real TEXT file rather than a bare blob -------
        # Wrapped in MacBinary for the same reason as the binaries: it is how
        # type and creator survive the trip onto the HFS volume. Without them
        # the guest gets a typeless file, which AppleBridgeConfig will not open.
        prefs_blob = os.path.join(staging, "AppleBridgePrefs.macbin")
        write_bytes(prefs_blob, macbinary.encode(
            guest_prefs_text(host_ip).encode("mac_roman"),
            name="AppleBridge Prefs", type_="TEXT", creator="ttxt"))
        staged.append(("AppleBridge Prefs", prefs_blob))

        # --- 3. make the volume ---------------------------------------------
        payload = sum(len(read_bytes(p)) for _, p in staged)
        size = kit_image_size(payload)
        write_bytes(image_path, b"\0" * size)
        out = run(["hformat", "-l", KIT_VOLUME, image_path])
        if "rror" in out or not exists(image_path):
            return (False, f"hformat failed on {image_path}: "
                           f"{out.strip()[:160]}", [])

        # --- 4. put the payload in -------------------------------------------
        out = run(["hmount", image_path])
        if "Volume" not in out:
            return (False, f"hmount failed on the new kit image {image_path}: "
                           f"{out.strip()[:160]}", [])
        try:
            for _, blob in staged:
                run(["hcopy", "-m", blob, ":"])
            listing = run(["hls", ":"])
        finally:
            run(["humount"])
    finally:
        if own_staging:
            shutil.rmtree(staging, ignore_errors=True)

    # Verify by the artifact, not by the exit status of the last command. Every
    # step above can report success and leave an empty volume, and an empty kit
    # is discovered by a person in an emulator rather than here.
    placed = [label for label, _ in staged if label.split()[0] in listing
              or label in listing]
    absent = [label for label, _ in staged
              if label not in placed]
    if absent:
        return (False, "the kit image was built but " + ", ".join(absent)
                       + " did not land on it — refusing to ship a volume "
                         "that mounts and cannot install", placed)

    msg = (f"kit image with {len(placed)} items: {image_path}. "
           f"Add it to the emulator's config as a second disk, then relaunch "
           f"(the disk list is read at LAUNCH only):\n"
           f"        disk {image_path}\n"
           f"    Inside the guest a `{KIT_VOLUME}` volume appears; run "
           f"`AppleBridgeInstaller` from it directly — no copying needed. It "
           f"preflights the machine, copies the suite and sets up autostart. "
           f"Prefs already carry IP={host_ip}.")
    if missing:
        msg += "  Not found: " + ", ".join(missing) + "."
    return (True, msg, placed)


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def seed_guest_prefs(image, host_ip, probes, run=None, hfs=None):
    """Rewrite `IP=` in a POWERED-OFF disk image's AppleBridge Prefs. -> (ok, msg).

    Two rules make this safe, and both were paid for:

    R14 — the daemon holds its own copy of the prefs and writes it back, so an
    edit made underneath a RUNNING daemon can be silently replaced by the values
    it started with. A powered-off image has no daemon, which is the only state
    in which this edit is durable.

    R20 — the file moves as BYTES. A classic-Mac text file read in Python's text
    mode arrives with every CR rewritten to LF; the guest then sees one enormous
    line. So: no text mode, no `hcopy -t`, and only the `IP=` line is touched —
    every other key (`APP=`, `HOME=`, `NET=`, `WIN=`) is preserved verbatim.
    """
    run = run or _run
    missing_hfs = probes.get("hfsutils", {}).get("missing")
    if missing_hfs:
        return (False, hfsutils_advice(missing_hfs, "read a disk image"))
    hfs = hfs or {}

    if emulator_running(probes):
        return False, ("refusing: an emulator is running. Writing an HFS volume "
                       "underneath a live emulator risks the image, and a running "
                       "daemon would overwrite the edit from its in-memory copy "
                       "(R14). Shut the guest down cleanly first.")
    if not host_ip or host_ip == host_config.BIND_ALL:
        return False, ("refusing: no host address to seed. The server binds "
                       "0.0.0.0, but the GUEST needs a concrete address to dial "
                       "— pass one, or set it in the guest by hand. A guessed "
                       "value binds successfully and waits forever (R2).")

    tmp = hfs.get("tmp") or os.path.join("/tmp", f"applebridge-prefs-{os.getpid()}")
    out = run(["hmount", image])
    if "is not a Macintosh" in out or "error" in out.lower():
        return False, f"hmount failed: {out.strip()[:200]}"
    try:
        run(["hcopy", "-r", GUEST_PREFS_HFS, tmp])
        try:
            with open(tmp, "rb") as fh:
                original = fh.read()
        except OSError:
            return False, (f"no `{GUEST_PREFS_HFS}` in that image. The GUEST-side "
                           "installer creates it; this only edits an existing one.")

        shutil.copyfile(tmp, tmp + ".orig")
        updated = rewrite_ip_line(original, host_ip)
        if updated == original:
            return True, f"IP= was already {host_ip}; nothing to change."
        with open(tmp, "wb") as fh:
            fh.write(updated)
        run(["hcopy", "-r", tmp, GUEST_PREFS_HFS])
        return True, (f"seeded IP={host_ip} into {GUEST_PREFS_HFS}; every other "
                      f"key preserved. Original kept at {tmp}.orig")
    finally:
        run(["humount"])


def rewrite_ip_line(data, host_ip):
    """-> `data` with the IP= value replaced, as bytes, CR endings intact.

    Kept separate from the hfsutils plumbing so the byte handling that R20 is
    about can be tested on its own, without a disk image.
    """
    want = b"IP=" + host_ip.encode("ascii")
    out, seen = [], False
    for line in re.split(b"(\r\n|\r|\n)", data):
        if line in (b"\r", b"\n", b"\r\n"):
            out.append(line)
            continue
        if line.startswith(b"IP=") and not seen:
            out.append(want)
            seen = True
        else:
            out.append(line)
    if not seen:
        # No IP= key at all: append one, matching the file's own line ending.
        eol = b"\r" if b"\r" in data else b"\n"
        joined = b"".join(out)
        return joined + (b"" if joined.endswith(eol) or not joined else eol) + want + eol
    return b"".join(out)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def format_text(probes, plan, results=None, dry_run=True):
    bundle, emu = probes["bundle"], probes["emulator_prefs"]
    ifaces = sorted({i for i, _ in probes["addresses"]})
    lines = ["AppleBridge host installer — slirp branch (D-018)", ""]
    lines.append(f"emulator bundle:  {bundle['app'] or '— not found —'}"
                 + (f"   ({bundle['source']})" if bundle["source"] else ""))
    lines.append(f"etherhelpertool:  {'present' if bundle['helper'] else 'absent'}")
    lines.append(f"current backend:  {emu.get('ether') or '—'}"
                 + (f"   (intended: {emu['intended']})" if emu.get("intended") else ""))
    lines.append(f"interfaces:       {', '.join(ifaces) or '—'}")
    lines.append(f"emulator running: {'yes' if emulator_running(probes) else 'no'}")
    hfs = probes.get("hfsutils") or {}
    lines.append("hfsutils:         "
                 + ("present" if not hfs.get("missing")
                    else "MISSING (" + ", ".join(hfs["missing"])
                         + ") — needed only for --export-guest-kit and "
                           "--seed-guest-prefs"))
    lines.append("")

    for note in plan["notes"]:
        lines.append(f"  note: {note['message']}")
    if plan["notes"]:
        lines.append("")

    if plan["refusals"]:
        for ref in plan["refusals"]:
            lines.append(f"REFUSING — {ref['message']}")
            if ref.get("detail"):
                lines += ["  " + l for l in ref["detail"].split("\n")]
        lines.append("")
        return "\n".join(lines)

    lines.append("PLAN" + (" (dry run — nothing was changed)" if dry_run else ""))
    for n, step in enumerate(plan["steps"], 1):
        lines.append(f"  {n}. {step['message']}")
        if step.get("detail"):
            lines.append(f"       why: {step['detail']}")
    if not plan["steps"]:
        lines.append("  nothing to do — this host is already configured.")
    lines.append("")

    if results:
        lines.append("RESULT")
        for key, ok, msg in results:
            lines.append(f"  [{'ok' if ok else 'FAILED'}] {key}: {msg}")
        lines.append("")

    lines += guest_checklist(probes["addresses"]) + [""]
    lines += tier_report() + [""]
    lines += exposure_report(probes["addresses"])
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    """The argument parser — strict, because the safety flag used to fail OPEN.

    Arguments were matched with `"--dry-run" in argv` and anything unrecognised
    was ignored. So `--help` — the single most likely thing a stranger types at
    a program they have not run before — was not a flag at all: it fell through
    and the installer PERFORMED THE INSTALLATION. Observed 2026-07-28 on the
    development machine, which rewrote local.env, took a backup and restarted
    the launchd agent in response to a request for usage text.

    The same hole covers every typo. `--dryrun`, `--dry_run`, `--dry-run=1` all
    meant "apply", and on an unconfigured host that is the whole install rather
    than a description of it. A safety flag that vanishes when misspelled is
    worse than no safety flag, because it is trusted.

    argparse rejects an unknown option and prints usage, so the failure mode is
    now exit 2 and no writes.
    """
    p = argparse.ArgumentParser(
        prog="install_bridge.py",
        description="AppleBridge host installer — configures the slirp branch "
                    "(D-018). Run with --dry-run first: it performs the same "
                    "computation and stops before the only stage that writes.",
        epilog="Exit codes: 0 ok, 1 a step failed, 2 bad arguments, "
               "3 refused (nothing was written).")
    p.add_argument("--dry-run", action="store_true",
                   help="derive and print the plan; change nothing")
    p.add_argument("--json", action="store_true",
                   help="machine-readable probes, plan and results")
    p.add_argument("--force-slirp", action="store_true",
                   help="convert a host already configured for etherhelper "
                        "(refused by default — that branch is somebody's "
                        "working AppleTalk setup)")
    p.add_argument("--emulator-app", metavar="PATH", default=None,
                   help="the emulator bundle to record, when discovery "
                        "cannot find it (several builds in one folder, or "
                        "a name nothing could guess). A flag rather than a "
                        "prompt on purpose: this branch exists so the "
                        "result can start with nobody at the keyboard.")
    p.add_argument("--no-agent", action="store_true",
                   help="configure only; do not install the launchd agent")
    p.add_argument("--export-guest-kit", metavar="DIR", nargs="?",
                   const="", default=None,
                   help="build a mountable disk image the guest can install "
                        "itself from: the suite plus a prefs file already "
                        "carrying this host's address. Defaults to the folder "
                        "holding the emulator's own disk images. Writes one "
                        "new file and nothing into any existing image. Needs "
                        "hfsutils. Set APPLEBRIDGE_GUEST_DIALS=<address> to "
                        "build a kit for a DIFFERENT host — the machine that "
                        "has the binaries is not always the machine that will "
                        "run the bridge.")
    p.add_argument("--seed-guest-prefs", metavar="IMAGE.DMG",
                   help="write IP= into a POWERED-OFF disk image's "
                        "AppleBridge Prefs (refused while an emulator runs)")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    dry_run = args.dry_run
    as_json = args.json
    force = args.force_slirp
    want_agent = not args.no_agent
    seed_image = args.seed_guest_prefs
    kit_dir = args.export_guest_kit

    probes = probe(emulator_app=args.emulator_app)
    plan = decide(probes, force_slirp=force, want_agent=want_agent)

    results = None
    if not dry_run and not plan["refusals"]:
        results = apply_plan(plan, probes)

    # Seeding is OPT-IN, and stays that way. The installer can find the image
    # (the path is in the prefs it already reads) and will NAME it — but writing
    # into somebody's disk volume is not something a program should do because
    # it can. A tool that edits other people's images is not one strangers
    # should run, whatever hfsutils permits. `--export-guest-kit` is the answer
    # for a machine you do not own: the host assembles a folder, the guest's own
    # installer does the installing, and nothing of theirs is touched.
    auto_seeded = False

    seed = None
    if seed_image:
        host_ip = os.environ.get("APPLEBRIDGE_GUEST_DIALS") or \
            dialable_address(probes["addresses"],
                             probes.get("default_route_interface")) or ""
        if dry_run:
            seed = (True, f"dry run: would seed IP={host_ip} into "
                          f"{seed_image}{GUEST_PREFS_HFS}"
                          + ("  (image discovered from the emulator prefs)"
                             if auto_seeded else ""))
        else:
            seed = seed_guest_prefs(seed_image, host_ip, probes)

    kit = None
    if kit_dir is not None:
        # Default beside the emulator's own disk images, not in the shared
        # folder: the kit is now a disk image the operator adds a `disk` line
        # for, so it belongs where their other images live. The shared folder
        # was the right home for the folder-shaped kit, and that kit could not
        # be launched from there at all (see KIT_VOLUME).
        siblings = [d for d in probes["emulator_prefs"].get("disks", [])
                    if os.path.basename(d) != KIT_IMAGE_NAME]
        dest = kit_dir or (os.path.dirname(siblings[0]) if siblings else "")
        host_ip = os.environ.get("APPLEBRIDGE_GUEST_DIALS") or \
            dialable_address(probes["addresses"],
                             probes.get("default_route_interface")) or ""
        if not dest:
            kit = (False, "no disk image in the emulator prefs to place the "
                          "kit beside — give --export-guest-kit a directory",
                   [])
        elif not host_ip:
            kit = (False, "no usable host address to put in the kit's prefs", [])
        elif dry_run:
            names = ", ".join(label for label, _ in KIT_APPS)
            target = (dest if dest.lower().endswith((".dmg", ".img", ".hfs"))
                      else os.path.join(dest, KIT_IMAGE_NAME))
            kit = (True, f"dry run: would build the kit image {target} "
                         f"(volume `{KIT_VOLUME}`) with IP={host_ip} "
                         f"({names} + prefs)", [])
        else:
            kit = export_guest_kit(dest, host_ip, probes)

    if as_json:
        print(json.dumps({"probes": probes, "plan": plan, "kit": kit,
                          "results": results, "seed": seed,
                          "dry_run": dry_run}, indent=2, default=str))
    else:
        print(format_text(probes, plan, results, dry_run))
        if seed:
            print(f"\nSEED: {'ok' if seed[0] else 'FAILED'} — {seed[1]}")
        if kit:
            print(f"\nGUEST KIT: {'ok' if kit[0] else 'FAILED'} — {kit[1]}")
            for f in kit[2]:
                print(f"    {f}")

    if plan["refusals"]:
        return 3
    if results and not all(ok for _, ok, _ in results):
        return 1
    if seed and not seed[0]:
        return 1
    if kit and not kit[0]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
