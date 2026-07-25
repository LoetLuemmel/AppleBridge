#!/bin/bash
#
# check_ether_backend.sh — keep Basilisk's Ethernet backend on the one the stack
# was set up for, BEFORE the emulator launches.
#
# Why this exists
# ---------------
# `~/.basilisk_ii_prefs.netmode` records the intended backend (start_stack.sh
# writes it), but nothing ever read it back — so a prefs file that drifted away
# from it survived silently. On 2026-07-25 an `ether slirp` left behind by an
# earlier experiment cost an hour: slirp is a user-mode IP-ONLY NAT, so TCP kept
# flowing and the bridge looked perfectly healthy, while AppleTalk frames were
# dropped and the guest's Chooser found no AppleShare server at all. The symptom
# pointed at the file-sharing stack; the cause was one word in a prefs file.
#
# `bridge_doctor` reports that drift after the fact. This closes it at the
# source: the launcher refuses to start an emulator whose backend contradicts
# the recorded intent, and by default repairs it first.
#
# Usage:  check_ether_backend.sh [<prefs> [<netmode>]]
#         AB_KEEP_ETHER=1 check_ether_backend.sh …   # report drift, change nothing
#
# Exit:   0  backend matches (or was repaired, or intent was just seeded)
#         1  drift left in place on purpose (AB_KEEP_ETHER)
#         2  cannot check (no prefs file)
#
# Kept separate from start_stack.sh so the test suite can drive every branch
# against temp files instead of the user's real configuration.
set -u

PREFS="${1:-$HOME/.basilisk_ii_prefs}"
NETMODE="${2:-$PREFS.netmode}"

if [ ! -f "$PREFS" ]; then
    echo "      WARN: no prefs file at $PREFS — cannot check the Ethernet backend."
    exit 2
fi

current="$(awk '/^ether[ \t]/{print $2; exit}' "$PREFS")"
intended=""
[ -f "$NETMODE" ] && intended="$(tr -d ' \t\r\n' < "$NETMODE")"

# No recorded intent yet: adopt what is configured now. That makes the mechanism
# self-establishing on a fresh machine instead of demanding a manual first write.
if [ -z "$intended" ]; then
    if [ -n "$current" ]; then
        printf '%s\n' "$current" > "$NETMODE"
        echo "      backend: $current (recorded as the intended backend)"
        exit 0
    fi
    echo "      WARN: prefs has no 'ether' line and no intended backend is recorded."
    exit 2
fi

if [ "$current" = "$intended" ]; then
    echo "      backend: $current (matches .netmode)"
    exit 0
fi

# --- drift ----------------------------------------------------------------
echo "      DRIFT: emulator backend is '${current:-<none>}', intended is '$intended'."
if [ "$current" = "slirp" ]; then
    echo "             slirp passes TCP but DROPS AppleTalk — the bridge will look"
    echo "             healthy while the Chooser finds no AppleShare server."
fi

if [ "${AB_KEEP_ETHER:-}" = "1" ]; then
    echo "      AB_KEEP_ETHER=1 — leaving it as is."
    exit 1
fi

backup="$PREFS.bak-ether-$(date +%Y%m%d-%H%M%S)"
cp "$PREFS" "$backup"
if [ -n "$current" ]; then
    # Only the backend token changes; every other pref is left untouched.
    #
    # Rewrite via awk + mv rather than `sed -i`: the in-place flag takes an
    # argument on BSD sed and none on GNU sed, so no single spelling works on
    # both — and the tests run on Linux CI while the script itself targets
    # macOS. A GNU sed silently treated the BSD form's '' as the script and
    # left the file untouched, i.e. it "repaired" nothing. Also collapses a
    # duplicate `ether` key, which would otherwise stay ambiguous.
    tmp="$PREFS.tmp.$$"
    awk -v want="ether $intended" \
        '/^ether[ \t]/ { if (!seen) { print want; seen = 1 } ; next } { print }' \
        "$PREFS" > "$tmp" && mv "$tmp" "$PREFS"
else
    printf 'ether %s\n' "$intended" >> "$PREFS"     # key was missing entirely
fi
echo "      repaired -> 'ether $intended' (backup: $backup)"

# A running emulator read its prefs at launch, so the repair does not reach it.
# Say so rather than let the next command act on a backend that isn't live yet.
if pgrep -x BasiliskII >/dev/null 2>&1; then
    echo "      NOTE: BasiliskII is already running on the old backend — quit and"
    echo "            relaunch it for this to take effect."
fi
exit 0
