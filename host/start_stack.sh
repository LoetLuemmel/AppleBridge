#!/bin/bash
#
# start_stack.sh — bring up the full AppleBridge stack, keeping Wi-Fi (en0) up for
# the host's own internet / the active Claude session.
#
# Why this exists
# ---------------
# The host is dual-homed on 192.168.3.0/24: en0 (Wi-Fi) and en8 (wired Thunderbolt).
# The emulated Mac (.244) sits BEHIND Basilisk's MACNAT (prefs: "ether etherhelper/en8"),
# so it can only connect OUT, and its outbound traffic is NAT'd through the host's
# DEFAULT-ROUTE interface — normally Wi-Fi (en0, .213). The Mac daemon dials the
# hardcoded host address .154.
#
# THE RULE (learned the hard way, 2026-06-21):
#   .154 must live on the SAME interface the NAT exits — i.e. the default-route
#   interface (en0). Then the daemon's connection is a clean same-interface path and
#   the MACNAT return packet gets back to the guest.
#
#   If .154 is instead aliased on the WIRED en8, the conversation is split across
#   interfaces: the reply from .154 (en8) to the NAT source (.213, en0) is swallowed
#   by the host's own stack, the handshake never completes, and the daemon's
#   synchronous OTConnect blocks → starves System 7's cooperative scheduler → the
#   whole emulator freezes at 100% CPU (looks like a crash; it isn't).
#
# DO NOT create a bridge. Basilisk's etherhelpertool owns en8 directly; pre-creating
# bridge100 + "addm en8" collides with it and SIGSEGVs the helper ("etherhelpertool:
# fret == -10") before any screen appears. This script only TEARS DOWN any stale
# bridge a previous (wrong) run may have left, so the etherhelper can own en8 cleanly.
#
# See: https://pit.390er.de/applebridge/anatomy-of-a-freeze-macnat-return-path/
#
# Idempotent: safe to re-run. One admin password dialog covers the privileged ops.
#
# Usage:  ./start_stack.sh
#
set -u

WIRED_IF="en8"                 # Thunderbolt, wired LAN — owned by Basilisk's etherhelper
HOST_IP="192.168.3.154"        # host identity the Mac daemon dials (hardcoded)
EMU_IP="192.168.3.244"         # the emulated Mac (behind MACNAT — not directly routable)
NETMASK="255.255.255.0"
STALE_BRIDGE="bridge100"       # only ever torn down here, never created
BASILISK_APP="/Users/pitforster/Documents/Basilisk/BasiliskII.app"
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

echo "[2/5] Privileged network setup (admin password dialog)…"
echo "      .154 -> ${DEFAULT_IF} (default-route iface, where MACNAT exits)"
PRIV="
# .154 belongs on the DEFAULT-ROUTE interface, not the wired one.
ifconfig $WIRED_IF -alias $HOST_IP 2>/dev/null || true     # strip any stale .154 off the wire
ifconfig $DEFAULT_IF inet $HOST_IP netmask $NETMASK alias   # put it where the NAT exits
# Clean up stale state from older (wrong) runs so the etherhelper can own en8:
if ifconfig $STALE_BRIDGE >/dev/null 2>&1; then ifconfig $STALE_BRIDGE destroy; fi
route -n delete -host $EMU_IP 2>/dev/null || true           # .244 is behind MACNAT; no host route
"
osascript -e "do shell script \"$PRIV\" with administrator privileges" || {
    echo "      ERROR: privileged setup failed (cancelled or wrong password)."; exit 1
}

echo "      ${DEFAULT_IF} addrs:"; ifconfig "$DEFAULT_IF" | grep "inet " | sed 's/^/        /'
if ifconfig "$WIRED_IF" 2>/dev/null | grep -q "inet ${HOST_IP} "; then
    echo "      WARN: .154 is STILL on $WIRED_IF — the freeze bug will return."
fi
if ifconfig "$STALE_BRIDGE" >/dev/null 2>&1; then
    echo "      WARN: $STALE_BRIDGE still present — etherhelper may SIGSEGV (fret == -10)."
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

echo "[4/5] Verifying host server is listening on $HOST_IP:9000…"
if lsof -nP -iTCP:9000 -sTCP:LISTEN 2>/dev/null | grep -q "${HOST_IP}:9000"; then
    echo "      OK — bound to ${HOST_IP}:9000 (+ control on 127.0.0.1:9001)"
else
    echo "      WARN: not listening. Last log lines:"; tail -n 6 /tmp/applebridge_server.log | sed 's/^/        /'
fi

echo "[5/5] Launching Basilisk II…"
open -a "$BASILISK_APP"

echo
echo "  Host-side stack is up. Now, INSIDE the emulator:"
echo "    1. launch  :bin:AppleBridge   (daemon dials ${HOST_IP}:9000)"
echo "    2. start   ToolServer ('MPSX')  — only ToolServer returns command output"
echo
echo "  Then smoke-test from the host:"
echo "    cd $SERVER_DIR && /usr/bin/python3 send_command.py 'Echo HELLO'    # expect STATUS:0"
echo
echo "  If the daemon hangs on CONNECTING at 100% CPU, .154 is on the wrong"
echo "  interface — it must be on ${DEFAULT_IF} (the default route)."
