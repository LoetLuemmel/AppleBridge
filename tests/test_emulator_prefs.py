#!/usr/bin/env python3
"""Which prefs file — the question that was a constant for four modules.

The defect these pin (2026-08-17, reported from a SheepShaver-on-Linux host):
`~/.basilisk_ii_prefs` was hardcoded, no SheepShaver path existed anywhere in
the tree, and the installer's own verification could not catch it — it
confirms a write by re-reading the file it just wrote, so writing to the
wrong file passes.

Every case below is driven from canned state: no home directory is read, no
emulator runs, nothing is written.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "host"))
import emulator_prefs                                            # noqa: E402
import install_bridge                                            # noqa: E402

HOME = "/home/tester"
BAS = os.path.join(HOME, ".basilisk_ii_prefs")
SHEEP = os.path.join(HOME, ".sheepshaver_prefs")


def present(*paths):
    """exists() over a fixed set — absent means absent, not 'ask the disk'."""
    known = set(paths)
    return lambda p: p in known


class Resolution(unittest.TestCase):
    def test_a_running_sheepshaver_wins_over_every_file_on_disk(self):
        """The process holds the file the installer is about to rewrite."""
        r = emulator_prefs.resolve(processes={"sheepshaver": {"pid": 7}},
                                   home=HOME, exists=present(BAS))
        self.assertEqual(r["path"], SHEEP)
        self.assertEqual(r["source"], "running process")

    def test_a_running_basilisk_is_still_basilisk(self):
        r = emulator_prefs.resolve(processes={"basilisk": {"pid": 7}},
                                   home=HOME, exists=present(BAS, SHEEP))
        self.assertEqual(r["path"], BAS)

    def test_the_discovered_emulator_names_the_file_when_nothing_runs(self):
        r = emulator_prefs.resolve(
            bundle={"app": "/opt/SheepShaver.AppImage"},
            home=HOME, exists=present())
        self.assertEqual(r["path"], SHEEP)
        self.assertEqual(r["source"], "discovered emulator")

    def test_a_bundle_is_matched_case_insensitively(self):
        """`SheepShaver` in a bundle, `sheepshaver` from a distro package."""
        for app in ("/Applications/SheepShaver.app", "/usr/bin/sheepshaver"):
            self.assertEqual(
                emulator_prefs.resolve(bundle={"app": app}, home=HOME,
                                       exists=present())["path"], SHEEP)

    def test_one_prefs_file_on_disk_settles_it(self):
        r = emulator_prefs.resolve(home=HOME, exists=present(SHEEP))
        self.assertEqual(r["path"], SHEEP)
        self.assertFalse(r["ambiguous"])

    def test_two_files_and_nothing_running_is_reported_as_ambiguous(self):
        """The developer machine is this case: both emulators installed.

        Answering by mtime is evidence, not proof — so the flag travels with
        the answer instead of being resolved away in silence.
        """
        r = emulator_prefs.resolve(
            home=HOME, exists=present(BAS, SHEEP),
            getmtime=lambda p: 200.0 if p == SHEEP else 100.0)
        self.assertEqual(r["path"], SHEEP)
        self.assertTrue(r["ambiguous"])
        self.assertEqual(r["present"], ["basilisk", "sheepshaver"])
        self.assertIn("AMBIGUOUS", emulator_prefs.describe(r))

    def test_no_prefs_file_yet_still_names_one(self):
        """A file that does not exist has to be named before it can be made."""
        r = emulator_prefs.resolve(home=HOME, exists=present())
        self.assertEqual(r["path"], BAS)
        self.assertIn("default", r["source"])
        self.assertFalse(r["ambiguous"])

    def test_an_unreadable_mtime_does_not_raise(self):
        def boom(_):
            raise OSError("gone")
        r = emulator_prefs.resolve(home=HOME, exists=present(BAS, SHEEP),
                                   getmtime=boom)
        self.assertIn(r["path"], (BAS, SHEEP))

    def test_the_netmode_sidecar_follows_the_prefs_file(self):
        """Otherwise a SheepShaver host records its intent beside Basilisk's
        file, and the drift check compares two different machines."""
        r = emulator_prefs.resolve(processes={"sheepshaver": {"pid": 1}},
                                   home=HOME, exists=present())
        self.assertEqual(r["netmode"], SHEEP + ".netmode")


class ProbeUsesTheResolution(unittest.TestCase):
    """The installer must not read Basilisk's file while SheepShaver runs."""

    def _probe(self, pgrep_out, exists):
        def run(argv):
            if argv[:2] == ["pgrep", "-fl"]:
                return pgrep_out if "SheepShaver" in argv[2] \
                    or "BasiliskII" in argv[2] else ""
            return ""
        return install_bridge.probe(run=run, read=lambda p: "", exists=exists,
                                    addresses=[], local_env_path="/nowhere")

    def test_a_running_sheepshaver_selects_its_own_prefs(self):
        probes = self._probe("4242 /usr/bin/SheepShaver", present())
        self.assertTrue(probes["paths"]["prefs"].endswith(".sheepshaver_prefs"),
                        probes["paths"]["prefs"])
        self.assertEqual(probes["prefs_choice"]["source"], "running process")

    def test_the_netmode_path_travels_with_it(self):
        probes = self._probe("4242 /usr/bin/SheepShaver", present())
        self.assertEqual(probes["paths"]["netmode"],
                         probes["paths"]["prefs"] + ".netmode")

    def test_a_pinned_path_is_still_honoured(self):
        """The suite pins paths everywhere; resolution must not override it."""
        probes = install_bridge.probe(
            run=lambda a: "", read=lambda p: "", exists=present(),
            addresses=[], prefs_path="/tmp/pinned_prefs",
            local_env_path="/nowhere")
        self.assertEqual(probes["paths"]["prefs"], "/tmp/pinned_prefs")
        self.assertEqual(probes["paths"]["netmode"], "/tmp/pinned_prefs.netmode")


class BundleMayCorrectButNotReassure(unittest.TestCase):
    """Discovery walks an ordered list, Basilisk first — so it can fix the
    path without earning the right to drop the ambiguity flag."""

    def test_a_bundle_correction_keeps_the_ambiguity(self):
        home = os.path.expanduser("~")
        bas, sheep = (os.path.join(home, ".basilisk_ii_prefs"),
                      os.path.join(home, ".sheepshaver_prefs"))

        def run(argv):
            if argv[:2] == ["pgrep", "-fl"]:
                return ""                       # nothing running
            return ""

        probes = install_bridge.probe(
            run=run, read=lambda p: "",
            exists=lambda p: p in (bas, sheep, "/opt/SheepShaver.AppImage"),
            addresses=[], local_env_path="/nowhere",
            emulator_app="/opt/SheepShaver.AppImage")
        self.assertTrue(probes["paths"]["prefs"].endswith(".sheepshaver_prefs"))
        self.assertTrue(probes["prefs_choice"]["ambiguous"],
                        "two emulators installed: the doubt survives the fix")


if __name__ == "__main__":
    unittest.main(verbosity=1)
