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
     appleshare_login / ip_conflict -> NO input, spoken alert        ; screenshot + log + notification
     system_error (the bomb)  -> click Restart, guest_boot.py        ; screenshot + log + notification (2026-11-17, see below)
  bridge silent on THREE checks in a row (two fired twice on 2026-10-09 during a daemon reconnect), emulator present and NOT busy (cpu < 50 %)
     -> an unknown modal is likely: screenshot + spoken alert + notification, NO input (a person decides what to click)

The bomb (2026-11-17: S19's "Begruessung" stopped mbp2013 with "error type 10" at 09:13; nothing reacted - this watcher
skipped it because its unknown-modal rule wants < 50 % CPU and a bombed Basilisk spins at 100 %, hang_watch would have
killed the emulator after 5 minutes; the operator clicked Restart by hand). The system-error dialog is answered at once:
its two text lines (the program's name and the error type) are read with xavier-eval's bitmap_reader where it is
installed, the operator gets a notification (operator_notify.py, APPLEBRIDGE_NOTIFY_CMD) naming the machine, the program
and the error, the guest's real mouse clicks
Restart (refs/system_error.json "click", guest_input -> cliclick, on a remote guest its tools copy),
and guest_boot.py brings the restarted System 7 back (AppleShare login, volumes). A program that bombs a guest must be
found and stopped - the message says so.
Screenshots: /tmp/dialog_watch_<port>/<time>_<what>.png. Log: /tmp/applebridge_dialog_watch_<port>.log.
A guest on another Mac: APPLEBRIDGE_CTRL_PORT=9011 APPLEBRIDGE_GUEST_SSH=user@host (same as guest_boot.py).
stdlib only; run with /usr/bin/python3."""
import os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import fb_export, guest_remote, screenshot_decode
import guest_boot as GB
import operator_notify

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


def notify(msg):
    return operator_notify.notify(msg, log=log)


def bomb_text(f, ref):
    """the bomb's lines after the first ('"Begruessung"', 'error type 10') via bitmap_reader, or []"""
    try:
        sys.path.insert(0, os.environ.get("APPLEBRIDGE_TEXT_READER_DIR", os.path.expanduser("~/Documents/Dev/xavier-eval")))
        import bitmap_reader

        class _Frame:                                         # the fields bitmap_reader takes from think_project_gate.Frame
            pass
        fr = _Frame(); fr.f = f; fr.px = f["pixels"]; fr.rb = f["row_bytes"]; fr.w = f["width"]; fr.h = f["height"]
        x0, y0, x1, y1 = ref.get("text_rect") or ref["rect"]
        return [l["text"].strip() for l in bitmap_reader.read(fr, (x0, y0, x1 - x0, y1 - y0)) if l.get("text", "").strip()][1:]
    except Exception as e:
        log(f"bomb text unreadable ({type(e).__name__}: {str(e)[:60]})")
        return []


def answer_bomb(f):
    import json
    ref = json.load(open(os.path.join(GB.REFS, "system_error.json")))
    p = shot(f, "system_error"); lines = bomb_text(f, ref); what = " / ".join(lines) or "Text nicht gelesen"
    log(f"system-error bomb: {what} ({p})")
    GB.say(f"Systemfehler im Gast {WHERE}. Ich klicke auf Neustart.")
    notify(f"Systemfehler-Bombe ({what}). Ich klicke 'Restart'. Das Programm muss gefunden und gestoppt werden.")
    try:
        import guest_input
        with guest_input.Session() as s:
            s.cliclick(guest_input.build_click(s.point(*ref["click"])))
        log("bomb: Restart clicked")
    except Exception as e:
        log(f"bomb: Restart click failed ({type(e).__name__}: {str(e)[:100]})")
        notify(f"Klick auf 'Restart' fehlgeschlagen ({str(e)[:80]}). Bitte von Hand klicken.")
        return
    time.sleep(25)                                            # System 7 restarts inside the same emulator process
    try:
        GB.main(420); back = GB.ctrl("PROCLIST", 12).startswith("STATUS:0")
    except SystemExit as e:
        back = not e.code
    except Exception as e:
        log(f"bomb: guest_boot failed ({type(e).__name__})"); back = False
    log(f"bomb: guest {'back' if back else 'NOT back'} after the restart")
    notify("nach dem Bomben-Neustart wieder da." if back else "kommt nach dem Bomben-Neustart NICHT zurück - bitte nachsehen.")


def emulator_cpu():
    """-> cpu % of the emulator, or None when no emulator runs (on the guest's own Mac)"""
    cmd = ["sh", "-c", "ps -Ao %cpu,comm | grep '[B]asiliskII' | head -1"]
    r = guest_remote.run(cmd) if guest_remote.active() else subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout if isinstance(r.stdout, str) else (r.stdout or b"").decode()).strip()
    try: return float(out.split()[0].replace(",", "."))
    except (ValueError, IndexError): return None


def main():
    silent = 0; alerted = None; waiting = 0; waiting_d = None
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
        if d == "system_error":
            answer_bomb(f); alerted = None; silent = 0; time.sleep(CHECK_S); continue
        if d in ("appleshare_login", "ip_conflict"):
            waiting = waiting + 1 if waiting_d == d else 1; waiting_d = d
            if waiting == 2:                                  # still there one check later: guest_boot did not take it
                notify(f"wartet auf {'die AppleShare-Anmeldung' if d == 'appleshare_login' else 'einen IP-Adresskonflikt-Dialog'} - bitte prüfen.")
            if alerted != d:
                p = shot(f, d); log(f"{d} waiting - no input given ({p})"); alerted = d
                GB.say(f"Auf dem Gast {WHERE} wartet ein Dialog: {'AppleShare-Anmeldung' if d == 'appleshare_login' else 'IP-Adresskonflikt'}. Bitte prüfen.")
            time.sleep(CHECK_S); continue
        waiting_d = None
        answered = GB.ctrl("PROCLIST", 12).startswith("STATUS:0")
        silent = 0 if answered else silent + 1
        if answered: alerted = None
        if silent >= 3 and f is not None and alerted != "unknown":
            cpu = emulator_cpu()
            if cpu is not None and cpu < 50:                      # a hang (>= 90 %) belongs to hang_watch.py
                p = shot(f, "bridge_silent"); log(f"bridge silent on {silent} checks, emulator at {cpu:.0f} % CPU - a modal is likely ({p})")
                GB.say(f"Der Gast {WHERE} antwortet nicht, der Emulator ist aber ruhig. Vermutlich wartet ein Dialog. Ein Bildschirmfoto liegt bereit.")
                notify(f"antwortet nicht, Emulator ruhig ({cpu:.0f} % CPU) - vermutlich wartet ein Dialog. Bildschirmfoto: {p}")
                alerted = "unknown"
        time.sleep(CHECK_S)


if __name__ == "__main__":
    main()
