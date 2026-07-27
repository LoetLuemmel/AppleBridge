"""Tests for host/host_config.py — the address is configuration, not a literal.

Two failures on 2026-07-27 motivate every case below, and both were silent in
their own way (R1/R2 in docs/INSTALLER_REQUIREMENTS.md):

  * the host server bound a hardcoded 192.168.3.154 and died elsewhere with
    `Errno 49`, a message naming neither the address nor the interfaces;
  * the guest installer seeded the same address, so on a LAN where it answered
    the daemon connected to the WRONG MACHINE and reported full health.

The second is why the ratchets here are worth more than the unit tests: a
returning literal would not break anything visibly.

Run: python3 tests/test_host_ip_config.py   (or via pytest)
"""

import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "host"))
import host_config  # noqa: E402


def _read(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# --- resolution order --------------------------------------------------------

def test_environment_wins():
    got, src = host_config.resolve_host_ip(env={"APPLEBRIDGE_HOST_IP": "10.1.2.3"},
                                           local_env_path="/nonexistent")
    assert got == "10.1.2.3"
    assert src == "APPLEBRIDGE_HOST_IP"


def test_local_env_is_used_when_the_environment_is_silent(tmp=None):
    path = os.path.join(_ROOT, "tests", "_tmp_local.env")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# a comment\nAPPLEBRIDGE_HOST_IP = \"10.9.9.9\"\n")
    try:
        got, src = host_config.resolve_host_ip(env={}, local_env_path=path)
        assert got == "10.9.9.9", got
        assert src.endswith("local.env")
    finally:
        os.unlink(path)


def test_environment_beats_the_file():
    path = os.path.join(_ROOT, "tests", "_tmp_local2.env")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("APPLEBRIDGE_HOST_IP=10.0.0.1\n")
    try:
        got, _ = host_config.resolve_host_ip(env={"APPLEBRIDGE_HOST_IP": "10.0.0.2"},
                                             local_env_path=path)
        assert got == "10.0.0.2", got
    finally:
        os.unlink(path)


def test_a_fresh_clone_binds_everything_rather_than_guessing():
    """No config anywhere -> 0.0.0.0. Never a derived address.

    Deriving one from the default route would be the same class of mistake the
    seeded default was: a plausible value that binds successfully and then waits
    for a daemon that was told to dial something else.
    """
    got, src = host_config.resolve_host_ip(env={}, local_env_path="/nonexistent")
    assert got == "0.0.0.0"
    assert "default" in src


def test_a_blank_value_counts_as_unset():
    got, _ = host_config.resolve_host_ip(env={"APPLEBRIDGE_HOST_IP": "   "},
                                         local_env_path="/nonexistent")
    assert got == "0.0.0.0"


def test_missing_local_env_is_normal_not_an_error():
    assert host_config.read_local_env("/nonexistent/nope.env") == {}


# --- what the operator is told ----------------------------------------------

_IFCONFIG = """\
lo0: flags=8049<UP,LOOPBACK> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST> mtu 1500
\tinet 192.168.3.240 netmask 0xffffff00 broadcast 192.168.3.255
\tinet 192.168.3.154 netmask 0xffffff00 broadcast 192.168.3.255
en8: flags=8963<UP,BROADCAST,PROMISC> mtu 1500
\tinet6 fe80::1%en8 prefixlen 64
"""


def test_addresses_are_listed_per_interface_without_loopback():
    got = host_config.ipv4_addresses(run=lambda cmd: _IFCONFIG)
    assert got == [("en0", "192.168.3.240"), ("en0", "192.168.3.154")], got


def test_reachability_hint_names_what_the_guest_should_dial():
    hint = host_config.describe_reachability(
        addresses=[("en0", "192.168.3.158")])
    assert "IP=" in hint and "192.168.3.158" in hint and "en0" in hint


def test_bind_failure_names_the_address_and_the_alternatives():
    """`Errno 49` alone cost a round of diagnosis; the explanation must not."""
    text = host_config.explain_bind_failure(
        "192.168.3.154", addresses=[("en0", "192.168.3.158")])
    assert "192.168.3.154" in text, "the address that failed must appear"
    assert "192.168.3.158" in text, "the addresses that exist must appear"
    assert "alias" in text, "the fix (an alias) must be named"
    assert "APPLEBRIDGE_HOST_IP" in text, "where to configure it must be named"


def test_bind_failure_still_says_something_with_no_addresses_at_all():
    text = host_config.explain_bind_failure("10.0.0.1", addresses=[])
    assert "10.0.0.1" in text and "network" in text.lower()


# --- ratchets: the literals must not come back -------------------------------

# host_config.py quotes the removed literal in its own docstring as the reason
# it exists; that is provenance, not configuration.
_IP_RE = re.compile(r"192\.168\.3\.\d+")
_RUNTIME_FILES = [
    "host/host_server.py", "host/bridge_doctor.py", "host/run_server.sh",
    "host/start_stack.sh", "host/ensure_host_alias.sh",
    "host/install_alias_daemon.sh", "host/install_host_service.sh",
    "host/deploy_host.sh",
]


def test_no_host_address_literal_in_the_runtime_files():
    offenders = []
    for rel in _RUNTIME_FILES:
        for n, line in enumerate(_read(rel).split("\n"), 1):
            if _IP_RE.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()[:90]}")
    assert not offenders, (
        "a machine-specific address is back in the runtime files — it belongs in "
        "host/local.env (untracked). See R1:\n  " + "\n  ".join(offenders))


def test_the_guest_ships_no_default_host_address():
    """R2: an unconfigured daemon must say so, not dial a guess."""
    src = _read("mac/src/prefs.c")
    m = re.search(r'#define\s+DEFAULT_HOST_IP\s+"([^"]*)"', src)
    assert m, "DEFAULT_HOST_IP is gone from prefs.c — has the mechanism moved?"
    assert m.group(1) == "", (
        f"prefs.c seeds a host address ({m.group(1)!r}). On a LAN where it "
        "answers, the daemon connects to the wrong machine and reports health.")


def test_the_daemon_refuses_to_dial_without_an_address():
    src = _read("mac/src/main.c")
    assert "gPrefs.ip[0] == '\\0'" in src, \
        "main.c no longer guards the empty-IP case before connecting"
    assert "LogNoHostIPHint" in src, \
        "the empty-IP case must explain itself on the console, not fail silently"


def test_local_env_is_not_tracked():
    assert "host/local.env" in _read(".gitignore"), \
        "host/local.env must stay out of version control — it is one machine's address"
    assert os.path.exists(os.path.join(_ROOT, "host", "local.env.example")), \
        "the example file documents the format and must be shipped"


def test_no_derivative_of_local_env_is_tracked_either():
    """Ask git what is tracked, not .gitignore what it intends to ignore.

    The assertion above passed while two files carrying this machine's
    addresses sat in the repository: `install_bridge.py` keeps a timestamped
    backup beside `local.env` before rewriting it, `.gitignore` named only the
    live file, and a `git add -A` committed the copies (2026-07-27). The
    installer written to stop shipping one machine's addresses shipped them.

    A test that reads the ignore list can only confirm an intention. This one
    checks the outcome.
    """
    import subprocess
    try:
        out = subprocess.run(["git", "ls-files"], cwd=_ROOT, capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):       # no git in this sandbox
        return
    if not out.strip():                                  # not a checkout
        return
    tracked = [f for f in out.split("\n")
               if os.path.basename(f).startswith("local.env")
               and not f.endswith("local.env.example")]
    assert not tracked, ("machine-specific configuration is in the repository: "
                         + ", ".join(tracked))


def test_every_module_host_server_imports_is_deployed():
    """The deployed copy is a separate directory; a missed import breaks it there.

    This already happened once with macbinary.py, which is why deploy_host.sh
    carries an explicit list — a list nothing checked until now.
    """
    imported = set(re.findall(r"^import (\w+)", _read("host/host_server.py"), re.M))
    local = {m for m in imported
             if os.path.exists(os.path.join(_ROOT, "host", m + ".py"))}
    runtime = _read("host/deploy_host.sh")
    missing = sorted(m for m in local if m + ".py" not in runtime)
    assert not missing, ("host_server.py imports modules absent from "
                         "deploy_host.sh's RUNTIME_FILES: " + ", ".join(missing))



# --- the control port: loopback by default, never open without a token -------

def test_control_port_defaults_to_loopback():
    addr, src = host_config.resolve_control_bind(env={}, local_env_path="/nonexistent")
    assert addr == "127.0.0.1"
    assert "default" in src


def test_control_bind_can_be_widened_deliberately():
    addr, src = host_config.resolve_control_bind(
        env={"APPLEBRIDGE_CTRL_BIND": "0.0.0.0"}, local_env_path="/nonexistent")
    assert addr == "0.0.0.0"
    assert src == "APPLEBRIDGE_CTRL_BIND"


def test_loopback_needs_no_token():
    for addr in ("127.0.0.1", "localhost", "::1"):
        assert host_config.check_control_exposure(addr, "") is None, addr


def test_an_exposed_control_port_without_a_token_is_refused():
    """Fail closed. An open, unauthenticated command channel into the guest
    would work perfectly and be noticed by nobody — the failure class this
    project keeps rediscovering. So the combination must not be able to run."""
    why = host_config.check_control_exposure("0.0.0.0", "")
    assert why, "an exposed control port with no token must be refused"
    assert "APPLEBRIDGE_CTRL_TOKEN" in why, "the refusal must name the fix"
    assert "0.0.0.0" in why, "the refusal must name the address that caused it"


def test_an_exposed_control_port_with_a_token_is_allowed():
    assert host_config.check_control_exposure("192.168.3.154", "s3cret") is None


def test_the_server_actually_enforces_the_refusal():
    """The rule is worthless if it lives only in host_config."""
    src = _read("host/host_server.py")
    assert "check_control_exposure" in src, \
        "host_server.py must consult the exposure rule before binding :9001"
    assert "resolve_control_bind" in src, \
        "host_server.py must resolve the control bind rather than hardcode it"
    assert '(("127.0.0.1", CONTROL_PORT))' not in src, \
        "the control bind address must no longer be a literal"


# --- R12/R13: the two diagnostics that pointed at the developer's machine ----

def test_interactive_mode_says_it_has_no_control_port():
    """R12: the mode is chosen by isatty(); saying so is the whole fix.

    A server started in a terminal serves :9000 flawlessly and :9001 not at all,
    and every tool then fails against something that looks healthy.
    """
    src = _read("host/host_server.py")
    assert "APPLEBRIDGE_FORCE_CONTROL" in src, \
        "there must be a way to get the control port from a terminal"
    i = src.find("sys.stdin.isatty()")
    assert i > 0
    window = src[i - 900:i + 900]
    assert "NO control port" in window, \
        "interactive mode must state that the control port is absent"
    assert "/dev/null" in window, \
        "the message must name the redirect that avoids the trap"


def test_the_doctor_distinguishes_absent_from_unloaded():
    """R13: a machine with no launchd agent starts the server by hand.

    Telling its owner to bootstrap a plist that was never installed is a false
    lead with an authoritative tone — worse than saying nothing.
    """
    src = _read("host/bridge_doctor.py")
    assert '"installed"' in src, \
        "the launchd probe must report whether the agent exists at all"
    assert "host_server_not_running" in src, \
        "an absent agent needs its own finding, not the unloaded-job one"
    assert 'installed but not loaded' in src, \
        "the bootstrap advice must be reserved for an agent that is installed"


# --- configuration must actually reach the launchd-served server -------------
# `host/local.env` sits beside the repo; launchd runs a deployed copy and
# deploy_host.sh syncs only the runtime modules. So the file resolved correctly
# for anything started by hand and was INERT for the real server — the developer
# machine's APPLEBRIDGE_HOST_IP had never once reached it, and nothing looked
# wrong because the wildcard fallback accepts the guest anyway (2026-07-27).
# The installer now resolves the values and writes them into the plist.

def test_a_configured_address_is_carried_into_the_agent():
    env = {"APPLEBRIDGE_HOST_IP": "10.9.9.9"}
    assert host_config.launchd_environment(env=env, local_env_path="/nonexistent") \
        == {"APPLEBRIDGE_HOST_IP": "10.9.9.9"}


def test_the_wildcard_default_is_not_written_into_the_plist():
    # It is what the server does with no configuration; stating it in a plist
    # only creates a second thing to keep in step.
    assert host_config.launchd_environment(env={}, local_env_path="/nonexistent") == {}
    assert host_config.launchd_environment_xml(env={}, local_env_path="/nonexistent") == ""


def test_the_control_token_never_travels_in_the_plist():
    # A plist is a world-readable file; a shared secret does not belong in one.
    env = {"APPLEBRIDGE_CTRL_TOKEN": "s3cret", "APPLEBRIDGE_CTRL_BIND": "0.0.0.0"}
    got = host_config.launchd_environment(env=env, local_env_path="/nonexistent")
    assert "APPLEBRIDGE_CTRL_TOKEN" not in got
    assert got.get("APPLEBRIDGE_CTRL_BIND") == "0.0.0.0"
    assert "s3cret" not in host_config.launchd_environment_xml(
        env=env, local_env_path="/nonexistent")


def test_a_loopback_control_bind_is_not_worth_carrying():
    env = {"APPLEBRIDGE_CTRL_BIND": "127.0.0.1"}
    assert host_config.launchd_environment(env=env, local_env_path="/nonexistent") == {}


def test_the_emitted_block_is_a_plist_dict_with_escaped_values():
    xml = host_config.launchd_environment_xml(
        env={"APPLEBRIDGE_HOST_IP": "10.9.9.9"}, local_env_path="/nonexistent")
    assert "<key>EnvironmentVariables</key>" in xml
    assert "<key>APPLEBRIDGE_HOST_IP</key>" in xml
    assert "<string>10.9.9.9</string>" in xml
    assert host_config._xml('a&b<c>"d"') == "a&amp;b&lt;c&gt;&quot;d&quot;"


def test_the_installer_writes_that_block_and_says_to_re_run_it():
    src = _read("host/install_host_service.sh")
    assert "launchd_environment_xml" in src, \
        "the plist must take its values from the one place that resolves them"
    assert "$ENV_BLOCK" in src
    assert "Re-run this installer" in src, \
        "deploy_host.sh syncs code, not configuration — say so where it matters"


def test_the_service_installer_names_no_single_machines_interface():
    # R13: the closing hint used to read ".154 must be aliased on en0".
    src = _read("host/install_host_service.sh")
    for literal in ("192.168.3.154", "(en0)"):
        assert literal not in src, f"machine-specific literal in the hints: {literal}"


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
