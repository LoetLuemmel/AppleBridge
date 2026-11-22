#!/bin/sh
# AppleBridge System-7 floor test guest (2026-11-22) -- lives on the C2 as ~/s7test/run_s7test.sh, beside
# ~/s7test/basilisk_ii_prefs (both from host/tools/s7floor/c2/). Separate from basilisk-odroid-c2: own /data
# (~/s7test), own PRAM, ether slirp (no tap, no host network: the guest is 10.0.2.15 and dials the hub through
# slirp's NAT), the production ROM mounted read-only, VNC only on the docker bridge.
# Usage: run_s7test.sh <image>   (the image is linked as /data/s7.img)
# --cap-add NET_ADMIN although slirp needs no tap: the image's BasiliskII carries a file capability for the tap
# device, and without NET_ADMIN in the bounding set the exec fails with "Operation not permitted".
set -e
cd "$HOME/s7test"
ln -sf "$1" s7.img
docker rm -f basilisk-s7test >/dev/null 2>&1 || true
docker run -d --name basilisk-s7test \
  --cap-add NET_ADMIN -e TZ=Europe/Berlin -e HOME=/data \
  -v "$HOME/s7test:/data" \
  -v "$HOME/basilisk/data/Mac.ROM:/data/Mac.ROM:ro" \
  -v "$HOME/basilisk/entrypoint.sh:/usr/local/bin/entrypoint.sh:ro" \
  -e GEOMETRY=640x480x24 \
  -e BASILISK_FB_DUMP=/data/fb.bin \
  basilisk2:clip
# VNC for watching (operator 2026-11-22): own server, reachable on the docker bridge, NOT on the C2 host network.
# On the Mac:  ssh -N -L 5902:172.17.0.2:5900 pit@192.168.3.4   ->  vnc://localhost:5902
sleep 3
docker exec -d basilisk-s7test sh -c "X0tigervnc -display :1 -rfbport 5900 -SecurityTypes VncAuth -PasswordFile /data/vncpasswd -localhost=0 -AlwaysShared=1 >/tmp/x0vnc.log 2>&1"
docker inspect -f "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}" basilisk-s7test
