# c2watch — keeps the odroid-c2 guest reachable

`c2_watch.py` runs on the hub as the LaunchAgent `de.390er.c2watch`, from a copy in
`~/Library/Application Support/AppleBridge/c2watch/` (this file is the source; copy it there after a change and
`launchctl kickstart -k gui/$(id -u)/de.390er.c2watch`). `hang_watch.py` does not fit this guest: it relaunches
emulator bundles on this Mac, while odroid-c2 is a Basilisk II in a Docker container on the C2 (`pit@192.168.3.4`).

What it does, and why each rule exists, is in the module docstring. In short (2026-11-24):

- **Dead daemon** (the host server answers "Mac daemon not connected" for 150 s) → restart the container.
- **Deaf tun0** (guest sends, bridge delivers nothing — `ip -s link show tun0`: RX grows, TX stays at ~1 or
  `dropped` climbs) → after 60 s re-attach tun0 to `br0` as root in the container; restart only if that fails.
  Random on about every second container start that day; independent of how the container was stopped.
  **Measured 12:34 the same day: the re-attach did NOT cure it** (`tx 1 -> 1`), the restart is what helps; the
  attempt costs 15 s and stays only until a second observation says the same.
- **Clean restarts**: `docker restart` alone hard-kills Basilisk 10 s after SIGTERM, while System 7 is still asking
  "Are you sure you want to shut down?". The watch answers the dialog with Return and waits for the guest to
  power off.

- **Evidence first**: before every restart the guest's screen is saved on the C2 as
  `~/basilisk/data/fb/before_<stamp>_restart.bin` (last 20; `host/fb_export.py` reads them).

Log: `/tmp/c2_watch.log`. One probe by hand: `/usr/bin/python3 c2_watch.py --once`.
