"""A stand-in for Basilisk II with cxmon: SIGINT enters the monitor, a break
point set with `ba` is hit shortly after `x`, and the prompt/flush behaviour
matches cxmon (prompt written, stdout flushed, then a blocking line read)."""
import signal
import sys
import time

PROMPT = "[0000000000000000]-> "
REGS = "D0: 00000000 D1: 00000040\nA7: 06e365c4\n000a4024: 7137 4e71 EMULOP.L #$00000037\nnext PC: 000a4026\n"
state = {"interrupt": False, "bps": set(), "resumed": 0.0}


def on_int(*_):
    state["interrupt"] = True


signal.signal(signal.SIGINT, on_int)
signal.signal(signal.SIGTERM, signal.SIG_IGN)          # Basilisk ignores SIGTERM too
print("Basilisk II V1.1 by Christian Bauer et al.\nioctl(DKIOCCDREADTOC) failed: Bad file descriptor", flush=True)


def monitor():
    sys.stdout.write(PROMPT)
    sys.stdout.flush()
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        cmd = line.strip()
        if cmd == "x":
            state["resumed"] = time.monotonic()
            return
        if cmd.startswith("m $f80"):
            out = "0000000000000f80: 0002e890 07c2eb20 07c2eb80 07c2ebcc  '....... ........'\n"
        elif cmd.startswith("ba $"):
            state["bps"].add(int(cmd[4:], 16))
            out = ""
        elif cmd == "br 0":
            state["bps"].clear()
            out = "Removed all break points!\n"
        elif cmd == "regs":
            out = REGS
        else:
            out = "*** Unknown command\n"
        sys.stdout.write(out + PROMPT)
        sys.stdout.flush()


while True:
    if state["interrupt"]:
        state["interrupt"] = False
        sys.stdout.write(REGS)
        monitor()
    elif state["bps"] and state["resumed"] and time.monotonic() - state["resumed"] > 0.3:
        a = min(state["bps"])
        state["resumed"] = 0.0
        sys.stdout.write(f"Stopped at break point address: {a:08x}. Last PC: 07c099d6\n" + REGS)
        monitor()
    time.sleep(0.02)
