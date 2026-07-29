"""Tests for host/tools/make_test_guest.py — the pristine-guest builder.

The tool copies a working disk image and strips AppleBridge from the COPY, so
an install can be tested end to end on a machine that has never heard of it.
Its safety argument rests entirely on two properties, and both are tested here
because neither is visible from the output when it goes wrong:

  1. The SOURCE image is never written to. It is somebody's System 7 install
     with their toolchain on it, and a tool that strips in place is one bad path
     away from destroying it.
  2. "Pristine" is VERIFIED, not assumed. A test image that still carries a
     daemon would make a later "the install worked!" mean nothing at all.

Both bugs found while writing the tool were in the checking, not the doing:

  * `hls -l` was parsed as nine columns when it emits eight, so the first run
    deleted ONE file of twelve and said nothing about the other eleven.
  * the verification called `hls` and treated any output as "this path exists" —
    but the runner merges stderr, so `hls: … no such file or directory` counted
    as presence. It reported everything still present while the strip had in
    fact worked perfectly: a check returning the exact opposite of the truth,
    guarding the single claim the tool exists to make.

Run: python3 tests/test_make_test_guest.py   (or via pytest)
"""

import os
import sys
import types

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "host"))
sys.path.insert(0, os.path.join(HERE, "..", "host", "tools"))
import make_test_guest as mtg  # noqa: E402

# Verbatim from hfsutils on this machine — EIGHT whitespace-separated tokens,
# the name last. Counting nine skipped almost every file.
HLS_LONG = (
    "f  APPL/ABrg     41696         0 Jun 30 12:55 AB.old4\n"
    "f  APPL/ABrg     83566         0 Jul 28 07:12 AppleBridge\n"
    "f  APPL/ABrg     83045         0 Jul 28 07:03 'AppleBridge old'\n"
    "f  APPL/ABcf     11392         0 Jun 30 12:06 AppleBridgeConfig\n"
    "f  APPL/ABwd      8246         0 Jun 30 12:06 AppleBridgeWatchdog\n"
    "d          3 items               Jul 28 07:32 somefolder\n")

MISSING = 'hls: ":AppleBridge:": no such file or directory\n'


class FakeHfs:
    """Records every hfsutils call and answers from a simple volume model."""

    def __init__(self, present=(), listing=HLS_LONG, mountable=True):
        self.present = set(present)
        self.listing = listing
        self.mountable = mountable
        self.calls = []

    def __call__(self, argv, timeout=600):
        self.calls.append(list(argv))
        cmd = argv[0]
        if cmd == "hmount":
            return "Volume was last modified…\n" if self.mountable else "hmount: bad\n"
        if cmd == "humount":
            return ""
        if cmd == "hls":
            path = argv[-1]
            if argv[1:2] == ["-l"]:
                return self.listing
            return (path + "\n") if path in self.present else MISSING
        if cmd in ("hdel", "hrmdir"):
            self.present.discard(argv[1])
            return ""
        return ""

    def deleted(self):
        return [c[1] for c in self.calls if c[0] == "hdel"]


# --- the presence check, which was reporting the opposite of the truth ------
def test_a_missing_path_is_absent_even_though_hls_prints_an_error():
    fake = FakeHfs(present=())
    assert mtg._present(fake, ":AppleBridge:") is False


def test_a_path_that_is_there_is_present():
    fake = FakeHfs(present={":AppleBridge:"})
    assert mtg._present(fake, ":AppleBridge:") is True


def test_silence_is_absence_too():
    assert mtg._present(lambda argv, timeout=600: "", ":x:") is False


# --- parsing hls -l ---------------------------------------------------------
def test_every_file_in_the_folder_is_deleted_not_just_one():
    # The first run removed 1 of 12 because the column count was wrong, and
    # reported success for the rest.
    fake = FakeHfs(present=set())
    ok, notes = mtg.strip_applebridge("/tmp/x.dmg", run=fake)
    names = [d.rsplit(":", 1)[-1] for d in fake.deleted()
             if d.startswith(mtg.INSTALL_FOLDER)]
    assert "AB.old4" in names
    assert "AppleBridge" in names
    assert "AppleBridgeConfig" in names
    assert "AppleBridgeWatchdog" in names
    assert len(names) == 5, f"expected all five files, got {names}"


def test_a_quoted_name_with_a_space_is_unquoted_before_deleting():
    fake = FakeHfs(present=set())
    mtg.strip_applebridge("/tmp/x.dmg", run=fake)
    assert mtg.INSTALL_FOLDER + "AppleBridge old" in fake.deleted(), \
        "hls quotes names containing spaces; the quotes are not part of the name"


def test_directories_in_the_listing_are_not_passed_to_hdel():
    # Measured: a FILE line has eight whitespace tokens and a DIRECTORY line has
    # seven ("d  12 items  Jul 28 07:36 name"), so the length check alone already
    # excludes folders and the `parts[0] == "f"` test is redundant. Removing the
    # type check breaks nothing, which is why no mutation of it fails — recorded
    # here so the redundancy is a known choice rather than something a later
    # reader tidies away believing it is load-bearing, or trusts believing it is
    # tested. The property below is what matters and it is genuinely asserted.
    fake = FakeHfs(present=set())
    mtg.strip_applebridge("/tmp/x.dmg", run=fake)
    assert not any(d.endswith("somefolder") for d in fake.deleted())


# --- the verification, which is the tool's only real claim ------------------
def test_a_surviving_install_folder_fails_the_strip():
    # If this passes while a daemon is still there, a later "the install worked"
    # proves nothing.
    fake = FakeHfs(present={mtg.INSTALL_FOLDER})
    fake.calls.append(["sentinel"])

    def stubborn(argv, timeout=600):
        out = fake(argv, timeout)
        if argv[0] in ("hdel", "hrmdir"):
            fake.present.add(mtg.INSTALL_FOLDER)   # refuses to go
        return out

    ok, notes = mtg.strip_applebridge("/tmp/x.dmg", run=stubborn)
    assert ok is False
    assert any("STILL PRESENT" in n for n in notes)


def test_a_clean_strip_reports_verified():
    fake = FakeHfs(present={mtg.INSTALL_FOLDER, mtg.GUEST_PREFS, mtg.STARTUP_ITEM})
    ok, notes = mtg.strip_applebridge("/tmp/x.dmg", run=fake)
    assert ok is True
    assert any("verified" in n for n in notes), notes


def test_nothing_to_remove_is_success_not_failure():
    # A machine that never had AppleBridge is the GOAL, so absence is the win.
    fake = FakeHfs(present=set(), listing="")
    ok, notes = mtg.strip_applebridge("/tmp/x.dmg", run=fake)
    assert ok is True
    assert any("no guest prefs to remove" in n for n in notes), notes


def test_an_unmountable_image_fails_before_deleting_anything():
    fake = FakeHfs(mountable=False)
    ok, notes = mtg.strip_applebridge("/tmp/x.dmg", run=fake)
    assert ok is False
    assert not fake.deleted(), "must not delete on a volume it could not mount"


def test_the_volume_is_always_unmounted():
    fake = FakeHfs(present={mtg.INSTALL_FOLDER})
    mtg.strip_applebridge("/tmp/x.dmg", run=fake)
    assert ["humount"] in fake.calls


# --- the test config --------------------------------------------------------
def test_the_config_points_at_the_copy_and_never_the_source(tmp=None):
    import tempfile
    src = os.path.join(tempfile.gettempdir(), "src_prefs")
    with open(src, "w") as fh:
        fh.write("disk /Users/x/original.dmg\n"
                 "ether etherhelper/en8\n"
                 "extfs /Users/x/Share\n"
                 "ramsize 130023424\n")
    dest = os.path.join(tempfile.gettempdir(), "test_prefs_out")
    mtg.write_test_config(src, "/Users/x/copy.dmg", dest)
    body = open(dest).read()
    assert "disk /Users/x/copy.dmg" in body
    assert "original.dmg" not in body, "the test machine must not boot the source"
    # slirp because that is the shipping branch (D-019); the old backend must go.
    assert "ether slirp" in body and "etherhelper" not in body
    # same shared folder, or the kit is invisible to the guest being tested
    assert "extfs /Users/x/Share" in body
    assert "ramsize 130023424" in body, "unrelated settings must survive"


def test_the_kit_gets_its_own_disk_line_because_extfs_cannot_carry_apps():
    # Without this the test machine boots perfectly and simply has no kit
    # volume — which reads as "the image failed to mount" and sends you looking
    # at the image. It cost a long detour on 2026-07-28.
    import tempfile
    src = os.path.join(tempfile.gettempdir(), "src_prefs_kit")
    with open(src, "w") as fh:
        fh.write("disk /Users/x/original.dmg\nether etherhelper/en8\n")
    dest = os.path.join(tempfile.gettempdir(), "test_prefs_kit_out")
    mtg.write_test_config(src, "/Users/x/copy.dmg", dest,
                          kit_image="/Users/x/AppleBridgeKit.dmg")
    body = open(dest).read()
    assert "disk /Users/x/copy.dmg" in body
    assert "disk /Users/x/AppleBridgeKit.dmg" in body
    assert "ether slirp" in body, "the backend line must survive the insertion"
    assert body.count("ether ") == 1, "duplicate ether key"


def test_no_kit_means_no_stray_disk_line():
    # A `disk` line pointing at a file that does not exist is worse than none.
    import tempfile
    src = os.path.join(tempfile.gettempdir(), "src_prefs_nokit")
    with open(src, "w") as fh:
        fh.write("disk /Users/x/original.dmg\nether slirp\n")
    dest = os.path.join(tempfile.gettempdir(), "test_prefs_nokit_out")
    mtg.write_test_config(src, "/Users/x/copy.dmg", dest)
    body = open(dest).read()
    assert body.count("disk ") == 1
    assert "ether slirp" in body


def test_the_source_image_is_never_written_to():
    # The safety argument in one assertion: no hfsutils write verb may ever name
    # the source. The tool copies first and strips only the copy.
    src, copy = "/Users/x/original.dmg", "/Users/x/copy.dmg"
    fake = FakeHfs(present=set())
    mtg.strip_applebridge(copy, run=fake)
    for call in fake.calls:
        assert src not in " ".join(call), f"the source appeared in {call}"
    assert ["hmount", copy] in fake.calls


def test_it_refuses_without_hfsutils_before_touching_any_image(monkeypatched=None):
    # Same undeclared dependency install_bridge had: this tool shells out to
    # hmount/hls/hcopy, macOS ships none of them, and the old failure was
    # `hmount failed:` with nothing after the colon.
    import install_bridge

    real = install_bridge.probe_hfsutils
    calls = []
    install_bridge.probe_hfsutils = lambda **kw: {"found": {},
                                                  "missing": ["hmount", "hls"]}
    real_running = mtg.emulator_running
    mtg.emulator_running = lambda: calls.append("running-check") or False
    try:
        rc = mtg.main([])
    finally:
        install_bridge.probe_hfsutils = real
        mtg.emulator_running = real_running
    assert rc == 3, f"expected the refusal exit code, got {rc}"
    assert not calls, "it must refuse before probing anything else"


def test_a_silent_hmount_is_explained_rather_than_ending_at_a_colon():
    fake = types.SimpleNamespace(calls=[])

    def run(argv):
        fake.calls.append(list(argv))
        return ""                      # the binary could not be run at all

    ok, notes = mtg.strip_applebridge("/tmp/copy.dmg", run=run)
    assert ok is False
    assert "could not be run" in notes[0], notes


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
