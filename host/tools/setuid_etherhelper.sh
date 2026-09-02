#!/bin/bash
#
# setuid_etherhelper.sh — make a BasiliskII bundle's etherhelpertool setuid-root
# so the patched runtool.c runs it WITHOUT the macOS authorization dialog
# (no password prompt for guest networking). Re-run after every fork rebuild:
# a build writes the tool user-owned, which drops the setuid bit.
#
# Usage:  ./setuid_etherhelper.sh [/path/to/BasiliskII.app]
#         default: the bundle named in host/local.env (APPLEBRIDGE_EMULATOR_APP)
#
# NOTE: macOS "App Management" blocks chown-to-root inside a bundle once ANY
# instance of that bundle-id has been LAUNCHED — even root gets "Operation not
# permitted", even after cp -R to a new path (the protection follows the
# bundle-id, not the file; stripping xattrs / lack of signature does not lift
# it). The reliable window is a FRESH xcodebuild output that has never been
# opened: setuid its etherhelpertool BEFORE first launch, then `mv` it to its
# final path (mv preserves the setuid-root bit). That is how BasiliskII-ab.app
# was made. So after a rebuild: setuid in the build-products dir (or a fresh
# copy that has never been launched), not in the running app. Granting Terminal
# Full Disk Access / App Management also works but is the fragile path.
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"      # host/
APP="${1:-}"
if [ -z "$APP" ]; then
    # shellcheck disable=SC1091
    [ -f "$HERE/local.env" ] && . "$HERE/local.env"
    APP="${APPLEBRIDGE_EMULATOR_APP:-}"
fi
if [ -z "$APP" ] || [ ! -d "$APP" ]; then
    echo "no app bundle (pass one, or set APPLEBRIDGE_EMULATOR_APP in host/local.env)"; exit 2
fi

EHT="$APP/Contents/Resources/etherhelpertool"
if [ ! -f "$EHT" ]; then echo "no etherhelpertool in $APP"; exit 2; fi

echo "setuid-root: $EHT"
osascript -e "do shell script \"/usr/sbin/chown root:wheel '$EHT' && /bin/chmod 4755 '$EHT'\" with administrator privileges" || {
    echo "FAILED — likely App Management on a registered bundle. Grant Terminal"
    echo "App Management (or Full Disk Access), or copy the bundle to a fresh path."
    exit 1
}
ls -la "$EHT"
case "$(ls -la "$EHT")" in
    -rws*root*) echo "OK — setuid-root set. Relaunch the emulator: networking is now password-free." ;;
    *) echo "WARN — setuid bit not present after the change; check the listing above." ;;
esac
