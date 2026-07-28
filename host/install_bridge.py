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
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bridge_doctor                                   # noqa: E402  (path first)
import host_config                                     # noqa: E402

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
def probe_emulator_bundle(run=None, exists=None, candidates=BUNDLE_CANDIDATES):
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
    if not found:
        hit = run(["mdfind", "-name", "BasiliskII.app"]).strip().splitlines()
        for line in hit:
            if line.endswith(".app") and exists(line):
                found, source = line, "mdfind"
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
          local_env_path=LOCAL_ENV):
    """Everything the decision needs, gathered read-only."""
    run = run or _run
    read = read or _read

    return {
        "bundle": probe_emulator_bundle(run, exists),
        "processes": bridge_doctor.probe_processes(run),
        "emulator_prefs": bridge_doctor.probe_emulator_prefs(
            read, prefs_path, netmode_path),
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
KIT_SUBDIR = "AppleBridge Kit"


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


def export_guest_kit(dest, host_ip, probes, run=None, exists=None,
                     read_bytes=None, write_bytes=None):
    """Assemble a guest kit on the HOST. -> (ok, message, [placed]).

    Why a kit and not a write into the guest's disk image: a program that edits
    other people's disk volumes is not one strangers should run, whatever
    hfsutils makes technically possible. The guest already HAS a real installer
    — Gestalt preflight, fork-aware copy, prefs seeding, Startup Items alias —
    and it refuses environments that cannot work, which is its whole value. What
    was missing was never installation; it was DISTRIBUTION: nobody assembled
    the folder that installer expects.

    So the host builds the folder and puts it where the guest can already see
    it (Basilisk's `extfs`, which the guest mounts as `Unix:`). The operator
    opens it inside the guest and double-clicks. Nothing of theirs is written.

    The binaries come out of a POWERED-OFF image as MacBinary (`hcopy -m`),
    because the repository tracks source, not 68K artifacts — so this clones a
    working install rather than building one. A release payload would drop into
    the same folder unchanged.
    """
    run = run or _run
    exists = exists or os.path.exists
    if probes["processes"]["basilisk"] or probes["processes"]["sheepshaver"]:
        return (False, "an emulator is running — its image cannot be read "
                       "safely; quit it first (mac_shutdown, or Special > Shut "
                       "Down in the guest)", [])
    images = [d for d in probes["emulator_prefs"].get("disks", []) if exists(d)]
    if not images:
        return (False, "no readable disk image in the emulator prefs to take "
                       "the binaries from", [])

    # Its own subfolder: the shared folder is somebody's working directory, and
    # scattering four files across it is not a kit, it is litter.
    dest = os.path.join(dest, KIT_SUBDIR)
    os.makedirs(dest, exist_ok=True)

    placed, missing = [], []
    src = images[0]
    out = run(["hmount", src])
    if "Volume" not in out:
        return (False, f"hmount failed on {src}: {out.strip()[:160]}", [])
    try:
        for label, names in KIT_APPS:
            target = os.path.join(dest, label + ".bin")
            for folder in KIT_DIRS:
                for name in names:
                    run(["hcopy", "-m", folder + name, target])
                    if exists(target):
                        break
                if exists(target):
                    break
            if exists(target):
                placed.append(label + ".bin")
            else:
                missing.append(label + (" — REQUIRED" if label in KIT_REQUIRED
                                        else " (optional)"))
    finally:
        run(["humount"])

    prefs_path = os.path.join(dest, "AppleBridge Prefs")
    (write_bytes or _write_bytes)(prefs_path,
                                  guest_prefs_text(host_ip).encode("mac_roman"))
    placed.append("AppleBridge Prefs")

    short = [m for m in missing if "REQUIRED" in m]
    if short:
        return (False, "cannot ship a kit without " + ", ".join(short)
                       + " — searched " + " and ".join(KIT_DIRS), placed)
    msg = (f"kit for a guest to install itself: {len(placed)} files in {dest}. "
           f"Open that folder INSIDE the guest (Basilisk shows it as `Unix:`) "
           f"and run `AppleBridge Installer` — it preflights the machine, "
           f"copies the suite and sets up autostart. Prefs already carry "
           f"IP={host_ip}.")
    if missing:
        msg += "  Not found: " + ", ".join(missing) + "."
    return (True, msg, placed)


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
    p.add_argument("--no-agent", action="store_true",
                   help="configure only; do not install the launchd agent")
    p.add_argument("--export-guest-kit", metavar="DIR", nargs="?",
                   const="", default=None,
                   help="assemble a folder the guest can install itself from: "
                        "the suite plus a prefs file already carrying this "
                        "host's address. Defaults to the emulator's shared "
                        "folder, which the guest sees as `Unix:`. Writes "
                        "nothing into any disk image.")
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

    probes = probe()
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
        dest = kit_dir or (probes["emulator_prefs"].get("shared_folder") or "")
        host_ip = os.environ.get("APPLEBRIDGE_GUEST_DIALS") or \
            dialable_address(probes["addresses"],
                             probes.get("default_route_interface")) or ""
        if not dest:
            kit = (False, "no shared folder configured in the emulator prefs "
                          "(`extfs`) — give --export-guest-kit a directory", [])
        elif not host_ip:
            kit = (False, "no usable host address to put in the kit's prefs", [])
        elif dry_run:
            kit = (True, f"dry run: would assemble a guest kit in {dest} "
                         f"with IP={host_ip} ({', '.join(KIT_APPS)} + prefs)", [])
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
