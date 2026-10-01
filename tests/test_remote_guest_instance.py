"""A second host server for a guest on ANOTHER machine must not touch this one.

2026-10-01: a second emulator host (a MacBook with one Wi-Fi NIC) cannot reach a host server on itself (D-015), so its
guest's daemon dials a second address on the development Mac, where a second instance of host_server.py listens. Each
instance needs its own control port and log — and the HOST* verbs (real mouse, host screen, emulator window) act on
THIS Mac, i.e. on the LOCAL guest. Sent to the remote-guest instance they would drive the wrong machine, so that
instance refuses them, and it must refuse them before anything else: also when its daemon is not connected.

Checked here: the defaults are untouched without the variables; the variables reach the module; the refusal is placed
ahead of the daemon check in the dispatch; the launch scripts carry the second address and the second agent."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.join(os.path.dirname(HERE), "host")
PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": {detail}"))
    PASS += ok; FAIL += not ok


def module_values(env):
    code = ("import sys; sys.path.insert(0, %r); import host_server as h; "
            "print(h.CONTROL_PORT, h.LOG_PATH, h.REMOTE_GUEST)") % HOST
    e = {k: v for k, v in os.environ.items() if not k.startswith("APPLEBRIDGE_")}
    e.update(env)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=e, cwd=HOST, timeout=60)
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]


def test_defaults_unchanged():
    check("defaults: control 9001, the old log, no refusal",
          module_values({}) == "9001 /tmp/applebridge_server.log False", module_values({}))


def test_variables_reach_the_module():
    got = module_values({"APPLEBRIDGE_CTRL_PORT": "9011", "APPLEBRIDGE_LOG": "/tmp/x_remote.log",
                         "APPLEBRIDGE_REMOTE_GUEST": "1"})
    check("variables: control 9011, own log, refusal on", got == "9011 /tmp/x_remote.log True", got)


def test_refusal_precedes_the_daemon_check():
    src = open(os.path.join(HOST, "host_server.py")).read()
    guard = src.find('if cmd and REMOTE_GUEST and cmd.startswith("HOST")')
    daemon = src.find("if not server.connected and cmd not in")
    check("the HOST* refusal is in the dispatch", guard > 0)
    check("…and ahead of the not-connected rejection (HOSTSHOT/HOSTKEY are exempt from it)", 0 < guard < daemon,
          f"guard at {guard}, daemon check at {daemon}")


def test_launch_scripts_carry_the_second_instance():
    stack = open(os.path.join(HOST, "start_stack.sh")).read()
    check("start_stack.sh reads APPLEBRIDGE_REMOTE_HOST_IP", 'REMOTE_IP="${APPLEBRIDGE_REMOTE_HOST_IP:-}"' in stack)
    check("start_stack.sh places its alias in the privileged step", "inet $REMOTE_IP netmask $NETMASK alias" in stack)
    check("start_stack.sh skips the password only when BOTH aliases are present",
          'grep -q "inet ${REMOTE_IP} "' in stack)
    inst = open(os.path.join(HOST, "install_remote_guest_service.sh")).read()
    check("the remote agent sets REMOTE_GUEST=1",
          "<key>APPLEBRIDGE_REMOTE_GUEST</key>\n        <string>1</string>" in inst)
    check("the remote agent refuses control port 9001", '"$CTRL_PORT" = "9001"' in inst)
    deploy = open(os.path.join(HOST, "deploy_host.sh")).read()
    check("deploy_host.sh restarts the remote agent too", '"gui/$(id -u)/$LABEL-remote"' in deploy)


if __name__ == "__main__":
    test_defaults_unchanged(); test_variables_reach_the_module()
    test_refusal_precedes_the_daemon_check(); test_launch_scripts_carry_the_second_instance()
    print(f"\n{PASS}/{PASS + FAIL} passed")
    sys.exit(1 if FAIL else 0)
