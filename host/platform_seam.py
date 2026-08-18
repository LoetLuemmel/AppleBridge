#!/usr/bin/env python3
"""What differs between the host operating systems, in one place.

Why this exists
---------------
The bridge itself never knew what host it ran on — protocol, server, build
surface and the daemon-side screenshot all execute in the guest. What knows
is the code that *installs* and *diagnoses* it, and that code was written on
one macOS machine: `ifconfig`, `route -n get default`, `launchd`, `~/Library`,
`brew install`, and a bundle path with `Contents/MacOS` in it.

Run unchanged on Linux (measured 2026-08-18 in a container: Docker Desktop,
LinuxKit 6.10.14, `python:3.12-slim`, non-root) nothing crashed — every probe
degraded to empty, which is the injectable-runner design holding up. What it
produced instead was confident nonsense:

  * `"one usable interface (none found)"` — zero read as one, because address
    discovery shells out to `ifconfig` and a modern distro ships `ip`;
  * a plan whose third step installs a **launchd** agent, justified by the
    TCC-protected `~/Documents` that does not exist there;
  * a diagnosis whose fix line is launchd advice — R13's own prohibition,
    diagnostics describing another machine;
  * `brew install hfsutils` offered to a Debian host, where it is `apt`.

So this module answers five questions and nothing else: which system, which
addresses, which default route, how a background service is installed, and how
a missing tool is obtained. Every answer is data, so the callers stay
declarative and the tests drive all platforms from canned output on any host.

Scope note (D-024): the Ethernet **backend** is not one of the five. On Linux
the installer still targets slirp — nothing else comes up unattended — but
Linux is not a one-backend platform, so the backend stays a value the tooling
reads and can write, never a constant this module hands out.
"""

import os
import re
import subprocess
import sys

DARWIN = "darwin"
LINUX = "linux"


def system(platform_name=None):
    """-> 'darwin' | 'linux' | the raw sys.platform for anything else.

    Injectable rather than read at import: the tests exercise both platforms
    on whichever one they happen to run on, which is the only way the Linux
    branches are covered by a macOS developer machine and vice versa.
    """
    name = platform_name if platform_name is not None else sys.platform
    if name.startswith("darwin"):
        return DARWIN
    if name.startswith("linux"):
        return LINUX
    return name


def _run(argv, timeout=8.0):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------
# addresses and routing
# --------------------------------------------------------------------------
# `ifconfig` is present on macOS always and on Linux only where somebody
# installed net-tools. Asking `ip` first on Linux and falling back keeps a host
# with both working, and a host with neither answers empty rather than wrong.
def ipv4_addresses(run=None, platform_name=None):
    """-> [(interface, address)] for every non-loopback IPv4 on this machine."""
    run = run or _run
    if system(platform_name) == LINUX:
        found = _parse_ip_addr(run(["ip", "-o", "-4", "addr", "show"]))
        if found:
            return found
    return _parse_ifconfig(run(["ifconfig"]))


def _parse_ip_addr(out):
    """`ip -o -4 addr show` -> one line per address, interface in field 2."""
    found = []
    for line in (out or "").splitlines():
        m = re.match(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)", line)
        if m and not m.group(2).startswith("127."):
            found.append((m.group(1), m.group(2)))
    return found


def _parse_ifconfig(out):
    found, iface = [], None
    for line in (out or "").splitlines():
        head = re.match(r"^(\w+):", line)
        if head:
            iface = head.group(1)
            continue
        m = re.search(r"^\s+inet (\d+\.\d+\.\d+\.\d+)", line)
        if m and iface and not m.group(1).startswith("127."):
            found.append((iface, m.group(1)))
    return found


def default_route_interface(run=None, platform_name=None):
    """-> the interface the default route exits by, or None.

    BSD answers `route -n get default` with an `interface:` line; Linux answers
    `ip route show default` with `default via <gw> dev <if>`. Asking the wrong
    one returns empty, and empty here is the input to the rule about which
    interface must carry the host address — so it fails as a wrong diagnosis,
    not as a missing one.
    """
    run = run or _run

    def from_ip():
        m = re.search(r"\bdev\s+(\S+)",
                      run(["ip", "route", "show", "default"]) or "")
        return m.group(1) if m else None

    def from_route():
        for line in (run(["route", "-n", "get", "default"]) or "").splitlines():
            m = re.search(r"interface:\s*(\S+)", line)
            if m:
                return m.group(1)
        return None

    # Preferred first, then the other — same shape as the address probe, and
    # for the same reason: a host may carry both tools, and an answer from
    # either is a fact while an empty answer is the input to the rule about
    # which interface must hold the host address.
    order = (from_ip, from_route) if system(platform_name) == LINUX \
        else (from_route, from_ip)
    for source in order:
        found = source()
        if found:
            return found
    return None


# --------------------------------------------------------------------------
# background service
# --------------------------------------------------------------------------
LAUNCHD_LABEL = "de.390er.applebridge-host"
SYSTEMD_UNIT = "applebridge-host.service"


def service(platform_name=None, home=None):
    """-> how a background service is installed on this host, as data.

    `supported` is the field that matters: where it is False the installer must
    not plan the step, and the diagnosis must not offer its fix line. Saying
    "not implemented here" is a true sentence about the machine in front of
    you; proposing launchd on Linux is not.
    """
    home = home if home is not None else os.path.expanduser("~")
    sysname = system(platform_name)
    if sysname == DARWIN:
        return {
            "kind": "launchd",
            "supported": True,
            "label": LAUNCHD_LABEL,
            "unit_path": os.path.join(home, "Library", "LaunchAgents",
                                      LAUNCHD_LABEL + ".plist"),
            "log_dir": os.path.join(home, "Library", "Logs", "AppleBridge"),
            "deployed_dir": os.path.join(home, "Library", "Application Support",
                                         "AppleBridge"),
            # The deployed copy exists because the repo lives under
            # TCC-protected ~/Documents, which launchd cannot read without Full
            # Disk Access. That reason is macOS-only, and so is the copy.
            "needs_deployed_copy": True,
            "list_cmd": ["launchctl", "list"],
        }
    if sysname == LINUX:
        return {
            "kind": "systemd --user",
            # Deliberately False until the unit is written AND run on a real
            # Linux host. A seam that claims a service it has never started is
            # the same defect one layer up.
            "supported": False,
            "label": SYSTEMD_UNIT,
            "unit_path": os.path.join(home, ".config", "systemd", "user",
                                      SYSTEMD_UNIT),
            "log_dir": os.path.join(home, ".local", "state", "applebridge"),
            "deployed_dir": None,
            # No TCC on Linux: the server can run from the checkout.
            "needs_deployed_copy": False,
            "list_cmd": ["systemctl", "--user", "list-units", "--type=service"],
        }
    return {"kind": None, "supported": False, "label": None, "unit_path": None,
            "log_dir": None, "deployed_dir": None, "needs_deployed_copy": False,
            "list_cmd": None}


def manual_start_hint():
    """How to start the host server without any service manager at all."""
    return "cd host && ./run_server.sh < /dev/null &"


# --------------------------------------------------------------------------
# getting a missing tool
# --------------------------------------------------------------------------
# Probed, not assumed: a machine has the package manager it has, and naming one
# it does not carry is the same dead end as naming none. Order is the usual
# preference on each platform.
PACKAGE_MANAGERS = {
    DARWIN: [("brew", "brew install %s"),
             ("port", "sudo port install %s")],
    LINUX: [("apt-get", "sudo apt-get install -y %s"),
            ("dnf", "sudo dnf install -y %s"),
            ("pacman", "sudo pacman -S %s"),
            ("zypper", "sudo zypper install %s"),
            ("apk", "sudo apk add %s")],
}


def install_hint(package, which=None, platform_name=None):
    """-> the command that installs `package` here, or a platform-shaped guess.

    Returns (command, certain). `certain` is False when no manager was found
    on PATH: the caller then has a sentence to print and the reader has one to
    adapt, which beats both silence and a confident wrong instruction.
    """
    import shutil
    which = which or shutil.which
    sysname = system(platform_name)
    for tool, template in PACKAGE_MANAGERS.get(sysname, []):
        if which(tool):
            return (template % package, True)
    fallback = PACKAGE_MANAGERS.get(sysname) or []
    if fallback:
        return (fallback[0][1] % package, False)
    return ("install %s with this system's package manager" % package, False)


def package_note(package, which=None, platform_name=None):
    """One sentence naming where `package` comes from on this host."""
    cmd, certain = install_hint(package, which, platform_name)
    if system(platform_name) == DARWIN:
        origin = "not part of macOS"
    elif system(platform_name) == LINUX:
        origin = "packaged by most distributions"
    else:
        origin = "not installed"
    if certain:
        return f"{package} is {origin} — `{cmd}`"
    return (f"{package} is {origin}, and no package manager was found on PATH "
            f"— try `{cmd}` or this system's equivalent")
