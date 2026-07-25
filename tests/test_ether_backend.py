"""Tests for host/check_ether_backend.sh — the emulator-backend preflight.

The script guards a failure mode that does NOT look like a network failure: on
`ether slirp` the bridge keeps working (slirp forwards TCP) while AppleTalk is
silently dropped, so the only visible symptom is an empty Chooser. Every branch
is exercised here against temp files, so the real prefs are never touched.

Run: python3 tests/test_ether_backend.py   (or via pytest)
"""

import os
import shutil
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "host",
                      "check_ether_backend.sh")

PREFS_BODY = ("disk /Users/pit/System761.dmg\n"
              "extfs /Users/pit/Desktop/Share\n"
              "screen win/1024/768\n"
              "{ether}"
              "ramsize 130023424\n"
              "idlewait true\n")


class Case:
    """One temp prefs/netmode pair plus the script run over it."""

    def __init__(self, ether="etherhelper/en8", netmode="etherhelper/en8",
                 keep=False):
        self.dir = tempfile.mkdtemp()
        self.prefs = os.path.join(self.dir, "prefs")
        self.netmode = self.prefs + ".netmode"
        body = PREFS_BODY.format(ether=("ether %s\n" % ether) if ether else "")
        with open(self.prefs, "w") as fh:
            fh.write(body)
        if netmode is not None:
            with open(self.netmode, "w") as fh:
                fh.write(netmode + "\n")
        env = dict(os.environ)
        env.pop("AB_KEEP_ETHER", None)
        if keep:
            env["AB_KEEP_ETHER"] = "1"
        p = subprocess.run(["bash", SCRIPT, self.prefs, self.netmode],
                           capture_output=True, text=True, env=env)
        self.rc = p.returncode
        self.out = p.stdout + p.stderr

    def prefs_text(self):
        with open(self.prefs) as fh:
            return fh.read()

    def ether(self):
        for line in self.prefs_text().splitlines():
            if line.startswith("ether "):
                return line.split(None, 1)[1]
        return None

    def backups(self):
        return [f for f in os.listdir(self.dir) if "bak-ether-" in f]

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def run(**kw):
    c = Case(**kw)
    try:
        yield_ = (c.rc, c.out, c.ether(), c.prefs_text(), c.backups())
    finally:
        c.cleanup()
    return yield_


# --- the matching case ------------------------------------------------------
def test_matching_backend_passes_quietly():
    rc, out, ether, _, backups = run()
    assert rc == 0
    assert "matches .netmode" in out
    assert ether == "etherhelper/en8"
    assert backups == []          # nothing rewritten, nothing backed up


# --- drift: the case this exists for ---------------------------------------
def test_slirp_drift_is_repaired_by_default():
    rc, out, ether, _, backups = run(ether="slirp")
    assert rc == 0
    assert "DRIFT" in out
    assert ether == "etherhelper/en8"     # repaired
    assert len(backups) == 1              # and the old file kept


def test_slirp_drift_explains_the_non_obvious_consequence():
    # Without this line the message reads as a harmless config nit.
    _, out, _, _, _ = run(ether="slirp")
    assert "AppleTalk" in out and "Chooser" in out


def test_repair_touches_only_the_ether_line():
    _, _, _, text, _ = run(ether="slirp")
    assert "ramsize 130023424" in text
    assert "screen win/1024/768" in text
    assert "extfs /Users/pit/Desktop/Share" in text
    assert text.count("ether ") == 1       # replaced, not appended


def test_repair_collapses_a_duplicate_ether_key():
    # Two `ether` lines leave the effective backend ambiguous; the repair must
    # end with exactly one.
    d = tempfile.mkdtemp()
    try:
        prefs = os.path.join(d, "prefs")
        with open(prefs, "w") as fh:
            fh.write("screen win/1024/768\nether slirp\nether slirp\nramsize 1\n")
        netmode = prefs + ".netmode"
        with open(netmode, "w") as fh:
            fh.write("etherhelper/en8\n")
        subprocess.run(["bash", SCRIPT, prefs, netmode], capture_output=True)
        with open(prefs) as fh:
            text = fh.read()
        assert text.count("ether ") == 1
        assert "ether etherhelper/en8" in text
        assert "ramsize 1" in text
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_in_place_edit_stays_portable():
    # `sed -i` takes an argument on BSD and none on GNU, so no single spelling
    # works on both. The BSD form silently no-ops under GNU sed — it "repaired"
    # nothing, which is how this reached CI (the repair tests run on Linux).
    with open(SCRIPT) as fh:
        code = [l for l in fh if not l.lstrip().startswith("#")]
    assert not any("sed -i" in l for l in code)   # the comment may name it


def test_drift_between_two_real_backends_is_also_repaired():
    # Not slirp-specific: any mismatch against the recorded intent is drift.
    rc, out, ether, _, _ = run(ether="etherhelper/en0", netmode="etherhelper/en8")
    assert rc == 0 and "DRIFT" in out
    assert ether == "etherhelper/en8"
    assert "AppleTalk" not in out          # the slirp-only note stays out


def test_keep_flag_reports_but_changes_nothing():
    rc, out, ether, _, backups = run(ether="slirp", keep=True)
    assert rc == 1                          # distinct exit: drift left in place
    assert "AB_KEEP_ETHER=1" in out
    assert ether == "slirp"                 # untouched
    assert backups == []


# --- first run on a machine with no recorded intent ------------------------
def test_missing_netmode_seeds_the_current_backend():
    rc, out, ether, _, _ = run(netmode=None)
    assert rc == 0
    assert "recorded as the intended backend" in out
    assert ether == "etherhelper/en8"


def test_seeded_netmode_is_actually_written():
    c = Case(netmode=None)
    try:
        assert os.path.exists(c.netmode)
        with open(c.netmode) as fh:
            assert fh.read().strip() == "etherhelper/en8"
    finally:
        c.cleanup()


# --- degenerate inputs must not corrupt anything ---------------------------
def test_missing_ether_key_is_appended_not_left_broken():
    rc, out, ether, text, _ = run(ether=None)
    assert rc == 0
    assert ether == "etherhelper/en8"
    assert text.count("ether ") == 1


def test_no_ether_key_and_no_intent_cannot_be_checked():
    rc, out, _, _, _ = run(ether=None, netmode=None)
    assert rc == 2
    assert "WARN" in out


def test_absent_prefs_file_reports_rather_than_guesses():
    d = tempfile.mkdtemp()
    try:
        p = subprocess.run(["bash", SCRIPT, os.path.join(d, "nope"),
                            os.path.join(d, "nope.netmode")],
                           capture_output=True, text=True)
        assert p.returncode == 2
        assert "no prefs file" in (p.stdout + p.stderr)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_netmode_with_stray_whitespace_still_compares_equal():
    c = Case()
    c.cleanup()
    d = tempfile.mkdtemp()
    try:
        prefs = os.path.join(d, "prefs")
        with open(prefs, "w") as fh:
            fh.write(PREFS_BODY.format(ether="ether etherhelper/en8\n"))
        netmode = prefs + ".netmode"
        with open(netmode, "w") as fh:
            fh.write("  etherhelper/en8  \r\n")     # trailing CR + spaces
        p = subprocess.run(["bash", SCRIPT, prefs, netmode],
                           capture_output=True, text=True)
        assert p.returncode == 0
        assert "matches .netmode" in p.stdout
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- the launcher must actually call it ------------------------------------
def test_start_stack_runs_the_preflight_before_launching():
    path = os.path.join(os.path.dirname(__file__), "..", "host",
                        "start_stack.sh")
    with open(path) as fh:
        text = fh.read()
    assert "check_ether_backend.sh" in text
    assert text.index("check_ether_backend.sh") < text.index("Launching Basilisk II")


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
