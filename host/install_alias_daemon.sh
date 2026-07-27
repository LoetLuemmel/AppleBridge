#!/bin/bash
#
# install_alias_daemon.sh — install a root LaunchDaemon that keeps the host IP
# the configured host address (host/local.env) aliased on the default-route
# interface across reboots and network
# changes. Pairs with install_host_service.sh (the user LaunchAgent for the
# server). MUST be run as root:
#
#     sudo ./install_alias_daemon.sh
#
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "This installs a root LaunchDaemon. Re-run with sudo:" >&2
    echo "    sudo $0" >&2
    exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
DESTDIR="/Library/Application Support/AppleBridge"
SCRIPT="$DESTDIR/ensure_host_alias.sh"
LABEL="de.390er.applebridge-alias"
PLIST="/Library/LaunchDaemons/$LABEL.plist"

echo "[1/3] Installing alias script -> $SCRIPT (root-owned)"
mkdir -p "$DESTDIR"
cp "$SRC/ensure_host_alias.sh" "$SCRIPT"
# root-owned and not group/other-writable: a root daemon must not run a script
# that a non-root user could modify.
chown root:wheel "$SCRIPT"
chmod 755 "$SCRIPT"

echo "[2/3] Writing LaunchDaemon -> $PLIST"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$SCRIPT</string>
    </array>
    <!-- Run at boot/load, then re-check every 60 s so a reboot, a Wi-Fi change,
         or a dock event re-establishes the alias on the (current) default route. -->
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>/tmp/applebridge_alias.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/applebridge_alias.log</string>
</dict>
</plist>
PLIST_EOF
chown root:wheel "$PLIST"
chmod 644 "$PLIST"
plutil -lint "$PLIST"

echo "[3/3] Loading the daemon"
launchctl bootout system "$PLIST" 2>/dev/null || true
launchctl bootstrap system "$PLIST"
sleep 2
launchctl print "system/$LABEL" >/dev/null 2>&1 && echo "  loaded: $LABEL"

echo
echo "Done. .154 will be (re)aliased on the default-route NIC at boot and every 60s."
echo "  - Verify:    ifconfig \$(route -n get default | awk '/interface:/{print \$2}') | grep \"\$APPLEBRIDGE_HOST_IP\""
echo "  - Uninstall: sudo launchctl bootout system $PLIST && sudo rm $PLIST"
echo "  - Log:       /tmp/applebridge_alias.log (and: log show --predicate 'process == \"logger\"' | grep applebridge-alias)"
