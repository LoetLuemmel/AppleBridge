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
import re
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
                           "prefs_path": "/tmp/prefs",
                           "disks": ["/tmp/guest.dmg"],
                           "shared_folder": "/tmp/Share"},
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

def test_a_running_emulator_blocks_a_prefs_rewrite():
    plan = ib.decide(probes(ether="etherhelper/en8", helper=False, running=True))
    assert keys(plan["refusals"]) == ["emulator_running"]
    assert not plan["steps"]


def test_a_running_emulator_does_not_block_what_it_never_reads():
    # The machine this was built for: already on slirp, guest up, only the
    # INTENT record stale. Refusing here locked the installer out of the exact
    # configuration it exists to produce — the prefs need no rewrite, and
    # .netmode is not a file the emulator reads.
    plan = ib.decide(probes(ether="slirp", intended="etherhelper/en8",
                            running=True))
    assert not plan["refusals"]
    assert "set_backend" in keys(plan["steps"])


def test_a_stale_intent_record_is_named_as_the_silent_repair_it_causes():
    step = [s for s in ib.decide(probes(ether="slirp", intended="etherhelper/en8"))
            ["steps"] if s["key"] == "set_backend"][0]
    assert "would repair this machine away from slirp" in step["detail"]
    assert step["desired"] == "slirp"


def test_the_running_emulator_refusal_does_not_suggest_killing_it():
    # D-004: hard-terminating BasiliskII can corrupt the guest's disk image.
    detail = ib.decide(probes(ether="etherhelper/en8", helper=False,
                              running=True))["refusals"][0]["detail"]
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
    #
    # Assert on an ASSIGNMENT LINE, not on the substring. The file explains its
    # own omission in a comment, so `APPLEBRIDGE_HOST_IP` legitimately appears
    # once — and `grep -c APPLEBRIDGE_HOST_IP local.env` returning 1 on the 2013
    # MacBook (2026-07-28) read as a failed install until the file was opened.
    # A substring test is right by luck here: it passes because the comment has
    # no `=` after the name, which is a property of today's wording, not of the
    # thing being checked.
    text = ib.render_local_env("/Applications/BasiliskII.app")
    assignments = [ln for ln in text.splitlines()
                   if re.match(r"\s*(export\s+)?APPLEBRIDGE_HOST_IP\s*=", ln)]
    assert not assignments, assignments
    assert "0.0.0.0" in text


def test_the_generated_local_env_says_why_the_address_is_absent():
    # The omission is the deliberate part (R7), so the file has to say so —
    # otherwise the next person to read it adds the variable back.
    text = ib.render_local_env("/Applications/BasiliskII.app")
    explains = [ln for ln in text.splitlines()
                if ln.lstrip().startswith("#") and "APPLEBRIDGE_HOST_IP" in ln]
    assert explains, "nothing in the file explains the missing address"


def test_a_commented_out_assignment_would_still_be_caught():
    # The guard must not be satisfiable by parking the old value behind a `#`,
    # which is how a "removed" setting comes back.
    import re as _re
    line = "#APPLEBRIDGE_HOST_IP=192.168.3.154"
    assert _re.match(r"\s*(export\s+)?APPLEBRIDGE_HOST_IP\s*=", line) is None
    # ...so the comment form is caught by the separate literal check instead:
    assert "192.168.3.154" not in ib.render_local_env("/Applications/B.app")


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


def test_the_checklist_leads_with_dhcp():
    # Measured 2026-07-28 on a live guest: slirp answers BOOTP/DHCP and hands
    # out all four values INCLUDING the name server, and the daemon reconnected
    # and completed the v0.2 handshake on it. Leading with the manual values
    # made the operator type the one field they are most likely to omit.
    lines = "\n".join(ib.guest_checklist(HOST_ADDRS))
    assert "Using DHCP Server" in lines
    assert lines.index("Using DHCP Server") < lines.index("Configure     Manually"), \
        "DHCP is the recommended path, so it comes first"


def test_the_manual_values_survive_as_the_fallback():
    # Not every emulator build answers DHCP, and a checklist that only works on
    # the machine it was written on is this project's oldest defect (R1).
    # Scoped to the MANUAL block. "the value appears somewhere in the text"
    # passed with the fallback deleted, because the DHCP paragraph names the
    # same addresses while explaining that DHCP supplies them — the third time
    # today an assertion matched prose about the thing instead of the thing.
    lines = "\n".join(ib.guest_checklist(HOST_ADDRS))
    assert "Manually" in lines, "no manual fallback at all"
    manual = lines[lines.index("Manually"):]
    for value in (ib.GUEST_ADDR, ib.GUEST_MASK, ib.GUEST_ROUTER, ib.GUEST_RESOLVER):
        assert value in manual, f"{value} missing from the manual fallback"


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


TRANSLOC = ("/private/var/folders/kz/T/AppTranslocation/6E49C8B5-86BD/d/"
            "BasiliskII_letzter.app")


def test_a_translocated_bundle_is_never_recorded_as_configuration():
    # Observed on this project's own machine, 2026-07-27: a quarantined
    # emulator runs from a per-launch throwaway mount, and the uuid had already
    # changed between two launches an hour apart. Writing that into local.env
    # gives start_stack.sh a path that expires when the app quits.
    def run(argv, **kw):
        return (f"21573 {TRANSLOC}/Contents/MacOS/BasiliskII\n"
                if argv[0] == "pgrep" else "")

    out = ib.probe_emulator_bundle(run, exists=lambda p: TRANSLOC in p,
                                   candidates=())
    assert out["app"] is None, "an expiring path is not configuration"
    assert out["translocated"] == TRANSLOC
    assert out["helper"] is True, \
        "the helper question is about the bundle, and the copy carries one"


def test_the_translocation_is_reported_with_the_way_out():
    p = probes(app=None, ether="slirp", intended="slirp")
    p["bundle"]["translocated"] = TRANSLOC
    note = [n for n in ib.decide(p)["notes"]
            if n["key"] == "emulator_translocated"][0]
    assert "xattr -dr com.apple.quarantine" in note["message"]


def test_a_real_bundle_still_wins_over_a_translocated_one():
    def run(argv, **kw):
        return (f"1 {TRANSLOC}/Contents/MacOS/BasiliskII\n"
                "2 /Applications/BasiliskII.app/Contents/MacOS/BasiliskII\n"
                if argv[0] == "pgrep" else "")

    out = ib.probe_emulator_bundle(run, exists=lambda p: True, candidates=())
    assert out["app"] == "/Applications/BasiliskII.app"


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


# --- the requirements register must not lose a row --------------------------
# R4 and R16-R19 had no row in the mechanism table for weeks. Three different
# states were rendered identically as absence: done elsewhere (R4, R16, in the
# daemon), and not implementable at all (R17-R19, practice rules on how the
# tooling is used). A reader could not tell "covered" from "nobody looked" —
# the same defect as a check that reports without examining, in a table.

def _requirements_doc():
    path = os.path.join(os.path.dirname(__file__), "..", "docs",
                        "INSTALLER_REQUIREMENTS.md")
    return open(path, encoding="utf-8").read()


def _numbered_requirements(doc):
    return {m for m in re.findall(r"^## (R\d+)", doc, re.M)}


def _table_rows(doc):
    """Requirement ids named in the mechanism table (one row may cover several)."""
    start = doc.index("| requirement | mechanism |")
    end = doc.index("\n\n", start)
    ids = set()
    for line in doc[start:end].splitlines():
        if line.startswith("|"):
            ids |= set(re.findall(r"\bR\d+\b", line.split("|")[1]))
    return ids


def test_every_requirement_has_a_row_in_the_table():
    doc = _requirements_doc()
    missing = sorted(_numbered_requirements(doc) - _table_rows(doc),
                     key=lambda r: int(r[1:]))
    assert not missing, (
        "requirement(s) with no row in the mechanism table: " + ", ".join(missing)
        + " — add one saying where the mechanism is, or why this requirement is "
          "not the installer's to satisfy. Omission reads as 'nobody looked'.")


def test_the_table_names_no_requirement_that_does_not_exist():
    # The other direction: a row for a requirement that was renamed or removed
    # claims coverage of nothing.
    doc = _requirements_doc()
    stray = sorted(_table_rows(doc) - _numbered_requirements(doc),
                   key=lambda r: int(r[1:]))
    assert not stray, f"table rows for non-existent requirements: {stray}"


def _row_for(doc, req):
    """The table row naming `req`, or None. Returning None rather than raising
    is deliberate: a bare next() turned a deleted row into StopIteration, so the
    suite died with a traceback instead of naming the missing requirement."""
    start = doc.index("| requirement | mechanism |")
    table = doc[start:doc.index("\n\n", start)]
    for line in table.splitlines():
        if line.startswith("|") and re.search(rf"\b{req}\b", line.split("|")[1]):
            return line
    return None


def test_a_row_that_is_not_the_installers_says_so_rather_than_going_quiet():
    doc = _requirements_doc()
    for req, phrase in (("R4", "not the installer"), ("R16", "not the installer"),
                        ("R17", "practice rule"), ("R18", "practice rule"),
                        ("R19", "practice rule")):
        row = _row_for(doc, req)
        assert row is not None, f"{req} has no row at all"
        assert phrase in row, (
            f"{req}'s row must say '{phrase}' — otherwise a requirement nobody "
            "can implement is indistinguishable from one nobody did")


def test_the_scan_found_the_requirements_at_all():
    # A renamed heading style or a moved file empties both sets and every
    # assertion above passes while examining nothing.
    doc = _requirements_doc()
    assert len(_numbered_requirements(doc)) >= 20, "requirement headings not found"
    assert len(_table_rows(doc)) >= 20, "mechanism table not found"


# --- the guest kit: distribution, not image surgery -------------------------
# The guest already HAS a real installer — Gestalt preflight, fork-aware copy,
# prefs seeding, Startup Items alias — and it refuses environments that cannot
# work, which is its entire value. What was missing was never installation; it
# was DISTRIBUTION: nobody assembled the folder that installer expects.
#
# The alternative considered and rejected was writing the suite straight into
# the guest's disk image with hfsutils. It works, and it is not something a
# stranger should run: a program that edits other people's volumes has a blast
# radius equal to their whole machine. The kit goes to the emulator's shared
# folder instead, which the guest already sees as `Unix:`, and nothing of theirs
# is touched.

def test_the_prefs_carry_the_address_and_nothing_machine_specific():
    txt = ib.guest_prefs_text("192.168.3.240")
    assert "IP=192.168.3.240" in txt
    # HOME= names the folder the suite landed in, which only the guest installer
    # knows. Shipping one meant shipping THIS developer's volume name onto a
    # stranger's machine — an R1 literal smuggled in through a template.
    assert "HOME=" not in txt.replace("# HOME=", ""), \
        "HOME= belongs to the guest installer, not to the kit"


def test_the_generated_prefs_are_ascii_and_lf():
    # Read by a 68K daemon through a MacRoman path; CR/LF matters (R20) and a
    # decorative character in a comment is a risk with no upside.
    raw = ib.guest_prefs_text("192.168.3.240").encode("mac_roman")
    assert all(b < 128 for b in raw), "non-ASCII in a generated config file"
    assert b"\r" not in raw, "must stay LF-terminated like the guest's own file"


def test_the_transport_default_is_stated_not_hidden():
    assert "NET=OT" in ib.guest_prefs_text("1.2.3.4")
    assert ib.guest_prefs_text("1.2.3.4", net="MacTCP").count("NET=MacTCP") == 1


def test_the_installer_itself_is_required_not_optional():
    # A kit of binaries with no installer is a pile of files and a manual
    # procedure — exactly what this replaces.
    assert "AppleBridgeInstaller" in ib.KIT_REQUIRED
    assert "AppleBridge" in ib.KIT_REQUIRED


def test_both_the_deployed_and_the_build_folder_are_searched():
    # The suite lives in :AppleBridge: on a deployed machine and in
    # :MPW:AppleBridge:bin: on a build machine; the installer is only in the
    # latter here. Searching one would have shipped an incomplete kit.
    assert ":AppleBridge:" in ib.KIT_DIRS and ":MPW:AppleBridge:bin:" in ib.KIT_DIRS


def test_the_installer_is_looked_for_under_both_spellings():
    names = dict((label, names) for label, names in ib.KIT_APPS)
    assert "AppleBridgeInstaller" in names["AppleBridgeInstaller"]
    assert "AppleBridge Installer" in names["AppleBridgeInstaller"]


def test_a_running_emulator_blocks_the_export():
    ok, msg, placed = ib.export_guest_kit("/tmp/kit", "1.2.3.4",
                                          probes(running=True),
                                          run=lambda a: "", exists=lambda p: True)
    assert ok is False and "running" in msg


def test_no_readable_image_is_refused_rather_than_half_done():
    ok, msg, _ = ib.export_guest_kit("/tmp/kit", "1.2.3.4", probes(),
                                     run=lambda a: "", exists=lambda p: False)
    assert ok is False and "no readable disk image" in msg


def test_a_missing_required_binary_fails_the_whole_kit():
    # Half a kit is worse than none: it looks installable and is not.
    ok, msg, _ = ib.export_guest_kit(
        "/tmp/kit", "1.2.3.4",
        probes(),
        run=lambda a: "Volume ..." if a[0] == "hmount" else "",
        exists=lambda p: p.endswith(".dmg"),        # image yes, copies no
        write_bytes=lambda p, d: None)
    assert ok is False
    assert "REQUIRED" in msg and "cannot ship a kit" in msg


# --- choosing the address the guest dials -----------------------------------
# This was `addresses[0]` — whichever interface ifconfig listed first. On a
# multi-homed host that is a coin toss, and losing it produces R2 in its purest
# form: the daemon dials, waits forever, and every status field reads healthy.

def test_the_default_route_interface_wins():
    addrs = [("en8", "10.0.0.5"), ("en0", "192.168.3.240")]
    assert ib.dialable_address(addrs, "en0") == "192.168.3.240"


def test_loopback_is_never_offered_to_a_guest():
    # A guest cannot reach the host's 127.0.0.1, whatever the host thinks.
    addrs = [("lo0", "127.0.0.1"), ("en0", "192.168.3.240")]
    assert ib.dialable_address(addrs, "lo0") == "192.168.3.240"
    assert ib.dialable_address([("lo0", "127.0.0.1")], "lo0") is None


def test_an_unknown_default_route_falls_back_but_still_skips_loopback():
    addrs = [("lo0", "127.0.0.1"), ("en5", "192.168.9.9")]
    assert ib.dialable_address(addrs, "en0") == "192.168.9.9"


def test_no_addresses_yields_none_rather_than_a_guess():
    assert ib.dialable_address([], "en0") is None


# --- finding the guest's disk image without being told ----------------------
# The path sits in the same prefs file the installer already reads for `ether`.
# Asking the operator to supply it was a gap, not a design.

def test_the_disk_image_is_read_from_the_emulator_prefs():
    read = lambda path: ("disk /Users/x/System761 weiter.dmg\n"
                         "ether slirp\nramsize 130023424\n")
    got = ib.bridge_doctor.probe_emulator_prefs(read, "/tmp/p", "/tmp/n")
    assert got["disks"] == ["/Users/x/System761 weiter.dmg"], got
    assert got["ether"] == "slirp", "reading disks must not break the ether parse"


def test_an_image_path_with_spaces_survives():
    # "System761 weiter.dmg" — splitting on whitespace would truncate it.
    read = lambda path: "disk /Users/x/My Disk Image.dmg\n"
    got = ib.bridge_doctor.probe_emulator_prefs(read, "/tmp/p", "/tmp/n")
    assert got["disks"] == ["/Users/x/My Disk Image.dmg"], got


def test_several_disks_are_all_offered():
    read = lambda path: "disk /a.dmg\ndisk /b.dmg\nether slirp\n"
    assert ib.bridge_doctor.probe_emulator_prefs(read, "/tmp/p", "/tmp/n")["disks"] \
        == ["/a.dmg", "/b.dmg"]


def test_no_disk_line_is_not_an_error():
    read = lambda path: "ether slirp\n"
    assert ib.bridge_doctor.probe_emulator_prefs(read, "/tmp/p", "/tmp/n")["disks"] == []


def test_seeding_is_opt_in_so_there_is_nothing_to_opt_out_of():
    # PR #127 made seeding automatic whenever an image was discoverable, and
    # --no-seed existed to escape it. Writing into somebody's disk volume
    # because you can is not shipped behaviour, so the default is back to
    # explicit and the escape hatch is unnecessary.
    code, _ = _parse(["--no-seed"])
    assert code == 2, "--no-seed should no longer exist"
    code, ns = _parse([])
    assert code is None and ns.seed_guest_prefs is None


# --- argument parsing: the safety flag must not fail open -------------------
# `--dry-run` was matched with `"--dry-run" in argv`, and anything unrecognised
# was ignored. So `--help` — the first thing a stranger types at an unfamiliar
# program — was not a flag: it fell through and the installer INSTALLED.
# Observed 2026-07-28 on the development machine, which rewrote local.env, took
# a backup and restarted the launchd agent in answer to a request for usage.
# Every misspelling had the same effect: `--dryrun` meant apply.
#
# A safety flag that disappears when mistyped is worse than none, because it is
# trusted. On an unconfigured host the difference is the whole install rather
# than a description of it.

def _parse(args):
    """-> (exit_code, parsed) — exit code None when parsing succeeded."""
    try:
        return None, ib.build_parser().parse_args(args)
    except SystemExit as e:
        return e.code, None


def test_an_unknown_flag_is_refused_not_ignored():
    for bad in ("--dryrun", "--dry_run", "--helpp", "-n", "--force_slirp"):
        code, _ = _parse([bad])
        assert code == 2, f"{bad} was accepted (exit {code})"


def test_help_is_a_flag_and_not_an_installation():
    code, _ = _parse(["--help"])
    assert code == 0, f"--help exited {code}"


def test_a_misspelled_safety_flag_never_reaches_the_prober():
    # The behavioural claim, not just the parse: nothing that touches the host
    # may run before the arguments are understood.
    called = []
    real = ib.probe
    ib.probe = lambda *a, **k: called.append(1) or (_ for _ in ()).throw(
        AssertionError("probe() ran despite bad arguments"))
    try:
        try:
            ib.main(["--dryrun"])
        except SystemExit as e:
            assert e.code == 2, e.code
        else:
            raise AssertionError("main() accepted --dryrun")
    finally:
        ib.probe = real
    assert not called


def test_every_documented_flag_still_parses():
    # Renaming or dropping one silently would be the same class of surprise.
    code, ns = _parse(["--dry-run", "--json", "--force-slirp", "--no-agent",
                       "--seed-guest-prefs", "/tmp/x.dmg"])
    assert code is None
    assert (ns.dry_run, ns.json, ns.force_slirp, ns.no_agent) == \
        (True, True, True, True)
    assert ns.seed_guest_prefs == "/tmp/x.dmg"


def test_seeding_without_an_image_is_an_argument_error():
    code, _ = _parse(["--seed-guest-prefs"])
    assert code == 2


def test_no_arguments_still_means_apply():
    # The documented contract is unchanged: --dry-run describes, bare applies.
    # Only the failure mode moved, from "apply" to "exit 2".
    code, ns = _parse([])
    assert code is None and ns.dry_run is False


def test_the_hand_rolled_matching_is_gone():
    # CODE only. Searching the raw source matched the docstring that explains
    # the old behaviour — the fourth assertion today to match prose about the
    # thing instead of the thing, so: drop comments and string literals first.
    import io
    import tokenize
    path = os.path.join(os.path.dirname(__file__), "..", "host",
                        "install_bridge.py")
    src = open(path, encoding="utf-8").read()
    code = "".join(
        tok.string for tok in tokenize.generate_tokens(io.StringIO(src).readline)
        if tok.type not in (tokenize.COMMENT, tokenize.STRING))
    assert "--dry-run" not in code, \
        "substring matching is back; unknown flags are ignored again"
    assert "build_parser" in code, "the parser is not being used"


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


def test_the_launcher_never_hands_open_another_machines_path():
    # The fallback IS the R1 defect the variable exists to remove, so it may be
    # used only when it is really present. On the 2013 MacBook (2026-07-28) the
    # emulator was Gatekeeper-translocated, the installer correctly recorded no
    # path, and this line would have passed `open -a` a directory belonging to a
    # different computer — whose error message then sends you looking for a
    # Basilisk install that was never supposed to be there.
    text = _start_stack()
    assert 'BASILISK_APP="${APPLEBRIDGE_EMULATOR_APP:-}"' in text, \
        "the developer path must not be an unconditional default"
    fallback = text.index("/Users/pitforster/Documents/Basilisk/BasiliskII.app")
    guard = text.index('[ -d "/Users/pitforster/Documents/Basilisk/BasiliskII.app" ]')
    assert guard <= fallback + 200, "the fallback must be guarded by -d"


def test_the_closing_advice_matches_the_branch_it_is_printed_on():
    # The interface rule is an ETHERHELPER rule. Printed unconditionally, it
    # sent a slirp operator whose daemon would not connect to go and fix an
    # alias their branch does not have and never places. Seen on the 2013
    # MacBook, 2026-07-28, at the end of an otherwise clean install.
    text = _start_stack()
    tail = text[text.index("Host-side stack is up"):]
    # Assert the guard EXISTS before indexing on it: removing it made this test
    # raise ValueError instead of failing, so the suite went red with a
    # traceback rather than with the reason. A red build that does not say why
    # is only half a test.
    assert 'ETHER_BACKEND" = "slirp"' in tail, \
        "the closing advice is not branch-aware at all"
    guard = tail.index('ETHER_BACKEND" = "slirp"')
    rule = tail.index("wrong interface")
    assert guard < rule, "the interface rule must sit in the non-slirp branch"
    assert "10.0.2.2" in tail[guard:rule], \
        "the slirp branch needs its OWN failure hint, not silence"


def test_toolserver_is_offered_as_a_tier_not_a_step():
    # install_bridge.py says "absent MPW is a tier you do not have, not a failed
    # install" — and this text contradicted it two commands later, including a
    # smoke test (`Echo HELLO`) that NEEDS ToolServer. On a guest without one,
    # the suggested proof of a working bridge returns nothing.
    # Only what is PRINTED counts. Reading the whole tail matched the comment
    # above the fix, which mentions `Echo HELLO` while explaining why it is not
    # the right first suggestion — the assertion then failed on correct code.
    # Third variant of the same near-miss today: check the thing, not prose
    # about the thing.
    tail = _start_stack()
    tail = tail[tail.index("Host-side stack is up"):]
    printed = "\n".join(ln for ln in tail.splitlines()
                        if ln.lstrip().startswith("echo "))
    assert "OPTIONAL" in printed, "ToolServer is a tier, not a required step"
    assert "MACSTATUS" in printed, "the tier-independent smoke test must be offered"
    assert printed.index("MACSTATUS") < printed.index("Echo HELLO"), \
        "offer the check that works on either tier first"


def test_the_launcher_says_so_instead_of_launching_nothing():
    # With no bundle it must NAME the situation, not run `open -a ""` and leave
    # the operator to interpret whatever open says about an empty argument.
    text = _start_stack()
    launch = text.index("[5/5] Launching")
    tail = text[launch:launch + 700]
    assert 'if [ -n "$BASILISK_APP" ]' in tail
    assert "SKIPPED" in tail and "install_bridge.py" in tail


def test_the_plan_does_not_promise_an_emulator_it_did_not_find():
    # The plan step read "…and the discovered emulator" while the header two
    # lines above said `— not found —`. Text asserting something it did not
    # check is the defect class this whole installer exists to remove.
    src = open(os.path.join(os.path.dirname(__file__), "..", "host",
                            "install_bridge.py"), encoding="utf-8").read()
    assert 'NO emulator path' in src, \
        "the write_local_env step must say which of the two cases it is in"


def test_the_launcher_matches_a_wildcard_listener_the_way_lsof_prints_it():
    # First slirp launch, 2026-07-27: step 4 reported "not listening" two lines
    # below its own log saying it was. lsof renders a wildcard bind as `*:9000`,
    # never `0.0.0.0:9000`, and the check grepped for the latter.
    text = _start_stack()
    assert r'LSOF_PATTERN=' in text
    assert r"'\*:9000'" in text, "the wildcard bind must be matched as lsof prints it"


def test_rewrite_preserves_lf_endings_because_mpw_c_swaps_the_escapes():
    # The real guest file has LF and no CR: prefs.c writes "\r", but MPW C maps
    # '\r' to 0x0A. Converting it to CR would corrupt the bridge's own config.
    lf = (b"# AppleBridge preferences\nIP=192.168.3.154\nDEBUG=0\nNET=OT\n"
          b"HOME=MeinMac:AppleBridge:\n")
    out = ib.rewrite_ip_line(lf, "192.168.3.240")
    assert b"\r" not in out, "must not impose CR on a file that has none"
    assert b"IP=192.168.3.240\n" in out
    assert len(out) == len(lf), "same-length address, same-length file"
    assert b"HOME=MeinMac:AppleBridge:\n" in out


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
