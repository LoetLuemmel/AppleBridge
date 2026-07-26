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
       test_process_mutations.py)

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
