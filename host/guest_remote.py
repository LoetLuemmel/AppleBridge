"""guest_remote.py — run the host-side guest tools on ANOTHER Mac over SSH (2026-10-01).

A guest emulated on a second machine (the 2013 MacBook) is driven through its own host-server instance for every
bridge verb. What the bridge cannot do — the real mouse (`cliclick`), the emulator window's geometry (`osascript`),
the framebuffer export (SIGUSR1 + the dump file) — has to happen on THAT machine. With

    APPLEBRIDGE_GUEST_SSH=user@host              (unset: everything stays local, nothing changes)
    APPLEBRIDGE_GUEST_TOOLS=Documents/BasiliskII/tools   (where cliclick lives there, relative to $HOME)

guest_input and fb_export send their commands through `run()` below instead of running them here. One SSH master
connection is kept open (ControlMaster), so a click costs a round trip, not a handshake.

The remote Mac needs Accessibility for sshd (System Events keystrokes, cliclick) — without it osascript answers
error 1002 — and a cliclick built for ITS macOS (Homebrew's needs the build machine's version)."""
import os
import shlex
import subprocess

TARGET = os.environ.get("APPLEBRIDGE_GUEST_SSH", "")
TOOLS = os.environ.get("APPLEBRIDGE_GUEST_TOOLS", "Documents/BasiliskII/tools")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ControlMaster=auto",
       "-o", "ControlPath=/tmp/ab-guest-ssh-%r@%h-%p", "-o", "ControlPersist=600"]


def active():
    return bool(TARGET)


def remote_command(argv):
    """argv as ONE remote shell command; cliclick resolves to the copy in TOOLS (unquoted, so $HOME expands)"""
    exe = f"$HOME/{TOOLS}/cliclick" if argv[0] == "cliclick" else shlex.quote(argv[0])
    return " ".join([exe] + [shlex.quote(str(a)) for a in argv[1:]])


def run(argv, timeout=None, text=True, input=None):
    """subprocess.run of argv on the remote Mac -> CompletedProcess (same fields as a local run)"""
    return subprocess.run(SSH + [TARGET, remote_command(argv)], capture_output=True, text=text,
                          timeout=timeout, input=input)


def shell(script, timeout=None, text=True):
    """a remote shell script verbatim (the caller quotes) -> CompletedProcess"""
    return subprocess.run(SSH + [TARGET, script], capture_output=True, text=text, timeout=timeout)


def fetch(remote_path, local_path, timeout=60):
    """copy one file from the remote Mac (scp over the same master connection)"""
    opts = [o for o in SSH[1:] if o != "-o"]
    scp = ["scp", "-q"] + sum((["-o", o] for o in opts), []) + [f"{TARGET}:{remote_path}", local_path]
    p = subprocess.run(scp, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise OSError(f"scp {remote_path}: {(p.stderr or p.stdout).strip()}")
    return local_path
