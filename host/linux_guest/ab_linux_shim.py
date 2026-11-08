#!/usr/bin/env python3
"""ab_linux_shim.py — the macOS host tools the hub calls over SSH, rebuilt for a LINUX guest host (2026-11-08).

The hub drives a guest on another machine through guest_remote.py: it runs `cliclick`, `osascript`, `screencapture`
and `sips` THERE, by name. A Linux guest host (odroid-c2: BasiliskII in a Docker container on Xvfb :1, no window
manager) has none of them, so the first wish built there failed with "osascript: command not found". This one file
answers to all four names (install.sh links them) and does the same job with xdotool inside the container and the
emulator's own framebuffer export. The hub's code does not change; it cannot tell the difference.

Only the forms the hub actually sends are implemented (guest_input.py, think_project_gate.py, project_build.py,
fb_export.py). Anything else exits 1 with "unsupported", so a new form fails loudly instead of doing something else.

Config, all in ~/.applebridge (written by install.sh):
    container   the Docker container that runs BasiliskII   (default basilisk-odroid-c2)
    display     its X display                               (default :1)
    fb_dump     where the emulator writes its frame dump    (host path of the bind mount)
    BasiliskII  a copy of the container's binary — fb_export checks it for the export marker before any SIGUSR1

Keys: BasiliskII on Linux swaps Option and Command (swap_opt_cmd defaults to true off __APPLE__), so the Mac's
Command key is X11 Alt and Option is Super. cliclick's `cmd` therefore becomes `alt` here.
"""
import os
import re
import shlex
import subprocess
import sys
import time

CONF = os.path.expanduser("~/.applebridge")
sys.path.insert(0, os.path.join(CONF, "lib"))


def conf(name, default):
    try:
        return open(os.path.join(CONF, name)).read().strip() or default
    except OSError:
        return default


CONTAINER = conf("container", "basilisk-odroid-c2")
DISPLAY = conf("display", ":1")
APP = "BasiliskII"
WINDOW_NAME = "Basilisk II"
MODS = {"cmd": "alt", "alt": "super", "shift": "shift", "ctrl": "ctrl", "fn": None}
KEYS = {"return": "Return", "enter": "KP_Enter", "esc": "Escape", "tab": "Tab", "space": "space", "delete": "BackSpace",
        "fwd-delete": "Delete", "arrow-up": "Up", "arrow-down": "Down", "arrow-left": "Left", "arrow-right": "Right",
        "home": "Home", "end": "End", "page-up": "Prior", "page-down": "Next"}


def die(msg, code=1):
    print(f"{os.path.basename(sys.argv[0])} (linux shim): {msg}", file=sys.stderr)
    sys.exit(code)


def xdo(args, timeout=30):
    """ONE xdotool invocation (a chain) inside the container -> stdout"""
    p = subprocess.run(["docker", "exec", "-e", f"DISPLAY={DISPLAY}", CONTAINER, "xdotool"] + [str(a) for a in args],
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        die(f"xdotool {' '.join(map(str, args[:6]))}...: {(p.stderr or p.stdout).strip()}")
    return p.stdout


def window():
    """(id, x, y, w, h) of the emulator window — read every call, never cached (guest_input's rule)"""
    out = xdo(["search", "--name", f"^{WINDOW_NAME}$", "getwindowgeometry", "--shell", "%@"])
    vals = dict(re.findall(r"(\w+)=(\d+)", out))
    if "WINDOW" not in vals:
        die(f"no '{WINDOW_NAME}' window on {DISPLAY} in {CONTAINER}")
    return int(vals["WINDOW"]), int(vals["X"]), int(vals["Y"]), int(vals["WIDTH"]), int(vals["HEIGHT"])


def raise_emulator():
    wid = window()[0]
    # no window manager: windowactivate needs _NET_ACTIVE_WINDOW and fails; raise + X focus is all there is
    xdo(["windowraise", wid, "windowfocus", "--sync", wid])


# ---------------------------------------------------------------- cliclick
def pt(s):
    m = re.fullmatch(r"(-?\d+),(-?\d+)", s)
    if not m:
        die(f"bad point {s!r}")
    return m.group(1), m.group(2)


def cliclick(argv):
    chain, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("-e", "-w"):          # easing / default wait: the gesture is the same without them
            i += 2
            continue
        if a.startswith("-"):
            die(f"unsupported option {a}")
        verb, _, arg = a.partition(":")
        if verb == "m":
            chain += ["mousemove", *pt(arg)]
        elif verb == "w":
            chain += ["sleep", "%.3f" % (int(arg) / 1000)]
        elif verb in ("c", "dc", "tc"):
            n = {"c": 1, "dc": 2, "tc": 3}[verb]
            chain += ["mousemove", *pt(arg), "click", "--repeat", n, "--delay", 120, 1]
        elif verb == "dd":
            chain += ["mousemove", *pt(arg), "mousedown", 1]
        elif verb == "du":
            chain += ["mousemove", *pt(arg), "mouseup", 1]
        elif verb == "dm":
            chain += ["mousemove", *pt(arg)]
        elif verb in ("kd", "ku"):
            keys = []
            for k in arg.split(","):
                if k not in MODS or MODS[k] is None:
                    die(f"unsupported modifier {k!r}")
                keys.append(MODS[k])
            chain += ["keydown" if verb == "kd" else "keyup", "+".join(keys)]
        elif verb == "kp":
            if arg not in KEYS:
                die(f"unsupported key {arg!r}")
            chain += ["key", KEYS[arg]]
        elif verb == "t":
            chain += ["type", "--delay", 60, arg]
        elif verb == "p":
            chain += ["getmouselocation"]
        else:
            die(f"unsupported command {a!r}")
        i += 1
    if chain:
        sys.stdout.write(xdo(chain))


# ---------------------------------------------------------------- osascript
def osascript(argv):
    if not argv or argv[0] != "-e":
        die("only `osascript -e <script> [-e ...]` is supported")
    script = "\n".join(argv[i + 1] for i in range(0, len(argv), 2) if argv[i] == "-e")
    if "display notification" in script:
        return                                                       # nobody sits at this box
    m = re.search(r'keystroke "(.)"(?: using \{?([\w ,]+?)\}?)?\s*$', script, re.M)
    if m:
        raise_emulator()
        mods = [{"command": "alt", "option": "super", "shift": "shift", "control": "ctrl"}.get(w.strip().split()[0])
                for w in (m.group(2) or "").split(",") if w.strip()] if m.group(2) else []
        if None in mods:
            die(f"unsupported keystroke modifiers {m.group(2)!r}")
        time.sleep(0.6 if "delay" in script else 0)
        xdo(["key", "+".join(mods + [m.group(1)])])
        return
    if "set frontmost" in script:
        target = re.search(r'process "([^"]+)"|process whose name is "([^"]+)"', script)
        name = target and (target.group(1) or target.group(2))
        if name == APP:
            raise_emulator()
        return                                                       # handing focus "back" to another app: there is none
    wid, x, y, w, h = window()
    if "position of window 1" in script and "frontmost is true" in script:
        print(f"{APP}|{x}|{y}|{w}|{h}")                              # front_and_geometry()
        return
    if "{position, size} of window 1" in script:
        print(f"{x}, {y}, {w}, {h}")                                 # Session.geometry()
        return
    if "frontmost is true" in script:
        print(APP)                                                   # frontmost_app(): only one app on this display
        return
    die("unsupported script: " + script.replace("\n", " | ")[:200])


# ---------------------------------------------------------------- screencapture
def screencapture(argv):
    rect, out = None, None
    for a in argv:
        if a == "-x":
            continue
        if a.startswith("-R"):
            rect = tuple(int(v) for v in a[2:].split(","))
        elif a.startswith("-"):
            die(f"unsupported option {a}")
        else:
            out = a
    if not out:
        die("no output file")
    import fb_export, screenshot_decode
    os.chdir(CONF)                                   # fb_export resolves the bare binary name "BasiliskII" here
    os.environ["BASILISK_FB_DUMP"] = conf("fb_dump", "/tmp/basilisk_fb.bin")
    pid, _ = fb_export.check()
    path = fb_export.dump_path()
    lock = path + ".lock"                            # the hub's remote dump takes the same lock (fb_export.request_dump)
    for _ in range(200):
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            time.sleep(0.05)
    else:
        os.rmdir(lock); os.mkdir(lock)               # a lock older than 10 s belongs to a killed reader
    try:
        frame = fb_export.parse_dump(fb_export.request_dump(pid))
    finally:
        os.rmdir(lock)
    region = None
    if rect:
        _, wx, wy, _, _ = window()                   # host point -> guest pixel (no title bar under Xvfb)
        region = (rect[0] - wx, rect[1] - wy, rect[2], rect[3])
    png = screenshot_decode.raw_to_png_indexed(frame["width"], frame["height"], frame["depth"], frame["row_bytes"],
                                               frame["palette"], frame["pixels"], region=region)
    with open(out, "wb") as fh:
        fh.write(png)


# ---------------------------------------------------------------- sips
def png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        die(f"{path}: not a PNG")
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def sips(argv):
    if argv[:1] == ["--resampleHeightWidth"] and len(argv) == 4:
        w, h = png_size(argv[3])
        if (w, h) != (int(argv[2]), int(argv[1])):
            die(f"resampling {w}x{h} -> {argv[2]}x{argv[1]} is not supported (captures here are 1:1)")
        print(argv[3])
        return
    if argv and all(a in ("-g", "pixelWidth", "pixelHeight") for a in argv[:-1]):
        w, h = png_size(argv[-1])
        print(f"{argv[-1]}\n  pixelWidth: {w}\n  pixelHeight: {h}")
        return
    die("unsupported: sips " + " ".join(map(shlex.quote, argv)))


TOOLS = {"cliclick": cliclick, "osascript": osascript, "screencapture": screencapture, "sips": sips}

if __name__ == "__main__":
    name = os.path.basename(sys.argv[0])
    if name not in TOOLS:
        die(f"called as {name!r}; link it as one of {', '.join(TOOLS)}")
    TOOLS[name](sys.argv[1:])
