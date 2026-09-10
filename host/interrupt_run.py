#!/usr/bin/env python3
"""interrupt_run.py — send the classic Macintosh interrupt (Command-period) to the front guest app on Basilisk II (2026-09-10).

Pit: "ein Modul, das bei Ctrl+. den Run auf dem BAII unterbricht." The classic abort gesture is Command-period; this posts it
into the guest event queue over the control port (:9001), reaching whatever app is frontmost.

    interrupt_run.py            # Command-period once
    interrupt_run.py --twice    # Command-period twice (some apps poll it late)
    interrupt_run.py --quit     # Command-period, then Command-Q as a fallback

LIMIT (honest): a synthetic key reaches an app only when it services the event queue (WaitNextEvent). An app in a hard spin
(a Button() loop, a tracking loop, an infinite loop with no yield) will NOT see it — no synthetic input can reach such a loop
(the same rule as menus/modal dialogs). For that case the clean stop is still mac_shutdown, never a hard kill of BasiliskII.
"""
import socket, sys, time

CTRL = ("127.0.0.1", 9001)
CMD_PERIOD = "KEY:46:47:256"   # char '.' (46), virtual keycode 47, cmdKey (256)
CMD_Q = "KEY:113:12:256"       # char 'q' (113), keycode 12, cmdKey — Ablage/Beenden fallback

def send(verb, timeout=5.0):
    with socket.create_connection(CTRL, timeout=timeout) as s:
        s.sendall((verb + "\n\n").encode()); s.settimeout(timeout)
        try: return s.recv(4096).decode("latin-1", "replace")
        except socket.timeout: return ""

def interrupt(twice=False, quit_after=False):
    r = send(CMD_PERIOD); time.sleep(0.4)
    if twice: send(CMD_PERIOD); time.sleep(0.4)
    if quit_after: time.sleep(0.6); send(CMD_Q)
    return r

if __name__ == "__main__":
    a = sys.argv[1:]
    out = interrupt(twice="--twice" in a, quit_after="--quit" in a)
    print("Command-period sent to the front guest app" + (" (+Cmd-Q)" if "--quit" in a else "") + ("  reply: " + out.strip()[:60] if out.strip() else ""))
