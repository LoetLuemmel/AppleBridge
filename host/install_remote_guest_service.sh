#!/bin/bash
#
# install_remote_guest_service.sh — a SECOND host server, as its own LaunchAgent, for a guest that runs on ANOTHER
# machine (2026-10-01: a second emulator host with a single Wi-Fi NIC). Such a guest cannot reach a server on its own
# host (D-015), so its daemon dials a second address on THIS machine instead; this agent listens there.
#
# Same deployed runtime as the first agent (deploy_host.sh syncs it and restarts both); what differs is the
# environment the agent carries:
#   APPLEBRIDGE_HOST_IP       = APPLEBRIDGE_REMOTE_HOST_IP from host/local.env  (where the remote daemon dials :9000)
#   APPLEBRIDGE_CTRL_PORT     = APPLEBRIDGE_REMOTE_CTRL_PORT (default 9011)     (the first instance keeps 9001)
#   APPLEBRIDGE_LOG           = /tmp/applebridge_server_remote.log
#   APPLEBRIDGE_REMOTE_GUEST  = 1   — the HOST* verbs act on THIS Mac's screen and mouse, i.e. on the LOCAL guest;
#                                     this instance refuses them rather than drive the wrong machine.
# The alias for APPLEBRIDGE_REMOTE_HOST_IP is placed by start_stack.sh in the same privileged step as the first one.
# Until it exists the agent retries every 30 s (ThrottleInterval), exactly like the first.
#
# Usage:  ./install_remote_guest_service.sh            install / refresh
#         ./install_remote_guest_service.sh --remove   unload and delete the agent
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/AppleBridge"
LABEL="de.390er.applebridge-host-remote"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "${1:-}" = "--remove" ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"; echo "removed $LABEL"; exit 0
fi

# shellcheck disable=SC1091
[ -f "$SRC/local.env" ] && . "$SRC/local.env"
REMOTE_IP="${APPLEBRIDGE_REMOTE_HOST_IP:-}"
CTRL_PORT="${APPLEBRIDGE_REMOTE_CTRL_PORT:-9011}"
if [ -z "$REMOTE_IP" ]; then
    echo "APPLEBRIDGE_REMOTE_HOST_IP is not set in host/local.env — the address the remote guest's daemon dials." >&2
    exit 2
fi
if [ "$CTRL_PORT" = "9001" ]; then
    echo "APPLEBRIDGE_REMOTE_CTRL_PORT must differ from the first instance's 9001." >&2; exit 2
fi

echo "[1/2] Writing LaunchAgent -> $PLIST  (daemon ${REMOTE_IP}:9000, control 127.0.0.1:${CTRL_PORT})"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$DEST/run_server.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DEST</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>APPLEBRIDGE_HOST_IP</key>
        <string>$REMOTE_IP</string>
        <key>APPLEBRIDGE_CTRL_PORT</key>
        <string>$CTRL_PORT</string>
        <key>APPLEBRIDGE_LOG</key>
        <string>/tmp/applebridge_server_remote.log</string>
        <key>APPLEBRIDGE_REMOTE_GUEST</key>
        <string>1</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>/tmp/applebridge_host_remote_launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/applebridge_host_remote_launchd.log</string>
</dict>
</plist>
PLIST_EOF
plutil -lint "$PLIST"

echo "[2/2] Syncing the runtime (deploy_host.sh --no-restart) and starting $LABEL"
"$SRC/deploy_host.sh" --no-restart
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 2
launchctl list | grep "$LABEL" || true
