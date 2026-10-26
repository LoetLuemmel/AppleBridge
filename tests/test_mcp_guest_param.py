"""The MCP `guest` parameter (D-028 phase 4): one MCP server for every guest of host/guests.conf.

No guests, no table on disk: the table is stubbed and the connection is a fake that records which control port the
call reached. That port is the whole contract - every tool reaches the bridge through get_connection().

Run: python3 tests/test_mcp_guest_param.py
"""

import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from mcp import mac_connection as C, tools as T  # noqa: E402

TABLE = T.guest_table.parse("dev 9000 9001 -\nfar 9010 9011 u@192.0.2.1 /x")


class FakeConn:
    def __init__(self):
        self.ports = []
        self.last_notes = None

    def is_connected(self):
        return True

    def send_command(self, command, timeout=30.0):
        self.ports.append(C.current_port())
        return 0, "ok", ""


def with_stubs(fn):
    def run():
        conn, load, handlers = FakeConn(), T.guest_table.load, dict(T.TOOL_HANDLERS)
        T.guest_table.load = lambda path=None: TABLE
        get = T.get_connection
        T.get_connection = lambda: conn
        try:
            fn(conn)
        finally:
            T.guest_table.load, T.get_connection = load, get
            T.TOOL_HANDLERS.clear(); T.TOOL_HANDLERS.update(handlers)
    run.__name__ = fn.__name__
    return run


def test_every_tool_but_the_reference_lookups_takes_a_guest():
    have = {t["name"] for t in T.TOOLS if "guest" in t["inputSchema"].get("properties", {})}
    assert have == {t["name"] for t in T.TOOLS} - T.NO_GUEST_PARAM, sorted(have)
    assert all("guest" not in t["inputSchema"].get("required", []) for t in T.TOOLS), "the parameter is optional"


@with_stubs
def test_a_named_guest_reaches_its_own_control_port_and_only_for_that_call(conn):
    T.call_tool("mpw_execute", {"command": "Echo a", "guest": "far"})
    T.call_tool("mpw_execute", {"command": "Echo b"})
    assert conn.ports == [9011, C.DEFAULT_PORT], conn.ports
    assert C.current_port() == C.DEFAULT_PORT


@with_stubs
def test_the_result_says_which_guest_answered(conn):
    r = T.call_tool("mpw_execute", {"command": "Echo a", "guest": "far"})
    assert r.get("guest") == "far", r
    assert "guest" not in T.call_tool("mpw_execute", {"command": "Echo a"})


@with_stubs
def test_an_unknown_guest_is_an_error_naming_the_table_and_runs_nothing(conn):
    r = T.call_tool("mpw_execute", {"command": "Echo a", "guest": "nobody"})
    assert not r["success"] and "dev, far" in r["error"], r
    assert conn.ports == []


@with_stubs
def test_host_local_tools_are_refused_per_call_for_a_guest_on_another_machine(conn):
    called = []
    T.TOOL_HANDLERS["mac_host_click"] = lambda **kw: called.append(kw) or {"success": True}
    r = T.call_tool("mac_host_click", {"x": 1, "y": 1, "guest": "far"})
    assert r.get("refused") and "'far' runs on another machine" in r["error"] and not called, r
    r = T.call_tool("mac_host_click", {"x": 1, "y": 1, "guest": "dev"})
    assert r.get("success") and called == [{"x": 1, "y": 1}], (r, called)


def test_without_a_table_a_named_guest_is_explained_not_guessed():
    load = T.guest_table.load
    T.guest_table.load = lambda path=None: []
    try:
        r = T.call_tool("mpw_execute", {"command": "Echo a", "guest": "far"})
    finally:
        T.guest_table.load = load
    assert not r["success"] and "single-guest" in r["error"], r


def test_the_reference_lookups_ignore_no_argument_they_do_not_have():
    assert "guest" not in T.TOOL_HANDLERS["mac_toolbox_lookup"].__code__.co_varnames


def test_the_pass_at_k_loop_compiles_on_the_named_guest():
    src = open(os.path.join(ROOT, "mcp", "tools.py")).read()
    body = src[src.index("def mac_generate_compilable"):src.index("TOOL_HANDLERS = {")]
    assert "APPLEBRIDGE_CTRL_PORT=str(current_port())" in body and "env=env" in body


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
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
