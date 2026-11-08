#!/bin/bash
# install.sh user@host [container] [display] [fb_dump] — put the macOS tool stand-ins on a LINUX guest host (2026-11-08).
# Run from the hub (this folder). Needs: SSH key login, docker for that user, and xdotool INSIDE the container. No sudo.
# Re-run after the container image changes (it copies the emulator binary for fb_export's marker check).
#   ~/.applebridge/              config (container, display, fb_dump), lib/ (fb_export + decoder), BasiliskII (binary copy)
#   ~/Documents/BasiliskII/tools/cliclick            -> shim   (guest_remote.py's default APPLEBRIDGE_GUEST_TOOLS)
#   ~/.applebridge/bin/{osascript,screencapture,sips} -> shim  (called by bare name over SSH; ~/.bashrc puts the folder on
#                                                               PATH ABOVE Debian's "not interactive: return" line)
set -e
T=${1:?usage: install.sh user@host [container] [display] [fb_dump]}
C=${2:-basilisk-odroid-c2}; D=${3:-:1}; F=${4:-/home/${T%@*}/basilisk/data/fb/basilisk_fb.bin}
H="$(cd "$(dirname "$0")/.." && pwd)"
ssh "$T" "mkdir -p ~/.applebridge/lib ~/Documents/BasiliskII/tools"
scp -q "$H/linux_guest/ab_linux_shim.py" "$T:.applebridge/ab_linux_shim.py"
scp -q "$H/fb_export.py" "$H/guest_remote.py" "$H/screenshot_decode.py" "$T:.applebridge/lib/"
ssh "$T" "set -e; cd ~/.applebridge; chmod 755 ab_linux_shim.py
  echo '$C' > container; echo '$D' > display; echo '$F' > fb_dump
  docker exec '$C' sh -c 'command -v xdotool' >/dev/null || { echo 'xdotool missing in container $C'; exit 1; }
  docker cp '$C':\$(docker exec '$C' sh -c 'command -v BasiliskII') BasiliskII 2>/dev/null
  LC_ALL=C grep -a -q BASILISK_FB_DUMP BasiliskII || { echo 'emulator binary has no fb-export marker'; exit 1; }
  ln -sf ~/.applebridge/ab_linux_shim.py ~/Documents/BasiliskII/tools/cliclick
  mkdir -p bin; for t in osascript screencapture sips; do ln -sf ~/.applebridge/ab_linux_shim.py bin/\$t; done
  grep -q '.applebridge/bin' ~/.bashrc || echo 'ADD to the TOP of ~/.bashrc (before its non-interactive return):
    case \":\$PATH:\" in *\":\$HOME/.applebridge/bin:\"*) ;; *) PATH=\"\$HOME/.applebridge/bin:\$PATH\";; esac'
  echo installed on \$(hostname): container $C display $D dump $F"
