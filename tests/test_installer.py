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
import macbinary  # noqa: E402

HOST_ADDRS = [("en0", "192.168.3.213")]
TWO_NICS = [("en0", "192.168.3.213"), ("en8", "192.168.3.154")]


def probes(ether="slirp", intended="slirp", helper=False, running=False,
           addresses=None, app="/Applications/BasiliskII.app",
           hfs_missing=()):
    """A probe dict, as `probe()` would return it, without touching the host."""
    return {
        "bundle": {"app": app, "helper": helper, "source": "well-known location"},
        "hfsutils": {"found": {} if hfs_missing
                     else {t: "/usr/local/bin/" + t for t in ib.HFS_TOOLS},
                     "missing": list(hfs_missing)},
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
    # Asserts the PROPERTY, not the sentence: it refuses, and it says where it
    # looked. The wording changed when the seeder learned about kit volumes,
    # and a test pinned to a phrase would have failed for no defect — while one
    # pinned to "refuses" alone would pass even if the message went blank.
    assert not ok
    assert ib.GUEST_PREFS_HFS in msg, msg
    assert "installer" in msg, msg


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
# radius equal to their whole machine. The kit is a SEPARATE image the operator
# mounts, and nothing of theirs is touched.
#
# It shipped first as a folder in the emulator's shared directory, and that was
# undeliverable: measured 2026-07-28, Basilisk's `extfs` presents 68K
# applications to the guest as DOCUMENTS, so the installer could not be launched
# from there at all. Confirmed twice, the second time on a folder the guest had
# never seen, after a full restart. The tests below hold the disk-image shape,
# because the failure was invisible from the host — every file was present and
# correct, and the kit still could not be used.

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


def test_an_idle_image_can_be_read_while_another_guest_runs():
    # The guard used to be "any emulator is running", which is right about the
    # danger and wrong about the scope. A machine with a working guest and a
    # test guest could not build a kit from the idle image because the OTHER
    # one was up — measured 2026-07-28, and the reason this test exists.
    p = probes(running=True)
    p["emulator_prefs"]["disks"] = ["/img/idle.dmg"]
    io = KitIO()
    io.run = lambda a: ("PID  CMD  /img/other.dmg\n" if a[0] == "lsof"
                        else KitIO.run(io, a))
    ok, msg, _ = ib.export_guest_kit(
        "/tmp/kitdir", "1.2.3.4", p, run=io.run, exists=lambda p_: True,
        read_bytes=lambda p_: b"z" * 10,
        write_bytes=lambda p_, d: io.written.__setitem__(p_, d),
        staging="/tmp/kitstage")
    assert ok is True, msg


def test_the_image_the_running_emulator_has_open_is_refused():
    p = probes(running=True)
    p["emulator_prefs"]["disks"] = ["/img/live.dmg"]
    ok, msg, _ = ib.export_guest_kit(
        "/tmp/kitdir", "1.2.3.4", p,
        run=lambda a: ("n 32884 x /img/live.dmg\n" if a[0] == "lsof" else ""),
        exists=lambda p_: True)
    assert ok is False and "live.dmg" in msg


def test_a_filename_with_spaces_is_matched_whole():
    # "System761 weiter.dmg". Splitting the lsof line on whitespace compares
    # "weiter.dmg" against the full path and never matches, so the guard would
    # pass a LIVE image as idle — the exact case it exists to catch.
    p = probes(running=True)
    p["emulator_prefs"]["disks"] = ["/img/System761 weiter.dmg"]
    assert ib.image_in_use(
        "/img/System761 weiter.dmg", p,
        run=lambda a: "BasiliskII 32884 pit txt REG /img/System761 weiter.dmg\n")


def test_lsof_telling_us_nothing_is_treated_as_in_use():
    # Silence is not "the file is closed". A torn read of somebody's System 7
    # volume is not worth saving one shutdown.
    p = probes(running=True)
    assert ib.image_in_use("/img/x.dmg", p, run=lambda a: "") is True


def test_no_emulator_means_no_image_is_in_use():
    assert ib.image_in_use("/img/x.dmg", probes(), run=lambda a: "") is False


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


# --- the kit is a mountable HFS image ---------------------------------------
class KitIO:
    """Injected IO for the kit builder: records calls, invents no filesystem."""

    FULL = ("AppleBridge\nAppleBridgeWatchdog\nAppleBridgeConfig\n"
            "AppleBridgeInstaller\nAppleBridge Prefs\n")

    def __init__(self, listing=None):
        self.calls = []
        self.written = {}
        self.listing = self.FULL if listing is None else listing

    def run(self, argv):
        self.calls.append(list(argv))
        if argv[0] == "hmount":
            return "Volume name is whatever\n"
        if argv[0] == "hls":
            return self.listing
        return ""

    def build(self, dest="/tmp/kitdir", host_ip="192.168.3.154", release=False, **kw):
        # read_bytes is overridable so a test can hand back a binary with an
        # address baked into it — the defect the scan exists for.
        return ib.export_guest_kit(
            dest, host_ip, kw.pop("probes_", None) or probes(),
            run=self.run, exists=lambda p: True,
            read_bytes=kw.pop("read_bytes", lambda p: b"z" * 1000),
            write_bytes=lambda p, d: self.written.__setitem__(p, d),
            staging="/tmp/kitstage", release=release, **kw)

    def argv_for(self, verb):
        return [c for c in self.calls if c[0] == verb]


def test_the_kit_is_a_disk_image_not_a_folder_of_files():
    # The whole point. A folder in the shared directory was measured
    # undeliverable: extfs shows the applications as documents.
    io = KitIO()
    ok, msg, placed = io.build()
    assert ok is True, msg
    assert any(c[0] == "hformat" for c in io.calls), \
        "no volume was formatted — this is not a mountable kit"
    assert "/tmp/kitdir/" + ib.KIT_IMAGE_NAME in io.written


def test_the_volume_is_named_so_the_operator_can_find_it():
    io = KitIO()
    io.build()
    fmt = io.argv_for("hformat")[0]
    assert "-l" in fmt and ib.KIT_VOLUME in fmt


def test_the_payload_is_copied_onto_the_new_volume_as_macbinary():
    # -m is what carries BOTH forks and the Finder info. Without it the guest
    # gets a data fork only, which for a 68K app is empty (D-013) — the exact
    # failure that made the shared-folder kit useless.
    io = KitIO()
    io.build()
    inbound = [c for c in io.argv_for("hcopy") if c[-1] == ":"]
    assert len(inbound) == 5, f"expected 4 apps + prefs, got {len(inbound)}"
    assert all("-m" in c for c in inbound)


def test_the_message_tells_the_operator_the_disk_line_and_to_relaunch():
    # Basilisk reads its disk list at LAUNCH. Leaving that out means the kit is
    # built, the line is added, nothing appears, and the tool looks broken.
    io = KitIO()
    ok, msg, _ = io.build()
    assert "disk /tmp/kitdir/" + ib.KIT_IMAGE_NAME in msg
    assert "LAUNCH" in msg or "relaunch" in msg


def test_the_message_does_not_tell_anyone_to_install_from_the_shared_folder():
    # The instruction that shipped and could not be followed.
    io = KitIO()
    ok, msg, _ = io.build()
    assert "Unix:" not in msg and "shared folder" not in msg.lower()


def test_a_volume_that_did_not_receive_the_payload_is_refused():
    # Every hfsutils step can report success and leave an empty volume. Without
    # this check the failure surfaces as a person double-clicking an empty disk.
    io = KitIO(listing="")
    ok, msg, _ = io.build()
    assert ok is False
    assert "did not land" in msg


def test_a_previously_built_kit_is_not_used_as_its_own_source():
    # Once the `disk` line is added the kit is in the emulator's disk list. A
    # second run that read it as the source would find no binaries and report a
    # missing REQUIRED file about a volume nobody meant to search.
    p = probes()
    p["emulator_prefs"]["disks"] = ["/img/" + ib.KIT_IMAGE_NAME, "/img/guest.dmg"]
    io = KitIO()
    io.build(probes_=p)
    assert ["hmount", "/img/guest.dmg"] in io.calls
    assert ["hmount", "/img/" + ib.KIT_IMAGE_NAME] not in io.calls


def test_an_explicit_dmg_path_is_used_as_given():
    io = KitIO()
    ok, msg, _ = io.build(dest="/somewhere/MyKit.dmg")
    assert "/somewhere/MyKit.dmg" in io.written
    assert "/somewhere/MyKit.dmg/" not in msg


def test_the_prefs_land_as_a_TEXT_file_and_keep_their_LF_endings():
    # A typeless prefs file will not open in AppleBridgeConfig, and a CR
    # conversion would corrupt the very configuration the bridge depends on
    # (R20). hcopy -t would have done exactly that, which is why it is not used.
    io = KitIO()
    io.build()
    blob = next(v for k, v in io.written.items() if k.endswith(".macbin"))
    parts = macbinary.decode(blob)
    assert parts["type"] == b"TEXT"
    assert b"\r" not in parts["data"], "prefs must stay LF-terminated"
    assert b"IP=192.168.3.154" in parts["data"]


def test_the_volume_is_unmounted_even_when_a_step_fails():
    # A left-mounted volume poisons every later hfsutils call in the process.
    io = KitIO(listing="")
    io.build()
    assert io.argv_for("humount"), "never unmounted"
    assert len(io.argv_for("humount")) == len(io.argv_for("hmount"))


# --- no baked-in host address may ship in a binary ---------------------------
# The kit shipped one on 2026-07-28. `AppleBridgeInstaller` had not been rebuilt
# since 2026-07-02 — five days before the commit that emptied DEFAULT_HOST_IP —
# so it still carried 192.168.3.154, and because nothing read the kit's prefs
# file that literal WAS the address a fresh install received. It looked perfect
# on the one LAN where the number is right.

def test_a_hardcoded_address_in_a_binary_is_found():
    assert ib.payload_host_literals(b"\x00\x01192.168.3.154\x00junk") == ["192.168.3.154"]


def test_version_strings_are_not_mistaken_for_addresses():
    for benign in (b"0.8d32", b"v1.2.3", b"MPW 3.5", b"1.2.3"):
        assert ib.payload_host_literals(benign) == [], benign


def test_an_impossible_octet_is_not_an_address():
    assert ib.payload_host_literals(b"999.1.1.1 and 1.2.3.999") == []


def test_the_kit_is_refused_when_a_binary_carries_an_address():
    # Refuse, not warn. A wrong address is worse than none: the daemon connects
    # to whatever answers and every status field reads healthy.
    io = KitIO()
    ok, msg, _ = io.build(read_bytes=lambda p: b"prefix 192.168.3.154 suffix")
    assert ok is False
    assert "192.168.3.154" in msg and "hardcoded host address" in msg


def test_the_prefs_file_may_carry_the_address_because_that_is_its_job():
    # The scan must run BEFORE the prefs are staged, or every kit is rejected:
    # the prefs file is the one place an address belongs.
    io = KitIO()
    ok, msg, placed = io.build()          # read_bytes returns address-free bytes
    assert ok is True, msg
    assert "AppleBridge Prefs" in placed


# --- sizing ------------------------------------------------------------------
def test_the_volume_has_room_for_the_desktop_database():
    # Sized exactly to its contents, the volume has nowhere to put the desktop
    # database the Finder writes on mount, and it fails inside the emulator
    # where nobody will connect the symptom back to the size calculation.
    assert ib.kit_image_size(1000) >= ib.KIT_MIN_BYTES
    assert ib.kit_image_size(50 * 1024 * 1024) > 50 * 1024 * 1024


def test_the_volume_size_is_a_whole_number_of_blocks():
    for payload in (0, 1, 12345, 3 * 1024 * 1024 + 7):
        assert ib.kit_image_size(payload) % 512 == 0


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


# --- the three findings from the first install on a machine nobody prepared --
# (2026-07-29, a second host: PitsMacBook2013). Each was invisible here because
# the developer machine happens to satisfy it.

def test_the_bundle_is_found_beside_the_disk_images_when_the_emulator_is_down():
    # The real layout that defeated all three original stages: folder named
    # `BasiliskII` (not `Basilisk`), bundle renamed `BasiliskII_letzter.app`,
    # emulator not running -- which is the state every install runs in.
    prefs = {"disks": ["/Users/x/Documents/BasiliskII/Macintosh.dmg"],
             "rom": "/Users/x/Documents/BasiliskII/PERFORMA.ROM"}
    dirs = ib.bundle_dirs_from_prefs(prefs)
    assert dirs == ["/Users/x/Documents/BasiliskII"], dirs

    out = ib.probe_emulator_bundle(
        run=lambda argv: "",                       # nothing running, mdfind dry
        exists=lambda p: p.endswith("BasiliskII_letzter.app/Contents/MacOS/"
                                    "BasiliskII"),
        candidates=(),                             # no well-known hit
        prefs_dirs=dirs,
        listdir=lambda d: ["Macintosh.dmg", "BasiliskII_letzter.app", "notes.txt"])
    assert out["app"] == "/Users/x/Documents/BasiliskII/BasiliskII_letzter.app", out
    assert out["source"] == "beside the emulator's disk images"


def test_bundle_dirs_are_deduplicated_and_ordered():
    prefs = {"disks": ["/a/one.dmg", "/a/two.dmg", "/b/three.dmg"],
             "rom": "/a/Mac.ROM"}
    assert ib.bundle_dirs_from_prefs(prefs) == ["/a", "/b"]


def test_a_bundle_is_judged_by_its_executable_not_its_name():
    # Measured on one real folder: every name rule is wrong in BOTH directions.
    # `Kanji-2020-01-22.app` IS an emulator and matches no prefix;
    # `BasiliskIIGUI.app` matches `BasiliskII*` and is a front-end, not one.
    real = {"/f/Kanji-2020-01-22.app/Contents/MacOS/BasiliskII",
            "/f/org_BasiliskII.app/Contents/MacOS/BasiliskII",
            "/f/BasiliskIIGUI.app/Contents/MacOS/BasiliskIIGUI"}
    ex = lambda p: p in real
    assert ib.is_emulator_bundle("/f/Kanji-2020-01-22.app", ex) is True
    assert ib.is_emulator_bundle("/f/org_BasiliskII.app", ex) is True
    assert ib.is_emulator_bundle("/f/BasiliskIIGUI.app", ex) is False, \
        "a GUI front-end must not be recorded as the emulator"

    out = ib.probe_emulator_bundle(
        run=lambda a: "", exists=ex, candidates=(), prefs_dirs=["/f"],
        listdir=lambda d: ["BasiliskIIGUI.app", "Kanji-2020-01-22.app"])
    assert out["app"] == "/f/Kanji-2020-01-22.app", out


def test_mdfind_looks_for_the_EXECUTABLE_so_renames_cannot_hide_it():
    seen = []

    def run(argv):
        seen.append(argv)
        return ("/weird/Some Old Build.app/Contents/MacOS/BasiliskII\n"
                if argv[0] == "mdfind" else "")

    out = ib.probe_emulator_bundle(run=run, exists=lambda p: True,
                                   candidates=(), prefs_dirs=())
    queries = " ".join(a for c in seen if c[0] == "mdfind" for a in c)
    assert '"BasiliskII"' in queries and ".app" not in queries, queries
    assert out["app"] == "/weird/Some Old Build.app", out


def test_an_operator_who_knows_can_name_the_bundle_and_is_never_prompted():
    out = ib.probe_emulator_bundle(run=lambda a: 1 / 0,     # must not probe
                                   exists=lambda p: True,
                                   override="/opt/Mine.app")
    assert out["app"] == "/opt/Mine.app" and out["source"] == "--emulator-app"

    missing = ib.probe_emulator_bundle(run=lambda a: "", exists=lambda p: False,
                                       override="/nope.app")
    assert missing["app"] is None and missing["override_missing"] == "/nope.app"


def test_a_kit_export_without_hfsutils_names_the_tool_and_how_to_get_it():
    io = KitIO()
    ok, msg, _ = io.build(probes_=probes(hfs_missing=["hmount", "hcopy"]))
    assert ok is False
    assert "hfsutils" in msg and "brew install hfsutils" in msg, msg
    assert "hmount" in msg, "say WHICH tools are missing"
    assert not io.calls, "it must refuse before shelling out to a missing tool"


def test_seeding_prefs_without_hfsutils_refuses_the_same_way():
    ok, msg = ib.seed_guest_prefs("/tmp/x.dmg", "192.168.3.1",
                                  probes(hfs_missing=list(ib.HFS_TOOLS)),
                                  run=lambda argv: "")
    assert ok is False and "brew install hfsutils" in msg, msg


def test_an_hmount_that_produces_no_output_says_so_instead_of_a_bare_colon():
    # The runner degrades to "" on OSError, so the old message ended at the
    # colon and pointed at the disk image -- which was never the problem.
    class Silent(KitIO):
        def run(self, argv):
            self.calls.append(list(argv))
            return ""                              # hmount says nothing at all

    ok, msg, _ = Silent().build()
    assert ok is False
    assert msg.rstrip().endswith(")") and "could not be run" in msg, msg


def test_a_machine_that_never_ran_applebridge_is_told_how_to_get_a_kit():
    # The bootstrap gap: a kit is built FROM a guest that already has the
    # suite, so this refusal is the normal experience on a fresh machine and
    # must carry the way out, not just a list of absent files.
    ok, msg, _ = ib.export_guest_kit(
        "/tmp/kit", "1.2.3.4", probes(),
        run=lambda a: "Volume ..." if a[0] == "hmount" else "",
        exists=lambda p: p.endswith(".dmg"),        # source volume has none
        write_bytes=lambda p, d: None)
    assert ok is False
    assert "APPLEBRIDGE_GUEST_DIALS" in msg, msg
    assert "already has AppleBridge" in msg, msg


def test_the_report_states_whether_hfsutils_is_present():
    text = ib.format_text(probes(hfs_missing=["hformat"]),
                          ib.decide(probes(hfs_missing=["hformat"])))
    assert "hfsutils:" in text and "MISSING" in text and "hformat" in text, text
    assert "hfsutils:         present" in ib.format_text(
        probes(), ib.decide(probes()))


# --- the release kit: an artifact that may be handed to a stranger ----------

def test_a_release_kit_ships_no_address_at_all():
    # The whole point. An address baked into a PUBLISHED kit points every
    # downloader's guest at the machine that built it -- and on any LAN where
    # that number answers, it connects and reports full health (R2), once per
    # user instead of once.
    text = ib.guest_prefs_text("")
    assert "\nIP=\n" in text, text
    assert not ib.payload_host_literals(text.encode("mac_roman")), text


def test_a_normal_kit_still_carries_the_address_because_that_is_its_job():
    text = ib.guest_prefs_text("192.168.3.240")
    assert "IP=192.168.3.240" in text


def test_the_release_prefs_say_where_the_address_comes_from_instead():
    # An empty field with no explanation reads as a bug. Name both ways out.
    text = ib.guest_prefs_text("")
    assert "--seed-guest-prefs" in text and "AppleBridgeConfig" in text


def test_the_release_export_refuses_prefs_that_carry_an_address():
    # A guard on the ONE file where an address legitimately lives, so it cannot
    # be smuggled into a release by a future edit of the template. Simulated by
    # making the template return an address even in release mode.
    real = ib.guest_prefs_text
    ib.guest_prefs_text = lambda ip, net="OT": "IP=192.168.3.240\nNET=OT\n"
    try:
        io = KitIO()
        ok, msg, _ = io.build(release=True)
    finally:
        ib.guest_prefs_text = real
    assert ok is False
    assert "192.168.3.240" in msg and "release kit" in msg, msg
    assert not any(c[0] == "hformat" for c in io.calls), \
        "it must refuse BEFORE writing an image somebody could publish"


def test_a_release_kit_builds_and_says_the_address_is_missing_on_purpose():
    io = KitIO()
    ok, msg, _ = io.build(release=True)
    assert ok is True, msg
    assert "carry NO address" in msg and "--seed-guest-prefs" in msg, msg


def test_the_seeder_finds_the_prefs_in_a_KIT_as_well_as_an_installed_guest():
    # A downloaded release kit is stamped before it is ever mounted, so the
    # seeder must handle a volume that has no System Folder at all.
    assert ib.KIT_PREFS_HFS == ":AppleBridge Prefs"
    assert ib.GUEST_PREFS_HFS.startswith(":System Folder:")

    seen = []

    def run(argv):
        seen.append(list(argv))
        if argv[0] == "hmount":
            return "Volume name is whatever\n"
        if argv[0] == "hcopy" and argv[2] == ib.GUEST_PREFS_HFS:
            return ""                      # absent: this is a kit, not a guest
        if argv[0] == "hcopy" and argv[2] == ib.KIT_PREFS_HFS:
            open(argv[3], "wb").write(b"# prefs\nIP=\nNET=OT\n")
        return ""

    ok, msg = ib.seed_guest_prefs("/tmp/kit.dmg", "192.168.3.158", probes(),
                                  run=run,
                                  hfs={"tmp": "/tmp/_ab_seed_test"})
    assert ok is True, msg
    assert ib.KIT_PREFS_HFS in msg, msg
    copied_back = [c for c in seen if c[0] == "hcopy" and c[-1] == ib.KIT_PREFS_HFS]
    assert copied_back, "the edited file was never written back to the kit"


def test_the_seeder_says_both_places_it_looked_when_it_finds_neither():
    ok, msg = ib.seed_guest_prefs(
        "/tmp/x.dmg", "192.168.3.1", probes(),
        run=lambda a: "Volume name is x\n" if a[0] == "hmount" else "",
        hfs={"tmp": "/tmp/_ab_seed_missing"})
    assert ok is False
    assert ib.GUEST_PREFS_HFS in msg and ib.KIT_PREFS_HFS in msg, msg


def test_seeding_preserves_the_prefs_type_and_creator():
    # `hcopy -r` moves bytes and nothing else, so a seeded file came back as
    # ????/UNIX — and a typeless prefs file is one AppleBridgeConfig will not
    # open, while the daemon reads by path and carries on. Invisible until a
    # kit was listed after seeding (2026-07-29).
    calls = []

    def run(argv):
        calls.append(list(argv))
        if argv[0] == "hmount":
            return "Volume name is whatever\n"
        if argv[0] == "hls":
            return "f  TEXT/ABrg    0    441 Jul 29 11:46 AppleBridge Prefs\n"
        if argv[0] == "hcopy" and argv[2] == ib.GUEST_PREFS_HFS:
            open(argv[3], "wb").write(b"# prefs\nIP=1.2.3.4\nNET=OT\n")
        return ""

    ok, msg = ib.seed_guest_prefs("/tmp/g.dmg", "192.168.3.9", probes(),
                                  run=run, hfs={"tmp": "/tmp/_ab_tc_test"})
    assert ok is True, msg
    fix = [c for c in calls if c[0] == "hattrib"]
    assert fix, "type/creator were never restored"
    assert "-t" in fix[0] and "TEXT" in fix[0], fix
    assert "ABrg" in fix[0], "it must restore what was THERE, not a hardcoded pair"


def test_the_type_creator_probe_reads_what_hls_reports():
    run = lambda a: "f  TEXT/ttxt   0   441 Jul 29 11:46 AppleBridge Prefs\n"
    assert ib.hfs_type_creator(run, ":x") == ("TEXT", "ttxt")
    assert ib.hfs_type_creator(lambda a: "", ":x") is None


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
