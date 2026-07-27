#!/usr/bin/env python3
"""Where the host's own address comes from — configuration, never a literal.

`host_server.py` used to open with `HOST_INTERFACE = "192.168.3.154"`, and five
further files repeated it. That address exists on one machine in the world.
Installing on a second one (2026-07-27) produced `OSError: [Errno 49] Can't
assign requested address`, a message naming neither the address it wanted nor
the interfaces it looked at — see R1 in `docs/INSTALLER_REQUIREMENTS.md`.

Resolution order, most specific first:

1. `APPLEBRIDGE_HOST_IP` in the environment — what a launchd plist or a shell
   sets, and what tests drive.
2. `host/local.env`, a generated `KEY=value` file that is **not** in version
   control. This is the file an installer writes; its absence is normal.
3. `0.0.0.0` — bind every address. A fresh clone must work without editing
   source, and on a machine with one interface this is also the right answer.

Deliberately no derivation from the default route. The host address is one end
of a pair: the other is `IP=` in the guest's `AppleBridge Prefs`, which lives
inside the emulator and cannot be read from here. Guessing it would reproduce
the failure this module exists to remove — a plausible wrong value that binds
successfully and waits forever. What can be derived honestly is the *menu*:
which addresses this machine can be reached at, so the server can print them
and the person configuring the guest has something to copy.

Stdlib only, so `/usr/bin/python3` remains sufficient (D-007).
"""

import os
import re
import subprocess

ENV_VAR = "APPLEBRIDGE_HOST_IP"
BIND_ALL = "0.0.0.0"
LOCAL_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local.env")


def read_local_env(path=LOCAL_ENV):
    """-> {KEY: value} from a generated env file; {} if it is absent.

    Absence is the normal state of a fresh clone, not an error.
    """
    values = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def resolve_host_ip(env=None, local_env_path=LOCAL_ENV):
    """-> (address, source) — the address to bind, and where it came from.

    `source` is for the log line: an operator who sees the wrong address needs
    to know which of the three places to edit.
    """
    env = os.environ if env is None else env
    from_env = (env.get(ENV_VAR) or "").strip()
    if from_env:
        return from_env, ENV_VAR
    from_file = (read_local_env(local_env_path).get(ENV_VAR) or "").strip()
    if from_file:
        return from_file, os.path.basename(local_env_path)
    return BIND_ALL, "default (no %s, no %s)" % (ENV_VAR, os.path.basename(local_env_path))


def ipv4_addresses(run=None):
    """-> [(interface, address)] for every non-loopback IPv4 on this machine.

    Used to answer the two questions a failed or wildcard bind raises: which
    address should the guest dial, and — when an explicit one was configured —
    which interfaces actually carry addresses, so a missing alias is visible
    instead of merely implied.
    """
    runner = run or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True, timeout=10).stdout)
    try:
        out = runner(["ifconfig"])
    except (OSError, subprocess.SubprocessError):
        return []
    found, iface = [], None
    for line in out.splitlines():
        head = re.match(r"^(\w+):", line)
        if head:
            iface = head.group(1)
            continue
        m = re.search(r"^\s+inet (\d+\.\d+\.\d+\.\d+)", line)
        if m and iface and not m.group(1).startswith("127."):
            found.append((iface, m.group(1)))
    return found


def describe_reachability(addresses=None):
    """-> a line naming what the guest's `IP=` should be set to, or '' if unknown."""
    addrs = ipv4_addresses() if addresses is None else addresses
    if not addrs:
        return ""
    return "guest 'IP=' should be one of: " + ", ".join(
        f"{addr} ({iface})" for iface, addr in addrs)


def explain_bind_failure(wanted, addresses=None):
    """-> a multi-line explanation of why binding `wanted` failed.

    The bare `Errno 49` is accurate and useless. What the operator needs is the
    address that was asked for, where it came from, and the addresses that do
    exist — at which point a missing `ifconfig ... alias` is self-evident.
    """
    addrs = ipv4_addresses() if addresses is None else addresses
    lines = [f"cannot bind {wanted}: no interface on this machine carries that address."]
    if addrs:
        lines.append("  addresses present: "
                     + ", ".join(f"{a} ({i})" for i, a in addrs))
        lines.append(f"  fix: set {ENV_VAR} to one of them, add {wanted} as an alias")
        lines.append(f"       (sudo ifconfig <iface> inet {wanted} netmask 255.255.255.0 alias),")
        lines.append(f"       or unset {ENV_VAR} to bind {BIND_ALL}.")
    else:
        lines.append("  no non-loopback IPv4 addresses found at all — is the network up?")
    return "\n".join(lines)
