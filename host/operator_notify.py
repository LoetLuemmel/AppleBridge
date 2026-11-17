#!/usr/bin/env python3
"""operator_notify.py - tell the operator, on whatever channel they chose, that a guest needs attention (2026-11-17).

Why: on the night of 2026-11-16 Basilisk II on a guest crashed, was relaunched, and then failed every build until
morning; spoken alerts (say) only help someone sitting next to the Mac. Used by hang_watch.py (crash, hang kill, relaunch
result) and dialog_watch.py (system-error bomb, a dialog that waits for a person).

AppleBridge ships no channel of its own - only a hook. APPLEBRIDGE_NOTIFY_CMD is a shell command that receives the
message text on stdin (one line, "AppleBridge <machine>: <what happened>") and exits 0 on success. Unset = nothing is
sent; the watchers still log and speak as before. Examples (put the line in host/local.env, which is not committed):
  APPLEBRIDGE_NOTIFY_CMD=osascript -e 'display notification (do shell script "cat") with title "AppleBridge"'
  APPLEBRIDGE_NOTIFY_CMD=curl -s -d @- https://ntfy.sh/<your-topic>
  APPLEBRIDGE_NOTIFY_CMD=/path/to/your/own/script.sh      (an iMessage gateway, mail, a chat webhook ...)
Secrets (tokens, phone numbers) belong in that script or in local.env, never in the repo or the message.

  APPLEBRIDGE_GUEST_NAME   the machine's name in the message (default: the guest table row for APPLEBRIDGE_CTRL_PORT,
                           else "port <n>")
Test:  APPLEBRIDGE_CTRL_PORT=9011 /usr/bin/python3 operator_notify.py "test message"
stdlib only."""
import os
import subprocess
import sys

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


def machine_name():
    port = local_env("APPLEBRIDGE_CTRL_PORT", "9001")
    name = local_env("APPLEBRIDGE_GUEST_NAME")
    if name:
        return name
    try:                                              # the guest table names every row (guests.conf, D-028)
        sys.path.insert(0, HERE); import guests
        for g in guests.load() or []:
            if str(g.ctrl_port) == str(port):
                return g.name
    except Exception:
        pass
    return f"port {port}"


def message_text(msg, name=None):
    return f"AppleBridge {name or machine_name()}: {msg}"


def notify(msg, log=print, name=None, run=subprocess.run):
    """hand the message to APPLEBRIDGE_NOTIFY_CMD -> True if it exited 0. Never raises; the log names the text only."""
    cmd = local_env("APPLEBRIDGE_NOTIFY_CMD")
    text = message_text(msg, name)
    if not cmd:
        log(f"notify: no APPLEBRIDGE_NOTIFY_CMD - not sent: {text}")
        return False
    try:
        r = run(["/bin/sh", "-c", cmd], input=text + "\n", capture_output=True, text=True, timeout=60)
        ok = r.returncode == 0
        log(f"notify {'sent' if ok else 'FAILED (exit ' + str(r.returncode) + ')'}: {text}")
        return ok
    except Exception as e:
        log(f"notify FAILED ({type(e).__name__}): {text}")
        return False


if __name__ == "__main__":
    sys.exit(0 if notify(" ".join(sys.argv[1:]) or "test message from operator_notify") else 1)
