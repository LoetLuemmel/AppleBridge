#!/bin/bash
#
# install_dialog_watch.sh — the dialog watchdog (dialog_watch.py) as LaunchAgents, one per guest (2026-10-09).
#
#   de.390er.applebridge-dialogwatch          the guest emulated on THIS Mac   (control port 9001)
#   de.390er.applebridge-dialogwatch-remote   the guest on another Mac         (APPLEBRIDGE_REMOTE_CTRL_PORT, over SSH to
#                                             APPLEBRIDGE_REMOTE_GUEST_SSH)
#
# Same layout as install_hang_watch.sh and for the same reason: launchd cannot read ~/Documents (TCC), so the watcher runs
# from its own copy under ~/Library/Application Support/AppleBridge/dialogwatch, with host/refs/ and local.env (mode 600).
# Loading an agent needs no restart of the Mac or of an emulator: launchctl bootstrap starts it in the running session.
#
# Usage:  ./install_dialog_watch.sh            install / refresh (re-run after changing any of the files below)
#         ./install_dialog_watch.sh --remove   unload and delete both agents and the copy
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/AppleBridge/dialogwatch"
LABEL="de.390er.applebridge-dialogwatch"
FILES=(dialog_watch.py guest_boot.py fb_export.py guest_remote.py screenshot_decode.py)

if [ "${1:-}" = "--remove" ]; then
    for l in "$LABEL" "$LABEL-remote"; do
        launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true; rm -f "$HOME/Library/LaunchAgents/$l.plist"
    done
    rm -rf "$DEST"; echo "removed the dialog watchdog"; exit 0
fi

# shellcheck disable=SC1091
[ -f "$SRC/local.env" ] && . "$SRC/local.env"

echo "[1/3] Copying the watchdog -> $DEST"
mkdir -p "$DEST/refs"
for f in "${FILES[@]}"; do cp "$SRC/$f" "$DEST/$f"; done
[ -d "$SRC/refs" ] && cp "$SRC"/refs/*.json "$DEST/refs/" 2>/dev/null || true
if [ -f "$SRC/local.env" ]; then cp "$SRC/local.env" "$DEST/local.env"; chmod 600 "$DEST/local.env"; fi

agent() {   # label, port, [guest ssh]
    local plist="$HOME/Library/LaunchAgents/$1.plist" extra=""
    [ -n "${3:-}" ] && extra="<key>APPLEBRIDGE_GUEST_SSH</key><string>$3</string>"
    cat > "$plist" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$1</string>
    <key>ProgramArguments</key>
    <array><string>/usr/bin/python3</string><string>-u</string><string>$DEST/dialog_watch.py</string></array>
    <key>WorkingDirectory</key><string>$DEST</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>APPLEBRIDGE_CTRL_PORT</key><string>$2</string>
        $extra
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>ProcessType</key><string>Background</string>
    <key>StandardOutPath</key><string>/tmp/applebridge_dialogwatch_launchd.log</string>
    <key>StandardErrorPath</key><string>/tmp/applebridge_dialogwatch_launchd.log</string>
</dict>
</plist>
PLIST_EOF
    plutil -lint "$plist" >/dev/null
    launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    echo "      $1: port $2, log /tmp/applebridge_dialog_watch_$2.log${3:+, emulator on $3}"
}

echo "[2/3] The local guest"
agent "$LABEL" 9001

echo "[3/3] The guest on another Mac"
if [ -n "${APPLEBRIDGE_REMOTE_GUEST_SSH:-}" ]; then
    agent "$LABEL-remote" "${APPLEBRIDGE_REMOTE_CTRL_PORT:-9011}" "$APPLEBRIDGE_REMOTE_GUEST_SSH"
else
    echo "      no APPLEBRIDGE_REMOTE_GUEST_SSH in local.env - skipped"
fi
