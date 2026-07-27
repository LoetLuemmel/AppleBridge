#!/bin/bash
#
# ensure_host_alias.sh — make the configured AppleBridge host IP present on
# the DEFAULT-ROUTE interface, and only there. Idempotent. Run as root at boot
# and periodically by the de.390er.applebridge-alias LaunchDaemon.
#
# Why this exists: the host server binds that address:9000 and the guest's MACNAT
# return path must come back on the interface its traffic exits — the default
# route (normally Wi-Fi, en0). If it sits on a different NIC the daemon's connect
# can't complete (the "Anatomy of a Freeze" case). A plain `ifconfig alias` does
# not survive a reboot, so this restores it. See:
#   https://pit.390er.de/applebridge/anatomy-of-a-freeze-macnat-return-path/
#
# The address comes from configuration, never from this file (R1). Without it
# there is nothing to alias — and no way to derive it, since the matching value
# lives in the guest's prefs inside the emulator.
_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
[ -f "$_DIR/local.env" ] && . "$_DIR/local.env"
HOST_IP="${APPLEBRIDGE_HOST_IP:-}"
MASK="${APPLEBRIDGE_HOST_MASK:-255.255.255.0}"

if [ -z "$HOST_IP" ]; then
    logger -t applebridge-alias "no APPLEBRIDGE_HOST_IP set (host/local.env) — nothing to alias"
    exit 0                      # not an error: 0.0.0.0 setups need no alias
fi

# The interface .154 must live on = the host's default-route interface.
IF="$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')"
[ -z "$IF" ] && exit 0          # no default route yet (early boot) — a later run catches it

# Already correct? Nothing to do.
if ifconfig "$IF" 2>/dev/null | grep -q "inet ${HOST_IP} "; then
    exit 0
fi

# Strip any stale .154 off OTHER interfaces first — a split across interfaces is
# exactly what wedges the guest.
for other in $(ifconfig -l); do
    [ "$other" = "$IF" ] && continue
    if ifconfig "$other" 2>/dev/null | grep -q "inet ${HOST_IP} "; then
        ifconfig "$other" -alias "$HOST_IP" 2>/dev/null || true
    fi
done

ifconfig "$IF" inet "$HOST_IP" netmask "$MASK" alias && \
    logger -t applebridge-alias "aliased ${HOST_IP} on ${IF}"
