#!/bin/bash
#
# start_stack.sh — bring up the full AppleBridge stack, keeping Wi-Fi (en0) up for
# the host's own internet / the active Claude session.
#
# Why this exists
# ---------------
# It serves BOTH backends, and they need opposite things of it. On `slirp` there
# is no privileged step at all — no alias, no bridge, no password — which is the
# branch host/install_bridge.py configures (D-018). Everything below about
# interfaces belongs to the `etherhelper` branch, which is set up by hand.
#
# On that branch this host is dual-homed: en0 (Wi-Fi) and en8 (wired Thunderbolt).
# Two interfaces are a PRECONDITION, not a detail — see D-015 and the note at the
# foot of this file.
# The emulated Mac (.244) sits BEHIND Basilisk's MACNAT (prefs: "ether etherhelper/en8"),
# so it can only connect OUT, and its outbound traffic is NAT'd through the host's
# DEFAULT-ROUTE interface — normally Wi-Fi (en0, .213). The Mac daemon dials the
# address configured in host/local.env (APPLEBRIDGE_HOST_IP); unset means 0.0.0.0.
#
# THE RULE (learned the hard way, 2026-06-21):
#   that address must live on the SAME interface the NAT exits — i.e. the default-route
#   interface (en0). Then the daemon's connection is a clean same-interface path and
#   the MACNAT return packet gets back to the guest.
#
#   If it is instead aliased on the WIRED en8, the conversation is split across
#   interfaces: the reply from .154 (en8) to the NAT source (.213, en0) is swallowed
#   by the host's own stack, the handshake never completes, and the daemon's
#   synchronous OTConnect blocks → starves System 7's cooperative scheduler → the
#   whole emulator freezes at 100% CPU (looks like a crash; it isn't).
#
# THE BRIDGE IS REQUIRED (corrected 2026-07-27). The etherhelper backend needs
# bridge100 with the wired NIC as a member, and it must exist BEFORE the emulator
# starts — as Emaculation documents and as the operator's own launcher does. This
# script previously DESTROYED it, citing an etherhelpertool SIGSEGV ("fret == -10").
# That crash is real but comes from touching the bridge WHILE the helper owns the
# NIC; the prohibition was the wrong lesson. See D-016 and R15.
#
# See: https://pit.390er.de/applebridge/anatomy-of-a-freeze-macnat-return-path/
#
# Idempotent: safe to re-run. One admin password dialog covers the privileged ops.
#
# Usage:  ./start_stack.sh
#
set -u

# local.env FIRST: it is the generated machine-specific configuration, and every
# value below may come from it. Sourcing it after the assignments (as this script
# did) meant an APPLEBRIDGE_WIRED_IF in the file was read and then ignored.
# shellcheck disable=SC1091
[ -f "$(dirname "$0")/local.env" ] && . "$(dirname "$0")/local.env"
WIRED_IF="${APPLEBRIDGE_WIRED_IF:-en8}"     # wired LAN the etherhelper bridges onto
HOST_IP="${APPLEBRIDGE_HOST_IP:-}"   # from host/local.env or the environment (R1)
EMU_IP="${APPLEBRIDGE_GUEST_IP:-}"   # the emulated Mac, if known (behind MACNAT — never routable)
NETMASK="255.255.255.0"
BRIDGE="${APPLEBRIDGE_BRIDGE:-bridge100}"   # REQUIRED by the etherhelper backend
# The emulator bundle is one machine's path unless it is configured (R1);
# install_bridge.py discovers it and writes APPLEBRIDGE_EMULATOR_APP.
BASILISK_APP="${APPLEBRIDGE_EMULATOR_APP:-/Users/pitforster/Documents/Basilisk/BasiliskII.app}"
SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"

# The interface .154 must live on = the host's default-route interface (where the
# guest's MACNAT traffic exits). Detect it instead of hardcoding; fall back to en0.
DEFAULT_IF="$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')"
DEFAULT_IF="${DEFAULT_IF:-en0}"

echo "[1/5] Emulator backend preflight…"
# The prefs' "ether" backend must match the one this stack was set up for. slirp
# in particular is deceptive: it passes TCP (so the bridge looks healthy) but
# drops AppleTalk, which surfaces as an empty Chooser, not as a network fault.
# Repairs by default; AB_KEEP_ETHER=1 reports and changes nothing.
"$SERVER_DIR/check_ether_backend.sh" || true
ETHER_BACKEND="$(awk '/^ether[ \t]/{print $2; exit}' "$HOME/.basilisk_ii_prefs" 2>/dev/null)"

# On slirp there is NO privileged network step at all: no alias to place (the
# guest dials through the host's own stack), no bridge, no helper. Asking for a
# password here would contradict the argument the whole branch rests on — that
# it is the one setup which starts without somebody at the keyboard (D-018).
# This block used to run unconditionally, raising an admin dialog to execute
# nothing but comments whenever APPLEBRIDGE_HOST_IP was unset.
NEEDS_PRIV=1
if [ "$ETHER_BACKEND" = "slirp" ]; then
    NEEDS_PRIV=0
    echo "[2/5] Network setup: nothing to do — slirp needs no privileged step."
    echo "      (no interface alias, no bridge, no password. No AppleTalk either.)"
elif [ -z "$HOST_IP" ] && [ -z "$EMU_IP" ]; then
    NEEDS_PRIV=0
    echo "[2/5] Network setup: nothing to do — no configured address, no guest IP."
fi

if [ "$NEEDS_PRIV" = "1" ]; then
echo "[2/5] Privileged network setup (admin password dialog)…"
# With no configured address there is no alias to place — the server binds
# 0.0.0.0 and the guest dials whatever this machine already answers on. The
# rest of the privileged block still applies, so run it either way.
if [ -n "$HOST_IP" ]; then
    echo "      ${HOST_IP} -> ${DEFAULT_IF} (default-route iface, where MACNAT exits)"
    ALIAS_OPS="
ifconfig $WIRED_IF -alias $HOST_IP 2>/dev/null || true     # strip a stale alias off the wire
ifconfig $DEFAULT_IF inet $HOST_IP netmask $NETMASK alias   # put it where the NAT exits
"
else
    echo "      no APPLEBRIDGE_HOST_IP (host/local.env) — no alias to place, server binds 0.0.0.0"
    ALIAS_OPS=""
fi
# A stale host route to the guest only exists if someone once added one, which
# needs the guest's address. Unknown -> nothing to clean up.
if [ -n "$EMU_IP" ]; then
    ROUTE_OPS="route -n delete -host $EMU_IP 2>/dev/null || true   # guest is behind MACNAT; no host route"
else
    ROUTE_OPS=""
fi
# NO bridge handling here, in either direction (D-017). With `etherhelper/<if>`
# the helper owns the NIC directly and no bridge is on the path — measured both
# ways 2026-07-27. This script used to DESTROY one (removing what the operator's
# launcher deliberately creates), then briefly CREATED one (overcorrection). It
# now only reports, because the bridge belongs to the tap mode and to the
# operator's launcher, not to this script.
PRIV="
# The host address belongs on the DEFAULT-ROUTE interface, not the wired one.
$ALIAS_OPS
$ROUTE_OPS
"
osascript -e "do shell script \"$PRIV\" with administrator privileges" || {
    echo "      ERROR: privileged setup failed (cancelled or wrong password)."; exit 1
}

echo "      ${DEFAULT_IF} addrs:"; ifconfig "$DEFAULT_IF" | grep "inet " | sed 's/^/        /'
if [ -n "$HOST_IP" ] && ifconfig "$WIRED_IF" 2>/dev/null | grep -q "inet ${HOST_IP} "; then
    echo "      WARN: ${HOST_IP} is STILL on $WIRED_IF — the freeze bug will return."
fi
fi   # NEEDS_PRIV
if [ "${ETHER_BACKEND%%/*}" = "etherhelper" ]; then
    if ifconfig "$BRIDGE" 2>/dev/null | grep -q "member: $WIRED_IF"; then
        echo "      bridge: $BRIDGE present with $WIRED_IF (harmless; not on the path in this mode)"
    else
        echo "      bridge: none on $WIRED_IF — expected with etherhelper/<if> (D-017)"
    fi
    echo "      NOTE: this path needs TWO password prompts per launch (bridge +"
    echo "            BasiliskII elevating its built-in etherhelper), so it cannot"
    echo "            start unattended. A slirp setup needs none."
fi

echo "[3/5] (Re)starting host server…"
LABEL="de.390er.applebridge-host"
if [ -f "$HOME/Library/LaunchAgents/$LABEL.plist" ]; then
    # Preferred path: the launchd agent owns the server. deploy_host.sh syncs the
    # repo runtime to the deployed copy (repo is TCC-protected, launchd can't read
    # it) and kickstarts the agent — so the ONE running server is always the fresh
    # deployed copy. No pkill/nohup here, or it would race the agent's KeepAlive.
    "$SERVER_DIR/deploy_host.sh"
    sleep 2
else
    # No agent installed — run straight from the repo (system python, firewall).
    echo "      (LaunchAgent not installed; running repo copy directly — see install_host_service.sh)"
    pkill -f host_server.py 2>/dev/null && sleep 1
    ( cd "$SERVER_DIR" && nohup ./run_server.sh > /tmp/applebridge_server.log 2>&1 & )
    sleep 2
fi

# With no configured address the server binds 0.0.0.0, so match that instead of
# an address this script does not know — but match it the way lsof PRINTS it.
# A wildcard bind shows as `*:9000`, never as `0.0.0.0:9000`, so grepping for
# the latter reported "not listening" about a server whose own log two lines
# above said it was listening (observed 2026-07-27, the first slirp launch).
EXPECT_BIND="${HOST_IP:-0.0.0.0}"
if [ "$EXPECT_BIND" = "0.0.0.0" ]; then
    LSOF_PATTERN='\*:9000'
else
    LSOF_PATTERN="${EXPECT_BIND}:9000"
fi
echo "[4/5] Verifying host server is listening on ${EXPECT_BIND}:9000…"
if lsof -nP -iTCP:9000 -sTCP:LISTEN 2>/dev/null | grep -q "${LSOF_PATTERN}"; then
    echo "      OK — bound to ${EXPECT_BIND}:9000 (+ control on 127.0.0.1:9001)"
else
    echo "      WARN: not listening. Last log lines:"; tail -n 6 /tmp/applebridge_server.log | sed 's/^/        /'
fi

echo "[5/5] Launching Basilisk II…"
open -a "$BASILISK_APP"

echo
echo "  Host-side stack is up. Now, INSIDE the emulator:"
echo "    1. launch  :bin:AppleBridge   (daemon dials the IP= in its prefs:9000)"
echo "    2. start   ToolServer ('MPSX')  — only ToolServer returns command output"
echo
echo "  Then smoke-test from the host:"
echo "    cd $SERVER_DIR && /usr/bin/python3 send_command.py 'Echo HELLO'    # expect STATUS:0"
echo
echo "  If the daemon hangs on CONNECTING at 100% CPU, the host address is on the"
echo "  wrong interface — it must be on ${DEFAULT_IF} (the default route). On a"
echo "  machine with only ONE interface, etherhelper cannot reach the host at all"
echo "  and the backend must be slirp (D-015)."
