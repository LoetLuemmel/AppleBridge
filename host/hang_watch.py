#!/usr/bin/env python3
"""hang_watch.py — stop and restart a HUNG guest emulator without waiting for anybody (2026-10-04).

Why: a generated program that loops without yielding takes System 7 down with it. The daemon gets no time, so the
bridge goes silent; Force Quit (Cmd-Opt-Esc) is handled inside the event manager the program never calls, so it does
nothing; `mac_shutdown` cannot be delivered. On 2026-10-04 the local guest sat like that at 100 % CPU from 14:28 for
about 1.5 h, fan at full speed, because nothing watched between an agent's turns. The operator's instruction: "Next
time this happens, please also hard-kill BAII yourself" — for the 2013 guest as well, and as part of AppleBridge.
This is the one exception to "never hard-kill Basilisk" (D-004): a HEALTHY guest is never touched.

HUNG means all of these, judged once per INTERVAL by `HangJudge` (a pure state machine, tested without an emulator):
  * the bridge has answered at least once since this emulator process started (a booting guest is silent at high
    CPU too, and is left alone);
  * since then it has been silent — PROCLIST over the control port without "Finder" — for SILENT_S seconds;
  * the emulator process used >= CPU_MIN % CPU at every check in that window (the guests run with `idlewait`: idle is
    3-5 %, so a silent guest at low CPU is a network or host-server problem, not a hang, and killing would not help);
  * the framebuffer did not change in that window, where it can be read (fb-export build) — a changing screen means
    somebody or something is still at work in the guest, e.g. the operator inside a modal dialog that starves the
    daemon. Where the frame cannot be read, this condition is skipped.
A refused control port (host server down) is no evidence at all: the window neither grows nor resets.

Then: TERM, KILL after 10 s, relaunch the bundle, run guest_boot.py (login, alerts, volumes), say what happened, log it.
After a kill the judge waits for the bridge to answer again before it can kill a second time — no kill loop.

Configuration (environment, or host/local.env):
  APPLEBRIDGE_CTRL_PORT         the guest's control port (9001 local, 9011 for the 2013 guest)
  APPLEBRIDGE_GUEST_SSH         unset: the emulator runs on this Mac; user@host: it runs THERE (guest_remote.py)
  APPLEBRIDGE_EMULATOR_APP      the bundle to relaunch, a path on the machine the emulator runs on
                                (for a remote guest: APPLEBRIDGE_GUEST_EMULATOR_APP, so local.env can hold both)
  APPLEBRIDGE_HANG_SILENT_S     default 300      APPLEBRIDGE_HANG_CPU   default 90      APPLEBRIDGE_HANG_INTERVAL  default 60
  APPLEBRIDGE_HANG_LOG          default /tmp/applebridge_hang_watch_<port>.log
Run:  hang_watch.py            watch forever (the LaunchAgent: install_hang_watch.sh)
      hang_watch.py --once     one observation, printed — what the judge would see right now"""
import hashlib
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guest_remote  # noqa: E402


def local_env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v
    try:
        for line in open(os.path.join(HERE, "local.env")):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return default


PORT = int(local_env("APPLEBRIDGE_CTRL_PORT", "9001"))
REMOTE = guest_remote.active()
APP = local_env("APPLEBRIDGE_GUEST_EMULATOR_APP" if REMOTE else "APPLEBRIDGE_EMULATOR_APP")
SILENT_S = float(local_env("APPLEBRIDGE_HANG_SILENT_S", "300"))
CPU_MIN = float(local_env("APPLEBRIDGE_HANG_CPU", "90"))
INTERVAL = float(local_env("APPLEBRIDGE_HANG_INTERVAL", "60"))
LOG = local_env("APPLEBRIDGE_HANG_LOG", f"/tmp/applebridge_hang_watch_{PORT}.log")
WHERE = guest_remote.TARGET if REMOTE else "this Mac"


class HangJudge:
    """observe(pid, answered, cpu, frame) -> "ok" | "booting" | "quiet" | "silent" | "kill"

    answered: True (PROCLIST shows Finder), False (no answer / no Finder), None (control port refused: no evidence)
    cpu: percent, or None if unknown;  frame: a hash of the framebuffer, or None if it cannot be read"""

    def __init__(self, silent_s=SILENT_S, cpu_min=CPU_MIN):
        self.silent_s, self.cpu_min = silent_s, cpu_min
        self.pid = None
        self.armed = False          # the bridge answered since this process started
        self.since = None           # start of the current silent, busy, frozen window
        self.frame = None

    def observe(self, pid, answered, cpu, frame, now=None):
        now = time.time() if now is None else now
        if pid != self.pid:                      # a new emulator process: wait until it has answered once
            self.pid, self.armed, self.since, self.frame = pid, False, None, None
        if pid is None:
            return "quiet"
        if answered:
            self.armed, self.since, self.frame = True, None, frame
            return "ok"
        if not self.armed:
            return "booting"
        if answered is None:                     # host server down: says nothing about the guest
            return "silent" if self.since is not None else "quiet"
        if cpu is None or cpu < self.cpu_min:    # silent but idle: not a CPU-burning hang
            self.since = None
            return "quiet"
        if frame is not None and self.frame is not None and frame != self.frame:
            self.since, self.frame = None, frame # the screen still changes: something is at work
            return "quiet"
        if frame is not None:
            self.frame = frame
        if self.since is None:
            self.since = now
        if now - self.since >= self.silent_s:
            self.armed, self.since = False, None # after a kill: wait for an answer before the next one
            return "kill"
        return "silent"


# -- the machine the emulator runs on ----------------------------------------------------------------------------------
def sh(argv, timeout=20):
    """argv on the emulator's machine -> CompletedProcess"""
    if REMOTE:
        return guest_remote.run(argv, timeout=timeout)
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def emulator_pid():
    try:
        out = sh(["pgrep", "-x", "BasiliskII"]).stdout.split()
        return int(out[0]) if out else None
    except Exception:
        return None


def emulator_cpu(pid):
    try:
        return float(sh(["ps", "-o", "%cpu=", "-p", str(pid)]).stdout.strip().replace(",", "."))
    except Exception:
        return None


def frame_hash():
    try:
        import fb_export
        pid, _ = fb_export.check()
        return hashlib.sha1(fb_export.parse_dump(fb_export.request_dump(pid))["pixels"]).hexdigest()
    except Exception:
        return None


def bridge_answers():
    """True / False / None (control port refused = host server down)"""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=5) as s:
            s.settimeout(20)
            s.sendall(b"PROCLIST\n\n")
            b = b""
            while True:
                d = s.recv(65536)
                if not d:
                    break
                b += d
                if b"Finder" in b:
                    return True
        return False
    except ConnectionRefusedError:
        return None
    except OSError:
        return False


# -- acting ------------------------------------------------------------------------------------------------------------
def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line, flush=True)
    try:
        open(LOG, "a").write(line + "\n")
    except OSError:
        pass


def say(msg):
    log(msg)
    subprocess.run(["say", "-v", "Anna", msg], capture_output=True)


def restart(pid):
    guest = "Der Gast auf dem zweiten Mac" if REMOTE else "Der lokale Gast"
    say(f"{guest} hängt seit {int(SILENT_S / 60)} Minuten bei voller Last. Ich beende Basilisk und starte neu.")
    sh(["kill", str(pid)])
    for _ in range(10):
        time.sleep(1)
        if emulator_pid() != pid:
            break
    else:
        log(f"pid {pid} survived TERM - KILL")
        sh(["kill", "-9", str(pid)])
    time.sleep(3)
    if not APP:
        say("Kein Emulatorpfad konfiguriert. Basilisk ist beendet, aber nicht neu gestartet.")
        return
    r = sh(["open", APP], timeout=30)
    if r.returncode != 0:                        # LaunchServices can answer -600 after a bundle died (2026-10-01)
        log(f"open failed ({(r.stderr or '').strip()}) - starting the binary directly")
        exe = os.path.join(APP, "Contents", "MacOS", "BasiliskII")
        if REMOTE:
            guest_remote.shell(f"nohup {exe!r} >/dev/null 2>&1 </dev/null &", timeout=30)
        else:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(5)
    boot = subprocess.run([sys.executable, os.path.join(HERE, "guest_boot.py")], capture_output=True, text=True,
                          timeout=900, env=dict(os.environ, APPLEBRIDGE_CTRL_PORT=str(PORT)))
    log("guest_boot: " + (boot.stdout.strip().splitlines() or ["(no output)"])[-1])
    back = bridge_answers()
    if back:      # the build tool's own hang guard (xavier-eval project_build, 2026-09-29) refuses every build while its
        # flag exists; on 2026-10-04 it kept refusing for 1.5 h after the guest was back. The flag belongs to the hang
        # this restart has just ended, so it goes with it (kept beside the log, not deleted).
        flag = "/tmp/applebridge_guest_hung" + ("" if PORT == 9001 else f"_{PORT}")
        if os.path.exists(flag):
            os.replace(flag, f"{LOG}.flag-{time.strftime('%Y%m%d-%H%M%S')}")
            log(f"cleared {flag} (the build tool's hang flag for the hang just ended)")
    say(f"{guest} ist wieder da." if back else f"{guest} kommt nach dem Neustart nicht zurück.")


def observe_once(judge):
    pid = emulator_pid()
    answered = bridge_answers() if pid else None
    cpu = emulator_cpu(pid) if pid and not answered else None
    frame = frame_hash() if pid and answered is False else None
    verdict = judge.observe(pid, answered, cpu, frame)
    return verdict, pid, answered, cpu, frame


def main(argv):
    judge = HangJudge()
    if "--once" in argv:
        print(observe_once(judge)[:4])
        return 0
    log(f"watching the guest on {WHERE}, control port {PORT}: silent {SILENT_S:.0f} s at >= {CPU_MIN:.0f} % CPU -> restart")
    # the first observation, in full, proves the watchdog can SEE from where it runs (launchd: SSH key, fb dump, ps)
    pid = emulator_pid(); fr = frame_hash() if pid else None
    log(f"first look: emulator pid {pid}, cpu {emulator_cpu(pid) if pid else None}, bridge answers {bridge_answers()}, "
        f"frame {'unreadable' if fr is None else 'readable'}, relaunch path {APP or 'NOT SET'}")
    while True:
        try:
            verdict, pid, answered, cpu, frame = observe_once(judge)
            if verdict in ("silent", "kill"):
                log(f"{verdict}: pid {pid} cpu {cpu} frame {'unreadable' if frame is None else frame[:8]} "
                    f"for {0 if judge.since is None else time.time() - judge.since:.0f} s")
            if verdict == "kill":
                restart(pid)
        except Exception as e:                   # the watcher itself must not die of one bad observation
            log(f"observation failed: {e!r}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
