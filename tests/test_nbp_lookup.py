"""Tests for the NBPLOOK verb's host half — mac_appletalk_browse.

The daemon half (PLookupName/NBPExtract against the .MPP driver) is verified
on-device; what is pinned here is everything the host does with it: the
positional verb it builds, the tab-separated lines it parses back, and the
distinction that motivated the feature — "AppleTalk is off" must NOT arrive
looking like "no servers found".

Run: python3 tests/test_nbp_lookup.py   (or via pytest)
"""

import os
import sys

# Import tools.py directly, stubbing its relative import of mac_connection, so we
# avoid the name clash between this repo's ./mcp package and the installed `mcp`
# SDK (same approach as test_input_modifiers.py).
_MCP = os.path.join(os.path.dirname(__file__), "..", "mcp")
sys.path.insert(0, _MCP)


def _load_tools():
    import types
    stub = types.ModuleType("mac_connection")
    stub.get_connection = lambda: None
    stub.MacConnection = object
    sys.modules.setdefault("mac_connection", stub)
    path = os.path.join(_MCP, "tools.py")
    src = open(path).read().replace("from .mac_connection import get_connection",
                                    "from mac_connection import get_connection")
    mod = types.ModuleType("abtools")
    mod.__file__ = os.path.abspath(path)
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


tools = _load_tools()

# One real reply, captured live from the daemon on 2026-07-25 (a netatalk server
# registering three entities under one name).
LIVE_ALL = ("ApfelNetz\tAFPServer\t*\t65280.79.128\r"
            "ApfelNetz\tnetatalk\t*\t65280.79.4\r"
            "ApfelNetz\tWorkstation\t*\t65280.79.4\r")


def _fake_conn(status=0, stdout="", stderr=""):
    """Stand in for the control-port connection, recording the verb sent."""
    seen = {}

    class Conn:
        def send_command(self, verb, timeout=None):
            seen["verb"] = verb
            seen["timeout"] = timeout
            return status, stdout, stderr

    tools.get_connection = lambda: Conn()
    return seen


# --- the verb the host builds ----------------------------------------------
def test_defaults_target_the_choosers_appleshare_list():
    seen = _fake_conn(stdout="")
    tools.mac_appletalk_browse()
    assert seen["verb"] == "NBPLOOK:AFPServer:*:="


def test_fields_are_positional_type_zone_object():
    seen = _fake_conn(stdout="")
    tools.mac_appletalk_browse(entity_type="LaserWriter", zone="Buero", name="HP")
    assert seen["verb"] == "NBPLOOK:LaserWriter:Buero:HP"


def test_empty_arguments_fall_back_to_the_wildcards():
    seen = _fake_conn(stdout="")
    tools.mac_appletalk_browse(entity_type="", zone="", name="")
    assert seen["verb"] == "NBPLOOK:AFPServer:*:="


def test_timeout_exceeds_the_daemons_nbp_retry_window():
    # NBP burns ~3 s of protocol time before it can answer at all; a default-ish
    # timeout would make a healthy lookup look like a dead daemon.
    seen = _fake_conn(stdout="")
    tools.mac_appletalk_browse()
    assert seen["timeout"] >= 10.0


# --- parsing the reply ------------------------------------------------------
def test_parses_a_live_multi_entity_reply():
    _fake_conn(stdout=LIVE_ALL)
    r = tools.mac_appletalk_browse(entity_type="=")
    assert r["success"] is True
    assert r["count"] == 3
    assert r["entities"][0] == {"name": "ApfelNetz", "type": "AFPServer",
                                "zone": "*", "address": "65280.79.128"}
    assert [e["type"] for e in r["entities"]] == ["AFPServer", "netatalk",
                                                  "Workstation"]


def test_accepts_lf_framed_lines_too():
    # The daemon emits CR; classic-Mac C maps '\n' to CR, so both must parse.
    _fake_conn(stdout="Server\tAFPServer\t*\t1.2.3\n")
    assert tools.mac_appletalk_browse()["count"] == 1


def test_short_or_blank_lines_are_skipped_not_crashed():
    _fake_conn(stdout="\rgarbage\rServer\tAFPServer\t*\t1.2.3\r\r")
    r = tools.mac_appletalk_browse()
    assert r["count"] == 1


def test_names_containing_spaces_survive_the_tab_split():
    _fake_conn(stdout="Pit's File Server\tAFPServer\tBuero 2\t65280.79.128\r")
    e = tools.mac_appletalk_browse()["entities"][0]
    assert e["name"] == "Pit's File Server"
    assert e["zone"] == "Buero 2"


# --- the distinction the feature exists for --------------------------------
def test_nothing_answering_is_success_with_an_explicit_note():
    _fake_conn(status=0, stdout="")
    r = tools.mac_appletalk_browse(entity_type="LaserWriter")
    assert r["success"] is True          # a lookup that found nothing WORKED
    assert r["count"] == 0
    assert "no entities" in r["note"]


def test_appletalk_switched_off_is_an_error_not_an_empty_list():
    _fake_conn(status=-1, stdout="",
               stderr="AppleTalk is inactive (see the Chooser / AppleTalk control panel)")
    r = tools.mac_appletalk_browse()
    assert r["success"] is False
    assert "inactive" in r["error"]
    assert "entities" not in r          # no empty list to misread as "none found"


def test_truncation_warning_is_surfaced_alongside_the_results():
    _fake_conn(status=0, stdout=LIVE_ALL,
               stderr="result truncated at 64 entities")
    r = tools.mac_appletalk_browse(entity_type="=")
    assert r["success"] is True
    assert r["count"] == 3
    assert "truncated" in r["note"]


def test_transport_failure_is_reported_not_swallowed():
    def boom():
        raise RuntimeError("Mac daemon not connected")
    tools.get_connection = boom
    r = tools.mac_appletalk_browse()
    assert r["success"] is False
    assert "not connected" in r["error"]


def test_tool_is_registered_in_the_dispatch_table_and_schema():
    assert "mac_appletalk_browse" in tools.TOOL_HANDLERS
    names = [t["name"] for t in tools.TOOLS]
    assert "mac_appletalk_browse" in names


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
