#!/usr/bin/env python3
"""c2_watch.py - bring the odroid-c2 guest back when its AppleBridge daemon is gone (2026-11-19).

Why: Blackbird version 1 (adespoton, app-wish.390er.de) recursed in DrawContent until the stack hit the heap; the app
vanished and took the AppleBridge daemon in the guest with it. The emulator kept running, the host server on the hub
answered "Mac daemon not connected", and the next build waited for "a free guest" until somebody restarted the
container by hand. hang_watch.py does not fit this guest (it relaunches Mac emulator bundles and needs a hung CPU).

Rule, judged every INTERVAL seconds on the hub:
  * evidence of death is ONLY the host server's own reply "Mac daemon not connected" on the control port (9031).
    A probe that waits or times out means the guest is busy with a build - no evidence, nothing changes.
  * dead for DEAD_S seconds without a single good answer -> `docker restart basilisk-odroid-c2` on the C2 (the
    operator-approved fix), then the live view script, then wait for the daemon, and tell the operator.
  * at most one restart per COOLDOWN_S, at most MAX_RESTARTS per 6 h - then it only reports.
  * a DEAF tun0 (the guest sends, the bridge delivers nothing - STOERUNGEN.md "Stoerung 2" on the C2) is acted on
    after 60 s, cooldown or not: first tun0 is taken off br0 and put back (no restart), then a restart (2026-11-24).
  * before every restart the guest's screen is saved on the C2 (~/basilisk/data/fb/before_*.bin, last 20).
  * every restart is CLEAN: SIGTERM makes System 7 ask "shut down?", Return answers it, the guest powers off and
    the container is started again. Only a guest that cannot answer gets the old hard `docker restart`.
Run:  /usr/bin/python3 c2_watch.py          (LaunchAgent de.390er.c2watch)
      /usr/bin/python3 c2_watch.py --once   one probe, printed
"""
import os, socket, subprocess, sys, time

PORT = int(os.environ.get("C2W_PORT", "9031"))
C2 = os.environ.get("C2W_SSH", "pit@192.168.3.4")
CONTAINER = os.environ.get("C2W_CONTAINER", "basilisk-odroid-c2")
INTERVAL, DEAD_S, COOLDOWN_S, MAX_RESTARTS = 30, 150, 900, 3
LOG = os.environ.get("C2W_LOG", "/tmp/c2_watch.log")
HW = os.path.expanduser("~/Library/Application Support/AppleBridge/hangwatch")


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    try: open(LOG, "a").write(line + "\n")
    except OSError: pass


def notify(msg):
    """iMessage etc. through the operator's own operator_notify.py (never raises)"""
    try:
        sys.path.insert(0, HW); import operator_notify
        return operator_notify.notify(msg, log=log, name="odroid-c2")
    except Exception as e:
        log(f"notify failed: {e}"); return False


def probe():
    """'ok' (daemon answers), 'dead' (host server says: not connected), 'busy' (no evidence), 'noserver' (port refused)"""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=5) as s:
            s.settimeout(60); s.sendall(b"PROCLIST/Q\n\n"); b = b""
            while True:
                d = s.recv(65536)
                if not d: break
                b += d
        if b.startswith(b"STATUS:0"): return "ok"
        if b"daemon not connected" in b: return "dead"
        return "busy"
    except ConnectionRefusedError: return "noserver"
    except OSError: return "busy"


def ssh(cmd, timeout=120):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", C2, cmd],
                          capture_output=True, text=True, timeout=timeout)


def tun_deaf():
    """'rx=.. tx=.. dropped=..' when tun0 delivers nothing to the guest, else None.

    STOERUNGEN.md 'Stoerung 2' (2026-11-13/18/22/24): after a container start the guest SENDS (rx grows) but the
    bridge hands it nothing - tx stays at ~1 packet, or tx_dropped climbs into the hundreds. The daemon then times
    out its connect forever; one more `docker restart` has always cured it. Healthy: ~75 tx packets per 30 s
    (LAN broadcasts), measured 2026-11-24."""
    try:
        r = ssh("cd /sys/class/net/tun0/statistics && cat rx_packets tx_packets tx_dropped", 20)
        rx, tx, dr = (int(v) for v in r.stdout.split())
    except Exception:
        return None
    return f"rx={rx} tx={tx} dropped={dr}" if (rx >= 20 and tx <= 10) or dr > 50 else None


def reattach_tun():
    """Try to cure a deaf tun0 WITHOUT restarting the guest: take it off br0 and put it back (root in the container,
    which has NET_ADMIN and host networking). -> True when frames reach the guest again within 15 s.
    Only tried when tun0 is NOT on br0 in forwarding state (checked first): when it is, a re-attach did nothing
    (2026-11-24 12:34, tx 1 -> 1), so the caller restarts right away."""
    def tx():
        r = ssh("cat /sys/class/net/tun0/statistics/tx_packets", 20)
        try: return int(r.stdout.strip())
        except ValueError: return None
    # Only worth it when tun0 is NOT properly on the bridge. In every deaf case measured on 2026-11-24 it was
    # (master br0, port state 3 = forwarding) and the re-attach changed nothing (12:34: tx 1 -> 1) - then go
    # straight to the restart instead of spending 15 s.
    r = ssh("basename $(readlink /sys/class/net/tun0/master 2>/dev/null) 2>/dev/null; "
            "cat /sys/class/net/tun0/brport/state 2>/dev/null", 20)
    port = r.stdout.split()
    if port == ["br0", "3"]:
        log("tun0 is on br0 and forwarding - a re-attach would not help, restarting instead")
        return False
    before = tx()
    r = ssh(f"docker exec -u 0 {CONTAINER} sh -c 'ip link set tun0 nomaster && sleep 1 && ip link set tun0 master br0'", 30)
    time.sleep(15)
    after = tx()
    ok = before is not None and after is not None and after - before > 10
    log(f"reattach tun0 to br0: rc={r.returncode} tx {before} -> {after} in 15 s -> {'CURED' if ok else 'no effect'}")
    return ok


def snapshot(why):
    """Keep the guest's screen before a restart (2026-11-24: at 10:42 a fresh boot hung where nobody could see it,
    and the restart wiped the evidence). The fb-export Basilisk dumps its framebuffer on SIGUSR1; the copy stays on
    the C2 as ~/basilisk/data/fb/before_<stamp>_<why>.bin (last 20 kept). host/fb_export.py turns it into a PNG."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    r = ssh(f"F=~/basilisk/data/fb/basilisk_fb.bin; rm -f $F; docker exec {CONTAINER} pkill -USR1 -x BasiliskII || exit 1; "
            f"for i in 1 2 3 4 5 6 7 8 9 10; do [ -s $F ] && break; sleep 0.3; done; sleep 0.3; "
            f"cp $F ~/basilisk/data/fb/before_{stamp}_{why}.bin && "
            f"ls -1t ~/basilisk/data/fb/before_*.bin | tail -n +21 | xargs -r rm -f; echo ok", 40)
    log(f"screen saved: before_{stamp}_{why}.bin" if "ok" in r.stdout else f"screen NOT saved ({r.stderr.strip()[:80]})")


def clean_restart():
    """Restart the container WITHOUT pulling the guest's plug. -> how it went (for the log).

    `docker restart` sends SIGTERM, which Basilisk hands the Mac as the power key: System 7 asks "Are you sure you
    want to shut down your computer now?" - and Docker hard-kills the emulator 10 s later, mid-question (seen
    2026-11-24 10:10 and 10:17: kill, kill, die). So: SIGTERM, a click on the dialog's Shut Down button, wait for
    the guest to power off and Basilisk to exit, then `docker start`. A guest that cannot
    answer (bomb, hang) still gets the old hard restart after 90 s."""
    try: snapshot("restart")
    except Exception as e: log(f"screen NOT saved ({e})")
    ssh(f"docker kill --signal=TERM {CONTAINER}", 30)
    t0 = time.time()
    while time.time() - t0 < 90:
        time.sleep(3)
        if int(time.time() - t0) % 10 < 3:   # every ~10 s: one answer can come too early (10:19, 2026-11-24)
            # CLICK "Shut Down" - not Return: with the AppleBridge console in front, Return went into the
            # daemon's command line (it held "TREE MeinMac:") and the dialog stayed (14:51, 2026-11-24).
            # The alert is centred on the 1024x768 screen; a click where there is no dialog hits empty desktop.
            ssh(f"docker exec -e DISPLAY=:1 {CONTAINER} xdotool mousemove 653 223 sleep 0.3 click 1", 30)
        r = ssh(f"docker inspect {CONTAINER} --format '{{{{.State.Running}}}}'", 30)
        if r.stdout.strip() == "false":
            ssh(f"docker start {CONTAINER}", 60)
            return f"clean shutdown in {int(time.time() - t0) + 3} s, then started"
    ssh(f"docker restart {CONTAINER}", 120)
    return "guest did not power off in 90 s - hard docker restart"


def restart():
    t0 = time.time()
    log(f"restart: {clean_restart()}")
    started, again = time.time(), 0
    while time.time() - t0 < 240 + 90 * again:
        time.sleep(10)
        if probe() == "ok": break
        # a deaf tun0 never heals by waiting: restart once more right away (at most twice)
        if again < 2 and time.time() - started >= 60:
            deaf = tun_deaf()
            if deaf and not reattach_tun():
                again += 1
                log(f"tun0 deaf after {int(time.time() - started)} s ({deaf}) - Stoerung 2, docker restart again ({again}/2)")
                log(f"restart: {clean_restart()}"); started = time.time()
    try: ssh("[ -x ~/basilisk/start_live_view.sh ] && ~/basilisk/start_live_view.sh >/dev/null 2>&1; true", 60)
    except Exception: pass
    st = probe()
    return f"daemon back after {int(time.time() - t0)} s" if st == "ok" else f"daemon NOT back after {int(time.time() - t0)} s ({st})"


def main():
    if "--once" in sys.argv: print(probe()); return
    log(f"watching odroid-c2: control port {PORT}, restart after {DEAD_S} s of 'daemon not connected'")
    dead_since, restarts, last_restart, said_noserver = None, [], 0, False
    while True:
        st = probe(); now = time.time()
        if st == "ok":
            if dead_since: log(f"daemon answers again after {int(now - dead_since)} s")
            dead_since = None; said_noserver = False
        elif st == "dead":
            if dead_since is None: dead_since = now; log("host server: Mac daemon not connected")
            restarts = [t for t in restarts if now - t < 6 * 3600]
            # A deaf tun0 (Stoerung 2) needs no 150 s of evidence and no cooldown: waiting never heals it. It also
            # follows starts the watch did not make itself (Docker's own restart after a shutdown, 2026-11-24 10:07).
            deaf = tun_deaf() if now - dead_since >= 60 else None
            if deaf:
                log(f"tun0 deaf ({deaf}) after {int(now - dead_since)} s without the daemon - Stoerung 2")
                if reattach_tun():
                    time.sleep(INTERVAL); continue      # the daemon reconnects by itself within 30 s
            if (deaf or now - dead_since >= DEAD_S) and (deaf or now - last_restart >= COOLDOWN_S):
                if len(restarts) >= MAX_RESTARTS:
                    if now - last_restart >= 3600:
                        notify(f"odroid-c2: Daemon weg seit {int((now - dead_since) / 60)} min, schon {len(restarts)} Neustarts in 6 h - ich starte nicht mehr selbst neu")
                        last_restart = now
                else:
                    log(f"daemon gone for {int(now - dead_since)} s -> docker restart {CONTAINER}")
                    res = restart(); log(res); restarts.append(now); last_restart = time.time()
                    notify(f"odroid-c2: AppleBridge-Daemon war {int((now - dead_since) / 60)} min weg - Container neu gestartet, {res}")
                    dead_since = None if "back after" in res and "NOT" not in res else time.time()
        elif st == "noserver" and not said_noserver:
            log("control port refused - the host server on the hub is down (not the guest; nothing restarted)"); said_noserver = True
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
