#!/bin/bash
#
# run_all.sh — run the host-edge test suite with the stdlib-only system Python
# (same /usr/bin/python3 the host server runs under). No pytest dependency; each
# test file is also runnable on its own and importable by pytest if installed.
#
#   ./tests/run_all.sh
#
set -u
cd "$(dirname "$0")"
PY="${PYTHON:-/usr/bin/python3}"

files=(test_macbinary.py test_screenshot_decode.py test_encoding_convert.py
       test_framing.py test_parse_response.py test_protocol_v02.py test_serial.py
       test_input_modifiers.py test_self_update.py test_ctrl_auth.py test_ctrl_deadline.py
       test_bridge_doctor.py test_nbp_lookup.py
       test_ether_backend.py test_guest_input.py test_guest_paths.py test_quit_verb_contract.py test_hostshot_verb.py test_system_shot.py test_aete_parameters.py test_guest_front_guard.py test_menu_title_point.py test_ae_handler_error.py test_tool_schema.py test_loop_guard.py test_read_file_contract.py test_success_from_a_reply.py test_typing_budget.py
       test_host_input_tools.py test_afp_mount.py
       test_native_verbs.py test_doc_claims.py test_hardware_findings.py
       test_process_mutations.py
       test_host_ip_config.py test_installer.py test_ae_wait_bound.py
       test_command_timeout.py test_decider_coverage.py
       test_build_file_list.py test_make_test_guest.py test_write_file.py
       test_build_verification.py test_session_brief.py test_notes.py test_notes_field.py test_dlgpatch_contract.py test_rsrc_extract.py test_ledger_diff.py test_counter_probe_contract.py test_notes_remote.py)

# test_host_ip_config.py was written with the R1/R2 repair and never added
# here, so CI ran none of its 23 ratchets — including "no host-address literal
# in the runtime files", the one holding the defect that suite exists for. It
# passed the whole time; nothing was executing it. A green run that runs
# nothing is this project's own named failure class, so: every tests/test_*.py
# belongs in this list, and test_registration_is_complete() below enforces it.
#
# smoke_e2e.py is deliberately NOT here — it drives the live stack and needs an
# emulator, so it is a manual pre-release gate rather than a CI suite.

fail=0
for f in "${files[@]}"; do
    echo "=== $f ==="
    "$PY" "$f" || fail=1
    echo
done

if [ "$fail" -eq 0 ]; then
    echo "ALL SUITES PASSED"
else
    echo "SOME SUITES FAILED"
fi
exit "$fail"
