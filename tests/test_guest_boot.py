"""guest_boot.py LOOKS before it acts (2026-10-01: a blind Return dismissed a network warning instead of the AppleShare
login). The recognition must name the dialog that is on screen and nothing when none is — checked on a synthetic frame
against a reference cut from it, and on a frame that differs."""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); HOST = os.path.join(os.path.dirname(HERE), "host"); sys.path.insert(0, HOST)
import guest_boot as GB
PASS = FAIL = 0
def check(name, ok, detail=""):
    global PASS, FAIL
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": {detail}")); PASS += ok; FAIL += not ok

W, H = 64, 40
pal = bytes(sum(([v, v, v] for v in range(256)), []))
def frame(marks):
    px = bytearray([255] * (W * H))
    for x, y in marks: px[y * W + x] = 0
    return {"palette": pal, "pixels": bytes(px), "row_bytes": W}
dialog = [(x, 10) for x in range(5, 50)] + [(5, y) for y in range(10, 30)] + [(20 + i, 15 + i // 2) for i in range(20)]
f_dialog, f_desk = frame(dialog), frame([(x, 35) for x in range(0, 60, 3)])
tmp = tempfile.mkdtemp(); rect = [2, 8, 60, 32]
json.dump({"rect": rect, "bits": GB.dark_bits(f_dialog, rect)}, open(os.path.join(tmp, "appleshare_login.json"), "w"))
GB.REFS = tmp
check("the dialog's own frame is recognised", GB.which_dialog(f_dialog) == "appleshare_login", GB.which_dialog(f_dialog))
check("a frame without it is not", GB.which_dialog(f_desk) is None, GB.which_dialog(f_desk))
f_close = frame(dialog[:-12])         # most of it, but more than 3 % of the region differs -> no match
check("a near miss (> 3 % different) is not taken for it", GB.which_dialog(f_close) is None, GB.which_dialog(f_close))
GB.REFS = os.path.join(tmp, "absent")
check("no reference directory -> nothing recognised", GB.which_dialog(f_dialog) is None)
stack = open(os.path.join(HOST, "start_stack.sh")).read()
check("start_stack.sh runs guest_boot.py after launching the emulator",
      stack.index('open "$BASILISK_APP"') < stack.index("guest_boot.py") and 'if [ -n "$BASILISK_APP" ]' in stack[stack.index("SKIPPED"):stack.index("guest_boot.py")])
print(f"\n{PASS}/{PASS + FAIL} passed"); sys.exit(1 if FAIL else 0)
