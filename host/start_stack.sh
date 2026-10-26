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
# The emulated Mac (here .244) sits BEHIND Basilisk's MACNAT (prefs: "ether etherhelper/en8"),
# so it can only connect OUT, and its outbound traffic is NAT'd through the host's
# DEFAULT-ROUTE interface — normally Wi-Fi (en0, here .232). The Mac daemon dials the
# address configured in host/local.env (APPLEBRIDGE_HOST_IP); unset means 0.0.0.0.
#
# THE RULE (learned the hard way, 2026-06-21):
#   that address must be the default-route interface's OWN address — en0's, never
#   en8's. en8 belongs to etherhelper (its Manual .154 is the helper's, not ours).
#   On en0 the daemon's connection is a clean same-interface path and the MACNAT
#   return packet gets back to the guest. On en8 the conversation is split across
#   interfaces, the reply is swallowed by the host's own stack, and the daemon
#   hangs on CONNECTING (a synchronous OTConnect then froze the whole emulator at
#   100% CPU — fixed in the daemon since, the wrong address still breaks the link).
#
# NO ALIAS (D-027, 2026-10-26): the address is a real one, reserved in the router.
#   This script used to place it as an `ifconfig … alias` with sudo; aliases live in
#   memory only, so every host reboot lost the bridge. Step 2 now only CHECKS.
# NO BRIDGE (D-017, measured both ways 2026-07-27): with `etherhelper/<if>` the
#   helper owns the NIC directly. A bridge belongs to the tap mode; step 2 reports
#   one if present and never creates or destroys it.
# THE LAUNCH IS VERIFIED (2026-10-26): `open` returns 0 when LaunchServices has the
#   request, not when the app runs — see launch_emulator below.
#
# See: https://pit.390er.de/applebridge/anatomy-of-a-freeze-macnat-return-path/
#
# Idempotent: safe to re-run; an emulator that is already running is not started
# twice. The script asks for no password itself (the one exception: removing a
# stale host route to a known guest address). BasiliskII may still ask on the
# etherhelper branch when it elevates its own helper; slirp asks for nothing.
# Exit status: non-zero when a check fails, the emulator does not start, or the
# guest does not come up (guest_boot.py's verdict).
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
#
# The fallback below is the developer machine's path, and it is exactly the R1
# defect this variable exists to remove — so it is used ONLY if it is really
# there. On the 2013 MacBook (2026-07-28) the emulator was Gatekeeper-
# translocated, so the installer correctly recorded no path, and this line would
# have handed `open -a` a directory belonging to a different computer. `open`
# then fails with its own message about that foreign path, which sends you
# looking for a Basilisk install that was never supposed to be there.
BASILISK_APP="${APPLEBRIDGE_EMULATOR_APP:-}"
if [ -z "$BASILISK_APP" ] && [ -d "/Users/pitforster/Documents/Basilisk/BasiliskII.app" ]; then
    BASILISK_APP="/Users/pitforster/Documents/Basilisk/BasiliskII.app"
fi
SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"

# `open` returns 0 once LaunchServices has the request, not once the app runs. After the hub's reboot on 2026-10-26 it
# returned 0 twice and no emulator ever ran (no crash report, nothing in the unified log), while the same `open` from
# another shell started it at once. So the launch is judged by the process - which must still be alive 5 s after it
# appears, since a pid was seen to come and go - and retried. Any BasiliskII counts as running: two emulators on one
# machine fight over the same disk image and NIC.
# By process NAME: `pgrep -f MacOS/BasiliskII` also matched a watcher shell whose command line held that string.
emulator_pid() { pgrep -x BasiliskII | head -1; }
launch_emulator() {
    local attempt t pid rc
    pid="$(emulator_pid)"
    if [ -n "$pid" ]; then
        echo "      already running: $(ps -o command= -p "$pid") (pid $pid) — not starting a second one"
        return 0
    fi
    for attempt in 1 2 3; do
        open "$BASILISK_APP"; rc=$?
        for t in $(seq 1 30); do
            sleep 1
            pid="$(emulator_pid)"; [ -n "$pid" ] || continue
            sleep 5
            pid="$(emulator_pid)"; [ -n "$pid" ] || continue
            echo "      running (pid $pid, attempt $attempt)"
            return 0
        done
        echo "      attempt $attempt: open returned $rc, but no emulator process is running after 30 s"
    done
    return 1
}

# The host address must be the default-route interface's own (where the
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
# The host address is a REAL address of this machine, never an alias placed here (D-027, 2026-10-26). Aliases are
# held only in memory: every reboot dropped them, and a macOS upgrade reset the wired NIC's service on top, so the bridge
# failed after each restart until someone re-typed `ifconfig … alias` with sudo. Now the router reserves the default-route
# interface's own address and local.env names it, so this script only CHECKS - it never changes an interface.
if [ "$ETHER_BACKEND" = "slirp" ]; then
    echo "[2/5] Network check: nothing to do — slirp needs no host address on an interface."
    echo "      (no privileged step, no password. No AppleTalk either.)"
elif [ -z "$HOST_IP" ]; then
    echo "[2/5] Network check: no APPLEBRIDGE_HOST_IP — the server binds 0.0.0.0."
elif ifconfig "$WIRED_IF" 2>/dev/null | grep -q "inet ${HOST_IP} "; then
    echo "[2/5] ERROR: ${HOST_IP} is an address of ${WIRED_IF}, the NIC etherhelper takes over. This Mac cannot reach"
    echo "      its own address there, so the guest's daemon would hang on CONNECTING. Set APPLEBRIDGE_HOST_IP in"
    echo "      host/local.env to an address of ${DEFAULT_IF} (the default route), e.g.:"
    ifconfig "$DEFAULT_IF" 2>/dev/null | awk '/inet /{print "        " $2}'
    exit 1
elif ! ifconfig "$DEFAULT_IF" 2>/dev/null | grep -q "inet ${HOST_IP} "; then
    echo "[2/5] ERROR: ${HOST_IP} (APPLEBRIDGE_HOST_IP) is not an address of ${DEFAULT_IF}, the default-route interface."
    echo "      Its addresses now:"
    ifconfig "$DEFAULT_IF" 2>/dev/null | awk '/inet /{print "        " $2}'
    echo "      Fix the router's reservation for ${DEFAULT_IF} or the value in host/local.env - do not alias it here."
    exit 1
else
    echo "[2/5] Network check: ${HOST_IP} is ${DEFAULT_IF}'s own address (default route) — OK."
fi
# A stale host route to the guest only exists if someone once added one, which needs the guest's address.
if [ -n "$EMU_IP" ] && route -n get "$EMU_IP" 2>/dev/null | grep -q "flags:.*HOST"; then
    echo "      removing a stale host route to the guest ${EMU_IP} (admin password dialog)…"
    osascript -e "do shell script \"route -n delete -host $EMU_IP\" with administrator privileges" || true
fi
# NO bridge handling here, in either direction (D-017). With `etherhelper/<if>`
# the helper owns the NIC directly and no bridge is on the path — measured both
# ways 2026-07-27. This script used to DESTROY one (removing what the operator's
# launcher deliberately creates), then briefly CREATED one (overcorrection). It
# now only reports, because the bridge belongs to the tap mode and to the
# operator's launcher, not to this script.
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
# Every other guest of the table (D-028): its instance is a launchd agent this script never started and, until
# 2026-10-26, never looked at. A WARN, not a stop: those guests run on other Macs and may be off.
if [ -f "$SERVER_DIR/guests.conf" ]; then
    /usr/bin/python3 "$SERVER_DIR/guests.py" --check-listeners "$EXPECT_BIND" | sed 's/^/      /'
fi

echo "[5/5] Launching Basilisk II…"
BOOT_RC=0
if [ -n "$BASILISK_APP" ]; then
    # `open <path>`, NOT `open -a`: -a resolves by bundle id and launched the
    # OTHER of two BasiliskII copies on disk, twice, 2026-08-31.
    if ! launch_emulator; then
        echo "      ERROR: $BASILISK_APP did not start after 3 attempts. Launch it by hand"
        echo "             (open \"$BASILISK_APP\") and run guest_boot.py, or see Console.app."
        exit 1
    fi
else
    echo "      SKIPPED — no emulator bundle configured on this machine."
    echo "      Everything above is up; launch the emulator yourself, or:"
    echo "        cd $SERVER_DIR && ./install_bridge.py     # discovers + records it"
    echo "      A translocated app is never recorded (its path changes per launch):"
    echo "        xattr -dr com.apple.quarantine <BasiliskII.app>, move it, relaunch."
fi
if [ -n "$BASILISK_APP" ]; then
    # Shepherd the boot by LOOKING at the guest's screen (2026-10-01): AppleShare login -> password from local.env,
    # "AppleTalk interrupted" -> OK, IP conflict -> stop and say so; then check the volumes and tidy the desktop
    # (daemon console hidden, Finder windows closed) so screen reading sees no stray windows. Needs host/refs/*.json.
    if [ -d "$SERVER_DIR/refs" ] && ls "$SERVER_DIR"/refs/*.json >/dev/null 2>&1; then
        echo "      shepherding the boot (guest_boot.py: screen -> login / alerts -> volumes -> tidy desktop)…"
        /usr/bin/python3 "$SERVER_DIR/guest_boot.py" 420 | sed 's/^/        /'
        BOOT_RC=${PIPESTATUS[0]}   # the pipe's status is sed's: a guest that never came up exited 0 (2026-10-26)
    else
        echo "      no host/refs/ — the AppleShare login and boot alerts are left to you"
    fi
fi

echo
echo "  Host-side stack is up. Now, INSIDE the emulator:"
echo "    1. launch  :bin:AppleBridge   (daemon dials the IP= in its prefs:9000)"
# ToolServer is a TIER, not a step. install_bridge.py says so plainly — "absent
# MPW is a tier you do not have, not a failed install" — and this line used to
# contradict it two commands later, on the 2013 MacBook (2026-07-28) where there
# is no MPW at all. The same went for the smoke test: `Echo HELLO` needs
# ToolServer, so on that machine the suggested proof of a working bridge returns
# nothing and reads as a broken install.
echo "    2. start   ToolServer ('MPSX')  — OPTIONAL. Only ToolServer returns"
echo "               command output, so mpw_execute / mac_compile / mac_build"
echo "               need it. The native surface (transfer, screenshot, input,"
echo "               LISTDIR, DISKINFO) does not."
echo
echo "  Then smoke-test from the host — this one works on either tier:"
echo "    printf 'MACSTATUS\\n\\n' | nc -w 5 localhost 9001    # expect host_connected=1"
echo "    cd $SERVER_DIR && /usr/bin/python3 send_command.py 'Echo HELLO'   # ToolServer only"
echo
# Branch-specific, because the two branches fail in ways that have nothing to do
# with each other. The interface rule below is an ETHERHELPER rule: it was
# printed unconditionally, so a slirp operator whose daemon would not connect
# was sent to fix an alias their branch does not have and never places.
if [ "$ETHER_BACKEND" = "slirp" ]; then
    echo "  If the daemon does not connect: the guest's IP= must name this machine —"
    echo "  either its LAN address or 10.0.2.2, which slirp forwards to the loopback"
    echo "  the server binds (measured 2026-07-31 on two hosts and two guests; the"
    echo "  peer then shows up as 127.0.0.1). An older rule here called 10.0.2.2"
    echo "  refused: that held while the server bound one specific address, and has"
    echo "  not since it binds 0.0.0.0. The LAN address is what you need when the"
    echo "  bridge server runs on a DIFFERENT machine than the emulator."
    echo "  Never 10.0.2.15 — that is the guest itself, and it sits three lines from"
    echo "  the host's address in the installer's checklist (R5)."
    echo "  Run ./install_bridge.py --dry-run to see the value this machine needs."
else
    echo "  If the daemon hangs on CONNECTING at 100% CPU, the host address is on the"
    echo "  wrong interface — it must be on ${DEFAULT_IF} (the default route). On a"
    echo "  machine with only ONE interface, etherhelper cannot reach the host at all"
    echo "  and the backend must be slirp (D-015)."
fi

# Last, so the advice above is still printed on a failed boot - it is the advice a failed boot needs.
if [ "$BOOT_RC" -ne 0 ]; then
    echo
    echo "  FAILED: the guest did not come up (guest_boot.py exit $BOOT_RC) — see its lines under [5/5]."
    exit "$BOOT_RC"
fi
