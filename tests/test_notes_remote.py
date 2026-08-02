"""notes.py speaks to a remote channel over ssh when APPLEBRIDGE_NOTES is a
`host:/path` spec -- for a session that drives the Mac by ssh from another
machine, where the Mac repo's own hooks never apply. This pins the contract
WITHOUT a network: read()/append() take the ssh executor as an injectable `run`
(the run_step(send, ...) shape from host/mpw.py), so a fake proves the wiring
and the local-file path is proven never to reach ssh.

Two failure modes this guards, both raised in review on 2026-08-02:
  - a local spec (no colon, a colon INSIDE a filename, a Windows drive) must not
    be mistaken for a remote target and must never trigger an ssh call;
  - the remote append must be one atomic O_APPEND write, not a `cat >>` that a
    large payload could split across write()s.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "host", "tools"))
import notes  # noqa: E402


def test_remote_detection():
    assert notes._remote("user@host:/abs/path") == ("user@host", "/abs/path")
    assert notes._remote("192.168.3.154:/tmp/x") == ("192.168.3.154", "/tmp/x")
    # none of these is a remote target -> stays a local file, no ssh
    for local in ("/tmp/applebridge_notes.log", "/tmp/a:b", "relative/p",
                  "C:\\win\\path", "", "nocolon"):
        assert notes._remote(local) is None, local


def test_read_remote_uses_run_not_network():
    calls = []

    def fake(host, cmd, stdin=None):
        calls.append((host, cmd, stdin))
        return True, "a\nb\n"

    assert notes.read("h:/p", run=fake) == ["a\n", "b\n"]
    assert calls[0][0] == "h"
    assert calls[0][1].startswith("cat ")


def test_append_remote_is_one_atomic_write():
    calls = []

    def fake(host, cmd, stdin=None):
        calls.append((host, cmd, stdin))
        return True, ""

    assert notes.append("hello", "h:/p", run=fake) is True
    _, cmd, stdin = calls[0]
    # a single O_APPEND write on the far side, NOT `cat >>`
    assert "open(sys.argv[1]" in cmd and "sys.stdin.read()" in cmd
    assert "cat >>" not in cmd
    assert stdin == "hello\n"


def test_ssh_error_degrades_to_nothing():
    def dead(host, cmd, stdin=None):
        return False, ""

    assert notes.read("h:/p", run=dead) == []
    assert notes.append("x", "h:/p", run=dead) is False


def test_local_path_never_calls_run():
    def boom(*a, **k):
        raise AssertionError("the ssh executor must not run for a local path")

    tf = tempfile.mktemp()
    try:
        assert notes.append("local line", tf, run=boom) is True
        assert notes.read(tf, run=boom) == ["local line\n"]
    finally:
        if os.path.exists(tf):
            os.remove(tf)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("ok:", _name)
    print("all notes-remote checks passed")
