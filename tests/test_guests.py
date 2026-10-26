"""The guest table and the installer that turns it into launchd agents (D-028, docs/MULTI_GUEST.md).

No launchd and no network: the table is parsed from text, and the installer's plan is computed against a temporary
LaunchAgents folder.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import guests as G            # noqa: E402
import install_guests as I    # noqa: E402

TABLE = """
# name      daemon  control  ssh                           emulator
dev2019     9000    9001     -                             /Users/x/Documents/Basilisk/BasiliskII-ab.app
mbp2013     9010    9011     pitforster@192.0.2.158        /Users/y/Documents/My Emulators/BasiliskII-ab.app   # spaces
"""


def _raises(text, fragment):
    try:
        G.parse(text)
    except G.GuestTableError as e:
        assert fragment in str(e), f"{fragment!r} not in {e}"
        return
    raise AssertionError(f"accepted: {text!r}")


# --- the table ---------------------------------------------------------------------------------------------------

def test_a_table_parses_with_comments_and_an_emulator_path_with_spaces():
    t = G.parse(TABLE)
    assert [g.name for g in t] == ["dev2019", "mbp2013"]
    assert t[0].local and not t[1].local
    assert t[1].ssh == "pitforster@192.0.2.158"
    assert t[1].emulator == "/Users/y/Documents/My Emulators/BasiliskII-ab.app"


def test_duplicate_names_and_ports_are_refused_with_the_line():
    _raises("a 9000 9001 -\na 9010 9011 u@h", "already used on line 1")
    _raises("a 9000 9001 -\nb 9010 9001 u@h", "port 9001 already used")
    _raises("a 9000 9001 -\nb 9001 9011 u@h", "port 9001 already used")


def test_bad_values_are_refused():
    _raises("a 9000 9000 -", "both 9000")
    _raises("a 80 9001 -", "outside 1024-65535")
    _raises("a x 9001 -", "must be numbers")
    _raises("a.b 9000 9001 -", "must be 1-31")
    _raises("a" * 32 + " 9000 9001 -", "must be 1-31")
    _raises("a 9000 9001 somehost", "user@host")
    _raises("a 9000 9001", "need name")


def test_only_one_guest_may_run_on_the_hub_itself():
    _raises("a 9000 9001 -\nb 9010 9011 -", "one emulator per machine")


def test_no_table_is_an_empty_list_not_an_error():
    assert G.load("/nonexistent/guests.conf") == []


# --- what each row becomes ---------------------------------------------------------------------------------------

def test_the_9001_row_keeps_the_single_guest_names_every_other_row_is_suffixed():
    dev, m = G.parse(TABLE)
    assert dev.label("host") == "de.390er.applebridge-host"
    assert dev.label("hangwatch") == "de.390er.applebridge-hangwatch"
    assert dev.server_log() == "/tmp/applebridge_server.log"
    assert m.label("host") == "de.390er.applebridge-host-mbp2013"
    assert m.label("dialogwatch") == "de.390er.applebridge-dialogwatch-mbp2013"
    assert m.server_log() == "/tmp/applebridge_server_mbp2013.log"


def test_server_env_carries_port_name_and_the_hubs_address_but_refuses_host_verbs_only_remotely():
    dev, m = G.parse(TABLE)
    base = {"APPLEBRIDGE_HOST_IP": "192.0.2.232"}
    e = m.server_env(base)
    assert e["APPLEBRIDGE_DAEMON_PORT"] == "9010" and e["APPLEBRIDGE_CTRL_PORT"] == "9011"
    assert e["APPLEBRIDGE_GUEST_NAME"] == "mbp2013" and e["APPLEBRIDGE_HOST_IP"] == "192.0.2.232"
    assert e["APPLEBRIDGE_REMOTE_GUEST"] == "1"
    assert "APPLEBRIDGE_REMOTE_GUEST" not in dev.server_env(base)
    assert base == {"APPLEBRIDGE_HOST_IP": "192.0.2.232"}, "the hub's env must not be mutated"


def test_watch_env_points_a_remote_watchdog_at_that_machine():
    dev, m = G.parse(TABLE)
    assert dev.watch_env("hangwatch") == {"APPLEBRIDGE_CTRL_PORT": "9001",
                                          "APPLEBRIDGE_HANG_LOG": "/tmp/applebridge_hang_watch_9001.log"}
    h = m.watch_env("hangwatch")
    assert h["APPLEBRIDGE_GUEST_SSH"] == "pitforster@192.0.2.158"
    assert h["APPLEBRIDGE_GUEST_EMULATOR_APP"].endswith("My Emulators/BasiliskII-ab.app")
    assert "APPLEBRIDGE_GUEST_EMULATOR_APP" not in m.watch_env("dialogwatch")


# --- the installer's plan ----------------------------------------------------------------------------------------

def _agents(*labels):
    d = tempfile.mkdtemp()
    for lbl in labels:
        open(os.path.join(d, lbl + ".plist"), "w").close()
    return d


def test_the_plan_writes_three_agents_per_guest():
    write, remove = I.plan(G.parse(TABLE), {}, agents_dir=_agents())
    assert len(write) == 6 and remove == []
    host = [p for g, k, p in write if g.name == "mbp2013" and k == "host"][0]
    assert host["ProgramArguments"][0].endswith("AppleBridge/run_server.sh")
    assert host["EnvironmentVariables"]["APPLEBRIDGE_DAEMON_PORT"] == "9010"
    assert host["KeepAlive"] is True and host["RunAtLoad"] is True


def test_the_plan_retires_the_two_guest_setups_remote_agents_and_rows_that_are_gone():
    d = _agents("de.390er.applebridge-host", "de.390er.applebridge-host-remote",
                "de.390er.applebridge-hangwatch-remote", "de.390er.applebridge-dialogwatch-old2013",
                "de.390er.applebridge-alias")              # not a guest agent: never touched
    write, remove = I.plan(G.parse("dev2019 9000 9001 -"), {}, agents_dir=d)
    assert remove == ["de.390er.applebridge-dialogwatch-old2013", "de.390er.applebridge-hangwatch-remote",
                      "de.390er.applebridge-host-remote"], remove


def test_a_single_guest_run_writes_only_that_row_and_retires_nothing():
    # 2026-10-26: a full run re-bootstraps every row, dropping the live links of guests that were working.
    d = _agents("de.390er.applebridge-host-remote")      # would be retired by a full run
    write, remove = I.plan(G.parse(TABLE), {}, agents_dir=d, only=["mbp2013"])
    assert {g.name for g, _, _ in write} == {"mbp2013"} and len(write) == 3
    assert remove == [], "a partial run does not know which rows are gone"


def test_a_single_guest_run_refuses_a_name_not_in_the_table():
    try:
        I.plan(G.parse(TABLE), {}, agents_dir=_agents(), only=["claudia"])
    except G.GuestTableError as e:
        assert "claudia" in str(e)
        return
    raise AssertionError("an unknown name installed nothing and reported success")


LSOF = ("Python 680 pit 5u IPv4 0x1 0t0 TCP 192.168.3.232:9000 (LISTEN)\n"
        "Python 680 pit 6u IPv4 0x2 0t0 TCP 127.0.0.1:9001 (LISTEN)\n"
        "Python 681 pit 5u IPv4 0x3 0t0 TCP 192.168.3.232:9010 (LISTEN)\n"
        "Python 681 pit 6u IPv4 0x4 0t0 TCP 127.0.0.1:9011 (LISTEN)\n")


def test_listener_check_passes_healthy_rows_and_leaves_the_first_to_start_stack():
    res = I.G.listener_check(G.parse(TABLE), "192.168.3.232", LSOF)
    assert [(g.name, ok) for g, ok, _ in res] == [("mbp2013", True)], res


def test_listener_check_names_a_daemon_port_on_the_wrong_address_and_a_silent_control_port():
    wrong = LSOF.replace("192.168.3.232:9010", "192.168.3.154:9010").replace("127.0.0.1:9011 (LISTEN)", "x")
    (g, ok, line), = I.G.listener_check(G.parse(TABLE), "192.168.3.232", wrong)
    assert not ok and "192.168.3.232:9010 (daemon)" in line and ":9011 (control)" in line, line
    assert "install_guests.py --guest mbp2013" in line and "applebridge_server_mbp2013.log" in line, line


def test_listener_check_reads_a_wildcard_bind_the_way_lsof_prints_it():
    star = LSOF.replace("192.168.3.232:9010", "*:9010")
    assert all(ok for _, ok, _ in I.G.listener_check(G.parse(TABLE), "0.0.0.0", star))


def test_start_stack_checks_every_row_before_the_launch_and_does_not_stop_on_it():
    stack = open(os.path.join(os.path.dirname(__file__), "..", "host", "start_stack.sh")).read()
    call = stack.index('guests.py" --check-listeners "$EXPECT_BIND"')
    assert stack.index("[4/5]") < call < stack.index("[5/5]")
    assert "exit" not in stack[call:stack.index("[5/5]")], "another Mac's guest being off must not stop this Mac's launch"


def test_installed_labels_do_not_match_a_longer_kind_by_prefix():
    # "de.390er.applebridge-hostX" is not a host agent: only the kind itself or kind + "-<name>".
    d = _agents("de.390er.applebridge-hostX", "de.390er.applebridge-host-a")
    assert I.installed_guest_labels(d) == ["de.390er.applebridge-host-a"]


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
