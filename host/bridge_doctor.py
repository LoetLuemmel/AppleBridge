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
import re
import subprocess

# The host identity the 68K daemon dials — resolved the same way host_server.py
# resolves it, so the diagnosis and the server can never disagree about which
# address was meant. The literal that used to sit here was a "duplicated as a
# default" copy, which is precisely how the two drift (R1).
try:
    import host_config
    DEFAULT_HOST_IP = host_config.resolve_host_ip()[0]
except ImportError:            # deployed copy predating the module
    DEFAULT_HOST_IP = os.environ.get("APPLEBRIDGE_HOST_IP", "0.0.0.0")
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
def probe_launchd(run, uid):
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

    return {"label": LAUNCHD_LABEL, "loaded": loaded, "pid": pid,
            "disabled": disabled}


def probe_sockets(run, host_ip):
    """Listening sockets for :9000/:9001 plus the daemon's established peer.

    The peer address is the one authoritative answer to "what IP does the guest
    actually have" — it is observed, not configured, so it also catches a guest
    that silently fell back to DHCP.
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
        m = re.search(rf"{re.escape(host_ip)}:{DAEMON_PORT}->(\S+):\d+", line)
        if m:
            peer = m.group(1)
            break

    return {"listen": listen, "guest_peer_ip": peer}


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
    for line in _read_lines(read, prefs_path):
        if line.startswith("ether "):
            ether = line.split(None, 1)[1].strip() if len(line.split()) > 1 else ""
            break
    intended = (read(netmode_path) or "").strip() or None
    return {"ether": ether, "intended": intended, "prefs_path": prefs_path}


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
    elif not ld["loaded"]:
        out.append(_finding(
            ERROR, "launchd_absent",
            f"Host server agent {LAUNCHD_LABEL} is not loaded in launchd.",
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
    if not on:
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
    if ether == "slirp":
        out.append(_finding(
            WARN, "ether_slirp",
            "Emulator is on the slirp backend: the guest gets a 10.0.2.x address "
            "behind an IP-only NAT. TCP still works — so the bridge itself looks "
            "healthy — but AppleTalk frames are dropped (Chooser finds no "
            "AppleShare server) and bulk throughput is ~80 % down.",
            f"set 'ether etherhelper/en8' in {emu['prefs_path']}, then relaunch "
            "BasiliskII (host/start_stack.sh)"))
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
            prefs_path=PREFS_PATH, netmode_path=NETMODE_PATH):
    """Run the full sweep and return {probes, findings, verdict, ok}.

    `run`/`read` are injectable so the test suite can drive every failure mode
    from canned output; production callers pass neither.
    """
    run = run or _run
    read = read or _read
    uid = os.getuid() if uid is None else uid

    probes = {
        "uid": uid,
        "launchd": probe_launchd(run, uid),
        "sockets": probe_sockets(run, host_ip),
        "network": probe_network(run, host_ip),
        "processes": probe_processes(run),
        "emulator_prefs": probe_emulator_prefs(read, prefs_path, netmode_path),
    }
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
