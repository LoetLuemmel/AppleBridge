#!/usr/bin/python3
"""mon_session.py - drive cxmon inside a debug build of Basilisk II from the host.

The debugger lives in the EMULATOR, not in the guest: when cxmon holds the
68K, the whole guest stops (the AppleBridge daemon with it), but the monitor
keeps answering because it runs in the Basilisk process. That is why this
module talks to the emulator directly - stdin through a FIFO, stdout into a
file - and never over the bridge. Measured 2026-11-28 on System 7.6.1: Ctrl-C
halts the CPU (Ticks frozen), `x` resumes it, break points on a trap's
dispatch-table address stop with the caller's registers.

Needs the mon-enabled build (macemu branch `cxmon`, x86_64): cxmon compiled
in, and BASILISK_MON_CONTINUE=1 honoured so leaving the monitor resumes the
guest instead of quitting it.

Safety rules this module enforces, each learned or inherited:
- Never two emulators: start refuses while any BasiliskII runs (they would
  fight over the disk image and the bridge port).
- The session runs on an APFS CLONE of the guest disk, never on the image
  itself: stopping a debug session means killing the emulator (it ignores
  SIGTERM), which is harmless only for a throwaway copy.
- The local hang/dialog watchdogs find the emulator by name and would treat
  a guest halted in the monitor as theirs; they are paused for the session
  and restored by stop.
- A write to the FIFO of a dead emulator blocks forever, so every write opens
  it O_NONBLOCK first (ENXIO = no reader = emulator gone).
- cxmon can run host shell commands and touch host files (`\\`, ls, rm, cp,
  mv, cd, `[`, `]`, o); those commands are refused - the tool debugs the guest,
  it does not open a shell on the host.

State lives in a session directory (default ~/.applebridge/mon), so a session
outlives the MCP server that started it.

CLI (for a human, or a quick check):
    mon_session.py start [disk] [--app App.bin [--keep-items]]  |  break  |  where  |  cmd "<mon command>"  |  continue
    mon_session.py wait [secs]   |  trap <name> |  status  |  stop
"""

import errno
import json
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


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


DEFAULT_APP = os.path.expanduser("~/Documents/Dev/macemu-mon/build-mon/Release/BasiliskII.app")
MON_MARKER = b"Removed all break points!"         # a cxmon string: present only in a mon build
PROMPT = re.compile(r"\[[0-9a-f]+\]-> $")
BREAK_HIT = re.compile(r"Stopped at break point address: ([0-9a-f]+)\. Last PC: ([0-9a-f]+)")
# Basilisk's sigsegv_dump_state(): a 68K write the host cannot map (a wild pointer)
# prints this and the 68K registers, then enters cxmon - and QUITS when cxmon is
# left. Without mon compiled in, the process simply dies (WurzelRegler, 2026-10-04).
SEGV = re.compile(r"Caught SIGSEGV at address (0x[0-9a-f]+)(?: \[IP=(0x[0-9a-f]+)\])?")
WATCH_LABELS = ("de.390er.applebridge-hangwatch", "de.390er.applebridge-dialogwatch")

# cxmon commands that reach the HOST (shell, file system, output redirection).
HOST_COMMANDS = {"\\", "ls", "rm", "cp", "mv", "cd", "[", "]", "o", "bs", "bl"}

# 68K trap dispatch tables in low memory (System 7): OS traps at $400 (256
# entries, index = word & $FF), Toolbox traps at $E00 (1024 entries, index =
# word & $3FF). Bit 11 of the trap word selects the Toolbox table.
OS_TABLE, TB_TABLE = 0x400, 0xE00
# Basilisk maps the ROM right after RAM ($07C00000 on a 124 MB guest); 68K Mac ROMs are at most 1 MB,
# so 2 MB is a safe bound for "this address is ROM code".
ROM_SPAN = 0x200000


class MonError(Exception):
    pass


def trap_table_address(word):
    """Low-memory address of the dispatch-table entry for an A-line word."""
    if word & 0xF000 != 0xA000:
        raise MonError(f"0x{word:04X} is not an A-line trap word")
    if word & 0x0800:
        return TB_TABLE + (word & 0x3FF) * 4
    return OS_TABLE + (word & 0xFF) * 4


def load_traps(path=os.path.join(HERE, "mon_traps.json")):
    with open(path) as f:
        return {k: int(v, 16) for k, v in json.load(f)["traps"].items()}


def resolve_trap(name, traps=None):
    """'WaitNextEvent', '_WaitNextEvent', 'A860' or '0xA860' -> (name, word)."""
    s = name.strip()
    m = re.fullmatch(r"(?:0x|\$)?([Aa][0-9A-Fa-f]{3})", s)
    if m:
        return s, int(m.group(1), 16)
    traps = load_traps() if traps is None else traps
    key = s.lstrip("_")
    if key in traps:
        return key, traps[key]
    low = {k.lower(): k for k in traps}
    if key.lower() in low:
        k = low[key.lower()]
        return k, traps[k]
    close = sorted(k for k in traps if key.lower() in k.lower())[:8]
    raise MonError(f"unknown trap {name!r}" + (f"; similar: {', '.join(close)}" if close else ""))


# ---- MacsBug names ----------------------------------------------------------
# A compiler that keeps MacsBug symbols (MPW, THINK C with the option, Retro68
# by default) ends each routine with its name: the final RTS ($4E75), RTD
# ($4E74 nnnn) or JMP (A0) ($4ED0), then a length byte $80|n, n name bytes,
# a pad byte to an even address, and a word giving the length of constant
# data that follows. The next routine starts after that.
_NAME_CHARS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_%.$<>:~")


def macsbug_names(mem, base):
    """[(routine_end, block_end, name)] for every MacsBug name in `mem`
    (bytes read at guest address `base`). routine_end = address of the name's
    length byte; block_end = where the next routine starts."""
    found = []
    i = 0
    while i + 4 <= len(mem):
        w = (mem[i] << 8) | mem[i + 1]
        if w in (0x4E75, 0x4ED0):
            j = i + 2
        elif w == 0x4E74:
            j = i + 4
        else:
            i += 2
            continue
        if j < len(mem) and 0x81 <= mem[j] <= 0x9F:
            n = mem[j] & 0x1F
            name = mem[j + 1:j + 1 + n]
            if len(name) == n and all(c in _NAME_CHARS for c in name):
                k = j + 1 + n
                k += k & 1                             # pad to even
                const = (mem[k] << 8 | mem[k + 1]) if k + 1 < len(mem) else 0
                found.append((base + j, base + k + 2 + const, name.decode("latin-1")))
                i = k + 2
                continue
        i += 2
    return found


def name_for(addr, mem, base):
    """The routine containing `addr`, as (name, start, offset), from a memory
    window that spans the address. start is None when the window does not
    reach back to the previous routine's name."""
    names = macsbug_names(mem, base)
    after = [n for n in names if n[0] > addr]
    if not after:
        return None
    end, _, name = after[0]
    before = [n for n in names if n[1] <= addr]
    start = before[-1][1] if before else None
    return name, start, (addr - start) if start is not None else None


_DUMP_LINE = re.compile(r"^([0-9a-f]+):((?:\s+[0-9a-f]{8}){1,4})", re.M)
_REG = re.compile(r"\b([DA][0-7]): ([0-9a-f]{8})")
_PC_LINE = re.compile(r"^([0-9a-f]{8}): [0-9a-f]{4}", re.M)


def _pid_alive(pid):
    if not pid:
        return False
    try:
        # A dead child of this process stays a zombie until reaped, and a
        # zombie still answers kill(pid, 0). The MCP server is the emulator's
        # parent, so reap first.
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass                                           # not our child: kill(0) is accurate
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _running_emulators():
    r = subprocess.run(["pgrep", "-x", "BasiliskII"], capture_output=True, text=True)
    return [int(p) for p in r.stdout.split()]


def boot_disk_from_prefs(prefs=os.path.expanduser("~/.basilisk_ii_prefs")):
    for line in open(prefs):
        if line.startswith("disk "):
            return line[5:].strip().lstrip("*")
    raise MonError(f"no 'disk' line in {prefs}")


class Session:
    """One debug session. Every method re-reads state from disk, so separate
    processes (an MCP server restart, the CLI) see the same session."""

    def __init__(self, root=None, app=None, launcher=None, watchers=None,
                 cloner=None, emulators=None, check_binary=True, installer=None):
        self.root = root or local_env("APPLEBRIDGE_MON_DIR", os.path.expanduser("~/.applebridge/mon"))
        self.app = app or local_env("APPLEBRIDGE_MON_APP", DEFAULT_APP)
        self.fifo = os.path.join(self.root, "mon.in")
        self.out = os.path.join(self.root, "mon.out")
        self.state_file = os.path.join(self.root, "state.json")
        # Seams for the tests: how to launch, pause watchdogs, clone a disk, find emulators.
        self._launcher = launcher
        self._watchers = watchers
        self._cloner = cloner
        self._emulators = emulators or _running_emulators
        self._installer = installer
        self._check_binary = check_binary

    # ---- state -----------------------------------------------------------
    def state(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, st):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, self.state_file)

    def _require_session(self):
        st = self.state()
        if not st:
            raise MonError("no debug session - start one with action 'start'")
        if not _pid_alive(st.get("emu_pid")):
            raise MonError("the debug emulator is no longer running (it quit or crashed); "
                           "'stop' cleans up, then 'start' again")
        return self._check_crash(st)

    # ---- output ----------------------------------------------------------
    def _size(self):
        try:
            return os.path.getsize(self.out)
        except OSError:
            return 0

    def _read_from(self, offset):
        with open(self.out, "rb") as f:
            f.seek(offset)
            return f.read().decode("latin-1")

    def _wait_prompt(self, offset, timeout):
        """Text written since `offset`, once it ends at a monitor prompt."""
        deadline = time.monotonic() + timeout
        text = ""
        while time.monotonic() < deadline:
            text = self._read_from(offset)
            if PROMPT.search(text):
                time.sleep(0.05)                       # let a trailing write land
                text2 = self._read_from(offset)
                if text2 == text:
                    return text, True
                text = text2
                continue
            time.sleep(0.05)
        return text, False

    @staticmethod
    def _clean(text):
        """Drop the prompt and the emulator's own chatter from monitor output."""
        lines = [ln for ln in text.splitlines() if "DKIOCCDREADTOC" not in ln]
        text = "\n".join(lines)
        return PROMPT.sub("", text).replace("\r", "").rstrip()

    # ---- the FIFO ----------------------------------------------------------
    def _send(self, line):
        if "\n" in line or "\r" in line:
            raise MonError("one monitor command per call (no line breaks)")
        try:
            fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            if e.errno == errno.ENXIO:
                raise MonError("the debug emulator is not reading its input (gone?)") from None
            raise
        try:
            os.write(fd, (line + "\n").encode("latin-1"))
        finally:
            os.close(fd)

    # ---- lifecycle ---------------------------------------------------------
    def start(self, disk=None, install=None, isolate=True, bridge=False):
        st = self.state()
        if st and _pid_alive(st.get("emu_pid")):
            raise MonError(f"a debug session is already running (pid {st['emu_pid']})")
        if st:
            self.stop()                                # a dead session: clean it up first
        running = self._emulators()
        if running:
            raise MonError(f"an emulator is already running (pid {', '.join(map(str, running))}). "
                           "Never two at once: shut the guest down cleanly (mac_shutdown) first - "
                           "the debug session boots a clone of its disk.")
        exe = os.path.join(self.app, "Contents", "MacOS", "BasiliskII")
        if self._check_binary:
            try:
                with open(exe, "rb") as f:
                    if MON_MARKER not in f.read():
                        raise MonError(f"{exe} has no cxmon compiled in (build macemu branch 'cxmon')")
            except OSError as e:
                raise MonError(f"debug emulator not found: {e} (set APPLEBRIDGE_MON_APP)") from None

        os.makedirs(self.root, exist_ok=True)
        disk = disk or boot_disk_from_prefs()
        clone = os.path.join(self.root, "guest_clone.dmg")
        if os.path.exists(clone):
            os.remove(clone)
        (self._cloner or self._clone)(disk, clone)
        installed = []
        for app in install or []:
            # Into the CLONE's Startup Items, so the program under test launches
            # by itself at boot - no bridge, no host mouse needed.
            installed.append((self._installer or self._install_startup)(clone, app, isolate))

        prefs = os.path.join(self.root, "prefs")
        self._write_prefs(prefs, clone, bridge=bridge)
        ram_size = 0
        for line in open(prefs):
            if line.startswith("ramsize "):
                ram_size = int(line.split()[1])

        if os.path.exists(self.fifo):
            os.remove(self.fifo)
        os.mkfifo(self.fifo)
        open(self.out, "wb").close()
        paused = (self._watchers or self._pause_watchers)()

        # Opening one end of a FIFO blocks until the other end exists, so open it
        # read-write first (never blocks) and derive both ends from that.
        anchor = os.open(self.fifo, os.O_RDWR)
        rd = os.open(self.fifo, os.O_RDONLY)
        wr = os.open(self.fifo, os.O_WRONLY)
        try:
            # A writer that never closes, so the monitor never reads EOF; detached
            # so the session outlives whoever started it.
            holder = subprocess.Popen(["sleep", "604800"], stdout=wr,
                                      stdin=subprocess.DEVNULL, start_new_session=True)
            env = dict(os.environ, BASILISK_MON_CONTINUE="1")
            launch = self._launcher or (lambda: [exe, "--config", prefs])
            with open(self.out, "wb") as out:
                emu = subprocess.Popen(launch(), stdin=rd, stdout=out, stderr=subprocess.STDOUT,
                                       env=env, start_new_session=True)
        finally:
            for fd in (anchor, rd, wr):
                os.close(fd)
        st = {"emu_pid": emu.pid, "holder_pid": holder.pid, "disk": disk, "clone": clone,
              "prefs": prefs, "app": self.app, "paused_watchers": paused,
              "mode": "running", "started": time.strftime("%Y-%m-%d %H:%M:%S"),
              "installed": installed, "ram_size": ram_size, "bridge": bridge}
        self._save(st)
        return st

    @staticmethod
    def _clone(src, dst):
        # APFS clone: instant, and the guest's own image is never opened.
        r = subprocess.run(["cp", "-c", src, dst], capture_output=True, text=True)
        if r.returncode != 0:
            raise MonError(f"could not clone {src}: {r.stderr.strip()}")

    @staticmethod
    def _install_startup(clone, macbinary, isolate=True):
        """Copy a MacBinary application into the clone's Startup Items (hfsutils).

        With `isolate` (the default) every other Startup Item is moved into a
        sibling folder first: otherwise the clone's own items (the AppleBridge
        watchdog, which starts the daemon and ToolServer) launch too, push the
        program under test into the background and change what it does - the
        blind test of 2026-11-28 took a backgrounded app for a hang. The clone
        is not mounted anywhere else yet, so this is safe."""
        if not os.path.exists(macbinary):
            raise MonError(f"no such file: {macbinary}")

        def hfs(*args):
            return subprocess.run(list(args), capture_output=True, text=True)

        def listing(path):                             # one entry per line: names contain spaces
            r = hfs("hls", "-1", path)
            return [ln for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else None

        r = hfs("hmount", clone)
        if r.returncode != 0:
            raise MonError(f"hmount failed on the clone: {r.stderr.strip()} (is hfsutils installed?)")
        moved = []
        try:
            dest = ":System Folder:Startup Items:"
            items = listing(dest)
            if items is None:
                raise MonError("the clone has no ':System Folder:Startup Items:'")
            if isolate and items:
                # A folder of our own: the guest may already have "Startup Items (Disabled)"
                # holding an older item of the same name (measured on the dev guest), and
                # HFS names are case-insensitive.
                aside = ":System Folder:Startup Items (mac_debug):"
                if listing(aside) is None:
                    hfs("hmkdir", aside)
                taken = {n.lower() for n in (listing(aside) or [])}
                for name in items:
                    target, k = name, 2
                    while target.lower() in taken:
                        target = f"{name[:27]} {k}"
                        k += 1
                    r = hfs("hrename", dest + name, aside + (target if target != name else ""))
                    if r.returncode != 0:
                        raise MonError(f"could not move {name!r} aside: {r.stderr.strip()}")
                    taken.add(target.lower())
                    moved.append(name)
            r = hfs("hcopy", "-m", macbinary, dest)
            if r.returncode != 0:
                raise MonError(f"hcopy failed: {r.stderr.strip()}")
            now = listing(dest)
        finally:
            hfs("humount", clone)
        return {"file": os.path.basename(macbinary), "into": dest, "startup_items": now,
                "moved_aside": moved}

    @staticmethod
    def _write_prefs(path, clone, base=os.path.expanduser("~/.basilisk_ii_prefs"), bridge=False):
        """bridge=True keeps the guest's own `ether` line, so its daemon reaches the
        host server and the ordinary bridge tools work in the debug session. With
        etherhelper that needs the debug bundle's helper to be the setuid one (a
        symlink to the production bundle's helper; the fork's runtool patch then
        starts it without the auth dialog)."""
        keep = []
        ether = None
        try:
            for line in open(base):
                key = line.split(" ", 1)[0]
                if key == "ether":
                    ether = line.rstrip("\n")
                if key in ("disk", "ether", "jit", "cdrom", "ignoresegv"):
                    continue
                keep.append(line.rstrip("\n"))
        except OSError:
            pass
        # jit false: break points are tested on the interpreter's path only.
        # ether slirp: no helper, no password; the bridge is not the channel here.
        # ignoresegv false: with it true, Basilisk tries to SKIP a faulting write; when the
        # skip fails it gives up without calling the dumper and the process dies (measured
        # 2026-11-28 on SlotKeeper: "No handler for exception_type = 1"). False sends a wild
        # write into cxmon instead.
        keep += [f"disk {clone}", (ether if bridge and ether else "ether slirp"), "jit false", "ignoresegv false"]
        with open(path, "w") as f:
            f.write("\n".join(keep) + "\n")

    @staticmethod
    def _pause_watchers():
        paused = []
        uid = os.getuid()
        for label in WATCH_LABELS:
            plist = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
            loaded = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                                    capture_output=True).returncode == 0
            if loaded and os.path.exists(plist):
                subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
                paused.append(plist)
        return paused

    @staticmethod
    def _restore_watchers(plists):
        uid = os.getuid()
        failed = []
        for plist in plists:
            r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", plist], capture_output=True, text=True)
            if r.returncode != 0 and "already" not in (r.stderr or "").lower():
                failed.append(f"{os.path.basename(plist)}: {r.stderr.strip()}")
        return failed

    def stop(self, keep_clone=False):
        st = self.state()
        if not st:
            return {"stopped": False, "note": "no session"}
        for key in ("emu_pid", "holder_pid"):
            pid = st.get(key)
            if _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 5
        while _pid_alive(st.get("emu_pid")) and time.monotonic() < deadline:
            time.sleep(0.2)
        killed = False
        if _pid_alive(st.get("emu_pid")):
            # Basilisk ignores SIGTERM. This is a CLONE, so a hard stop costs nothing.
            os.kill(st["emu_pid"], signal.SIGKILL)
            killed = True
        restore_failed = [] if self._watchers else self._restore_watchers(st.get("paused_watchers", []))
        if not keep_clone and st.get("clone") and os.path.exists(st["clone"]):
            os.remove(st["clone"])
        for p in (self.fifo, self.state_file):
            if os.path.exists(p):
                os.remove(p)
        return {"stopped": True, "hard_killed_clone": killed,
                "watchers_restored": [os.path.basename(p) for p in st.get("paused_watchers", [])],
                "watcher_restore_failed": restore_failed, "clone_kept": keep_clone}

    # ---- debugging -----------------------------------------------------------
    def break_in(self, timeout=10):
        st = self._require_session()
        if st["mode"] in ("stopped", "crashed"):
            return {"mode": st["mode"], "crash": st.get("crash"),
                    "output": "(already stopped in the monitor)"}
        # A break point may have stopped the guest since 'continue': the monitor
        # is then at its prompt, and a SIGINT would only set its abort flag.
        if BREAK_HIT.search(self._read_from(st.get("resume_offset", self._size()))):
            r = self.wait(timeout)
            r["note"] = "a break point had already stopped the guest"
            return r
        off = self._size()
        os.kill(st["emu_pid"], signal.SIGINT)
        text, ok = self._wait_prompt(off, timeout)
        if not ok:
            raise MonError(f"no monitor prompt within {timeout}s after SIGINT; output so far: {text[-500:]!r}")
        st["mode"] = "stopped"
        self._save(st)
        # The emulator's stdout is block-buffered into the file, so its start-up
        # banner can arrive with the first register dump; the dump starts at D0.
        i = text.find("D0: ")
        return {"mode": "stopped", "output": self._clean(text[i:] if i > 0 else text)}

    # Commands whose first operands are guest addresses the monitor will READ.
    _READS = {"m", "i", "b", "d68", "d", "d65", "d80", "d86", "d8086", "d8664", "h", "c", "t", "y", "yb", "yh", "yw", "f", ":"}

    def _check_addresses(self, cmd, st):
        """Refuse a monitor read outside guest RAM and ROM. cxmon reads guest
        memory straight through to the host, so an unmapped address (a wild
        pointer is exactly what one inspects after a crash) faults inside the
        monitor and takes the emulator down - measured 2026-11-28."""
        parts = cmd.split()
        if parts[0] not in self._READS:
            return
        ram = st.get("ram_size") or 0
        rom = st.get("rom_base")
        for tok in parts[1:3]:
            if not tok.startswith("$"):
                continue
            try:
                a = int(tok[1:], 16)
            except ValueError:
                continue
            in_ram = ram and 0 <= a <= ram
            in_rom = rom is not None and rom <= a <= rom + ROM_SPAN
            if not (in_ram or in_rom or not ram):
                raise MonError(f"${a:X} is outside guest RAM ($0-${ram:X})"
                               + (f" and ROM (${rom:X}+)" if rom is not None else "")
                               + "; the monitor would fault reading it and kill the emulator")


    def command(self, cmd, timeout=10):
        st = self._require_session()
        word = cmd.strip().split(" ", 1)[0] if cmd.strip() else ""
        if not word:
            raise MonError("empty command")
        if word == "x":
            raise MonError("use action 'continue' to resume the guest")
        if word in HOST_COMMANDS or word.startswith("\\"):
            raise MonError(f"monitor command {word!r} acts on the HOST (shell/files/output) and is refused")
        if st["mode"] not in ("stopped", "crashed"):
            raise MonError("the guest is running - break in first (action 'break'), or wait for a break point")
        self._check_addresses(cmd.strip(), st)
        off = self._size()
        self._send(cmd)
        text, ok = self._wait_prompt(off, timeout)
        if not ok:
            raise MonError(f"no prompt within {timeout}s; partial output: {text[-500:]!r}")
        return {"mode": st["mode"], "output": self._clean(text)}

    def resume(self):
        st = self._require_session()
        if st["mode"] == "crashed":
            raise MonError("the guest crashed (wild write); leaving the monitor would quit the emulator. "
                           "Inspect with 'where'/'cmd', then 'stop'")
        if st["mode"] != "stopped":
            return {"mode": st["mode"], "output": "(guest already running)"}
        st["resume_offset"] = self._size()
        self._send("x")
        st["mode"] = "running"
        self._save(st)
        return {"mode": "running"}

    def _crashed(self, st, off, m, deadline=None):
        """The guest made a write the host could not map; cxmon has the machine."""
        text, _ = self._wait_prompt(off, max(1.0, (deadline or time.monotonic() + 5) - time.monotonic()))
        st["mode"] = "crashed"
        st["crash"] = {"host_fault_address": m.group(1), "host_ip": m.group(2)}
        self._save(st)
        i = text.find("D0: ")
        return {"mode": "crashed", "hit": False, "crash": st["crash"],
                "output": self._clean(text[i:] if i >= 0 else text),
                "next": "inspect with 'where' and 'cmd' - the emulator QUITS when the monitor is left, "
                        "so 'continue' is refused; 'stop' ends the session"}

    def _check_crash(self, st):
        """A crash may have happened while nobody was waiting."""
        if st.get("mode") == "running":
            off = st.get("resume_offset", 0)
            m = SEGV.search(self._read_from(off))
            if m:
                self._crashed(st, off, m)
                return self.state()
        return st

    def wait(self, timeout=30):
        """Block until a break point stops the guest, or the timeout passes."""
        st = self._require_session()
        if st["mode"] == "crashed":
            return {"mode": "crashed", "hit": False, "crash": st.get("crash"),
                    "output": "(the guest crashed earlier; see 'where')"}
        if st["mode"] == "stopped":
            return {"mode": "stopped", "hit": False, "output": "(already stopped)"}
        off = st.get("resume_offset", self._size())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self._read_from(off)
            c = SEGV.search(text)
            if c:
                return self._crashed(st, off, c, deadline)
            m = BREAK_HIT.search(text)
            if m:
                text, _ = self._wait_prompt(off, max(1.0, deadline - time.monotonic()))
                st["mode"] = "stopped"
                self._save(st)
                return {"mode": "stopped", "hit": True, "address": "$" + m.group(1).lstrip("0").upper(),
                        "last_pc": "$" + m.group(2).lstrip("0").upper(), "output": self._clean(text)}
            if not _pid_alive(st["emu_pid"]):
                raise MonError("the debug emulator exited while running")
            time.sleep(0.1)
        return {"mode": "running", "hit": False, "output": f"no break point hit within {timeout}s"}

    def break_on_trap(self, name, timeout=10):
        """Set a break point on the address the dispatcher jumps to for a trap.
        That is the first patch if the trap is patched - which is what a call
        actually reaches."""
        tname, word = resolve_trap(name)
        table = trap_table_address(word)
        r = self.command(f"m ${table:x} ${table + 4:x}", timeout)
        m = re.search(r"^[0-9a-f]+:\s+([0-9a-f]{8})", r["output"], re.M)
        if not m:
            raise MonError(f"could not read the dispatch table at ${table:X}: {r['output']!r}")
        target = int(m.group(1), 16)
        r2 = self.command(f"ba ${target:x}", timeout)
        return {"mode": "stopped", "trap": tname, "trap_word": f"0x{word:04X}",
                "table_entry": f"${table:X}", "break_address": f"${target:X}",
                "output": r2["output"],
                "next": "action 'continue', then 'wait' for the hit"}

    # ---- reading the guest ---------------------------------------------------
    def read_mem(self, start, length):
        """Guest memory as bytes, via the monitor's hex dump (16 bytes a line)."""
        start &= ~1
        out = self.command(f"m ${start:x} ${start + length:x}", timeout=20)["output"]
        data = bytearray()
        expect = start
        for m in _DUMP_LINE.finditer(out):
            a = int(m.group(1), 16)
            if a != expect:
                break
            for word in m.group(2).split():
                data += int(word, 16).to_bytes(4, "big")
            expect += 16
        return bytes(data[:length])

    def registers(self):
        out = self.command("regs")["output"]
        regs = {k: int(v, 16) for k, v in _REG.findall(out)}
        pcs = _PC_LINE.findall(out)
        if pcs:
            regs["PC"] = int(pcs[-1], 16)
        return regs

    def _rom_base(self):
        st = self.state()
        if st.get("rom_base") is None:
            st["rom_base"] = int.from_bytes(self.read_mem(0x2AE, 4), "big")
            self._save(st)
        return st["rom_base"]

    def _describe(self, addr, rom_base, window=4096):
        if rom_base and rom_base <= addr < rom_base + ROM_SPAN:
            return {"address": f"${addr:X}", "where": "ROM"}
        ram = self.state().get("ram_size") or 0
        if ram and not (0 <= addr < ram):
            return {"address": f"${addr:X}", "where": "outside RAM and ROM (a wild address)"}
        lo = max(0, addr - window)
        hi = min(addr + window, ram) if ram else addr + window
        hit = name_for(addr, self.read_mem(lo, hi - lo), lo)
        if not hit:
            return {"address": f"${addr:X}", "where": "RAM, no MacsBug name within 4 KB (system code or a stripped binary)"}
        name, start, off = hit
        d = {"address": f"${addr:X}", "routine": name}
        if start is not None:
            d["routine_start"] = f"${start:X}"
            d["offset"] = f"+${off:X}"
        return d

    def where(self, depth=12):
        """Current routine and a backtrace along the A6 frame chain, each
        address named from the MacsBug names compiled into the code. The first
        frames may belong to a trap patch or ROM code the program called."""
        st = self._require_session()
        if st.get("mode") not in ("stopped", "crashed"):
            raise MonError("the guest is running - break in first")
        regs = self.registers()
        rom = self._rom_base()
        frames = [dict(self._describe(regs["PC"], rom), frame="PC")] if "PC" in regs else []
        a6 = regs.get("A6", 0)
        seen = set()
        for _ in range(depth):
            if not a6 or a6 & 1 or a6 in seen or (rom and a6 >= rom):
                break
            seen.add(a6)
            try:
                raw = self.read_mem(a6, 8)
            except MonError:
                frames.append({"frame": f"A6=${a6:X}", "where": "A6 points outside RAM - chain ends here"})
                break
            if len(raw) < 8:
                break
            saved, ret = int.from_bytes(raw[:4], "big"), int.from_bytes(raw[4:], "big")
            if not ret:
                break
            frames.append(dict(self._describe(ret, rom), frame=f"A6=${a6:X}", kind="return address"))
            if saved <= a6:                            # frames grow towards higher addresses
                break
            a6 = saved
        app = self.read_mem(0x910, 32)
        cur = app[1:1 + app[0]].decode("mac_roman") if app and app[0] < 32 else "?"   # "Test Classic.π"
        return {"mode": st["mode"], "crash": st.get("crash"), "pc": f"${regs.get('PC', 0):X}", "a6": f"${regs.get('A6', 0):X}",
                "a7": f"${regs.get('A7', 0):X}", "rom_base": f"${rom:X}", "cur_ap_name": cur,
                "frames": frames,
                "note": "frames follow A6; a routine built without LINK A6 does not appear. "
                        "cur_ap_name is the last process switched in, not necessarily the code at PC."}

    def status(self):
        st = self.state()
        if not st:
            return {"session": False}
        alive = _pid_alive(st.get("emu_pid"))
        if alive:
            st = self._check_crash(st)
        tail = self._clean(self._read_from(max(0, self._size() - 800)))
        return {"session": True, "alive": alive, "mode": st.get("mode") if alive else "dead",
                "crash": st.get("crash"),
                "pid": st.get("emu_pid"), "disk": st.get("disk"), "clone": st.get("clone"),
                "started": st.get("started"),
                "paused_watchers": [os.path.basename(p) for p in st.get("paused_watchers", [])],
                "output_tail": tail}


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    s, a = Session(), argv[1]
    try:
        if a == "start":
            rest = argv[2:]
            app = None
            if "--app" in rest:
                i = rest.index("--app")
                app = rest[i + 1]
                del rest[i:i + 2]
            keep = "--keep-items" in rest
            rest = [x for x in rest if x != "--keep-items"]
            bridge = "--bridge" in rest
            rest = [x for x in rest if x != "--bridge"]
            r = s.start(rest[0] if rest else None, install=[app] if app else None, isolate=not keep, bridge=bridge)
        elif a == "break":
            r = s.break_in()
        elif a == "cmd":
            r = s.command(argv[2])
        elif a == "continue":
            r = s.resume()
        elif a == "wait":
            r = s.wait(float(argv[2]) if len(argv) > 2 else 30)
        elif a == "trap":
            r = s.break_on_trap(argv[2])
        elif a == "where":
            r = s.where()
        elif a == "status":
            r = s.status()
        elif a == "stop":
            r = s.stop()
        else:
            raise SystemExit(__doc__)
    except MonError as e:
        print(f"error: {e}")
        return 1
    out = r.pop("output", None) if isinstance(r, dict) else None
    print(json.dumps(r, indent=1))
    if out:
        print("--- monitor output ---")
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
