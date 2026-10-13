#!/bin/bash
#
# install_hang_watch.sh — the hang watchdog (hang_watch.py) as LaunchAgents, one per guest (2026-10-04).
#
#   de.390er.applebridge-hangwatch          the guest emulated on THIS Mac            (control port 9001)
#   de.390er.applebridge-hangwatch-remote   the guest on another Mac, if configured   (APPLEBRIDGE_REMOTE_CTRL_PORT,
#                                           over SSH to APPLEBRIDGE_REMOTE_GUEST_SSH, relaunching
#                                           APPLEBRIDGE_GUEST_EMULATOR_APP there)
#
# Why a copy of its own, not the host server's deployed runtime: launchd cannot read ~/Documents (TCC, see
# deploy_host.sh), and the watchdog needs host/local.env (the emulator paths, the AppleShare password for guest_boot.py)
# and host/refs/. A local.env next to the SERVER's files would change what the server resolves (host_config reads the
# local.env beside it), so the watchdog lives in its own subdirectory and the server's directory stays as it was.
# local.env is copied mode 600; it never leaves this user's Library.
#
# Usage:  ./install_hang_watch.sh            install / refresh (re-run after changing any of the files below)
#         ./install_hang_watch.sh --remove   unload and delete both agents and the copy
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/AppleBridge/hangwatch"
LABEL="de.390er.applebridge-hangwatch"
FILES=(hang_watch.py guest_boot.py fb_export.py guest_remote.py screenshot_decode.py)

if [ "${1:-}" = "--remove" ]; then
    for l in "$LABEL" "$LABEL-remote"; do
        launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true; rm -f "$HOME/Library/LaunchAgents/$l.plist"
    done
    rm -rf "$DEST"; echo "removed the hang watchdog"; exit 0
fi

# shellcheck disable=SC1091
[ -f "$SRC/local.env" ] && . "$SRC/local.env"

echo "[1/3] Copying the watchdog -> $DEST"
mkdir -p "$DEST/refs"
for f in "${FILES[@]}"; do cp "$SRC/$f" "$DEST/$f"; done
[ -d "$SRC/refs" ] && cp "$SRC"/refs/*.json "$DEST/refs/" 2>/dev/null || true
if [ -f "$SRC/local.env" ]; then cp "$SRC/local.env" "$DEST/local.env"; chmod 600 "$DEST/local.env"; fi

agent() {   # label, port, log, [guest ssh], [emulator bundle on that machine]
    local plist="$HOME/Library/LaunchAgents/$1.plist" extra=""
    [ -n "${4:-}" ] && extra="<key>APPLEBRIDGE_GUEST_SSH</key><string>$4</string>"
    [ -n "${5:-}" ] && extra="$extra<key>APPLEBRIDGE_GUEST_EMULATOR_APP</key><string>$5</string>"
    cat > "$plist" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$1</string>
    <key>ProgramArguments</key>
    <array><string>/usr/bin/python3</string><string>-u</string><string>$DEST/hang_watch.py</string></array>
    <key>WorkingDirectory</key><string>$DEST</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>APPLEBRIDGE_CTRL_PORT</key><string>$2</string>
        <key>APPLEBRIDGE_HANG_LOG</key><string>$3</string>
        $extra
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>ProcessType</key><string>Background</string>
    <key>StandardOutPath</key><string>/tmp/applebridge_hangwatch_launchd.log</string>
    <key>StandardErrorPath</key><string>/tmp/applebridge_hangwatch_launchd.log</string>
</dict>
</plist>
PLIST_EOF
    plutil -lint "$plist" >/dev/null
    launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
    echo "      $1: port $2, log $3${4:+, emulator on $4}"
}

echo "[2/3] The local guest"
# 2026-10-13: the 9001 guest may run on another Mac too (APPLEBRIDGE_LOCAL_GUEST_SSH, e.g. Claudias-MBP .171, so the
# operator keeps this Mac's mouse); its bundle path there is APPLEBRIDGE_LOCAL_GUEST_EMULATOR_APP
agent "$LABEL" 9001 /tmp/applebridge_hang_watch_9001.log "${APPLEBRIDGE_LOCAL_GUEST_SSH:-}" "${APPLEBRIDGE_LOCAL_GUEST_EMULATOR_APP:-}"

echo "[3/3] The guest on another Mac"
if [ -n "${APPLEBRIDGE_REMOTE_GUEST_SSH:-}" ]; then
    [ -n "${APPLEBRIDGE_GUEST_EMULATOR_APP:-}" ] || echo "      WARNING: APPLEBRIDGE_GUEST_EMULATOR_APP unset - a hung remote guest is stopped but not relaunched"
    port="${APPLEBRIDGE_REMOTE_CTRL_PORT:-9011}"
    agent "$LABEL-remote" "$port" "/tmp/applebridge_hang_watch_$port.log" "$APPLEBRIDGE_REMOTE_GUEST_SSH"
else
    echo "      none configured (APPLEBRIDGE_REMOTE_GUEST_SSH in host/local.env)"
fi
sleep 3; launchctl list | grep applebridge-hangwatch || true
