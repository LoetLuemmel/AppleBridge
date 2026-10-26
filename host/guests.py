"""The guest table: n guests, one hub, a daemon/control port pair per guest (D-028).

`host/guests.conf` (machine-specific, untracked — see guests.conf.example) holds one line per guest:

    # name      daemon  control  ssh                           emulator (on that machine)
    dev2019     9000    9001     -                             /Users/…/BasiliskII-ab.app
    mbp2013     9010    9011     pitforster@192.168.3.158      /Users/…/BasiliskII-ab.app

Whitespace-separated; the emulator path is the rest of the line, so it may contain spaces. `ssh = -` is the guest on
the hub itself (one emulator per machine, so at most one such row). Every other row is a guest on another Mac: its
host-server instance refuses the HOST* verbs, which act on the hub's own screen and mouse.

The row with control port 9001 keeps the labels and log paths of the single-guest setup (`de.390er.applebridge-host`,
`/tmp/applebridge_server.log`), because start_stack.sh, deploy_host.sh, bridge_doctor and the notes all name those.
Every other row gets `…-<name>`.

Pure: no launchd, no network. install_guests.py does the installing.
"""

import os
import re

CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guests.conf")
LABEL_PREFIX = "de.390er.applebridge-"
PRIMARY_CTRL_PORT = 9001
# The daemon's NAME= field is 32 bytes with its terminator (mac/include/prefs.h PREFS_NAME_LEN), and the name becomes
# part of launchd labels and file names, so it is kept to characters that are safe in all three.
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,31}$")
KINDS = ("host", "hangwatch", "dialogwatch")


class GuestTableError(ValueError):
    pass


class Guest:
    def __init__(self, name, daemon_port, ctrl_port, ssh, emulator, line):
        self.name, self.daemon_port, self.ctrl_port = name, daemon_port, ctrl_port
        self.ssh = None if ssh == "-" else ssh
        self.emulator, self.line = emulator, line

    @property
    def local(self):
        return self.ssh is None

    @property
    def primary(self):
        return self.ctrl_port == PRIMARY_CTRL_PORT

    def label(self, kind):
        """launchd label of this guest's `kind` agent (host / hangwatch / dialogwatch)."""
        base = LABEL_PREFIX + kind
        return base if self.primary else f"{base}-{self.name}"

    def server_log(self):
        return "/tmp/applebridge_server.log" if self.primary else f"/tmp/applebridge_server_{self.name}.log"

    def server_env(self, base=None):
        """Environment of this guest's host_server.py instance: the hub's (`base`, host_config.launchd_environment(),
        i.e. the address and a non-local control bind — never a token) plus this row's own keys."""
        env = dict(base or {})
        env.update({"APPLEBRIDGE_DAEMON_PORT": str(self.daemon_port),
                    "APPLEBRIDGE_CTRL_PORT": str(self.ctrl_port),
                    "APPLEBRIDGE_GUEST_NAME": self.name,
                    "APPLEBRIDGE_LOG": self.server_log()})
        if not self.local:
            env["APPLEBRIDGE_REMOTE_GUEST"] = "1"
        return env

    def watch_env(self, kind):
        """Environment of this guest's hang or dialog watchdog."""
        env = {"APPLEBRIDGE_CTRL_PORT": str(self.ctrl_port)}
        if kind == "hangwatch":
            env["APPLEBRIDGE_HANG_LOG"] = f"/tmp/applebridge_hang_watch_{self.ctrl_port}.log"
        if self.ssh:
            env["APPLEBRIDGE_GUEST_SSH"] = self.ssh
            if kind == "hangwatch" and self.emulator:
                # hang_watch.py reads a remote guest's bundle from this key, the hub's own from APPLEBRIDGE_EMULATOR_APP
                env["APPLEBRIDGE_GUEST_EMULATOR_APP"] = self.emulator
        return env

    def __repr__(self):
        return f"Guest({self.name!r}, {self.daemon_port}/{self.ctrl_port}, ssh={self.ssh!r})"


def parse(text):
    """-> [Guest] from the table's text; raises GuestTableError naming the line for anything inconsistent."""
    guests, names, ports = [], {}, {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            raise GuestTableError(f"line {n}: need name, daemon port, control port, ssh (and an emulator path): {raw!r}")
        name, dport, cport, ssh = parts[:4]
        emulator = parts[4].strip() if len(parts) > 4 else ""
        if not NAME_RE.match(name):
            raise GuestTableError(f"line {n}: name {name!r} must be 1-31 of A-Z a-z 0-9 _ - (it becomes NAME= and a label)")
        try:
            d, c = int(dport), int(cport)
        except ValueError:
            raise GuestTableError(f"line {n}: ports must be numbers: {dport!r} {cport!r}")
        for p in (d, c):
            if not 1024 <= p <= 65535:
                raise GuestTableError(f"line {n}: port {p} outside 1024-65535")
        if d == c:
            raise GuestTableError(f"line {n}: daemon and control port are both {d}")
        if name in names:
            raise GuestTableError(f"line {n}: name {name!r} already used on line {names[name]}")
        for p in (d, c):
            if p in ports:
                raise GuestTableError(f"line {n}: port {p} already used on line {ports[p]}")
        if ssh != "-" and "@" not in ssh:
            raise GuestTableError(f"line {n}: ssh must be '-' (this Mac) or user@host, got {ssh!r}")
        names[name] = n
        ports[d] = ports[c] = n
        guests.append(Guest(name, d, c, ssh, emulator, n))
    local = [g for g in guests if g.local]
    if len(local) > 1:
        raise GuestTableError("more than one row with ssh '-': one emulator per machine, and the hub is one machine "
                              f"(lines {', '.join(str(g.line) for g in local)})")
    return guests


def load(path=None):
    """-> [Guest] from guests.conf, or [] if there is none (a single-guest setup without a table)."""
    path = path or CONF
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return parse(fh.read())


def by_name(guests, name):
    for g in guests:
        if g.name == name:
            return g
    raise GuestTableError(f"no guest named {name!r} in the table")


if __name__ == "__main__":
    import sys
    try:
        table = load(sys.argv[1] if len(sys.argv) > 1 else None)
    except GuestTableError as e:
        sys.exit(f"guests.conf: {e}")
    for g in table:
        print(f"{g.name:12} daemon {g.daemon_port}  control {g.ctrl_port}  "
              f"{'this Mac' if g.local else g.ssh:30} {g.label('host')}")
