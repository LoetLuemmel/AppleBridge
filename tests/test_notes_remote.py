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


def test_a_failed_ssh_READ_degrades_to_nothing():
    """Silence is the right answer for a read: an unreachable channel has no
    notes, and a reader that raised would take the session brief down with it."""
    def dead(host, cmd, stdin=None):
        return False, ""

    assert notes.read("h:/p", run=dead) == []


def test_a_failed_ssh_WRITE_does_NOT_degrade_to_nothing():
    """The mirror image, and the asymmetry is the point. A lost READ costs a
    view that can be asked for again; a lost WRITE costs a message nobody knows
    was sent. On 2026-08-04 this returned False, a caller did not look, and two
    notes stayed lost for over an hour while both sides thought the channel was
    quiet. This test used to assert exactly that False — under the name
    "degrades to nothing", which for a write is the defect, not the design."""
    def dead(host, cmd, stdin=None):
        return False, "ssh: connect to host jetson port 22: No route to host"

    try:
        notes.append("x", "h:/p", run=dead)
        raise AssertionError("a lost note must not be reported as anything but lost")
    except notes.ChannelWriteError as exc:
        assert "No route to host" in str(exc), exc

    # Still available to a caller who has explicitly decided not to care.
    assert notes.append("x", "h:/p", run=dead, raise_on_fail=False) is False


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


def test_ssh_run_pins_utf8_not_the_locale():
    """The executor must decode utf-8, not the locale. text=True alone uses the
    locale's encoding; on a headless C/POSIX box that is ASCII, and one non-ascii
    byte in a note (em-dash, umlaut, a stray >=) then raises UnicodeDecodeError --
    which is neither OSError nor SubprocessError, so a narrow except would let it
    out of read() and tear down the watcher. Fake the subprocess so this needs no
    network; assert the utf-8 pin and that non-ascii survives."""
    seen = {}

    class _CP:
        returncode = 0
        stdout = "café — ≥\n"

    def fake_run(argv, **kw):
        seen.update(kw)
        return _CP()

    orig = notes.subprocess.run
    notes.subprocess.run = fake_run
    try:
        ok, out = notes._ssh_run("h", "cat /x")
    finally:
        notes.subprocess.run = orig
    assert seen.get("encoding") == "utf-8", "must pin utf-8, not the locale"
    assert seen.get("errors") == "replace"
    assert ok and "café" in out


def test_ssh_run_swallows_any_exception():
    """Even a failure the utf-8 pin does not prevent must degrade to (False, '')
    -- a channel that crashes the hook is worse than one briefly silent."""
    def boom(argv, **kw):
        raise UnicodeDecodeError("utf-8", b"", 0, 1, "boom")

    orig = notes.subprocess.run
    notes.subprocess.run = boom
    try:
        assert notes._ssh_run("h", "cat /x") == (False, "")
    finally:
        notes.subprocess.run = orig


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("ok:", _name)
    print("all notes-remote checks passed")
