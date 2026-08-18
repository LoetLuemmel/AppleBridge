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

import platform_seam

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

    def safe(cmd):
        try:
            return runner(cmd) or ""
        except (OSError, subprocess.SubprocessError):
            return ""

    # `ifconfig` is macOS-always and Linux-only-if-net-tools. Measured on a
    # Linux container 2026-08-18: it returned nothing, and an EMPTY address
    # list is not a harmless gap — it is what made the installer print "one
    # usable interface (none found)" and leave the guest's `IP=` unanswerable.
    return platform_seam.ipv4_addresses(run=safe)


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


# --- the control port -------------------------------------------------------

CTRL_ENV_VAR = "APPLEBRIDGE_CTRL_BIND"
CTRL_LOOPBACK = "127.0.0.1"
_LOCAL_ONLY = {"127.0.0.1", "localhost", "::1"}


def resolve_control_bind(env=None, local_env_path=LOCAL_ENV):
    """-> (address, source) for the :9001 control port. Loopback unless told otherwise.

    The port has always bound loopback, and that was the right default when it
    was the *only* protection. It is not the only one any more: the control-port
    token (fail-closed, `APPLEBRIDGE_CTRL_TOKEN`) has been in place since PR #62.
    Loopback stays the default; a wider bind becomes a deliberate, authenticated
    choice — which lets one agent session drive bridges on several machines
    instead of one session per machine.
    """
    env = os.environ if env is None else env
    from_env = (env.get(CTRL_ENV_VAR) or "").strip()
    if from_env:
        return from_env, CTRL_ENV_VAR
    from_file = (read_local_env(local_env_path).get(CTRL_ENV_VAR) or "").strip()
    if from_file:
        return from_file, os.path.basename(local_env_path)
    return CTRL_LOOPBACK, "default (loopback)"


def control_bind_is_local(address):
    return address in _LOCAL_ONLY


# --- carrying configuration into launchd -------------------------------------

def launchd_environment(env=None, local_env_path=LOCAL_ENV):
    """-> {KEY: value} the LaunchAgent must carry, or {} when there is nothing.

    `host/local.env` sits beside the repo, but launchd runs a DEPLOYED copy from
    `~/Library/Application Support/AppleBridge` — and `deploy_host.sh` syncs only
    the runtime modules. So the file resolved fine for anything started by hand
    and was INERT for the real server: on the developer machine
    `APPLEBRIDGE_HOST_IP=192.168.3.154` had never once reached it (observed
    2026-07-27). Nothing looked wrong, because the fallback is a wildcard bind
    that accepts the guest anyway — a configuration file that reports success
    and does nothing, which is this project's recurring failure shape.

    Copying `local.env` into the deployed folder would fix it and leave two
    copies of machine configuration to drift apart. launchd's own
    `EnvironmentVariables` is where launchd configuration belongs, so the
    installer resolves the values once and writes them into the plist.

    Only NON-SECRET keys travel: `APPLEBRIDGE_CTRL_TOKEN` is deliberately absent,
    because a plist is a world-readable file and a shared secret does not belong
    in one. It keeps its `launchctl setenv` path.

    The wildcard default is not written either — it is what the server does with
    no configuration at all, and stating it in a plist only creates something
    else to keep in step.
    """
    out = {}
    ip, _src = resolve_host_ip(env=env, local_env_path=local_env_path)
    if ip and ip != BIND_ALL:
        out[ENV_VAR] = ip
    ctrl, _src = resolve_control_bind(env=env, local_env_path=local_env_path)
    if not control_bind_is_local(ctrl):
        out[CTRL_ENV_VAR] = ctrl
    return out


def launchd_environment_xml(env=None, local_env_path=LOCAL_ENV):
    """-> the plist `EnvironmentVariables` block, or '' when nothing to carry.

    Emitted by the installer into the LaunchAgent. Empty is the common and
    correct answer on the slirp branch, which configures no address at all.
    """
    values = launchd_environment(env=env, local_env_path=local_env_path)
    if not values:
        return ""
    lines = ["    <key>EnvironmentVariables</key>", "    <dict>"]
    for key in sorted(values):
        lines.append(f"        <key>{_xml(key)}</key>")
        lines.append(f"        <string>{_xml(values[key])}</string>")
    lines.append("    </dict>")
    return "\n".join(lines)


def _xml(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def check_control_exposure(address, token):
    """-> None if this combination may run, else the reason it must not.

    The one rule worth enforcing in code rather than documenting: a control port
    reachable beyond this machine is a command channel into the guest, so it may
    not be opened without a token. Refusing to start is deliberate — an
    unauthenticated open port that *works* would be discovered by no one, which
    is exactly the failure class this project keeps finding.
    """
    if control_bind_is_local(address) or token:
        return None
    return (f"refusing to start: {CTRL_ENV_VAR}={address} exposes the control port "
            f"beyond this machine, but APPLEBRIDGE_CTRL_TOKEN is not set.\n"
            f"  The control port is a command channel into the guest; open and "
            f"unauthenticated is not a combination this will run.\n"
            f"  Either set APPLEBRIDGE_CTRL_TOKEN on both sides, or leave "
            f"{CTRL_ENV_VAR} unset to bind {CTRL_LOOPBACK}.")
