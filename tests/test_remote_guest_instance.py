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
    # D-027 (2026-10-26): no script places an alias for either instance any more - each address must be a real one.
    check("start_stack.sh places no alias at all", "netmask $NETMASK alias" not in stack)
    check("start_stack.sh no longer handles the remote address", "APPLEBRIDGE_REMOTE_HOST_IP" not in stack)
    # D-028 (2026-10-26): the remote instance is a row of the guest table, written by install_guests.py.
    sys.path.insert(0, HOST)
    import guests as G
    remote = G.parse("dev 9000 9001 -\nother 9010 9011 u@192.0.2.1 /x")[1]
    check("a remote row's instance sets REMOTE_GUEST=1", remote.server_env({}).get("APPLEBRIDGE_REMOTE_GUEST") == "1")
    deploy = open(os.path.join(HOST, "deploy_host.sh")).read()
    check("deploy_host.sh restarts every other guest's instance", '"$HOME/Library/LaunchAgents/$LABEL"-*.plist' in deploy)
    check("…but only when the runtime changed (a local relaunch must not cut the remote guest's work)",
          'if [ "$CHANGED" = "1" ]; then' in deploy)


def test_mcp_side():
    """the MCP server of a remote guest: its port from the environment, the host-local tools refused before running"""
    root = os.path.dirname(HERE)
    code = ("import sys; sys.path.insert(0, %r); from mcp import mac_connection as C, tools as T; "
            "r = T.call_tool('mac_host_click', {'x': 1, 'y': 1}); "
            "print(C.DEFAULT_PORT, T.REMOTE_GUEST, r.get('refused'), sorted(T.HOST_LOCAL_TOOLS) == sorted(set(T.HOST_LOCAL_TOOLS) & set(T.TOOL_HANDLERS)))") % root
    e = {k: v for k, v in os.environ.items() if not k.startswith("APPLEBRIDGE_")}
    e.update({"APPLEBRIDGE_CTRL_PORT": "9011", "APPLEBRIDGE_REMOTE_GUEST": "1"})
    r = subprocess.run(["uv", "run", "python", "-c", code], capture_output=True, text=True, env=e, cwd=root, timeout=120)
    got = (r.stdout.strip().splitlines() or [r.stderr[-200:]])[-1]
    check("MCP: port 9011, remote on, mac_host_click refused, every guarded name is a real tool", got == "9011 True True True", got)
    # the remote server is registered LOCALLY (claude mcp add --scope local): a fresh clone has no second guest, and a
    # committed entry would start a server pointing at a port nobody listens on
    cfg = open(os.path.join(root, ".mcp.json")).read()
    check(".mcp.json (shipped) does NOT register a remote server", "APPLEBRIDGE_REMOTE_GUEST" not in cfg)


def test_guest_remote():
    """the SSH runner: inactive without its variable; cliclick resolves to the copy built for the remote macOS; every
    other argument is shell-quoted; the deploy carries the module (guest_input imports it)"""
    sys.path.insert(0, HOST)
    import importlib
    os.environ.pop("APPLEBRIDGE_GUEST_SSH", None)
    import guest_remote; importlib.reload(guest_remote)
    check("guest_remote inactive without APPLEBRIDGE_GUEST_SSH", not guest_remote.active())
    cmd = guest_remote.remote_command(["cliclick", "m:1,2", "w:150"])
    check("cliclick -> $HOME/<tools>/cliclick, unquoted so $HOME expands", cmd.startswith("$HOME/Documents/BasiliskII/tools/cliclick "), cmd)
    cmd = guest_remote.remote_command(["osascript", "-e", 'tell application "X" to activate'])
    check("arguments are shell-quoted", cmd == "osascript -e 'tell application \"X\" to activate'", cmd)
    deploy = open(os.path.join(HOST, "deploy_host.sh")).read()
    check("deploy_host.sh ships guest_remote.py (guest_input imports it)", "guest_remote.py" in deploy.split("RUNTIME_FILES=(")[1].split(")")[0])
    for mod in ("guest_input.py", "fb_export.py"):
        check(f"{mod} routes through guest_remote", "guest_remote.active()" in open(os.path.join(HOST, mod)).read())


if __name__ == "__main__":
    test_defaults_unchanged(); test_variables_reach_the_module()
    test_refusal_precedes_the_daemon_check(); test_launch_scripts_carry_the_second_instance(); test_mcp_side(); test_guest_remote()
    print(f"\n{PASS}/{PASS + FAIL} passed")
    sys.exit(1 if FAIL else 0)
