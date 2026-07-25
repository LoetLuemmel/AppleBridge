#!/bin/bash
#
# deploy_host.sh — push the current repo host runtime to the launchd-served
# deployed copy, and restart the agent so the change takes effect. One command,
# no remembered steps.
#
# WHY A DEPLOYED COPY EXISTS (and can't be removed): the repo lives under
# ~/Documents, which is TCC-protected. launchd cannot read it ("Operation not
# permitted") without Full Disk Access, so the stdlib-only host runtime is copied
# to a non-protected location and run from there. The repo stays the SINGLE
# SOURCE OF TRUTH; the deployed copy is a build artifact — never hand-edit it.
#
# This script is the one place that knows the full runtime file set, so a deploy
# can't miss a dependency (host_server.py imports screenshot_decode + macbinary).
# install_host_service.sh writes the LaunchAgent plist and then calls this.
#
# Usage:
#   ./deploy_host.sh          # sync + restart the agent (if installed)
#   ./deploy_host.sh --no-restart   # just sync the files + stamp
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/AppleBridge"
LABEL="de.390er.applebridge-host"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# The complete runtime set host_server.py needs at import/run time.
RUNTIME_FILES=(host_server.py screenshot_decode.py macbinary.py bridge_doctor.py
               run_server.sh)

RESTART=1
[ "${1:-}" = "--no-restart" ] && RESTART=0

echo "[deploy] repo:  $SRC"
echo "[deploy] dest:  $DEST"
mkdir -p "$DEST"

for f in "${RUNTIME_FILES[@]}"; do
    if [ ! -f "$SRC/$f" ]; then
        echo "[deploy] ERROR: missing runtime file $SRC/$f" >&2
        exit 1
    fi
    cp "$SRC/$f" "$DEST/$f"
done
chmod +x "$DEST/run_server.sh"

# Stamp the deployed copy so host_server.py can log which build is live and
# start_stack.sh can detect drift. git SHA when available, else a timestamp.
STAMP="$( { git -C "$SRC" rev-parse --short HEAD 2>/dev/null || true; } )"
STAMP="${STAMP:-nogit}-$(date +%Y%m%d-%H%M%S)"
printf '%s\n' "$STAMP" > "$DEST/.deploy_stamp"
echo "[deploy] synced ${#RUNTIME_FILES[@]} files, stamp=$STAMP"

if [ "$RESTART" -eq 0 ]; then
    echo "[deploy] --no-restart: files synced, agent not touched."
    exit 0
fi

if [ ! -f "$PLIST" ]; then
    echo "[deploy] LaunchAgent not installed yet — run install_host_service.sh first."
    echo "[deploy] (files are synced; nothing to restart.)"
    exit 0
fi

# Restart the agent so it re-execs the fresh deployed copy.
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
    echo "[deploy] kicked $LABEL (restarted on the fresh copy)."
else
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "[deploy] bootstrapped $LABEL."
fi
