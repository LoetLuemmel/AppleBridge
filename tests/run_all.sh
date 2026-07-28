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
       test_input_modifiers.py test_self_update.py test_ctrl_auth.py
       test_bridge_doctor.py test_nbp_lookup.py
       test_ether_backend.py test_guest_input.py
       test_host_input_tools.py test_afp_mount.py
       test_native_verbs.py test_doc_claims.py test_hardware_findings.py
       test_process_mutations.py
       test_host_ip_config.py test_installer.py test_ae_wait_bound.py
       test_command_timeout.py test_decider_coverage.py)

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
