#!/bin/bash
#
# ensure_host_alias.sh — make the AppleBridge host IP (192.168.3.154) present on
# the DEFAULT-ROUTE interface, and only there. Idempotent. Run as root at boot
# and periodically by the de.390er.applebridge-alias LaunchDaemon.
#
# Why this exists: the host server binds .154:9000 and the guest's MACNAT return
# path must come back on the interface its traffic exits — the default route
# (normally Wi-Fi, en0). If .154 sits on a different NIC the daemon's connect
# can't complete (the "Anatomy of a Freeze" case). A plain `ifconfig alias` does
# not survive a reboot, so this restores it. See:
#   https://pit.390er.de/applebridge/anatomy-of-a-freeze-macnat-return-path/
#
HOST_IP="192.168.3.154"
MASK="255.255.255.0"

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
