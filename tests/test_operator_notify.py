"""operator_notify.py hands a message about a guest to the operator's own channel (APPLEBRIDGE_NOTIFY_CMD, 2026-11-17).
Checked without any channel: the text goes on stdin, unset means nothing is sent, a failing or missing command never
raises."""
import os, sys, tempfile, types
HERE = os.path.dirname(os.path.abspath(__file__)); HOST = os.path.join(os.path.dirname(HERE), "host"); sys.path.insert(0, HOST)
import operator_notify as N
N.HERE = tempfile.mkdtemp()          # never the real host/local.env
PASS = FAIL = 0
def check(name, ok, detail=""):
    global PASS, FAIL
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": {detail}")); PASS += ok; FAIL += not ok

os.environ["APPLEBRIDGE_GUEST_NAME"] = "guest2"
check("the text starts with AppleBridge and the machine", N.message_text("x") == "AppleBridge guest2: x", N.message_text("x"))

os.environ["APPLEBRIDGE_NOTIFY_CMD"] = ""
calls, logs = [], []
def fake(rc):
    def run(argv, **kw):
        calls.append((argv, kw)); return types.SimpleNamespace(returncode=rc, stdout="", stderr="")
    return run
check("no command configured -> nothing is run, False", N.notify("x", log=logs.append, run=fake(0)) is False and not calls, calls)
check("... and the log says why", logs and "APPLEBRIDGE_NOTIFY_CMD" in logs[-1], logs)

os.environ["APPLEBRIDGE_NOTIFY_CMD"] = "my-notifier --flag"
ok = N.notify("Basilisk crashed", log=logs.append, run=fake(0))
argv, kw = calls[-1]
check("the command runs through /bin/sh -c", argv == ["/bin/sh", "-c", "my-notifier --flag"], argv)
check("the message goes on stdin", kw["input"] == "AppleBridge guest2: Basilisk crashed\n", kw)
check("exit 0 -> True, logged as sent", ok is True and "sent" in logs[-1], logs)
check("exit 1 -> False, logged as FAILED", N.notify("x", log=logs.append, run=fake(1)) is False and "FAILED" in logs[-1], logs)
def boom(argv, **kw): raise OSError("no such file")
check("a command that cannot start never raises", N.notify("x", log=logs.append, run=boom) is False and "FAILED" in logs[-1], logs)

with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as out: pass
os.environ["APPLEBRIDGE_NOTIFY_CMD"] = f"cat > {out.name}"
check("a real shell command receives the text", N.notify("real") is True and open(out.name).read() == "AppleBridge guest2: real\n",
      open(out.name).read())

print(f"\n{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
