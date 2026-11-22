#!/bin/sh
# guest.sh -- drive and look at the s7test guest (Basilisk II in the basilisk-s7test container on the C2).
#
#   guest.sh click X Y | dbl X Y | drag X1 Y1 X2 Y2 | key KEYS... | type TEXT
#   guest.sh shot OUT.png                 # framebuffer -> PNG on this Mac (fb-export: SIGUSR1, no bridge needed)
#
# Guest coordinates are X coordinates 1:1 (window at 0,0 on the container's Xvfb).
# Basilisk samples the pointer LATER than the button (2026-11-22): a press sent right after a move lands at the OLD
# position -- the first try dragged a disk icon instead of opening the File menu. So every gesture pauses after the
# move, and a click holds the button for a quarter second.
# A drag is one press -> move -> release in ONE call: a menu held open across calls would starve the daemon.
H="${S7FLOOR_HOST:-pit@192.168.3.4}"
C="${S7FLOOR_CONTAINER:-basilisk-s7test}"
X="docker exec -e DISPLAY=:1 $C xdotool"
case "$1" in
  click) ssh "$H" "$X mousemove $2 $3 sleep 0.4 mousedown 1 sleep 0.25 mouseup 1" ;;
  dbl)   ssh "$H" "$X mousemove $2 $3 sleep 0.4 mousedown 1 sleep 0.08 mouseup 1 sleep 0.1 mousedown 1 sleep 0.08 mouseup 1" ;;
  drag)  ssh "$H" "$X mousemove $2 $3 sleep 0.4 mousedown 1 sleep 0.4 mousemove $4 $5 sleep 0.6 mouseup 1" ;;
  key)   shift; ssh "$H" "$X key --delay 150 $*" ;;
  type)  shift; ssh "$H" "$X type --delay 150 '$*'" ;;
  shot)  exec /usr/bin/python3 "$(dirname "$0")/record.py" --once "$2" ;;
  *)     sed -n '2,10p' "$0"; exit 2 ;;
esac
