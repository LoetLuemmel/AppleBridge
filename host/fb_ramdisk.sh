#!/bin/zsh
# fb_ramdisk.sh (2026-10-28) - keep the framebuffer export off the SSD.
#
# Every fb-export dump is a full ~787 KB file write; the watchers and the build gates wrote about 2 GB a day to each
# emulator Mac's SSD (article "SSD wears", applebridge section). This script keeps an 8 MB RAM disk ABFB mounted
# (not shown in the Finder) and sets BASILISK_FB_DUMP=/Volumes/ABFB/basilisk_fb.bin in the user's launchd domain, so
# an emulator started by LaunchServices AFTERWARDS (`open`, the Finder, hang_watch's relaunch) dumps there. A running
# emulator keeps the path it started with until its next start. Readers without the variable (launchd watchers, SSH
# commands from the Xavier) look in both places - fb_export.dump_paths().
#
#   fb_ramdisk.sh ensure      mount the disk if missing, set the variable (idempotent; what the agent runs at login)
#   fb_ramdisk.sh install     ensure + a LaunchAgent that repeats it at every login (a RAM disk does not survive a reboot)
#   fb_ramdisk.sh uninstall   remove the agent, unset the variable, eject the disk (the emulator falls back to /tmp
#                             at its next start)
#   fb_ramdisk.sh status      what is mounted, what launchd hands out, where the running emulator's dump lands
VOL=ABFB; MNT=/Volumes/$VOL; DUMP=$MNT/basilisk_fb.bin; SECTORS=16384   # 16384 x 512 B = 8 MB, any screen mode fits
LABEL=de.390er.applebridge-fbramdisk; PLIST=$HOME/Library/LaunchAgents/$LABEL.plist
SELF=${0:A}

ensure() {
  if ! mount | grep -q " on $MNT "; then
    dev=$(hdiutil attach -nomount ram://$SECTORS | awk '{print $1}')
    [ -n "$dev" ] || { echo "fb_ramdisk: hdiutil attach failed" >&2; return 1; }
    newfs_hfs -v $VOL $dev >/dev/null || { hdiutil detach $dev >/dev/null; echo "fb_ramdisk: newfs_hfs failed" >&2; return 1; }
    diskutil mount -mountOptions nobrowse $dev >/dev/null || { hdiutil detach $dev >/dev/null; echo "fb_ramdisk: mount failed" >&2; return 1; }
  fi
  [ -w $MNT ] || { echo "fb_ramdisk: $MNT not writable" >&2; return 1; }
  launchctl setenv BASILISK_FB_DUMP $DUMP
  echo "fb_ramdisk: $MNT mounted, BASILISK_FB_DUMP=$(launchctl getenv BASILISK_FB_DUMP)"
}

case "$1" in
  ensure) ensure ;;
  install)
    ensure || exit 1
    mkdir -p ${PLIST:h}
    cat > $PLIST <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/zsh</string><string>$SELF</string><string>ensure</string></array>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/applebridge_fbramdisk.log</string>
  <key>StandardErrorPath</key><string>/tmp/applebridge_fbramdisk.log</string>
</dict></plist>
EOF
    launchctl unload $PLIST 2>/dev/null; launchctl load $PLIST && echo "fb_ramdisk: agent $LABEL installed" ;;
  uninstall)
    [ -f $PLIST ] && { launchctl unload $PLIST 2>/dev/null; rm -f $PLIST; echo "fb_ramdisk: agent removed"; }
    launchctl unsetenv BASILISK_FB_DUMP
    mount | grep -q " on $MNT " && diskutil eject $MNT >/dev/null && echo "fb_ramdisk: $MNT ejected"
    echo "fb_ramdisk: restart the emulator (cleanly) so it dumps to /tmp again" ;;
  status)
    mount | grep " on $MNT " || echo "not mounted: $MNT"
    echo "launchd BASILISK_FB_DUMP: $(launchctl getenv BASILISK_FB_DUMP || true)"
    [ -f $PLIST ] && echo "agent: $PLIST" || echo "agent: none"
    for f in $DUMP /tmp/basilisk_fb.bin; do [ -f $f ] && echo "$f: $(stat -f '%z B, %Sm' $f)"; done ;;
  *) sed -n '2,16p' $SELF | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
