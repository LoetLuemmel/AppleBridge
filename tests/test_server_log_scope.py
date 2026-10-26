"""Only the running server writes the server log (2026-10-26).

host_server.py opened its log at import, so every importer - above all this test suite - appended to the live
server's /tmp/applebridge_server.log. Test fixtures were read there as a real link drop, and bridge_doctor reads the
guest's last reported name from that file. Importing the module and logging must leave the log untouched.

Run: python3 tests/test_server_log_scope.py
"""

import os
import subprocess
import sys
import tempfile

HOST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host")


def test_importing_and_logging_does_not_touch_the_server_log():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "server.log")
    code = "import sys; sys.path.insert(0, %r); import host_server as h; h.log('from an importer')" % HOST
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=dict(os.environ, APPLEBRIDGE_LOG=path), timeout=60)
    assert r.returncode == 0, r.stderr[-400:]
    assert "from an importer" in r.stderr, "log() still reaches stderr"
    assert not os.path.exists(path), "an importer created or appended to the server log"


def test_main_is_what_opens_it():
    src = open(os.path.join(HOST, "host_server.py")).read()
    main = src[src.index("def main():"):]
    assert 'open(LOG_PATH, "a"' in main[:400], "main() opens the log first thing"
    assert src.count('open(LOG_PATH') == 1, "and nothing else does"


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
