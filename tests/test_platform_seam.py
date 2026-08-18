#!/usr/bin/env python3
"""The five questions that differ per host OS, driven from canned output.

Every case below runs on whichever platform the suite happens to be on: the
platform is a parameter, not an environment. That is the only way a macOS
developer machine covers the Linux branches — and the reason these exist at
all is that the Linux branches were written from inference until a container
run on 2026-08-18 produced their actual output.

The defects pinned here were observed, not imagined (Docker Desktop, LinuxKit
6.10.14, python:3.12-slim, non-root):

  * "one usable interface (none found)" — zero read as one;
  * a plan step installing a launchd agent on a host with no launchd;
  * a diagnosis whose fix line was launchd advice (R13);
  * `brew install hfsutils` offered to a Debian host.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "host"))
import platform_seam as seam                                     # noqa: E402
import install_bridge                                            # noqa: E402

IP_ADDR_OUT = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
    "2: eth0    inet 172.17.0.2/16 brd 172.17.255.255 scope global eth0\n"
    "3: wlan0    inet 192.168.3.154/24 brd 192.168.3.255 scope global wlan0\n")

IFCONFIG_OUT = (
    "lo0: flags=8049<UP,LOOPBACK>\n"
    "\tinet 127.0.0.1 netmask 0xff000000\n"
    "en0: flags=8863<UP,BROADCAST>\n"
    "\tinet 192.168.3.154 netmask 0xffffff00 broadcast 192.168.3.255\n")


class Addresses(unittest.TestCase):
    def test_linux_reads_ip_not_ifconfig(self):
        calls = []

        def run(cmd):
            calls.append(cmd[0])
            return IP_ADDR_OUT if cmd[0] == "ip" else ""

        self.assertEqual(
            seam.ipv4_addresses(run=run, platform_name="linux"),
            [("eth0", "172.17.0.2"), ("wlan0", "192.168.3.154")])
        self.assertEqual(calls[0], "ip", "ip is asked first on Linux")

    def test_linux_falls_back_to_ifconfig_where_net_tools_exists(self):
        def run(cmd):
            return IFCONFIG_OUT if cmd[0] == "ifconfig" else ""
        self.assertEqual(seam.ipv4_addresses(run=run, platform_name="linux"),
                         [("en0", "192.168.3.154")])

    def test_macos_uses_ifconfig(self):
        def run(cmd):
            return IFCONFIG_OUT if cmd[0] == "ifconfig" else ""
        self.assertEqual(seam.ipv4_addresses(run=run, platform_name="darwin"),
                         [("en0", "192.168.3.154")])

    def test_loopback_is_never_offered_as_the_address_to_dial(self):
        def run(cmd):
            return IP_ADDR_OUT if cmd[0] == "ip" else ""
        for _, addr in seam.ipv4_addresses(run=run, platform_name="linux"):
            self.assertFalse(addr.startswith("127."))

    def test_neither_tool_present_answers_empty_rather_than_wrong(self):
        self.assertEqual(
            seam.ipv4_addresses(run=lambda c: "", platform_name="linux"), [])


class DefaultRoute(unittest.TestCase):
    def test_linux_parses_ip_route(self):
        out = "default via 172.17.0.1 dev eth0 proto dhcp src 172.17.0.2\n"
        self.assertEqual(
            seam.default_route_interface(run=lambda c: out,
                                         platform_name="linux"), "eth0")

    def test_macos_parses_route_get(self):
        out = "   route to: default\ndestination: default\n  interface: en0\n"
        self.assertEqual(
            seam.default_route_interface(run=lambda c: out,
                                         platform_name="darwin"), "en0")

    def test_each_platform_falls_back_to_the_other_tool(self):
        """A host may carry both; an answer from either is a fact.

        This is also what keeps the canned-output suites platform-independent:
        `test_bridge_doctor` feeds BSD `route` output and must get the same
        diagnosis whichever machine runs it.
        """
        bsd = "  interface: en0\n"
        linux = "default via 10.0.0.1 dev eth0\n"
        self.assertEqual(seam.default_route_interface(
            run=lambda c: bsd if c[0] == "route" else "",
            platform_name="linux"), "en0")
        self.assertEqual(seam.default_route_interface(
            run=lambda c: linux if c[0] == "ip" else "",
            platform_name="darwin"), "eth0")

    def test_no_answer_is_none_not_a_guess(self):
        self.assertIsNone(seam.default_route_interface(
            run=lambda c: "", platform_name="linux"))


class Service(unittest.TestCase):
    def test_macos_is_launchd_and_supported(self):
        svc = seam.service(platform_name="darwin", home="/Users/x")
        self.assertEqual(svc["kind"], "launchd")
        self.assertTrue(svc["supported"])
        self.assertTrue(svc["needs_deployed_copy"], "TCC forces the copy")

    def test_linux_is_systemd_and_not_yet_claimed(self):
        """A seam that claims a service it has never started is the same
        defect one layer up."""
        svc = seam.service(platform_name="linux", home="/home/x")
        self.assertEqual(svc["kind"], "systemd --user")
        self.assertFalse(svc["supported"])
        self.assertFalse(svc["needs_deployed_copy"], "no TCC on Linux")
        self.assertNotIn("Library", svc["unit_path"])


class PackageAdvice(unittest.TestCase):
    def test_debian_is_told_apt_not_brew(self):
        note = seam.package_note("hfsutils", which=lambda t: t == "apt-get",
                                 platform_name="linux")
        self.assertIn("apt-get install", note)
        self.assertNotIn("brew", note)

    def test_macos_is_told_brew(self):
        note = seam.package_note("hfsutils", which=lambda t: t == "brew",
                                 platform_name="darwin")
        self.assertIn("brew install hfsutils", note)

    def test_no_manager_at_all_still_leaves_an_exit(self):
        """Measured 2026-07-29 on a host with neither Homebrew nor MacPorts:
        advice naming a manager that is not there is as much a dead end as
        no advice, so the sentence says it is a guess."""
        cmd, certain = seam.install_hint("hfsutils", which=lambda t: None,
                                         platform_name="linux")
        self.assertFalse(certain)
        note = seam.package_note("hfsutils", which=lambda t: None,
                                 platform_name="linux")
        self.assertIn("no package manager was found", note)


class ManualStart(unittest.TestCase):
    def test_the_hint_carries_the_redirect_that_makes_it_work(self):
        """`run_server.sh` on a TTY gives the interactive prompt and NO
        control port (R12), so the redirect is the whole instruction."""
        hint = seam.manual_start_hint()
        self.assertIn("run_server.sh", hint)
        self.assertIn("< /dev/null", hint)

    def test_a_host_with_no_service_manager_is_given_that_hint(self):
        svc = seam.service(platform_name="linux")
        self.assertFalse(svc["supported"])
        self.assertIn("run_server.sh", seam.manual_start_hint())


class ZeroIsNotOne(unittest.TestCase):
    """The exact sentence the container printed, and why it was wrong."""

    def _notes(self, addresses):
        probes = install_bridge.probe(
            run=lambda a: "", read=lambda p: "", exists=lambda p: False,
            addresses=addresses, prefs_path="/tmp/none", local_env_path="/none")
        plan = install_bridge.decide(probes)
        return {n["key"]: n for n in plan["notes"]}

    def test_no_interfaces_is_reported_as_none_not_as_one(self):
        notes = self._notes([])
        self.assertIn("no_interfaces", notes)
        self.assertNotIn("single_interface", notes)
        self.assertIn("NO usable interface", notes["no_interfaces"]["message"])

    def test_one_interface_still_gets_the_single_nic_note(self):
        notes = self._notes([("en0", "192.168.3.154")])
        self.assertIn("single_interface", notes)
        self.assertNotIn("no_interfaces", notes)


class LinuxDiscovery(unittest.TestCase):
    """A running emulator on Linux is an executable path, not a bundle."""

    def _find(self, cmdline, exists_path):
        return install_bridge.probe_emulator_bundle(
            run=lambda a: cmdline if a[:2] == ["pgrep", "-fl"] else "",
            exists=lambda p: p == exists_path)

    def test_a_plain_executable_is_found(self):
        b = self._find("4242 /usr/bin/SheepShaver", "/usr/bin/SheepShaver")
        self.assertEqual(b["app"], "/usr/bin/SheepShaver")
        self.assertEqual(b["source"], "running process")

    def test_an_appimage_is_found(self):
        b = self._find("99 /home/u/Apps/BasiliskII.AppImage",
                       "/home/u/Apps/BasiliskII.AppImage")
        self.assertEqual(b["app"], "/home/u/Apps/BasiliskII.AppImage")

    def test_a_macos_bundle_still_wins_as_the_bundle_not_the_executable(self):
        b = self._find("1 /Applications/BasiliskII.app/Contents/MacOS/BasiliskII",
                       "/Applications/BasiliskII.app")
        self.assertEqual(b["app"], "/Applications/BasiliskII.app")

    def test_a_failed_discovery_names_the_flag_that_fixes_it(self):
        probes = install_bridge.probe(
            run=lambda a: "", read=lambda p: "", exists=lambda p: False,
            addresses=[], prefs_path="/tmp/none", local_env_path="/none")
        text = " ".join(n["message"] for n in install_bridge.decide(probes)["notes"])
        self.assertIn("--emulator-app", text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
