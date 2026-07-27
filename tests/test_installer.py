"""Tests for host/install_bridge.py — the host-side installer, slirp branch.

The installer's most important behaviours are the ones where it does NOTHING: it
must refuse a host that already runs the etherhelper branch (D-018) and refuse to
rewrite an emulator's prefs underneath it. Both are driven here from canned probe
output, so the suite never touches a real prefs file, a real disk image, or the
developer machine's production configuration — which is itself one of the
scenarios below.

Every case corresponds to a numbered requirement in
docs/INSTALLER_REQUIREMENTS.md, named in the test.

Run: python3 tests/test_installer.py   (or via pytest)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
import install_bridge as ib  # noqa: E402

HOST_ADDRS = [("en0", "192.168.3.213")]
TWO_NICS = [("en0", "192.168.3.213"), ("en8", "192.168.3.154")]


def probes(ether="slirp", intended="slirp", helper=False, running=False,
           addresses=None, app="/Applications/BasiliskII.app"):
    """A probe dict, as `probe()` would return it, without touching the host."""
    return {
        "bundle": {"app": app, "helper": helper, "source": "well-known location"},
        "processes": {"basilisk": {"pid": 1, "cmd": "BasiliskII"} if running else None,
                      "sheepshaver": None, "etherhelpertool": None},
        "emulator_prefs": {"ether": ether, "intended": intended,
                           "prefs_path": "/tmp/prefs"},
        "addresses": HOST_ADDRS if addresses is None else addresses,
        "host_ip": ("0.0.0.0", "default"),
        "local_env_exists": False,
        "paths": {"prefs": "/tmp/prefs", "netmode": "/tmp/prefs.netmode",
                  "local_env": "/tmp/local.env"},
    }


def keys(items):
    return [i["key"] for i in items]


# --- the refusal D-018 is built on -----------------------------------------

def test_a_working_etherhelper_host_is_refused_not_converted():
    # The developer machine: etherhelper configured AND a helper in the bundle.
    # Its AppleTalk/AFP setup is production, so "install" must mean "hands off".
    plan = ib.decide(probes(ether="etherhelper/en8", intended="etherhelper/en8",
                            helper=True, addresses=TWO_NICS))
    assert keys(plan["refusals"]) == ["etherhelper_in_use"]
    assert not plan["steps"], "a refusal must leave no steps to apply"


def test_the_refusal_names_the_override_and_the_cost():
    plan = ib.decide(probes(ether="etherhelper/en8", helper=True))
    detail = plan["refusals"][0]["detail"]
    assert "--force-slirp" in detail
    assert "AppleTalk" in detail, "the cost must be stated, not buried (D-018)"


def test_force_slirp_converts_but_still_says_what_is_being_given_up():
    plan = ib.decide(probes(ether="etherhelper/en8", helper=True,
                            addresses=TWO_NICS), force_slirp=True)
    assert not plan["refusals"]
    assert "set_backend" in keys(plan["steps"])
    assert "etherhelper_available" in keys(plan["notes"])


def test_an_etherhelper_config_without_a_helper_is_not_the_refusal_case():
    # R8: an absent etherhelpertool settles the branch before anything else. A
    # prefs file naming a backend the bundle cannot provide is stale, not sacred.
    plan = ib.decide(probes(ether="etherhelper/en8", intended="etherhelper/en8",
                            helper=False))
    assert not plan["refusals"]
    assert "no_etherhelper" in keys(plan["notes"])


# --- the other refusal ------------------------------------------------------

def test_a_running_emulator_blocks_every_write():
    plan = ib.decide(probes(running=True))
    assert keys(plan["refusals"]) == ["emulator_running"]
    assert not plan["steps"]


def test_the_running_emulator_refusal_does_not_suggest_killing_it():
    # D-004: hard-terminating BasiliskII can corrupt the guest's disk image.
    detail = ib.decide(probes(running=True))["refusals"][0]["detail"]
    assert "mac_shutdown" in detail and "Never hard-kill" in detail


# --- the steps --------------------------------------------------------------

def test_an_already_slirp_host_only_needs_config_not_a_backend_rewrite():
    plan = ib.decide(probes(ether="slirp", intended="slirp"))
    assert "set_backend" not in keys(plan["steps"])
    assert "backend_already_slirp" in keys(plan["notes"])
    assert "write_local_env" in keys(plan["steps"])


def test_a_fresh_host_gets_backend_config_and_a_launch_path():
    plan = ib.decide(probes(ether=None, intended=None))
    assert keys(plan["steps"]) == ["set_backend", "write_local_env", "install_agent"]


def test_no_agent_replaces_the_step_with_the_redirect_that_r12_is_about():
    plan = ib.decide(probes(), want_agent=False)
    assert "install_agent" not in keys(plan["steps"])
    note = [n for n in plan["notes"] if n["key"] == "manual_launch"][0]
    assert "< /dev/null" in note["message"], "the redirect IS the point (R12)"
    assert "control port" in note["message"]


def test_a_single_nic_host_is_told_why_slirp_is_the_only_option():
    plan = ib.decide(probes(addresses=HOST_ADDRS))
    note = [n for n in plan["notes"] if n["key"] == "single_interface"][0]
    assert "D-015" in note["message"]


# --- local.env: R1, R2, R7, R15 --------------------------------------------

def test_the_generated_local_env_carries_no_host_address():
    # R2: a wrong default address is worse than none. R7: slirp needs 0.0.0.0.
    text = ib.render_local_env("/Applications/BasiliskII.app")
    assert "APPLEBRIDGE_HOST_IP=" not in text
    assert "0.0.0.0" in text


def test_the_generated_local_env_records_the_discovered_emulator():
    text = ib.render_local_env("/Users/x/BasiliskII.app")
    assert "APPLEBRIDGE_EMULATOR_APP=/Users/x/BasiliskII.app" in text


def test_the_generated_local_env_omits_the_other_branchs_keys():
    # R15: a bridge and a wired interface are properties of etherhelper, not of
    # AppleBridge. Carrying them here is how the two launchers contradicted
    # each other about the same NIC.
    text = ib.render_local_env(None)
    assert "APPLEBRIDGE_WIRED_IF" not in text
    assert "APPLEBRIDGE_BRIDGE" not in text


# --- what the operator is told: R5, R7, R10, R11 ---------------------------

def test_the_guest_checklist_labels_whose_address_each_field_is():
    lines = "\n".join(ib.guest_checklist(HOST_ADDRS))
    assert "GUEST's OWN" in lines and "THE HOST's" in lines, \
        "R5 is one word with two meanings; the labels are the fix"


def test_the_checklist_carries_the_resolver_that_gets_forgotten():
    lines = "\n".join(ib.guest_checklist(HOST_ADDRS))
    assert ib.GUEST_RESOLVER in lines and "23045" in lines


def test_the_checklist_warns_off_the_slirp_router_as_a_host_address():
    lines = "\n".join(ib.guest_checklist(HOST_ADDRS))
    assert f"NEVER `{ib.GUEST_ROUTER}`" in lines


def test_the_checklist_names_where_the_prefs_file_lives():
    # R3: not the installation folder, which is where a reasonable person looks.
    lines = "\n".join(ib.guest_checklist(HOST_ADDRS))
    assert "System Folder:Preferences:AppleBridge Prefs" in lines


def test_the_checklist_survives_a_host_with_no_readable_addresses():
    lines = "\n".join(ib.guest_checklist([]))
    assert "none could be read" in lines, "must not print `IP=` with nothing after it"


def test_the_tier_report_calls_toolserver_optional_not_missing():
    lines = "\n".join(ib.tier_report())
    assert "OPTIONAL" in lines and "not a" in lines
    assert "mpw_execute" in lines and "mac_compile" in lines


def test_the_tier_report_states_the_applealk_cost_of_this_branch():
    lines = "\n".join(ib.tier_report())
    assert "NOT ON THIS BRANCH: AppleTalk" in lines
    assert "mac_appletalk_browse" in lines


def test_the_exposure_report_orders_the_token_pair_guest_first():
    lines = "\n".join(ib.exposure_report(HOST_ADDRS))
    guest, host = lines.index("guest first"), lines.index("host second")
    assert guest < host, "the reverse order locks the bridge out"
    assert "9000" in lines


# --- rewrite_ip_line: R20, and only the one key ----------------------------

PREFS_CR = (b"# AppleBridge preferences\r"
            b"IP=192.168.3.154\r"
            b"DEBUG=0\r"
            b"NET=OT\r"
            b"HOME=MeinMac:AppleBridge:\r"
            b"APP=MeinMac:AB-ToolServer:ToolServer\r"
            b"WIN=40,8,300,600\r")


def test_only_the_ip_line_changes_and_cr_endings_survive():
    out = ib.rewrite_ip_line(PREFS_CR, "192.168.3.213")
    assert b"IP=192.168.3.213\r" in out
    assert b"\n" not in out, "an LF here is the R20 failure: the guest sees one line"
    for key in (b"DEBUG=0", b"NET=OT", b"HOME=MeinMac:AppleBridge:",
                b"APP=MeinMac:AB-ToolServer:ToolServer", b"WIN=40,8,300,600"):
        assert key in out, f"clobbered {key!r} — the daemon's own settings"


def test_the_line_count_is_unchanged():
    before, after = PREFS_CR.count(b"\r"), \
        ib.rewrite_ip_line(PREFS_CR, "10.0.0.1").count(b"\r")
    assert before == after


def test_an_absent_ip_key_is_appended_with_the_files_own_line_ending():
    out = ib.rewrite_ip_line(b"DEBUG=1\rNET=OT\r", "192.168.3.213")
    assert out.endswith(b"IP=192.168.3.213\r")
    assert b"\n" not in out


def test_a_second_ip_key_is_left_alone_rather_than_multiplied():
    # A duplicated key is already broken; silently rewriting both would hide it.
    out = ib.rewrite_ip_line(b"IP=1.1.1.1\rIP=2.2.2.2\r", "3.3.3.3")
    assert out == b"IP=3.3.3.3\rIP=2.2.2.2\r"


# --- seed_guest_prefs refusals ---------------------------------------------

def test_seeding_refuses_while_an_emulator_runs():
    ok, msg = ib.seed_guest_prefs("/tmp/img.dmg", "192.168.3.213",
                                  probes(running=True), run=lambda *a, **k: "")
    assert not ok
    assert "R14" in msg, "the reason is the daemon's in-memory copy, not caution"


def test_seeding_refuses_a_wildcard_address():
    # 0.0.0.0 is what the HOST binds; it is not something a guest can dial.
    ok, msg = ib.seed_guest_prefs("/tmp/img.dmg", "0.0.0.0", probes(),
                                  run=lambda *a, **k: "")
    assert not ok and "0.0.0.0" in msg


def test_seeding_refuses_when_no_address_was_given_at_all():
    ok, _ = ib.seed_guest_prefs("/tmp/img.dmg", "", probes(),
                                run=lambda *a, **k: "")
    assert not ok


def test_seeding_reports_a_failed_mount_instead_of_writing_blind():
    calls = []

    def run(argv, **kw):
        calls.append(argv[0])
        return "hmount: /tmp/img.dmg is not a Macintosh volume"

    ok, msg = ib.seed_guest_prefs("/tmp/img.dmg", "192.168.3.213", probes(), run=run)
    assert not ok and "hmount failed" in msg
    assert "hcopy" not in calls


def test_seeding_says_so_when_the_guest_has_no_prefs_file_yet():
    # The GUEST-side installer creates it; this only edits an existing one.
    def run(argv, **kw):
        return ""            # hmount ok, hcopy produces nothing

    tmp = os.path.join(tempfile.mkdtemp(), "absent")
    ok, msg = ib.seed_guest_prefs("/tmp/img.dmg", "192.168.3.213", probes(),
                                  run=run, hfs={"tmp": tmp})
    assert not ok and "installer creates it" in msg


# --- the bundle probe -------------------------------------------------------

def test_the_bundle_probe_prefers_a_running_process_over_a_guess():
    def run(argv, **kw):
        if argv[0] == "pgrep":
            return "42 /Users/x/Basilisk/BasiliskII.app/Contents/MacOS/BasiliskII\n"
        return ""

    out = ib.probe_emulator_bundle(run, exists=lambda p: True, candidates=())
    assert out["app"] == "/Users/x/Basilisk/BasiliskII.app"
    assert out["source"] == "running process"


def test_the_bundle_probe_detects_the_helper_and_its_absence():
    present = ib.probe_emulator_bundle(
        lambda *a, **k: "", exists=lambda p: True,
        candidates=("/Applications/BasiliskII.app",))
    assert present["helper"] is True

    absent = ib.probe_emulator_bundle(
        lambda *a, **k: "",
        exists=lambda p: "etherhelpertool" not in p,
        candidates=("/Applications/BasiliskII.app",))
    assert absent["helper"] is False
    assert absent["app"] == "/Applications/BasiliskII.app"


def test_a_host_with_no_emulator_at_all_is_not_an_exception():
    out = ib.probe_emulator_bundle(lambda *a, **k: "", exists=lambda p: False,
                                   candidates=("/Applications/BasiliskII.app",))
    assert out == {"app": None, "helper": False, "source": None}


# --- apply_plan writes exactly the plan ------------------------------------

def test_apply_writes_the_netmode_and_calls_the_existing_backend_script():
    written, ran = {}, []
    p = probes(ether="etherhelper/en8", intended=None, helper=False)
    plan = ib.decide(p)

    def run(argv, **kw):
        ran.append(argv)
        return "      repaired -> 'ether slirp'"

    ib.apply_plan(plan, p, run=run, write=written.__setitem__,
                  read=lambda path: "ether slirp\n" if path == "/tmp/prefs" else "")
    assert written["/tmp/prefs.netmode"].strip() == "slirp"
    assert any("check_ether_backend.sh" in a[0] for a in ran), \
        "reuse the script that already handles backups and the sed -i trap"


def test_apply_stops_at_the_first_failure():
    p = probes(ether=None, intended=None)
    plan = ib.decide(p)
    results = ib.apply_plan(plan, p, run=lambda *a, **k: "",
                            write=lambda *a: None,
                            read=lambda path: "ether etherhelper/en8\n")
    assert results[0][0] == "set_backend" and results[0][1] is False
    assert len(results) == 1, "a failed backend rewrite must not be followed by more"


# --- the launcher the installer configures (R15) ----------------------------

def _start_stack():
    path = os.path.join(os.path.dirname(__file__), "..", "host", "start_stack.sh")
    return open(path, encoding="utf-8").read()


def test_the_launcher_asks_for_no_password_on_slirp():
    # D-018's argument is that this branch starts without somebody at the
    # keyboard. The privileged block used to run unconditionally, raising an
    # admin dialog to execute nothing but comments.
    text = _start_stack()
    guard = text.index('NEEDS_PRIV" = "1"')
    assert guard < text.index("osascript"), \
        "the privileged block must sit behind the backend guard"
    slirp = text.index('ETHER_BACKEND" = "slirp"')
    assert "NEEDS_PRIV=0" in text[slirp:slirp + 200]


def test_the_launcher_reads_local_env_before_it_uses_the_values():
    # The file was sourced AFTER the assignments, so an APPLEBRIDGE_WIRED_IF in
    # it was read and then ignored — a configuration file that silently does
    # nothing is worse than none.
    text = _start_stack()
    assert text.index(". \"$(dirname \"$0\")/local.env\"") < text.index("WIRED_IF=")


def test_the_launcher_takes_the_emulator_path_from_configuration():
    assert "APPLEBRIDGE_EMULATOR_APP" in _start_stack()


# --- rendering --------------------------------------------------------------

def test_the_refusal_report_shows_no_plan_and_no_checklist():
    p = probes(ether="etherhelper/en8", helper=True)
    text = ib.format_text(p, ib.decide(p))
    assert "REFUSING" in text
    assert "PLAN" not in text and "GUEST-SIDE STEPS" not in text


def test_the_normal_report_carries_the_plan_the_checklist_and_the_tiers():
    p = probes(ether=None, intended=None)
    text = ib.format_text(p, ib.decide(p), dry_run=True)
    for expected in ("PLAN (dry run", "GUEST-SIDE STEPS", "TIERS", "EXPOSURE"):
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
