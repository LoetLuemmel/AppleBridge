"""Tests for host/bridge_doctor.py — the cross-layer stack diagnosis.

The doctor's whole value is naming the RIGHT layer, so these tests drive it
from canned command output: every scenario below cost a real debugging session
at least once, and each must produce its own distinct finding rather than the
generic "bridge is down".

Run: python3 tests/test_bridge_doctor.py   (or via pytest)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import bridge_doctor as bd  # noqa: E402

UID = 501
HOST_IP = "192.168.3.154"

LSOF_LISTEN_OK = (
    "COMMAND   PID       USER   FD   TYPE  DEVICE SIZE/OFF NODE NAME\n"
    "Python  10989 pitforster    4u  IPv4  0x5627      0t0  TCP "
    f"{HOST_IP}:9000 (LISTEN)\n"
    "Python  10989 pitforster    5u  IPv4  0x885d      0t0  TCP "
    "127.0.0.1:9001 (LISTEN)\n")

LSOF_ESTABLISHED = (
    "COMMAND   PID       USER   FD   TYPE  DEVICE SIZE/OFF NODE NAME\n"
    "Python  10989 pitforster    6u  IPv4  0x3eb2      0t0  TCP "
    f"{HOST_IP}:9000->192.168.3.244:2048 (ESTABLISHED)\n")


def ifconfig(en0_addrs=(HOST_IP, "192.168.3.240"), en8_addrs=()):
    """Render an ifconfig dump with the given addresses per interface."""
    out = ["lo0: flags=8049<UP,LOOPBACK> mtu 16384",
           "\tinet 127.0.0.1 netmask 0xff000000"]
    for name, addrs in (("en0", en0_addrs), ("en8", en8_addrs)):
        out.append(f"{name}: flags=8863<UP,BROADCAST,RUNNING> mtu 1500")
        out.append("\tether 00:0c:6c:0b:0e:ad")
        for a in addrs:
            out.append(f"\tinet {a} netmask 0xffffff00 broadcast 192.168.3.255")
    return "\n".join(out) + "\n"


def make_run(*, launchd_loaded=True, launchd_disabled=False, listening=True,
             established=True, en0=(HOST_IP, "192.168.3.240"), en8=(),
             default_if="en0", basilisk=True, etherhelper=True,
             sheepshaver=False):
    """Build a fake `run` that answers each probe command from the flags above."""
    def run(argv, timeout=4.0):
        cmd = " ".join(argv)
        if cmd == "launchctl list":
            return (f"10989\t0\t{bd.LAUNCHD_LABEL}\n" if launchd_loaded
                    else "-\t0\tcom.apple.something\n")
        if cmd.startswith("launchctl print-disabled"):
            flag = "true" if launchd_disabled else "false"
            return f'\t"{bd.LAUNCHD_LABEL}" => {flag}\n'
        if "-sTCP:LISTEN" in cmd:
            return LSOF_LISTEN_OK if listening else ""
        if "-sTCP:ESTABLISHED" in cmd:
            return LSOF_ESTABLISHED if established else ""
        if cmd == "ifconfig":
            return ifconfig(en0, en8)
        if cmd.startswith("route"):
            return (f"   route to: default\n  interface: {default_if}\n"
                    if default_if else "")
        if cmd.startswith("pgrep"):
            pattern = argv[-1]
            if pattern == "BasiliskII":
                return "11000 /Applications/BasiliskII.app/…/BasiliskII\n" if basilisk else ""
            if pattern == "SheepShaver":
                return "12000 /Applications/SheepShaver\n" if sheepshaver else ""
            if pattern == "etherhelpertool":
                return "11002 /…/etherhelpertool en8\n" if etherhelper else ""
        return ""
    return run


def make_read(ether="etherhelper/en8", netmode="etherhelper/en8"):
    def read(path):
        if path.endswith(".netmode"):
            return (netmode + "\n") if netmode else ""
        return ("disk /Users/pit/System761.dmg\nscreen win/1024/768\n"
                + (f"ether {ether}\n" if ether else "")
                + "ramsize 130023424\n")
    return read


def report(**kw):
    read_kw = {k: kw.pop(k) for k in ("ether", "netmode") if k in kw}
    # Whether the launchd agent is INSTALLED is now a separate input from
    # whether it is loaded; default True so every pre-existing case keeps its
    # meaning, and the two cases that care state it.
    installed = kw.pop("agent_installed", True)
    return bd.collect(run=make_run(**kw), read=make_read(**read_kw), uid=UID,
                      host_ip=HOST_IP, exists=lambda _p: installed)


def keys(rep):
    return {f["key"] for f in rep["findings"]}


# --- the healthy stack ------------------------------------------------------
def test_healthy_stack_is_ok():
    rep = report()
    assert rep["verdict"] == "ok", rep["findings"]
    assert rep["ok"] is True
    assert rep["findings"] == []


def test_healthy_stack_reports_observed_facts():
    p = report()["probes"]
    assert p["launchd"]["loaded"] is True and p["launchd"]["pid"] == 10989
    assert p["sockets"]["listen"]["daemon_port"] == HOST_IP
    assert p["sockets"]["listen"]["control_port"] == "127.0.0.1"
    assert p["sockets"]["guest_peer_ip"] == "192.168.3.244"
    assert p["network"]["host_ip_interfaces"] == ["en0"]
    assert p["network"]["default_route_interface"] == "en0"
    assert p["processes"]["etherhelpertool"]["pid"] == 11002
    assert p["emulator_prefs"]["ether"] == "etherhelper/en8"


# --- host server layer ------------------------------------------------------
def test_launchd_disabled_is_named_as_deliberate():
    rep = report(launchd_disabled=True, listening=False, established=False)
    assert "launchd_disabled" in keys(rep)
    assert rep["verdict"] == "error"
    fix = [f for f in rep["findings"] if f["key"] == "launchd_disabled"][0]["fix"]
    assert "launchctl enable" in fix and "bootstrap" in fix


def test_launchd_absent_reported_without_disabled_claim():
    """An INSTALLED agent that is not loaded: bootstrap advice is correct."""
    rep = report(launchd_loaded=False, listening=False, established=False)
    assert "launchd_absent" in keys(rep)
    assert "launchd_disabled" not in keys(rep)


def test_a_machine_without_the_agent_is_not_told_to_bootstrap_it():
    """R13: most installations have no plist and start the server by hand.

    Telling their owner to bootstrap one names a component they never had — a
    false lead in an authoritative tone, which is worse than silence.
    """
    rep = report(launchd_loaded=False, listening=False, established=False,
                 agent_installed=False)
    k = keys(rep)
    assert "launchd_absent" not in k, "must not advise bootstrapping an absent plist"
    assert "host_server_not_running" in k, "the real problem is that nothing listens"
    remedy = [f for f in rep["findings"] if f["key"] == "host_server_not_running"][0]
    assert "run_server.sh" in remedy["fix"]
    assert "/dev/null" in remedy["fix"], "the R12 trap must not be walked into here"


def test_a_hand_started_server_that_IS_listening_is_not_an_error():
    """No agent and no complaint: this is simply how that machine is run."""
    rep = report(launchd_loaded=False, agent_installed=False)
    assert "host_server_not_running" not in keys(rep)
    assert "launchd_absent" not in keys(rep)


def test_loaded_but_no_control_port_flags_crash_loop():
    rep = report(listening=False, established=False)
    assert "control_port_closed" in keys(rep)


# --- the .154 alias placement rule -----------------------------------------
def test_duplicate_alias_is_an_error_with_a_removal_command():
    rep = report(en8=(HOST_IP,))
    assert "host_ip_duplicate" in keys(rep)
    f = [x for x in rep["findings"] if x["key"] == "host_ip_duplicate"][0]
    assert f["level"] == "error"
    assert f"ifconfig en8 -alias {HOST_IP}" in f["fix"]     # strip the stray one
    assert "en0" not in f["fix"].split(";")[0]              # keep the good one


def test_alias_on_wrong_interface_is_flagged():
    rep = report(en0=("192.168.3.240",), en8=(HOST_IP,), default_if="en0")
    assert "host_ip_wrong_interface" in keys(rep)


def test_missing_alias_is_flagged():
    rep = report(en0=("192.168.3.240",))
    assert "host_ip_missing" in keys(rep)


# --- emulator transport backend --------------------------------------------
def test_slirp_warns_about_appletalk_even_though_tcp_works():
    rep = report(ether="slirp", netmode=None)
    f = [x for x in rep["findings"] if x["key"] == "ether_slirp"][0]
    assert "AppleTalk" in f["message"]          # the non-obvious consequence
    assert rep["verdict"] == "warn"
    assert rep["ok"] is True                    # TCP works: not a hard failure


def test_backend_drift_against_netmode_is_flagged():
    rep = report(ether="slirp", netmode="etherhelper/en8")
    assert {"ether_slirp", "ether_drift"} <= keys(rep)


def test_dead_etherhelper_is_an_error_only_when_that_backend_is_selected():
    rep = report(etherhelper=False, established=False)
    assert "etherhelper_dead" in keys(rep)
    # On slirp there IS no helper process, so its absence must not be reported.
    rep = report(etherhelper=False, established=False, ether="slirp",
                 netmode=None)
    assert "etherhelper_dead" not in keys(rep)


def test_no_helper_without_a_running_emulator_is_not_an_error():
    rep = report(basilisk=False, etherhelper=False, established=False)
    assert "etherhelper_dead" not in keys(rep)
    assert "emulator_down" in keys(rep)


# --- link state -------------------------------------------------------------
def test_emulator_up_but_no_guest_link_is_informational():
    rep = report(established=False)
    assert "no_guest_link" in keys(rep)
    assert rep["verdict"] == "info"
    assert rep["ok"] is True


def test_sheepshaver_counts_as_a_running_emulator():
    rep = report(basilisk=False, sheepshaver=True, etherhelper=False,
                 established=False)
    assert "emulator_down" not in keys(rep)


# --- short_reason: what the control port replies while the daemon is down ----
def test_short_reason_names_the_worst_finding_first():
    msg = bd.short_reason(report(launchd_disabled=True, en8=(HOST_IP,),
                                 listening=False, established=False))
    assert "DISABLED" in msg and "Fix:" in msg


def test_short_reason_prefers_errors_over_warnings():
    msg = bd.short_reason(report(ether="slirp", netmode=None, en8=(HOST_IP,)))
    assert "MORE THAN ONE interface" in msg        # the error, not the slirp warn


def test_short_reason_falls_back_to_the_retry_hint_when_all_is_well():
    msg = bd.short_reason(report(established=False))
    assert "30 s" in msg or "30s" in msg


def test_short_reason_reports_a_missing_emulator_plainly():
    rep = report(basilisk=False, etherhelper=False, established=False)
    assert "No emulator" in bd.short_reason(rep)


# --- robustness: probes must degrade, never raise ---------------------------
def test_every_probe_failing_still_yields_a_report():
    """Every input is injected, including whether the agent exists.

    Left to the real filesystem this passed on a machine that happens to have
    the plist and failed on one that does not — which is the very divergence
    bridge_doctor is for. A test that reads the developer's disk cannot detect
    that class of fault; it embodies it.
    """
    rep = bd.collect(run=lambda argv, timeout=4.0: "",
                     read=lambda path: "", uid=UID, host_ip=HOST_IP,
                     exists=lambda _p: True)         # agent installed, not loaded
    assert rep["verdict"] == "error"          # missing job + missing alias
    assert {"launchd_absent", "host_ip_missing"} <= keys(rep)
    assert isinstance(bd.format_text(rep), str)


def test_every_probe_failing_on_a_machine_without_the_agent():
    """The same collapse on an installation that has no launchd job at all."""
    rep = bd.collect(run=lambda argv, timeout=4.0: "",
                     read=lambda path: "", uid=UID, host_ip=HOST_IP,
                     exists=lambda _p: False)
    assert rep["verdict"] == "error"
    k = keys(rep)
    assert "host_server_not_running" in k
    assert "launchd_absent" not in k
    assert isinstance(bd.format_text(rep), str)


def test_run_helper_survives_a_missing_binary():
    assert bd._run(["definitely-not-a-real-binary-xyz"]) == ""


def test_format_text_lists_the_probe_lines_and_verdict():
    text = bd.format_text(report())
    for expected in ("launchd job:", ":9000 / :9001:", "default route:",
                     "emulator ether:", "guest peer IP:", "verdict: ok"):
        assert expected in text, text


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
