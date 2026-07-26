#!/usr/bin/env python3
"""
check_atalkd_conf.py — refuse an AppleTalk seed configuration that advertises
inside the reserved startup range (65280-65534).

Why this exists (2026-07-26). A netatalk seed router had been configured as

    eth0 -router -phase 2 -net 65280 -addr 65280.79 -zone "ApfelNetz"

for years. Nodes may use 65280-65534 to talk on the local wire *before* they
know a real network number, which is why machines could still see each other —
but a router must never ADVERTISE it. RTMP therefore offered nothing a node
could acquire, so a Macintosh SE/30 never left the startup range and ZIP had no
zone to give it: the Chooser stayed empty while the network looked healthy. The
setup had worked by coincidence, on machines that never needed the router's
answer.

This is one of the SE/30 findings deliberately converted into something that
runs without the SE/30 (ledger: "Translate the hardware-only findings into
checks the emulator can run"; DECISIONS.md D-014). It is a static check on a
config file — it cannot see a router that is misconfigured somewhere else on
the wire, only one whose file we can read.

    check_atalkd_conf.py /etc/netatalk/atalkd.conf
    ssh root@odroid cat /etc/netatalk/atalkd.conf | check_atalkd_conf.py -

Exit status: 0 clean, 1 problems found, 2 usage error.
"""
import re
import sys

# AppleTalk Phase 2 reserves the top of the network-number space for nodes that
# have not yet acquired a routable address (Inside AppleTalk, DDP chapter).
STARTUP_FIRST = 65280
STARTUP_LAST = 65534

# The maximum legal network number; 0 means "unknown/this net" and is not a seed.
NET_MAX = 65534


def _nets_in_range_spec(spec):
    """A -net value is either `N` or a range `N-M`; yield the endpoints.

    Returns None if the spec is not numeric (a variable, a typo) — the caller
    reports that separately rather than guessing."""
    parts = spec.split("-")
    if len(parts) > 2:
        return None
    try:
        return [int(p) for p in parts]
    except ValueError:
        return None


def check_line(line):
    """Findings for one configuration line, as a list of strings (empty = fine)."""
    problems = []
    code = line.split("#", 1)[0].strip()
    if not code:
        return problems

    for spec in re.findall(r"-net\s+(\S+)", code):
        nets = _nets_in_range_spec(spec)
        if nets is None:
            problems.append(f"-net {spec}: not a number or numeric range")
            continue
        for n in nets:
            if STARTUP_FIRST <= n <= STARTUP_LAST:
                problems.append(
                    f"-net {spec}: network {n} is inside the reserved startup "
                    f"range {STARTUP_FIRST}-{STARTUP_LAST}; a router must never "
                    f"advertise it (nodes cannot acquire a routable address)")
            elif n > NET_MAX or n < 0:
                problems.append(f"-net {spec}: network {n} is out of range 1-{NET_MAX}")
            elif n == 0:
                problems.append("-net 0: 0 means 'this net' and cannot be seeded")

    for addr in re.findall(r"-addr\s+(\S+)", code):
        m = re.match(r"^(\d+)\.(\d+)$", addr)
        if not m:
            problems.append(f"-addr {addr}: expected <net>.<node>")
            continue
        net = int(m.group(1))
        if STARTUP_FIRST <= net <= STARTUP_LAST:
            problems.append(
                f"-addr {addr}: network {net} is inside the reserved startup "
                f"range {STARTUP_FIRST}-{STARTUP_LAST}")

    return problems


def check_text(text):
    """Findings for a whole file, as (line_number, line, [problems]) tuples."""
    out = []
    for i, line in enumerate(text.splitlines(), start=1):
        problems = check_line(line)
        if problems:
            out.append((i, line.rstrip(), problems))
    return out


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print(f"usage: {argv[0]} <atalkd.conf|->", file=sys.stderr)
        return 2

    path = argv[1]
    try:
        text = sys.stdin.read() if path == "-" else open(path).read()
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2

    findings = check_text(text)
    if not findings:
        print(f"{path}: no seed inside {STARTUP_FIRST}-{STARTUP_LAST}")
        return 0

    for lineno, line, problems in findings:
        print(f"{path}:{lineno}: {line}")
        for p in problems:
            print(f"    {p}")
    print()
    print("Fix: seed a normal network number, e.g.")
    print('    eth0 -router -phase 2 -net 3-3 -addr 3.79 -zone "ApfelNetz"')
    print("then restart netatalk ENTIRELY — restarting afpd alone fails with")
    print("-1069 and logs nothing, and atalkd is what re-seeds.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
