#!/usr/bin/env python3
"""guest_boot.py — shepherd a guest from emulator launch to a clean, connected desktop, LOOKING before acting
(2026-10-01, operator: "nicht nur das Passwort tippen, sondern auch den Screenshot auswerten" — a blind Return had
dismissed a network warning instead of the AppleShare login).

Loop: read the guest's framebuffer (fb_export; over SSH for a guest on another Mac, APPLEBRIDGE_GUEST_SSH) and compare
it with pixel references of the dialogs a boot can show (host/refs/*.json — cut from real frames, machine-specific,
untracked):
  appleshare_login       -> bring the emulator to the front, type the password (APPLEBRIDGE_AFP_PASSWORD, local.env),
                            Return, then CHECK the dialog is gone (still there = password rejected -> stop, report)
  appletalk_interrupted  -> Return (a transient drop while the network helper takes the NIC)
  ip_conflict            -> do NOT dismiss: two machines use the guest's address; say it aloud, stop with an error
When the daemon answers on the control port (APPLEBRIDGE_CTRL_PORT) and no known dialog is up: check the expected volumes
(APPLEBRIDGE_EXPECT_VOLUMES, default "AppleShare"), then tidy the desktop for screen reading — hide the daemon console
(MONITOR:0), click the empty desktop (Finder front), Cmd-Option-W (close every Finder window).
Exit 0 ready, 1 a dialog needs a person, 2 timeout. stdlib only (the host's /usr/bin/python3)."""
import json, os, socket, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import fb_export, guest_remote

REFS = os.path.join(HERE, "refs")
PORT = int(os.environ.get("APPLEBRIDGE_CTRL_PORT", "9001"))
EXPECT = [v.strip() for v in os.environ.get("APPLEBRIDGE_EXPECT_VOLUMES", "AppleShare").split(",") if v.strip()]


def local_env(key):
    v = os.environ.get(key)
    if v: return v
    try:
        for l in open(os.path.join(HERE, "local.env")):
            if l.strip().startswith(key + "="): return l.split("=", 1)[1].strip().strip('"')
    except OSError: pass
    return ""


def ctrl(cmd, timeout=15):
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=5) as s:
            s.settimeout(timeout); s.sendall((cmd + "\n\n").encode()); b = b""
            while True:
                d = s.recv(65536)
                if not d: break
                b += d
        return b.decode("utf-8", "replace")
    except OSError as e:
        return f"STATUS:-1 {e}"


def frame():
    pid, _ = fb_export.check(); return fb_export.parse_dump(fb_export.request_dump(pid))


def dark_bits(f, rect):
    pal = f["palette"]; lum = [(pal[3 * i] + pal[3 * i + 1] + pal[3 * i + 2]) // 3 for i in range(256)]
    x0, y0, x1, y1 = rect; px, rb = f["pixels"], f["row_bytes"]
    return "".join("1" if lum[px[y * rb + x]] < 128 else "0" for y in range(y0, y1) for x in range(x0, x1))


def which_dialog(f):
    """the reference whose region matches best, if the match is near-exact: at most 5 % of the reference's INK differs.
    (Measured against the ink, not the region: a region is mostly background, and a 3 %-of-region rule let three
    quarters of the login dialog's text be missing — caught by tests/test_guest_boot.py.)"""
    best = None
    for fn in sorted(os.listdir(REFS)) if os.path.isdir(REFS) else []:
        if not fn.endswith(".json"): continue
        ref = json.load(open(os.path.join(REFS, fn))); got = dark_bits(f, ref["rect"])
        ink = max(1, ref["bits"].count("1"))
        diff = sum(1 for a, b in zip(got, ref["bits"]) if a != b) / ink
        if diff <= 0.05 and (best is None or diff < best[1]): best = (fn[:-5], diff)
    return best[0] if best else None


def keys(script_lines):
    args = sum((["-e", s] for s in script_lines), [])
    if guest_remote.active(): return guest_remote.run(["osascript"] + args).returncode
    return subprocess.run(["osascript"] + args, capture_output=True).returncode


FRONT = 'tell application "System Events" to set frontmost of (first process whose name is "BasiliskII") to true'


def say(msg):
    print(msg, flush=True)
    subprocess.run(["say", "-v", "Anna", msg], capture_output=True)


def main(timeout=420):
    t0 = time.time(); typed = 0; last = None
    while time.time() - t0 < timeout:
        try: f = frame()
        except Exception as e:
            if last != "noframe": print(f"no frame yet ({e})", flush=True); last = "noframe"
            time.sleep(3); continue
        d = which_dialog(f)
        if d != last: print(f"{time.time() - t0:5.1f}s screen: {d or 'no known dialog'}", flush=True); last = d
        if d == "appleshare_login":
            pw = local_env("APPLEBRIDGE_AFP_PASSWORD")
            if not pw: say("Der AppleShare-Anmeldedialog wartet, aber in local.env steht kein APPLEBRIDGE_AFP_PASSWORD."); return 1
            if typed >= 2: say("Das AppleShare-Passwort wurde zweimal nicht angenommen. Bitte den Dialog prüfen."); return 1
            keys([FRONT, "delay 0.6", f'tell application "System Events" to keystroke "{pw}"', "delay 0.2",
                  'tell application "System Events" to key code 36']); typed += 1
            time.sleep(4); continue
        if d == "appletalk_interrupted":
            keys([FRONT, "delay 0.6", 'tell application "System Events" to key code 36']); time.sleep(3); continue
        if d == "ip_conflict":
            say("Achtung. Der Gast meldet einen IP-Adresskonflikt. Ein anderes Gerät benutzt seine Adresse. Bitte prüfen."); return 1
        r = ctrl("DISKINFO")
        if r.startswith("STATUS:0"):
            missing = [v for v in EXPECT if ("\n" + v + "\t") not in r.replace("\r", "\n")]
            if missing:
                if time.time() - t0 > 120: say(f"Die Brücke steht, aber es fehlt: {', '.join(missing)}."); return 1
                time.sleep(4); continue
            ctrl("MONITOR:0"); ctrl("CLICK:1000:720"); time.sleep(1.0)            # the empty desktop corner -> Finder front
            ctrl("KEY:119:13:2304"); time.sleep(1.5)                             # Cmd-Option-W: close every Finder window
            print(f"{time.time() - t0:5.1f}s READY: bridge up, volumes {', '.join(EXPECT)}, desktop tidied", flush=True)
            return 0
        time.sleep(3)
    say("Der Gast ist nach sieben Minuten nicht bereit."); return 2


if __name__ == "__main__":
    sys.exit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 420))
