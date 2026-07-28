"""bridge_doctor — one-call, cross-layer diagnosis of the AppleBridge stack.

Why this exists
---------------
`mac_status` sees exactly two things: the host server's control port and the
daemon link. When the bridge is "down", the cause is almost always a layer
BELOW that — and mac_status reports all of them identically as
`host_server_running=false` / `daemon_connected=false`. Every such session so
far has been spent hand-probing the same eight things in the same order:

    launchd job -> listening sockets -> .154 alias placement -> default route
    -> BasiliskII -> etherhelpertool -> emulator "ether" backend -> peer IP

This module runs that sweep in one call and, crucially, runs it LOCALLY — it
never needs the control port, so it still answers when the host server is the
thing that is broken.

Failure modes it names outright (each cost a real session at least once):
  * launchd job booted out / `disable`d  -> ports closed, "bridge kaputt" is a
    deliberate shutdown, not a bug.
  * `.154` aliased on BOTH en0 and en8   -> the MACNAT return path splits across
    interfaces; the daemon's connect blocks and freezes the emulator at 100 %.
  * `ether slirp` in the Basilisk prefs  -> guest lands on 10.0.2.x behind an
    IP-only NAT: TCP works, so the bridge looks fine, but AppleTalk frames are
    dropped -> the Chooser finds no AppleShare server, and bulk throughput
    drops ~80 %.
  * etherhelpertool dead (adapter unplugged) -> guest has no NIC at all; only a
    full BasiliskII relaunch respawns the helper.

Design notes
  * stdlib only (the host server runs under /usr/bin/python3).
  * Every probe is read-only, short-timeout, and non-fatal: a probe that fails
    records `None` plus a note instead of raising, so one missing binary can
    never take the diagnosis down.
  * All shelling out goes through an injectable `run` callable, so the tests
    drive the whole matrix from canned command output with no live stack.
"""

import json
import os
import tempfile
import re
import subprocess

# The host identity the 68K daemon dials — resolved the same way host_server.py
# resolves it, so the diagnosis and the server can never disagree about which
# address was meant. The literal that used to sit here was a "duplicated as a
# default" copy, which is precisely how the two drift (R1).
BIND_ALL = "0.0.0.0"           # own constant: the import below may not land
try:
    import host_config
    DEFAULT_HOST_IP = host_config.resolve_host_ip()[0]
    BIND_ALL = host_config.BIND_ALL
except ImportError:            # deployed copy predating the module
    DEFAULT_HOST_IP = os.environ.get("APPLEBRIDGE_HOST_IP", BIND_ALL)
LAUNCHD_LABEL = "de.390er.applebridge-host"
DAEMON_PORT = 9000
CONTROL_PORT = 9001
PREFS_PATH = os.path.expanduser("~/.basilisk_ii_prefs")
NETMODE_PATH = os.path.expanduser("~/.basilisk_ii_prefs.netmode")

# Finding levels, ordered by severity — `verdict` reports the worst one seen.
ERROR, WARN, INFO = "error", "warn", "info"
_SEVERITY = {INFO: 0, WARN: 1, ERROR: 2}


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------
def _run(argv, timeout=4.0):
    """Run a probe command, returning stdout as text ('' on any failure).

    Probes must never raise: a missing binary, a non-zero exit or a hung
    command degrades that one field to empty, not the whole diagnosis.
    """
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _read(path):
    """Read a text file, returning '' if it is absent or unreadable."""
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _finding(level, key, message, fix=None):
    f = {"level": level, "key": key, "message": message}
    if fix:
        f["fix"] = fix
    return f


# --------------------------------------------------------------------------
# probes — each takes the injected runner/reader and returns a plain dict
# --------------------------------------------------------------------------
def probe_launchd(run, uid, exists=None):
    """launchd agent state: loaded (with pid), or absent, or explicitly disabled.

    `launchctl list` shows loaded jobs; `print-disabled` shows the separate
    "will not load even on demand" flag that a `launchctl disable` sets and a
    plain bootout does not.
    """
    listing = run(["launchctl", "list"])
    loaded, pid = False, None
    for line in listing.splitlines():
        if line.rstrip().endswith(LAUNCHD_LABEL):
            loaded = True
            first = line.split("\t")[0].strip()
            pid = int(first) if first.isdigit() else None
            break

    disabled = False
    for line in run(["launchctl", "print-disabled", f"gui/{uid}"]).splitlines():
        if LAUNCHD_LABEL in line:
            disabled = "true" in line.lower() or "disabled" in line.lower()
            break

    # Whether the agent is INSTALLED at all is a different question from whether
    # it is loaded, and the two call for opposite advice (R13). Injectable like
    # run/read: reading the real filesystem here made the probe behave one way
    # on the developer's machine (plist present) and another on a fresh one —
    # the exact defect class this tool exists to catch, so not in the tool.
    exists = exists or os.path.exists
    installed = exists(
        os.path.expanduser("~/Library/LaunchAgents/" + LAUNCHD_LABEL + ".plist"))

    return {"label": LAUNCHD_LABEL, "loaded": loaded, "pid": pid,
            "disabled": disabled, "installed": installed}


def probe_sockets(run):
    """Listening sockets for :9000/:9001 plus the daemon's established peer.

    The peer address is the one authoritative answer to "what IP does the guest
    actually have" — it is observed, not configured, so it also catches a guest
    that silently fell back to DHCP.

    That is exactly why the match must NOT be anchored on the configured host
    address. It was, and on the slirp branch there is no configured address (the
    server binds 0.0.0.0 by design, D-018), so the pattern never matched and the
    doctor reported "no guest is connected to :9000 yet" while `lsof` showed the
    link ESTABLISHED and the bridge was answering commands. Observed 2026-07-28
    on a freshly booted guest. A diagnostic that states a fact it did not check
    is worse than one that says nothing, and the whole branch the installer
    configures was affected.

    Anchoring on the port alone is also strictly more correct on the etherhelper
    branch: an established connection whose LOCAL end is :9000 is the daemon
    link, whatever address the server happens to be bound to.

    On slirp the peer is the host's OWN LAN address — the guest's traffic leaves
    through the host's stack — so `guest_peer_ip == host address` is normal
    there and not a sign of a misconfigured guest.
    """
    listen = {"daemon_port": None, "control_port": None}
    for line in run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]).splitlines():
        m = re.search(r"(\S+):(\d+)\s+\(LISTEN\)", line)
        if not m:
            continue
        addr, port = m.group(1), int(m.group(2))
        if port == DAEMON_PORT:
            listen["daemon_port"] = addr
        elif port == CONTROL_PORT:
            listen["control_port"] = addr

    peer = None
    for line in run(["lsof", "-nP", f"-iTCP:{DAEMON_PORT}",
                     "-sTCP:ESTABLISHED"]).splitlines():
        m = re.search(rf"\s(\S+):{DAEMON_PORT}->(\S+):\d+", line)
        if m:
            peer = m.group(2)
            break

    return {"listen": listen, "guest_peer_ip": peer}


LOCAL_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "local.env")
DEPLOY_DIR = os.path.expanduser("~/Library/Application Support/AppleBridge")


def probe_installation(read=None, exists=None, local_env=LOCAL_ENV_PATH,
                       deploy_dir=DEPLOY_DIR):
    """What the INSTALLER left behind — its contract, not its reasoning.

    The rest of this module diagnoses a running stack. Nothing checked whether
    the machine is correctly *installed*, and the two are different questions:
    `install_bridge.py --dry-run` proves the plan is right, the test suite
    proves every branch of the plan is right, and neither says anything about
    the state of this computer afterwards.

    Three ways that state goes wrong silently, all observed:

      * `local.env` gains a host address. On slirp the server must bind
        `0.0.0.0`; a specific address binds SUCCESSFULLY and then waits forever
        for a connection arriving on a different one (R1, R2).
      * `APPLEBRIDGE_EMULATOR_APP` goes stale — the recorded bundle is moved or
        renamed, which is exactly what happens after clearing a Gatekeeper
        quarantine and relocating the app (2026-07-28), and `start_stack.sh`
        then hands `open` a path that is not there.
      * the DEPLOYED copy drifts from the repo. launchd cannot read the repo
        under TCC-protected ~/Documents, so it runs a synced copy; editing the
        repo and forgetting `deploy_host.sh` means the fix you are testing is
        not the code that is running. That one cost a long detour.
    """
    read = read or _read
    exists = exists or os.path.exists

    text = read(local_env)
    present = bool(text)
    host_ip_line = None
    emulator_app = None
    for line in text.splitlines():
        bare = line.strip()
        if bare.startswith("#") or "=" not in bare:
            continue
        key, _, value = bare.partition("=")
        key = key.replace("export ", "").strip()
        if key == "APPLEBRIDGE_HOST_IP":
            host_ip_line = value.strip()
        elif key == "APPLEBRIDGE_EMULATOR_APP":
            emulator_app = value.strip()

    return {
        "local_env": local_env,
        "local_env_present": present,
        "host_ip_assigned": host_ip_line,          # None = correct on slirp
        "emulator_app": emulator_app,
        "emulator_app_exists": bool(emulator_app) and exists(emulator_app),
        "deploy_dir": deploy_dir,
        "deploy_present": exists(os.path.join(deploy_dir, "host_server.py")),
        "deploy_stamp": (read(os.path.join(deploy_dir, ".deploy_stamp")) or "").strip(),
    }


GUEST_PREFS_HFS = ":System Folder:Preferences:AppleBridge Prefs"


def probe_guest_ip(run, disks, emulator_running, exists=None, read=None):
    """The address the GUEST is configured to dial, read out of its disk image.

    Closes a loop nothing else could. The daemon dials the host, so a stale
    `IP=` presents as a daemon that hangs on CONNECTING with no explanation —
    and on a LAN where the stale address answers, it presents as something
    worse: a bridge that connects to somebody else's machine and reports full
    health (R2). Until now the only way to see that value was to boot the guest
    and look, which is precisely what a broken bridge prevents.

    hfsutils reads a POWERED-OFF image directly, so the check costs nothing and
    needs no guest. It is skipped while an emulator runs: mounting a live image
    risks the HFS volume, and the running daemon holds its own copy of the prefs
    anyway (R14).

    -> {"ip": <str|None>, "image": <path|None>, "checked": bool, "why": <str>}
    """
    exists = exists or os.path.exists
    if emulator_running:
        return {"ip": None, "image": None, "checked": False,
                "why": "an emulator is running; a live image is not read"}
    for image in disks:
        if not exists(image):
            continue
        out = run(["hmount", image])
        if "Volume" not in out:
            continue
        try:
            # hcopy, not hcat: hcat returns nothing on this hfsutils build, and
            # hcopy -r is what seed_guest_prefs already uses to read the same
            # file. -r keeps the bytes raw — the guest's prefs are LF-terminated
            # even though prefs.c writes "\r", because MPW C maps '\r' to 0x0A,
            # and a helpful conversion would corrupt the bridge's own config
            # (R20).
            tmp = os.path.join(tempfile.gettempdir(), "ab_guest_prefs.probe")
            run(["hcopy", "-r", GUEST_PREFS_HFS, tmp])
            text = (read or _read)(tmp)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            if not text:
                return {"ip": None, "image": image, "checked": True,
                        "why": "the guest's AppleBridge Prefs is empty or absent"}
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("IP="):
                    return {"ip": line[3:].strip(), "image": image,
                            "checked": True, "why": ""}
            return {"ip": None, "image": image, "checked": True,
                    "why": "no IP= line in the guest's AppleBridge Prefs"}
        finally:
            run(["humount"])
    return {"ip": None, "image": None, "checked": False,
            "why": "no readable disk image in the emulator prefs"}


def probe_network(run, host_ip):
    """Where the host IP lives vs. where the default route exits.

    THE RULE (TROUBLESHOOTING.md): .154 must sit on the default-route interface,
    because that is where the guest's MACNAT traffic exits. On a second NIC the
    conversation splits across interfaces and the daemon's connect never
    completes — which presents as a frozen emulator, not as a network error.
    """
    ifaces, current = {}, None
    for line in run(["ifconfig"]).splitlines():
        m = re.match(r"^(\w+):\s+flags=", line)
        if m:
            current = m.group(1)
            ifaces[current] = []
            continue
        m = re.match(r"^\s+inet (\d+\.\d+\.\d+\.\d+)", line)
        if m and current:
            ifaces[current].append(m.group(1))

    default_if = None
    for line in run(["route", "-n", "get", "default"]).splitlines():
        m = re.search(r"interface:\s*(\S+)", line)
        if m:
            default_if = m.group(1)
            break

    return {
        "host_ip": host_ip,
        "host_ip_interfaces": sorted(i for i, a in ifaces.items() if host_ip in a),
        "default_route_interface": default_if,
        "interfaces": ifaces,
    }


def probe_processes(run):
    """Emulator + its Ethernet helper child.

    etherhelpertool matters on its own: it owns the wired NIC directly, and if
    the adapter is unplugged it dies while BasiliskII keeps running — leaving a
    guest with no network card at all and a daemon that retries forever.
    """
    def _first(pattern):
        out = run(["pgrep", "-fl", pattern]).strip()
        if not out:
            return None
        head = out.splitlines()[0].split(None, 1)
        return {"pid": int(head[0]), "cmd": head[1] if len(head) > 1 else ""} \
            if head and head[0].isdigit() else None

    return {
        "basilisk": _first("BasiliskII"),
        "sheepshaver": _first("SheepShaver"),
        "etherhelpertool": _first("etherhelpertool"),
    }


def probe_emulator_prefs(read, prefs_path, netmode_path):
    """The emulator's Ethernet backend, and the intended one.

    `.basilisk_ii_prefs.netmode` records the backend the stack was set up for;
    start_stack.sh writes it. A prefs file that has drifted away from it (a
    benchmark run left `ether slirp` behind, say) is exactly the regression this
    field exists to catch.
    """
    ether = None
    disks = []
    shared = None
    for line in _read_lines(read, prefs_path):
        if line.startswith("ether "):
            ether = line.split(None, 1)[1].strip() if len(line.split()) > 1 else ""
        elif line.startswith("extfs "):
            # The host folder the guest sees as `Unix:`. Good for moving
            # documents and sources; NOT a channel for applications — `extfs`
            # presents a 68K app to the guest as a document, so it cannot be
            # launched from here (measured 2026-07-28). The guest kit therefore
            # ships as its own mountable disk image instead.
            e = line[len("extfs "):].strip()
            if e:
                shared = e
        elif line.startswith("disk "):
            # The guest's disk image, which the installer had been asking the
            # operator to supply by hand for --seed-guest-prefs even though it
            # sits in the prefs file this function already reads. A path with
            # spaces is normal here ("System761 weiter.dmg"), so take the whole
            # remainder rather than splitting on whitespace.
            d = line[len("disk "):].strip()
            if d:
                disks.append(d)
    intended = (read(netmode_path) or "").strip() or None
    return {"ether": ether, "intended": intended, "prefs_path": prefs_path,
            "disks": disks, "shared_folder": shared}


def _read_lines(read, path):
    return (read(path) or "").splitlines()


# --------------------------------------------------------------------------
# interpretation
# --------------------------------------------------------------------------
def interpret(probes):
    """Turn raw probe output into ranked, actionable findings.

    Order matters: the list reads top-down as "fix this first". Every finding
    that can be acted on carries a literal command in `fix`.
    """
    out = []
    ld = probes["launchd"]
    sk = probes["sockets"]
    net = probes["network"]
    proc = probes["processes"]
    emu = probes["emulator_prefs"]
    uid = probes["uid"]

    # --- host server -------------------------------------------------------
    if ld["disabled"]:
        out.append(_finding(
            ERROR, "launchd_disabled",
            f"Host server is DISABLED in launchd ({LAUNCHD_LABEL}) — it will not "
            "start, not even on demand or after a reboot. This is a deliberate "
            "shutdown, not a fault.",
            f"launchctl enable gui/{uid}/{LAUNCHD_LABEL} && launchctl bootstrap "
            f"gui/{uid} ~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist"))
    elif not ld["loaded"] and not ld.get("installed", True):
        # No plist on this machine: the server is meant to be started by hand,
        # which is the normal state of every installation but the developer's.
        # Advising a bootstrap here names a component the reader never had, so
        # only the ABSENCE of a listener is worth reporting (R13).
        if not sk["listen"]["daemon_port"] and not sk["listen"]["control_port"]:
            out.append(_finding(
                ERROR, "host_server_not_running",
                "Nothing is listening, and no launchd agent is installed on this "
                "machine — this installation starts the host server by hand.",
                "cd host && ./run_server.sh < /dev/null &   (the redirect matters: "
                "a terminal gives the interactive prompt and no control port)"))
    elif not ld["loaded"]:
        out.append(_finding(
            ERROR, "launchd_absent",
            f"Host server agent {LAUNCHD_LABEL} is installed but not loaded.",
            f"launchctl bootstrap gui/{uid} "
            f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist"))

    if ld["loaded"] and not sk["listen"]["control_port"]:
        out.append(_finding(
            WARN, "control_port_closed",
            f"launchd job is loaded but nothing listens on :{CONTROL_PORT} — the "
            "server may be crash-looping.",
            "tail /tmp/applebridge_server.log"))

    # --- the .154 alias placement rule -------------------------------------
    host_ip = net["host_ip"]
    on = net["host_ip_interfaces"]
    default_if = net["default_route_interface"]
    if host_ip == BIND_ALL:
        # A wildcard bind has no alias to place, and treating it as one produced
        # a confident ERROR saying the daemon "dials 0.0.0.0" — an address
        # nothing dials. This is the DEFAULT state of a slirp install, where the
        # installer deliberately writes no address (R7), so the wrong reading was
        # about to become the common one. What the operator still needs is the
        # menu: which of this machine's addresses the guest should be told to use.
        dialable = ", ".join(
            f"{a} ({i})" for i, addrs in sorted(net["interfaces"].items())
            for a in addrs if not a.startswith("127.")) or "none found"
        out.append(_finding(
            INFO, "host_ip_wildcard",
            "Server binds every address (no APPLEBRIDGE_HOST_IP configured), so "
            "there is no alias to place and no interface rule to break. The "
            f"guest's `IP=` must name one of: {dialable}."))
    elif not on:
        out.append(_finding(
            ERROR, "host_ip_missing",
            f"{host_ip} is not aliased on any interface — the daemon dials that "
            "address and will never reach the host.",
            "host/start_stack.sh   (sets the alias on the default-route interface)"))
    elif len(on) > 1:
        strays = [i for i in on if i != default_if] or on[1:]
        out.append(_finding(
            ERROR, "host_ip_duplicate",
            f"{host_ip} is aliased on MORE THAN ONE interface ({', '.join(on)}). "
            "The MACNAT return path then splits across interfaces: the daemon's "
            "connect never completes and the emulator freezes at 100 % CPU.",
            " ".join(f"sudo ifconfig {i} -alias {host_ip};" for i in strays)))
    elif default_if and on[0] != default_if:
        out.append(_finding(
            ERROR, "host_ip_wrong_interface",
            f"{host_ip} sits on {on[0]}, but the default route exits via "
            f"{default_if}. The guest's NAT'd traffic cannot complete its "
            "handshake — this is the classic emulator freeze.",
            f"sudo ifconfig {on[0]} -alias {host_ip}; sudo ifconfig {default_if} "
            f"inet {host_ip} netmask 255.255.255.0 alias"))

    # --- emulator transport backend ----------------------------------------
    ether, intended = emu["ether"], emu["intended"]
    emulator_running = bool(proc["basilisk"] or proc["sheepshaver"])
    if ether == "slirp" and intended == "slirp":
        # A CONFIGURED slirp host is a supported installation, not a fault
        # (D-018) — warning about it here would contradict the branch the
        # installer deliberately sets up. What still deserves saying is the cost,
        # because it is invisible: TCP keeps working, so the gap shows up as an
        # empty Chooser rather than as a network error.
        out.append(_finding(
            INFO, "ether_slirp_by_design",
            "Emulator is on the slirp backend, as configured. TCP is unaffected; "
            "AppleTalk is not carried at all — no Chooser, no AFP mounts, no "
            "mac_appletalk_browse. That is the branch's stated cost, not a fault."))
    elif ether == "slirp":
        out.append(_finding(
            WARN, "ether_slirp",
            "Emulator is on the slirp backend, which is NOT what this stack "
            "recorded: the guest gets a 10.0.2.x address behind an IP-only NAT. "
            "TCP still works — so the bridge itself looks healthy — but AppleTalk "
            "frames are dropped (Chooser finds no AppleShare server).",
            (f"set 'ether {intended}' in {emu['prefs_path']} and relaunch"
             if intended else
             "record the intended backend (host/install_bridge.py writes "
             f"{os.path.basename(NETMODE_PATH)}) or set {emu['prefs_path']} back "
             "to the backend this stack was set up for, then relaunch")))
    if ether and intended and ether != intended:
        out.append(_finding(
            WARN, "ether_drift",
            f"Emulator backend '{ether}' differs from the intended "
            f"'{intended}' recorded in .basilisk_ii_prefs.netmode.",
            f"set 'ether {intended}' in {emu['prefs_path']} and relaunch"))
    if (ether or "").startswith("etherhelper/") and emulator_running \
            and not proc["etherhelpertool"]:
        out.append(_finding(
            ERROR, "etherhelper_dead",
            "BasiliskII is running but its etherhelpertool child is gone — the "
            "guest has NO network card, so the daemon can never connect. Usually "
            "the Thunderbolt Ethernet adapter was unplugged.",
            "quit BasiliskII fully and relaunch (a guest-only reboot does NOT "
            "respawn the helper)"))

    # --- link state --------------------------------------------------------
    if not emulator_running:
        out.append(_finding(
            INFO, "emulator_down",
            "No emulator process (BasiliskII / SheepShaver) is running."))
    elif not sk["guest_peer_ip"]:
        out.append(_finding(
            INFO, "no_guest_link",
            f"Emulator is up but no guest is connected to :{DAEMON_PORT} yet — "
            "the daemon retries roughly every 30 s after a boot or a dropped "
            "link."))

    # --- the installer's contract -------------------------------------------
    # Everything above diagnoses a RUNNING stack. These ask a different
    # question: is this machine correctly INSTALLED? A plan can be right and
    # the result still wrong, and each of these fails without saying so.
    inst = probes.get("installation")
    if inst:
        if inst["host_ip_assigned"]:
            out.append(_finding(
                ERROR, "local_env_has_host_ip",
                f"local.env assigns APPLEBRIDGE_HOST_IP="
                f"{inst['host_ip_assigned']}, but this host is configured for "
                "slirp, where the guest's connection arrives from 127.0.0.1 or "
                "from a LAN address depending on which it dialled. A specific "
                "address BINDS SUCCESSFULLY and then waits forever (R1, R2).",
                f"delete that line from {inst['local_env']} and restart the "
                "server, or re-run install_bridge.py"))

        if inst["emulator_app"] and not inst["emulator_app_exists"]:
            out.append(_finding(
                WARN, "emulator_app_stale",
                f"local.env records an emulator at {inst['emulator_app']}, and "
                "nothing is there. start_stack.sh will hand `open` a path that "
                "does not exist — usually because the bundle was moved after "
                "clearing its Gatekeeper quarantine.",
                "cd host && ./install_bridge.py     # re-discovers and records it"))

        if not inst["local_env_present"]:
            out.append(_finding(
                INFO, "no_local_env",
                "No host/local.env. Defaults apply (wildcard bind, no emulator "
                "path), which is workable but means nothing was derived for "
                "this machine.",
                "cd host && ./install_bridge.py --dry-run"))

        if ld["installed"] and not inst["deploy_present"]:
            out.append(_finding(
                ERROR, "deploy_missing",
                f"The launchd agent is installed but {inst['deploy_dir']} has no "
                "host_server.py. launchd cannot read the repo under "
                "TCC-protected ~/Documents, so it runs a synced copy — and "
                "there is none.",
                "cd host && ./deploy_host.sh"))

    # --- is the guest dialling an address this host still answers on? -------
    # The daemon dials OUT, so a stale IP= presents as a daemon that hangs on
    # CONNECTING with no explanation — or, on a LAN where the stale address
    # answers, as a bridge that connects to somebody ELSE'S machine and reports
    # full health (R2). Neither is visible from the host until now: the value
    # lived only inside the guest, and reading it meant booting the guest, which
    # is exactly what a broken bridge prevents.
    gip = probes.get("guest_ip") or {}
    if gip.get("ip"):
        # net has "interfaces" (iface -> [addr]), not "addresses". The first
        # version of this read net["addresses"], which is always absent — so the
        # finding could never fire. A check that cannot fail is the very thing
        # this file exists to catch, written into the check for catching it.
        ours = {a for addrs in (net.get("interfaces") or {}).values() for a in addrs}
        if ours and gip["ip"] not in ours:
            out.append(_finding(
                ERROR, "guest_ip_stale",
                f"The guest is configured to dial {gip['ip']}, and this host "
                f"answers on {', '.join(sorted(ours))}. The daemon will wait "
                "forever — or worse, reach a different machine on this LAN that "
                "does answer there, and report full health.",
                f"cd host && ./install_bridge.py --seed-guest-prefs "
                f"'{gip['image']}'   # rewrites IP= with the emulator powered off"))

    return out


def verdict(findings):
    """Worst finding level present ('ok' when the list is empty)."""
    if not findings:
        return "ok"
    return max(findings, key=lambda f: _SEVERITY.get(f["level"], 0))["level"]


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------
def collect(run=None, read=None, uid=None, host_ip=DEFAULT_HOST_IP,
            prefs_path=PREFS_PATH, netmode_path=NETMODE_PATH, exists=None):
    """Run the full sweep and return {probes, findings, verdict, ok}.

    `run`/`read` are injectable so the test suite can drive every failure mode
    from canned output; production callers pass neither.
    """
    run = run or _run
    read = read or _read
    uid = os.getuid() if uid is None else uid

    probes = {
        "uid": uid,
        "launchd": probe_launchd(run, uid, exists),
        "sockets": probe_sockets(run),
        "network": probe_network(run, host_ip),
        "processes": probe_processes(run),
        "emulator_prefs": probe_emulator_prefs(read, prefs_path, netmode_path),
        "installation": probe_installation(read, exists),
    }
    probes["guest_ip"] = probe_guest_ip(
        run, probes["emulator_prefs"]["disks"],
        bool(probes["processes"]["basilisk"] or probes["processes"]["sheepshaver"]),
        exists=exists, read=read)
    findings = interpret(probes)
    v = verdict(findings)
    return {"probes": probes, "findings": findings, "verdict": v,
            "ok": v != ERROR}


def short_reason(report):
    """One line naming the most likely cause — for the 'not connected' reply.

    The control port rejects every verb while the daemon is down; that reply
    used to be a fixed paragraph blaming the Ethernet adapter, which was
    actively misleading whenever the real cause was something else. This gives
    it the finding that actually applies.
    """
    for level in (ERROR, WARN):
        for f in report["findings"]:
            if f["level"] == level:
                fix = f.get("fix")
                return f["message"] + (f" Fix: {fix}" if fix else "")
    procs = report["probes"]["processes"]
    if not (procs["basilisk"] or procs["sheepshaver"]):
        return ("No emulator is running — start BasiliskII (host/start_stack.sh).")
    return ("Host side looks healthy; the guest daemon has not dialled in (yet). "
            "It retries roughly every 30 s — a modal dialog or an open menu in "
            "the guest also stalls it until dismissed.")


def format_text(report):
    """Human-readable report for the CLI / MCP text output."""
    p, lines = report["probes"], []
    ld, sk, net = p["launchd"], p["sockets"], p["network"]
    proc, emu = p["processes"], p["emulator_prefs"]

    state = "disabled" if ld["disabled"] else ("loaded" if ld["loaded"] else "absent")
    pid = f" (pid {ld['pid']})" if ld["pid"] else ""
    lines.append(f"launchd job:      {state}{pid}")
    lines.append(f":{DAEMON_PORT} / :{CONTROL_PORT}:    "
                 f"{sk['listen']['daemon_port'] or '—'} / "
                 f"{sk['listen']['control_port'] or '—'}")
    ifaces = ", ".join(net["host_ip_interfaces"]) or "—"
    lines.append(f"{net['host_ip']} alias:  {ifaces}"
                 + ("   [duplicate!]" if len(net["host_ip_interfaces"]) > 1 else ""))
    lines.append(f"default route:    {net['default_route_interface'] or '—'}")
    for name, key in (("BasiliskII", "basilisk"), ("SheepShaver", "sheepshaver"),
                      ("etherhelpertool", "etherhelpertool")):
        entry = proc[key]
        if entry or key != "sheepshaver":       # SheepShaver only when present
            lines.append(f"{name + ':':17} {('pid ' + str(entry['pid'])) if entry else '—'}")
    lines.append(f"emulator ether:   {emu['ether'] or '—'}"
                 + (f"   (intended: {emu['intended']})" if emu["intended"] else ""))
    lines.append(f"guest peer IP:    {sk['guest_peer_ip'] or '—'}")
    # The installer's contract, PRINTED and not merely checked. A silent check
    # is one nobody trusts and everybody re-does by hand; showing the three
    # values it examined is what makes "verdict: info" mean something.
    inst = report["probes"].get("installation")
    if inst:
        env = "—"
        if inst["local_env_present"]:
            env = ("host address SET (wrong on slirp)" if inst["host_ip_assigned"]
                   else "no host address (correct)")
        lines.append(f"local.env:        {env}")
        app = inst["emulator_app"] or "—"
        if inst["emulator_app"] and not inst["emulator_app_exists"]:
            app += "   (MISSING)"
        lines.append(f"emulator app:     {app}")
        lines.append(f"deployed copy:    {inst['deploy_stamp'] or '—'}")
    gip = report["probes"].get("guest_ip") or {}
    if gip.get("checked"):
        lines.append(f"guest dials:      {gip['ip'] or '— (' + gip['why'] + ')'}")
    elif gip.get("why"):
        lines.append(f"guest dials:      not read ({gip['why']})")
    lines.append("")
    lines.append(f"verdict: {report['verdict']}")
    for f in report["findings"]:
        lines.append(f"  [{f['level']}] {f['message']}")
        if f.get("fix"):
            lines.append(f"          fix: {f['fix']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    rep = collect()
    if "--json" in sys.argv:
        print(json.dumps(rep, indent=2))
    else:
        print(format_text(rep))
    sys.exit(0 if rep["ok"] else 1)
