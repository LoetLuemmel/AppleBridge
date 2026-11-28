# Machine-level debugging: `mac_debug` and cxmon

`mac_debug` stops the emulated 68K at a break point or on a crash, and shows
registers, memory, disassembly and a named backtrace. The debugger runs
**inside the emulator**, not inside the guest. Why that is the only workable
place is recorded in [D-030](../DECISIONS.md). The accompanying measurements
are in the article [*Debugging Classic Mac Software from Outside the
Guest*](https://td5.390er.de/applebridge/debugging-classic-mac-software-from-outside-the-guest-cxmon/).

Code: `host/mon_session.py` (session and safety rules) and the MCP tool
`mac_debug` in `mcp/tools.py`. Tests: `tests/test_mon_session.py`, which
runs against a fake monitor, `tests/fixtures/fake_mon.py`.

## What it needs

- **A Basilisk II with cxmon compiled in.** This is the macemu fork, branch
  `cxmon` (https://github.com/LoetLuemmel/macemu, based on `fb-export`). The
  branch adds three things:
  - `ENABLE_MON` in the macOS Xcode build;
  - `BASILISK_MON_CONTINUE=1`, so that leaving the monitor after Ctrl-C
    resumes the guest instead of quitting;
  - a cxmon fix: `exit_abort()` reset SIGINT to the default action, so the
    second Ctrl-C killed the emulator (measured 2026-11-28).
- **An x86_64 host.** The arm64 CPU core (`uae_cpu_2021`) has no break-point
  hooks.
- **hfsutils**, for `start` with `app`.

### Building the debug emulator

```sh
git clone -b cxmon git@github.com:LoetLuemmel/macemu.git macemu-mon
cd macemu-mon && ./build-cxmon.sh            # cxmon -> build-cxmon/libcxmon.a (x86_64)
cd BasiliskII/src/MacOSX
xcodebuild -project BasiliskII.xcodeproj -target BasiliskII -configuration Release \
  -arch x86_64 ONLY_ACTIVE_ARCH=YES SYMROOT="$PWD/../../../build-mon"
```

Build into a separate `SYMROOT`, as above, never over the emulator the guest
normally runs. Then tell AppleBridge where the debug build is, in
`host/local.env`:

```
APPLEBRIDGE_MON_APP=/path/to/macemu-mon/build-mon/Release/BasiliskII.app
```

`start` refuses a binary without cxmon: it looks for a cxmon string in the
executable.

## How a session runs

- **Never two emulators.** `start` refuses while any `BasiliskII` runs. Shut
  the guest down cleanly first (`mac_shutdown`).
- **A disk copy, never the image itself.**
  - The session boots an APFS clone (`cp -c`) of the boot disk named in
    `~/.basilisk_ii_prefs`, under `~/.applebridge/mon/`.
  - The emulator ignores SIGTERM, so `stop` ends it with SIGKILL. That is
    harmless only because it is a copy. `stop` then deletes the copy.
- **Watchdogs paused.**
  - The local `hang_watch` and `dialog_watch` find the emulator by name, so a
    guest halted in the monitor would look like a hung production guest to
    them. `start` boots them out and `stop` restores them.
  - If a session dies without `stop`, they stay paused until the next `stop`.
    `status` lists them.
- **Prefs.** The session's prefs are the operator's, with four changes:
  - `jit false`: break points are only tested on the interpreter's path.
  - `ignoresegv false`: with `true`, Basilisk tries to skip a faulting write;
    when the skip fails it gives up without calling its state dumper, and the
    process dies before the monitor is reached (measured 2026-11-28).
  - `ether slirp`, unless bridge mode is used.
  - only the disk copy as disk.
- **The program under test.** `start` with `app` (a MacBinary on the host)
  puts it into the copy's Startup Items. All other Startup Items are moved
  aside first. Left in place, the AppleBridge watchdog starts the daemon and
  ToolServer, which pushes the program into the background and makes it look
  hung when it is not (measured 2026-11-28).

## Bridge mode

With `bridge: true` the session keeps the guest's own network, so its daemon
connects, and `mac_type`, `launch_app`, `mac_send_apple_event` and
`mac_put_file` work inside the session. That is how a THINK C program can be
built and run in the copy.

For an `etherhelper` guest, the debug bundle's helper must be a setuid-root
one. The fork's runtool patch then starts it without the password dialog.
The simplest way is a link to the production bundle's helper:

```sh
R=/path/to/macemu-mon/build-mon/Release/BasiliskII.app/Contents/Resources
mv "$R/etherhelpertool" "$R/etherhelpertool.unprivileged"
ln -s "/path/to/production/BasiliskII.app/Contents/Resources/etherhelpertool" "$R/etherhelpertool"
```

Every Xcode build replaces the link with an unprivileged copy. `start` with
`bridge` checks for this and refuses with a pointer here. With a real network,
the AppleShare login appears at boot; `host/guest_boot.py` answers it with a
few seconds of host keystrokes.

## Actions

| Action | Does |
|---|---|
| `start [disk] [app] [bridge] [keep_items]` | boots the copy; about 60 s to the Finder |
| `break` | SIGINT: stops the 68K now and returns the registers |
| `cmd` | one cxmon command while stopped: `regs`, `m $a $b`, `i $a $b`, `d68 $a $b`, `ba`/`bi`/`br` |
| `break_trap` | a break point where the dispatcher jumps for a trap (`WaitNextEvent`, `0xA9C8`) |
| `continue` / `wait` | resume; block until a break point hits or the guest crashes |
| `where` | routine at the PC and a backtrace along the A6 chain |
| `status` / `stop` | |

### Reading `where`

- **With MacsBug names** (Retro68 by default; MPW and THINK C only with the
  option): `ResetSlot+$20 ← Step+$20 ← main+$FC`.
- **Without names:** the routine's start (the nearest `LINK A6` below the
  address) and the offset into it.
- **The jump-table slot:** when the routine is called through the jump table,
  as THINK C does, `where` also gives the slot that every call site uses
  (`JSR $0C3A(A5)`) and the segment number.
- **Retro68 programs** have a single jump-table entry (their calls are
  PC-relative), so the slot never appears for them.

### A crash

A wild write enters the monitor in mode `crashed`.
- `where` and `cmd` still work there. `continue` is refused, because leaving
  the monitor quits the emulator.
- Monitor reads outside guest RAM and ROM are refused. cxmon reads straight
  through to the host, so dumping the wild address itself faults inside the
  monitor and kills the emulator (measured 2026-11-28).

## Limits

- No source-level names unless the binary carries MacsBug names.
- The trap table is read live. Its entries move between boots
  (`_WaitNextEvent` was at $2E890 in one boot and $2E728 in the next).
- One session on this Mac. A guest on another machine has no debug session.
- While the monitor holds the 68K, the AppleBridge daemon is stopped too, and
  the bridge drops after its heartbeat timeout. The monitor is driven from the
  host side, so the debugging itself is unaffected.
