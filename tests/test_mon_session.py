"""Tests for host/mon_session.py - the host-side driver of cxmon in Basilisk II.

The real target (a mon-enabled emulator) is stood in for by
tests/fixtures/fake_mon.py, which reproduces cxmon's prompt, flush and SIGINT
behaviour. The properties that matter: a dead emulator never blocks a write,
host-reaching monitor commands are refused, a second emulator is never
started, and a break point hit is reported with its address.

Run: python3 tests/test_mon_session.py   (or via pytest)
"""
import os
import shutil
import signal
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "host"))

import mon_session as ms  # noqa: E402

FAKE = os.path.join(HERE, "fixtures", "fake_mon.py")


def _session(emulators=lambda: []):
    root = tempfile.mkdtemp(prefix="monsess")
    disk = os.path.join(root, "src.dmg")
    open(disk, "wb").write(b"\0" * 1024)
    s = ms.Session(root=os.path.join(root, "s"), app=root, check_binary=False,
                   launcher=lambda: [sys.executable, "-u", FAKE],
                   watchers=lambda: [], cloner=shutil.copyfile, emulators=emulators)
    return s, disk, root


def _expect_error(fn, fragment):
    try:
        fn()
    except ms.MonError as e:
        assert fragment in str(e), str(e)
        return
    raise AssertionError(f"expected MonError containing {fragment!r}")


def test_trap_table_addresses():
    assert ms.trap_table_address(0xA860) == 0xF80        # WaitNextEvent, toolbox
    assert ms.trap_table_address(0xA9C8) == 0x1520       # SysBeep
    assert ms.trap_table_address(0xA11E) == 0x478        # NewPtr, OS trap (flag bits ignored)
    assert ms.trap_table_address(0xA51E) == 0x478        # NewPtr,SYS
    _expect_error(lambda: ms.trap_table_address(0x4E75), "not an A-line")


def test_resolve_trap_forms():
    assert ms.resolve_trap("WaitNextEvent")[1] == 0xA860
    assert ms.resolve_trap("_WaitNextEvent")[1] == 0xA860
    assert ms.resolve_trap("waitnextevent") == ("WaitNextEvent", 0xA860)
    assert ms.resolve_trap("0xA860")[1] == 0xA860
    assert ms.resolve_trap("$a9c8")[1] == 0xA9C8
    _expect_error(lambda: ms.resolve_trap("WaitNext"), "similar: WaitNextEvent")


def test_trap_table_is_complete_enough():
    t = ms.load_traps()
    assert len(t) > 700
    for name in ("SysBeep", "NewPtr", "GetResource", "DrawString", "InitGraf"):
        assert name in t, name


def test_start_refuses_a_second_emulator():
    s, disk, root = _session(emulators=lambda: [4242])
    try:
        _expect_error(lambda: s.start(disk), "Never two at once")
        assert not os.path.exists(os.path.join(root, "s", "state.json"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_prefs_drop_disk_ether_jit():
    root = tempfile.mkdtemp()
    try:
        base = os.path.join(root, "base")
        open(base, "w").write("disk /a.dmg\nether etherhelper/en8\njit true\nrom /r\nscreen win/1024/768\n")
        out = os.path.join(root, "p")
        ms.Session._write_prefs(out, "/clone.dmg", base=base)
        lines = open(out).read().splitlines()
        assert "rom /r" in lines and "screen win/1024/768" in lines
        assert [l for l in lines if l.startswith("disk")] == ["disk /clone.dmg"]
        assert "ether slirp" in lines and "jit false" in lines and "jit true" not in lines
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_full_cycle_break_trap_continue_hit():
    s, disk, root = _session()
    try:
        st = s.start(disk)
        assert os.path.exists(st["clone"]) and st["mode"] == "running"
        time.sleep(0.3)
        _expect_error(lambda: s.command("regs"), "guest is running")
        r = s.break_in()
        assert r["mode"] == "stopped" and "next PC" in r["output"]
        assert "DKIOCCDREADTOC" not in r["output"] and "->" not in r["output"]
        assert r["output"].startswith("D0: "), r["output"]          # no start-up banner
        r = s.break_on_trap("WaitNextEvent")
        assert r["table_entry"] == "$F80" and r["break_address"] == "$2E890"
        s.resume()
        r = s.wait(timeout=5)
        assert r["hit"] and r["address"] == "$2E890" and r["last_pc"] == "$7C099D6"
        assert s.command("br 0")["output"] == "Removed all break points!"
        s.resume()
        assert s.status()["mode"] == "running"
        r = s.wait(timeout=0.5)
        assert not r["hit"]
    finally:
        r = s.stop()
        shutil.rmtree(root, ignore_errors=True)
    assert r["stopped"] and r["hard_killed_clone"]        # the fake ignores SIGTERM, like Basilisk


def test_host_commands_and_x_are_refused():
    s, disk, root = _session()
    try:
        s.start(disk)
        time.sleep(0.3)
        s.break_in()
        for cmd in ("\\ \"rm -rf /\"", "rm foo", "ls", "cd /", "o \"/tmp/x\"", "] 0 10 \"f\"", "bs \"f\""):
            _expect_error(lambda c=cmd: s.command(c), "HOST")
        _expect_error(lambda: s.command("x"), "continue")
        _expect_error(lambda: s.command("regs\nrm foo"), "one monitor command")
    finally:
        s.stop()
        shutil.rmtree(root, ignore_errors=True)


def test_dead_emulator_never_blocks_a_write():
    s, disk, root = _session()
    try:
        st = s.start(disk)
        time.sleep(0.3)
        s.break_in()
        os.kill(st["emu_pid"], signal.SIGKILL)
        os.kill(st["holder_pid"], signal.SIGKILL)
        time.sleep(0.3)
        t0 = time.monotonic()
        _expect_error(lambda: s.command("regs"), "no longer running")
        _expect_error(lambda: s._send("regs"), "not reading")
        assert time.monotonic() - t0 < 2
        assert s.status()["mode"] == "dead"
    finally:
        s.stop()
        shutil.rmtree(root, ignore_errors=True)


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
