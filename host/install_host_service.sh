#!/bin/bash
#
# install_host_service.sh — run the AppleBridge host server as a launchd
# LaunchAgent, so it starts at login and restarts if it exits. No more
# "forgot to start the host server".
#
# Why a DEPLOYED copy (not run straight from this repo): the repo lives under
# ~/Documents, which is TCC-protected — launchd cannot read it without Full
# Disk Access (it fails with "Operation not permitted"). So the host runtime
# (three stdlib-only files) is copied to a non-protected location and run from
# there. Re-run this script after changing host_server.py / screenshot_decode.py.
#
# Prerequisite (unchanged): the server binds 192.168.3.154:9000, so the .154
# alias must be on the default-route interface (en0). Until it is, the agent
# just retries every 30s (ThrottleInterval). start_stack.sh sets the alias up;
# a boot-persistent alias would need a root LaunchDaemon (separate, needs sudo).
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/AppleBridge"
LABEL="de.390er.applebridge-host"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "[1/3] Deploying host runtime -> $DEST"
mkdir -p "$DEST"
cp "$SRC/host_server.py" "$SRC/screenshot_decode.py" "$SRC/run_server.sh" "$DEST"/
chmod +x "$DEST/run_server.sh"

echo "[2/3] Writing LaunchAgent -> $PLIST"
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
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>/tmp/applebridge_host_launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/applebridge_host_launchd.log</string>
</dict>
</plist>
PLIST_EOF
plutil -lint "$PLIST"

echo "[3/3] (Re)loading the agent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 2
launchctl list | grep "$LABEL" || true

echo
echo "Done. The host server now starts at login and is kept alive."
echo "  - Uninstall:  launchctl bootout gui/\$(id -u)/$LABEL && rm '$PLIST'"
echo "  - Log:        /tmp/applebridge_host_launchd.log + /tmp/applebridge_server.log"
echo "  - Reminder:   .154 must be aliased on the default-route NIC (en0)."
