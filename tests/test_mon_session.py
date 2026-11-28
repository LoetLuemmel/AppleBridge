"""Tests for host/mon_session.py - the host-side driver of cxmon in Basilisk II.

The real target (a mon-enabled emulator) is stood in for by
tests/fixtures/fake_mon.py, which reproduces cxmon's prompt, flush and SIGINT
behaviour. The properties that matter: a dead emulator never blocks a write,
host-reaching monitor commands are refused, a second emulator is never
started, and a break point hit is reported with its address.

Run: python3 tests/test_mon_session.py   (or via pytest)
"""
import json
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


def _session(emulators=lambda: [], image=None):
    root = tempfile.mkdtemp(prefix="monsess")
    disk = os.path.join(root, "src.dmg")
    open(disk, "wb").write(b"\0" * 1024)
    if image is not None:
        path = os.path.join(root, "image.json")
        json.dump(image, open(path, "w"))
        os.environ["FAKE_MON_IMAGE"] = path
    else:
        os.environ.pop("FAKE_MON_IMAGE", None)
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


# Two routines as Retro68 emits them (bytes from a real build): ...RTS, $87 "IsPrime",
# constant-length word; then LINK A6 ... RTS, $88 "DrawBars", pad, word.
ISPRIME_DRAWBARS = bytes.fromhex(
    "4e5e4e75" "87" + b"IsPrime".hex() + "0000"
    "4e56fffc" "7001" "6000fffc" "4e5e4e75" "88" + b"DrawBars".hex() + "00" "0000"
    "4e56fffc")


def test_macsbug_names_and_routine_bounds():
    base = 0x7A41000
    names = ms.macsbug_names(ISPRIME_DRAWBARS, base)
    assert [n[2] for n in names] == ["IsPrime", "DrawBars"]
    draw_start = names[0][1]
    assert ISPRIME_DRAWBARS[draw_start - base:draw_start - base + 2] == b"\x4e\x56"   # LINK A6
    name, start, off = ms.name_for(draw_start + 6, ISPRIME_DRAWBARS, base)
    assert (name, start, off) == ("DrawBars", draw_start, 6)
    assert ms.name_for(base + 2, ISPRIME_DRAWBARS, base)[0] == "IsPrime"
    assert ms.name_for(names[1][1] + 2, ISPRIME_DRAWBARS, base) is None   # past the last name


def test_macsbug_names_ignore_lookalikes():
    junk = bytes.fromhex("4e75" "85" "41422d4344" "0000" "4e75" "20" "414243")   # '-' and no high bit
    assert ms.macsbug_names(junk, 0) == []


def test_where_names_pc_and_walks_the_a6_chain():
    code = 0x7A41000
    names = ms.macsbug_names(ISPRIME_DRAWBARS, code)
    pc = names[0][1] + 6                                  # inside DrawBars
    ret_in_isprime = code + 2                             # a return address inside IsPrime
    image = {"regs": {"PC": f"{pc:x}", "A6": "7b43a60"},
             "mem": {f"{code:x}": ISPRIME_DRAWBARS.hex(),
                     "7b43a60": "07b43a80" + f"{ret_in_isprime:08x}",   # frame 1 -> frame 2
                     "7b43a80": "00000000" + "00000000",                # end of chain
                     "2ae": "07c00000",                                  # ROMBase, right after RAM
                     "910": "09" + b"PrimeBars".hex()}}
    s, disk, root = _session(image=image)
    try:
        s.start(disk)
        time.sleep(0.3)
        s.break_in()
        w = s.where()
        assert w["cur_ap_name"] == "PrimeBars" and w["rom_base"] == "$7C00000"
        assert w["frames"][0]["frame"] == "PC" and w["frames"][0]["routine"] == "DrawBars"
        assert w["frames"][0]["offset"] == "+$6"
        assert w["frames"][1]["routine"] == "IsPrime" and w["frames"][1]["kind"] == "return address"
        assert len(w["frames"]) == 2                     # stopped at the zero frame
    finally:
        s.stop()
        shutil.rmtree(root, ignore_errors=True)


def test_where_refuses_while_running():
    s, disk, root = _session()
    try:
        s.start(disk)
        _expect_error(s.where, "break in first")
    finally:
        s.stop()
        shutil.rmtree(root, ignore_errors=True)


class _FakeHFS:
    """hfsutils on a dict: enough for _install_startup (hmount/hls -1/hmkdir/hrename/hcopy/humount)."""

    def __init__(self, items):
        self.dirs = {":System Folder:Startup Items:": list(items)}
        self.calls = []

    def __call__(self, args, capture_output=True, text=True):
        self.calls.append(args)
        cmd, ok, out = args[0], 0, ""
        if cmd == "hls":
            path = args[-1]
            if path in self.dirs:
                out = "".join(n + "\n" for n in self.dirs[path])
            else:
                ok = 1
        elif cmd == "hmkdir":
            self.dirs[args[1]] = []
        elif cmd == "hrename":
            src, dst = args[1], args[2]
            folder, name = src.rsplit(":", 1)[0] + ":", src.rsplit(":", 1)[1]
            dfolder, dname = dst.rsplit(":", 1)[0] + ":", dst.rsplit(":", 1)[1] or name
            if dname.lower() in (n.lower() for n in self.dirs[dfolder]):
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": "File exists"})()
            self.dirs[folder].remove(name)
            self.dirs[dfolder].append(dname)
        elif cmd == "hcopy":
            self.dirs[args[-1]].append(os.path.basename(args[2]).rsplit(".", 1)[0])
        return type("R", (), {"returncode": ok, "stdout": out, "stderr": ""})()


def test_install_isolates_other_startup_items_with_spaces_in_names():
    fake = _FakeHFS(["AppleBridge Watchdog", "ToolServer alias"])
    real = ms.subprocess.run
    app = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
    app.close()
    try:
        ms.subprocess.run = fake
        r = ms.Session._install_startup("/clone.dmg", app.name)
    finally:
        ms.subprocess.run = real
        os.remove(app.name)
    assert r["moved_aside"] == ["AppleBridge Watchdog", "ToolServer alias"]
    assert r["startup_items"] == [os.path.basename(app.name)[:-4]]
    assert fake.dirs[":System Folder:Startup Items (mac_debug):"] == ["AppleBridge Watchdog", "ToolServer alias"]
    assert ["hls", "-1", ":System Folder:Startup Items:"] in fake.calls
    assert fake.calls[-1][0] == "humount"


def test_install_does_not_collide_with_an_existing_item_aside():
    fake = _FakeHFS(["AppleBridge Watchdog"])
    fake.dirs[":System Folder:Startup Items (mac_debug):"] = ["appleBridge watchdog"]   # case-insensitive clash
    real = ms.subprocess.run
    app = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
    app.close()
    try:
        ms.subprocess.run = fake
        r = ms.Session._install_startup("/clone.dmg", app.name)
    finally:
        ms.subprocess.run = real
        os.remove(app.name)
    assert r["moved_aside"] == ["AppleBridge Watchdog"]
    assert "AppleBridge Watchdog 2" in fake.dirs[":System Folder:Startup Items (mac_debug):"]


def test_install_can_keep_the_other_items():
    fake = _FakeHFS(["AppleBridge Watchdog"])
    real = ms.subprocess.run
    app = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
    app.close()
    try:
        ms.subprocess.run = fake
        r = ms.Session._install_startup("/clone.dmg", app.name, isolate=False)
    finally:
        ms.subprocess.run = real
        os.remove(app.name)
    assert r["moved_aside"] == [] and "AppleBridge Watchdog" in r["startup_items"]


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
