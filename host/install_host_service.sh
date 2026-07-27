#!/bin/bash
#
# install_host_service.sh — run the AppleBridge host server as a launchd
# LaunchAgent, so it starts at login and restarts if it exits. No more
# "forgot to start the host server".
#
# Why a DEPLOYED copy (not run straight from this repo): the repo lives under
# ~/Documents, which is TCC-protected — launchd cannot read it without Full
# Disk Access (it fails with "Operation not permitted"). So the stdlib-only
# host runtime is copied to a non-protected location and run from there. The
# repo stays the single source of truth; the deployed copy is a build artifact.
#
# This script writes the LaunchAgent plist and then hands the file sync + agent
# start to deploy_host.sh (the one place that knows the full runtime file set).
# After editing host_server.py / screenshot_decode.py / macbinary.py, you do NOT
# re-run this installer — just run ./deploy_host.sh to push the change live.
#
# Prerequisite, where an address IS configured: the server binds it on :9000, so
# that alias must sit on the default-route interface — the one the guest's NAT
# traffic exits. Until it does, the agent just retries every 30s
# (ThrottleInterval). start_stack.sh sets the alias up; a boot-persistent alias
# would need a root LaunchDaemon (separate, needs sudo). With NO address
# configured — the slirp branch — the server binds every address and none of
# this applies.
#
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/Application Support/AppleBridge"
LABEL="de.390er.applebridge-host"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "[1/2] Writing LaunchAgent -> $PLIST"
mkdir -p "$HOME/Library/LaunchAgents"

# Carry the machine's configuration INTO the agent. `host/local.env` lives beside
# the repo and deploy_host.sh syncs only the runtime modules, so that file was
# inert for the launchd-served server: APPLEBRIDGE_HOST_IP had never reached it,
# and nothing looked wrong because the fallback is a wildcard bind that accepts
# the guest anyway (observed 2026-07-27). host_config resolves the values in one
# place; secrets are deliberately NOT among them — a plist is world-readable, so
# APPLEBRIDGE_CTRL_TOKEN keeps its `launchctl setenv` path.
ENV_BLOCK="$(cd "$SRC" && /usr/bin/python3 -c \
    'import host_config; print(host_config.launchd_environment_xml())' 2>/dev/null)"
if [ -n "$ENV_BLOCK" ]; then
    echo "      carrying into the agent:"
    printf '%s\n' "$ENV_BLOCK" | sed -n 's/.*<string>\(.*\)<\/string>.*/        \1/p'
else
    echo "      no host address configured — the server will bind every address"
    echo "      (correct on the slirp branch, where there is none to configure)"
fi

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
$ENV_BLOCK
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

echo "[2/2] Syncing runtime + starting the agent (deploy_host.sh)"
# Clear any previously-loaded definition so the (possibly changed) plist above
# is what gets bootstrapped; deploy_host.sh then syncs the files and bootstraps.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
"$SRC/deploy_host.sh"
sleep 2
launchctl list | grep "$LABEL" || true

echo
echo "Done. The host server now starts at login and is kept alive."
echo "  - Redeploy after an edit:  ./deploy_host.sh"
echo "  - Uninstall:  launchctl bootout gui/\$(id -u)/$LABEL && rm '$PLIST'"
echo "  - Log:        /tmp/applebridge_host_launchd.log + /tmp/applebridge_server.log"
echo "  - Config:     the agent carries host/local.env's values as of NOW."
echo "                Re-run this installer after changing them — deploy_host.sh"
echo "                syncs code, not configuration."
if [ -n "$ENV_BLOCK" ]; then
    # Naming an interface that exists on one machine is the R13 defect; the
    # address is resolved, and where it belongs is stated as a rule, not a NIC.
    echo "  - Reminder:   that address must be aliased on the DEFAULT-ROUTE"
    echo "                interface, where the guest's NAT traffic exits."
fi
