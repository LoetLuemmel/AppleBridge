"""A stand-in for Basilisk II with cxmon: SIGINT enters the monitor, a break
point set with `ba` is hit shortly after `x`, and the prompt/flush behaviour
matches cxmon (prompt written, stdout flushed, then a blocking line read)."""
import json
import os
import signal
import sys
import time

# Optional memory image: {"hex address": "hex bytes"} plus "regs": {"PC": .., "A6": ..}.
IMAGE = json.load(open(os.environ["FAKE_MON_IMAGE"])) if os.environ.get("FAKE_MON_IMAGE") else {}
MEM = {}
for addr, data in IMAGE.get("mem", {}).items():
    for k, b in enumerate(bytes.fromhex(data)):
        MEM[int(addr, 16) + k] = b
for k, b in enumerate(bytes.fromhex("0002e89007c2eb2007c2eb8007c2ebcc")):
    MEM.setdefault(0xF80 + k, b)


def dump(a, b):
    lines = []
    a &= ~0xF if False else a
    while a < b:
        words = " ".join("".join(f"{MEM.get(a + i + j, 0):02x}" for j in range(4)) for i in range(0, 16, 4))
        lines.append(f"{a:016x}: {words}  '................'")
        a += 16
    return "\n".join(lines) + "\n"


def regs_text():
    r = IMAGE.get("regs", {})
    a = " ".join(f"A{i}: {int(r.get(f'A{i}', '0'), 16):08x}" for i in range(4, 8))
    pc = int(r.get("PC", "a4024"), 16)
    return ("D0: 00000000 D1: 00000040\n" + a + "\n" +
            f"{pc:08x}: 7137 4e71 EMULOP.L #$00000037\nnext PC: {pc + 2:08x}\n")

PROMPT = "[0000000000000000]-> "
REGS = "D0: 00000000 D1: 00000040\nA7: 06e365c4\n000a4024: 7137 4e71 EMULOP.L #$00000037\nnext PC: 000a4026\n"
state = {"interrupt": False, "bps": set(), "resumed": 0.0}


def on_int(*_):
    state["interrupt"] = True


START = time.monotonic()
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
        if cmd.startswith("m $"):
            a, b = (int(x.lstrip("$"), 16) for x in cmd[2:].split())
            out = dump(a, b)
        elif cmd.startswith("ba $"):
            state["bps"].add(int(cmd[4:], 16))
            out = ""
        elif cmd == "br 0":
            state["bps"].clear()
            out = "Removed all break points!\n"
        elif cmd == "regs":
            out = regs_text()
        else:
            out = "*** Unknown command\n"
        sys.stdout.write(out + PROMPT)
        sys.stdout.flush()


while True:
    if state["interrupt"]:
        state["interrupt"] = False
        sys.stdout.write(REGS)
        monitor()
    elif IMAGE.get("crash_after") and not state.get("crashed") and time.monotonic() - START > IMAGE["crash_after"]:
        state["crashed"] = True
        sys.stdout.write("Caught SIGSEGV at address 0x441d2004 [IP=0x10a3c2f]\n" + regs_text())
        monitor()
        sys.exit(0)                                    # leaving the monitor quits, like Basilisk
    elif state["bps"] and state["resumed"] and time.monotonic() - state["resumed"] > 0.3:
        a = min(state["bps"])
        state["resumed"] = 0.0
        sys.stdout.write(f"Stopped at break point address: {a:08x}. Last PC: 07c099d6\n" + REGS)
        monitor()
    time.sleep(0.02)
