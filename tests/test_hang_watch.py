"""hang_watch.py kills an emulator only when the guest is HUNG (2026-10-04: the local guest sat at 100 % CPU for 1.5 h
after a generated program stopped yielding). The decision is a pure state machine; every reason NOT to kill is checked
here, because a wrong kill of a healthy guest risks its disk image (D-004)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); HOST = os.path.join(os.path.dirname(HERE), "host"); sys.path.insert(0, HOST)
import hang_watch as HW
PASS = FAIL = 0
def check(name, ok, detail=""):
    global PASS, FAIL
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": {detail}")); PASS += ok; FAIL += not ok

def run(steps, silent_s=300, cpu_min=90):
    """steps: (pid, answered, cpu, frame) one per minute -> the verdicts"""
    j = HW.HangJudge(silent_s, cpu_min)
    return [j.observe(p, a, c, f, now=i * 60) for i, (p, a, c, f) in enumerate(steps)]

hung = [(1, True, 5, None)] + [(1, False, 100, "x")] * 6
v = run(hung)
check("answered, then silent at full CPU with a frozen screen for 5 min -> kill", v[-1] == "kill" and v.count("kill") == 1, v)
check("not before the window is full", "kill" not in v[:-1], v)
v = run([(1, False, 100, "x")] * 10)
check("a guest that never answered since launch (booting) is never killed", set(v) == {"booting"}, v)
v = run([(1, True, 5, None)] + [(1, False, 4, "x")] * 10)
check("silent but idle (network / daemon down, CPU low) -> no kill", "kill" not in v, v)
v = run([(1, True, 5, None)] + [(1, False, 100, f"f{i}") for i in range(10)])
check("silent at full CPU but the screen keeps changing (someone at work) -> no kill", "kill" not in v, v)
v = run([(1, True, 5, None)] + [(1, False, 100, None)] * 6)
check("frame unreadable: silence + CPU alone decide", v[-1] == "kill", v)
v = run([(1, True, 5, None)] + [(1, False, 100, "x")] * 3 + [(1, True, 50, None)] + [(1, False, 100, "x")] * 3)
check("an answer in between restarts the window", "kill" not in v, v)
v = run([(1, True, 5, None)] + [(1, False, 100, "x")] * 3 + [(1, False, 20, "x")] + [(1, False, 100, "x")] * 3)
check("one idle check in between restarts the window", "kill" not in v, v)
v = run([(1, True, 5, None)] + [(1, None, None, None)] * 10)
check("control port refused (host server down) is no evidence -> no kill", "kill" not in v, v)
v = run(hung + [(1, False, 100, "x")] * 10)
check("after a kill: no second kill until the bridge has answered again", v.count("kill") == 1, v)
v = run([(1, True, 5, None)] + [(1, False, 100, "x")] * 3 + [(2, False, 100, "x")] * 10)
check("a new emulator process starts unarmed (relaunched, booting)", "kill" not in v, v)
v = run([(None, None, None, None)] * 5)
check("no emulator running -> quiet", set(v) == {"quiet"}, v)

print(f"\n{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
