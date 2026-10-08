#!/usr/bin/env python3
"""dialog_watch.py — watch a guest for modal dialogs that block the bridge (2026-10-08, operator: "For such messageboxes we
need a watchdog, triggering a screenshot maybe").

Why: a modal in the guest is a tracking loop. The cooperative scheduler stops, the daemon never runs, and the host only sees
"no daemon connected" while the emulator idles — the hang watchdog rightly does nothing, because nothing hangs. Seen twice:
the Finder's "Empty Trash?" alert after a mis-targeted menu gesture (2026-10-07 08:02) and "AppleTalk network interrupted"
over a THINK C build on the 2013 (2026-10-08 16:44).

Every CHECK_S seconds, from the framebuffer (fb_export: no bridge traffic, works while the modal owns the guest):
  known dialog (host/refs/*.json, the boot shepherd's matcher)
     appletalk_interrupted -> Return (harmless, transient)          ; screenshot + log
     empty_trash           -> Escape = Cancel (never delete)        ; screenshot + log
     appleshare_login / ip_conflict -> NO input, spoken alert        ; screenshot + log
  bridge silent on two checks in a row, emulator present and NOT busy (cpu < 50 %)
     -> an unknown modal is likely: screenshot + spoken alert, NO input (a person decides what to click)
Screenshots: /tmp/dialog_watch_<port>/<time>_<what>.png. Log: /tmp/applebridge_dialog_watch_<port>.log.
A guest on another Mac: APPLEBRIDGE_CTRL_PORT=9011 APPLEBRIDGE_GUEST_SSH=user@host (same as guest_boot.py).
stdlib only; run with /usr/bin/python3."""
import os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import fb_export, guest_remote, screenshot_decode
import guest_boot as GB

PORT = GB.PORT
CHECK_S = int(os.environ.get("DIALOG_WATCH_S", "45"))
SHOTS = f"/tmp/dialog_watch_{PORT}"; LOG = f"/tmp/applebridge_dialog_watch_{PORT}.log"
WHERE = "auf dem zweiten Mac" if guest_remote.active() else "auf diesem Mac"


def log(msg):
    with open(LOG, "a") as f: f.write(time.strftime("%F %T ") + msg + "\n")


def shot(f, what):
    os.makedirs(SHOTS, exist_ok=True); p = os.path.join(SHOTS, time.strftime("%Y%m%d_%H%M%S_") + what + ".png")
    open(p, "wb").write(screenshot_decode.raw_to_png_indexed(f["width"], f["height"], f["depth"], f["row_bytes"], f["palette"], f["pixels"]))
    return p


def emulator_cpu():
    """-> cpu % of the emulator, or None when no emulator runs (on the guest's own Mac)"""
    cmd = ["sh", "-c", "ps -Ao %cpu,comm | grep '[B]asiliskII' | head -1"]
    r = guest_remote.run(cmd) if guest_remote.active() else subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout if isinstance(r.stdout, str) else (r.stdout or b"").decode()).strip()
    try: return float(out.split()[0].replace(",", "."))
    except (ValueError, IndexError): return None


def main():
    silent = 0; alerted = None
    log(f"watching control port {PORT} every {CHECK_S} s")
    while True:
        try: f = GB.frame()
        except Exception as e: f = None; log(f"no frame ({str(e)[:80]})")
        d = GB.which_dialog(f) if f else None
        if d == "appletalk_interrupted":
            p = shot(f, d); GB.keys([GB.FRONT, "delay 0.6", 'tell application "System Events" to key code 36'])
            log(f"AppleTalk-interrupted alert dismissed with Return ({p})"); time.sleep(5); continue
        if d == "empty_trash":                                    # the Finder's "Empty Trash?" alert: never OK - Escape is Cancel
            p = shot(f, d); GB.keys([GB.FRONT, "delay 0.6", 'tell application "System Events" to key code 53'])
            log(f"Empty-Trash alert CANCELLED with Escape ({p})"); time.sleep(5); continue
        if d in ("appleshare_login", "ip_conflict"):
            if alerted != d:
                p = shot(f, d); log(f"{d} waiting - no input given ({p})"); alerted = d
                GB.say(f"Auf dem Gast {WHERE} wartet ein Dialog: {'AppleShare-Anmeldung' if d == 'appleshare_login' else 'IP-Adresskonflikt'}. Bitte prüfen.")
            time.sleep(CHECK_S); continue
        answered = GB.ctrl("PROCLIST", 12).startswith("STATUS:0")
        silent = 0 if answered else silent + 1
        if answered: alerted = None
        if silent >= 2 and f is not None and alerted != "unknown":
            cpu = emulator_cpu()
            if cpu is not None and cpu < 50:                      # a hang (>= 90 %) belongs to hang_watch.py
                p = shot(f, "bridge_silent"); log(f"bridge silent on {silent} checks, emulator at {cpu:.0f} % CPU - a modal is likely ({p})")
                GB.say(f"Der Gast {WHERE} antwortet nicht, der Emulator ist aber ruhig. Vermutlich wartet ein Dialog. Ein Bildschirmfoto liegt bereit.")
                alerted = "unknown"
        time.sleep(CHECK_S)


if __name__ == "__main__":
    main()
