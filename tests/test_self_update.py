"""Tests for the daemon self-update host path: mac_status home= parsing and the
mac_update_daemon orchestration (stage as '<home>AppleBridge new' -> SWAPSELF).

The daemon-side rename is verified on-device; this pins the host contract — that
the tool stages beside the running daemon and sends the SWAPSELF verb, and that
mac_status surfaces the install folder the daemon reports.

Run: python3 tests/test_self_update.py   (or via pytest)
"""

import os
import sys
import types

_MCP = os.path.join(os.path.dirname(__file__), "..", "mcp")
sys.path.insert(0, _MCP)


def _load_tools():
    stub = types.ModuleType("mac_connection")
    stub.get_connection = lambda: None
    stub.MacConnection = object
    sys.modules["mac_connection"] = stub
    path = os.path.join(_MCP, "tools.py")
    src = open(path).read().replace("from .mac_connection import get_connection",
                                    "from mac_connection import get_connection")
    mod = types.ModuleType("abtools")
    mod.__file__ = os.path.abspath(path)
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


tools = _load_tools()


class FakeConn:
    def __init__(self, stat_body="", connected=True):
        self._stat = stat_body
        self._connected = connected
        self.sent = []

    def is_connected(self):
        return self._connected

    def send_command(self, cmd, timeout=30.0):
        self.sent.append(cmd)
        if cmd == "MACSTATUS":
            return (0, self._stat, "")
        if cmd == "SWAPSELF":
            return (0, "Swapped", "")
        return (0, "", "")


# --- mac_status home= parsing --------------------------------------------

def test_status_surfaces_home():
    body = ("host_connected=1;idle_seconds=0.0;missed_heartbeats=0;uptime=5;"
            "rx=1;tx=1;toolserver=1;net=OT;home=MeinMac:AppleBridge:;"
            "daemon_responding=1")
    tools.get_connection = lambda: FakeConn(stat_body=body)
    r = tools.mac_status()
    assert r["home"] == "MeinMac:AppleBridge:", r.get("home")
    assert r["toolserver_running"] is True


def test_status_home_absent_is_none():
    body = "host_connected=1;uptime=5;rx=1;tx=1;toolserver=0;net=OT;daemon_responding=1"
    tools.get_connection = lambda: FakeConn(stat_body=body)
    r = tools.mac_status()
    assert r["home"] is None, r.get("home")


# --- mac_update_daemon orchestration -------------------------------------

def _run_update(**kw):
    """Call mac_update_daemon with mac_put_file + connection stubbed; capture
    the staged path and the verbs sent."""
    conn = FakeConn(stat_body=kw.pop("stat_body",
        "toolserver=1;net=OT;home=MeinMac:AppleBridge:;daemon_responding=1"))
    tools.get_connection = lambda: conn
    staged = {}
    def fake_put(host_path, mac_path, type=None, creator=None, resource_path=None):
        staged["host_path"] = host_path
        staged["mac_path"] = mac_path
        staged["type"] = type
        staged["creator"] = creator
        return {"success": True}
    orig = tools.mac_put_file
    tools.mac_put_file = fake_put
    try:
        result = tools.mac_update_daemon("/tmp/AppleBridge.bin", **kw)
    finally:
        tools.mac_put_file = orig
    return result, staged, conn


def test_update_stages_beside_daemon_and_swaps():
    result, staged, conn = _run_update()
    assert result["success"] is True, result
    assert staged["mac_path"] == "MeinMac:AppleBridge:AppleBridge new", staged
    assert staged["type"] == "APPL" and staged["creator"] == "ABrg"
    assert "SWAPSELF" in conn.sent, conn.sent


def test_update_uses_explicit_mac_dir():
    result, staged, conn = _run_update(mac_dir="Macintosh HD:AppleBridge")
    assert staged["mac_path"] == "Macintosh HD:AppleBridge:AppleBridge new", staged
    assert result["success"] is True


def test_update_custom_staged_name():
    result, staged, conn = _run_update(staged_name="AppleBridge next")
    assert staged["mac_path"] == "MeinMac:AppleBridge:AppleBridge next", staged


def test_update_errors_when_no_home_and_no_mac_dir():
    result, staged, conn = _run_update(
        stat_body="toolserver=1;net=OT;daemon_responding=1")   # no home=
    assert result["success"] is False
    assert "mac_dir" in (result.get("error") or ""), result


def test_update_reports_swap_failure():
    conn = FakeConn(stat_body="home=MeinMac:AppleBridge:;daemon_responding=1")
    # daemon refuses the rename -> STATUS:-1 with an err code
    conn.send_command = lambda cmd, timeout=30.0: (
        (0, "home=MeinMac:AppleBridge:;daemon_responding=1", "") if cmd == "MACSTATUS"
        else (-1, "", "swap err -43"))
    tools.get_connection = lambda: conn
    orig = tools.mac_put_file
    tools.mac_put_file = lambda *a, **k: {"success": True}
    try:
        r = tools.mac_update_daemon("/tmp/AppleBridge.bin")
    finally:
        tools.mac_put_file = orig
    assert r["success"] is False and r["stage"] == "swapself", r
    assert "-43" in (r.get("error") or ""), r


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERR  {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
