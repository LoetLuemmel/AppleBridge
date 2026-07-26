# AppleBridge Troubleshooting

This document contains solutions to common issues, historical fixes, and known limitations.

## Start here: `bridge_doctor`

Before reading any section below, run the cross-layer diagnosis — it probes the
whole stack in one call and names the layer that is actually broken, with a
literal fix command:

```bash
/usr/bin/python3 host/bridge_doctor.py        # or the bridge_doctor MCP tool
printf 'DOCTOR\n\n' | nc localhost 9001       # same report via the control port
```

It answers even when the host server is down, and it catches the four causes
that otherwise all present identically as "Mac not connected": a `disable`d
launchd job, a `.154` alias duplicated onto a second NIC, a `slirp` emulator
backend (TCP works, AppleTalk does not — see *Chooser finds no AppleShare
server* below), and a dead `etherhelpertool`.

## Table of Contents

- [Apple Events Issues](#apple-events-issues)
- [Network and Connection](#network-and-connection)
- [Emulator (Basilisk II) Crashes](#emulator-basilisk-ii-crashes)
- [Compilation and Linking](#compilation-and-linking)
- [File System and Encoding](#file-system-and-encoding)
- [ToolServer vs MPW Shell](#toolserver-vs-mpw-shell)
- [Synthetic Input](#synthetic-input)
- [Known Limitations](#known-limitations)

---

## Apple Events Issues

### Error -903 (noPortErr) - FIXED ✅

The -903 error when sending Apple Events was caused by two issues:

#### 1. Missing SIZE Resource Flags

The AppleBridge Mac app needs to declare it can receive Apple Events.

**Fix:** Create `AppleBridge.r` with proper SIZE resource:

```c
#include "Types.r"

resource 'SIZE' (-1) {
    reserved,
    acceptSuspendResumeEvents,
    reserved,
    canBackground,
    doesActivateOnFGSwitch,
    backgroundAndForeground,
    dontGetFrontClicks,
    ignoreAppDiedEvents,
    isHighLevelEventAware,      /* REQUIRED for Apple Events! */
    localAndRemoteHLEvents,     /* Accept events from other apps */
    isStationeryAware,
    useTextEditServices,
    reserved,
    reserved,
    reserved,
    reserved,
    512 * 1024,    /* preferred size */
    256 * 1024     /* minimum size */
};
```

Add to application after linking:
```
Rez AppleBridge.r -a -o :bin:AppleBridge
```

#### 2. Wrong Event Loop Function

Must use `WaitNextEvent` (not `GetNextEvent`) and explicitly handle high-level events:

**Wrong:**
```c
GetNextEvent(everyEvent, &event);
```

**Correct:**
```c
if (WaitNextEvent(everyEvent, &event, 1, NULL)) {
    if (event.what == kHighLevelEvent) {
        AEProcessAppleEvent(&event);
    }
    // ... handle other events
}
```

**Why it matters:** `WaitNextEvent` is required for System 7+ event handling. `GetNextEvent` doesn't properly dispatch high-level events (Apple Events).

---

## Network and Connection

### Mac Can't Connect to Host

**Symptoms:**
- AppleBridge shows "Connecting to host..." indefinitely
- No connection established

**Start with `mac_status`** — it is answered host-side and always replies, whether or not
the daemon is up. `host_server_running: false` means the host layer; `host_server_running:
true` with `daemon_connected: false` means the guest layer, and the first thing to rule out
there is a dead `etherhelpertool` (see
[Guest has no network at all](#guest-has-no-network-at-all--etherhelpertool-died-adapter-unplugged)
below) — the host can be configured perfectly and still see nothing.

**Checklist:**

1. **Verify OpenTransport is installed**
   - System 7.6.1 needs OpenTransport 1.3 or later
   - Check Extensions folder for "Open Transport" and "Open Tpt Internet"

2. **Check network configuration**
   ```
   TCP/IP Control Panel → Configure → Using DHCP Server (or Manual)
   IP Address: 192.168.x.x
   Subnet mask: 255.255.255.0
   Router: 192.168.x.1
   ```

3. **Verify the bridge direction**
   - The guest is behind MACNAT and is **never pingable** — don't diagnose with ping.
     Watch the *outbound* connection instead: `/tmp/applebridge_server.log` should show
     `Mac connected from …`.
   - Confirm the emulator backend is `ether etherhelper/en8` (not slirp) and that the
     host's `.154` alias is on the default-route interface (`start_stack.sh` does this).

4. **Check the host firewall**
   ```bash
   # The host server runs under system Python; allow it to accept on :9000 if prompted.
   sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /usr/bin/python3
   ```
   Note: with the firewall in stealth mode a closed port sends no RST, so "server down"
   and "wrong NIC" both look like a timeout — diagnose via the log, not a refused connection.

5. **Verify the host IP in prefs**
   The daemon reads `IP=192.168.3.154` from the `AppleBridge Prefs` file (edit it in place
   or via AppleBridgeConfig). A compiled-in fallback (`.154`) applies if the file is missing.

### Guest has no network at all — `etherhelpertool` died (adapter unplugged)

**Symptoms:**
- Daemon console loops `Opening TCP endpoint… / Connecting to host… / connect timeout -
  no reply from host`, then `reconnecting in 30s`. Counters stay at `RX 0 TX 0`.
- The host side checks out completely: server listening on `:9000`, `.154` aliased on the
  default-route interface, firewall allowing the Python binary.
- **Re-plugging the network cable does not fix it**, and neither does restarting the host
  server or rebooting the guest.

**Cause:** Basilisk II runs `etherhelpertool` as a child process which owns the host
interface directly through a BPF handle (prefs: `ether etherhelper/en8`). If that adapter
is unplugged or re-enumerated — easy to do with Thunderbolt/USB Ethernet — the helper
**dies**. Basilisk II keeps running normally with **no network interface whatsoever**, so
the daemon dials into a void. The helper is spawned *only* at emulator launch, so it never
returns on its own.

This presents identically to a misconfigured host (same timeout, same silence), which is
what makes it expensive to diagnose. The host cannot see it at all.

**Diagnosis** — on the host; all three should agree:

```bash
pgrep -fl etherhelpertool      # nothing, while BasiliskII is alive => the helper is dead
ifconfig en8 | head -1         # PROMISC flag GONE (a working helper sets it)
netstat -ibn -I en8 | tail -1  # Ipkts frozen; sample twice ~30 s apart
```

The packet counters are the clincher: a *promiscuous* port on a live segment always picks
up broadcast traffic (ARP, mDNS). Compare against the default-route interface over the same
interval — during the 2026-07-24 outage `en8` moved **0 packets in either direction in 30 s**
while `en0` moved 2,836.

**Fix:** quit Basilisk II **fully** and relaunch. Shut the guest down cleanly first
(`mac_shutdown`, or Special → Shut Down) — never hard-kill the emulator, which risks an
unclean HFS unmount and a corrupted disk image. A guest-only reboot is **not** sufficient,
because the helper is only spawned when the emulator launches.

**Note:** `mac_status` will report `host_server_running: true` with
`daemon_connected: false`, which correctly narrows the fault to the guest side but cannot
see the missing NIC itself. Check the three commands above before touching host config.

### Daemon hangs on "CONNECTING" and freezes the emulator (100% CPU)

**Symptoms:**
- Daemon window stuck on "Active — CONNECTING"; never reaches CONNECTED
- Basilisk II pegs the host CPU at ~100% (fan spins up)
- You can't switch back to the Finder — the whole emulated Mac is frozen
- Looks like a crash, but there is **no crash report** (the host process is alive, spinning)

**Cause:** a host network-interface mismatch — **not** a crash, **not** the daemon code.
The emulated Mac sits behind Basilisk's MACNAT (`ether etherhelper/en8`), so it can only
connect *out*, and its traffic is NAT'd through the host's **default-route interface**
(normally Wi-Fi, `en0`). The daemon dials the hardcoded host IP `.154`. If `.154` is
aliased on a *different* interface than the default route (e.g. a wired `en8`), the
conversation is split across NICs: the MACNAT return packet is swallowed by the host's
own stack, the handshake never completes, and the daemon's **synchronous `OTConnect`**
blocks without yielding → starves System 7's cooperative scheduler → total freeze.

**Fix:** put `.154` on the **same interface as the default route**.
```bash
# detect the default-route interface (where MACNAT exits)
DEF=$(route -n get default | awk '/interface:/{print $2}')   # usually en0 (Wi-Fi)
sudo ifconfig en8 -alias 192.168.3.154 2>/dev/null            # strip stale .154 off other NIC
sudo ifconfig "$DEF" inet 192.168.3.154 netmask 255.255.255.0 alias
```
`host/start_stack.sh` does exactly this. **Tell it's fixed:** the server log shows the
guest's *real* address — `Mac connected from ('192.168.3.244', …)` ESTABLISHED — instead
of the host's own Wi-Fi IP (`.213`), which is what you see when the path is cross-interface.

**Do NOT pre-create a bridge.** `etherhelpertool` owns `en8` directly; a manually created
`bridge100` containing `en8` collides with it and SIGSEGVs the helper
(`etherhelpertool: fret == -10`) *before any screen appears*. Tear down any stale bridge
and let the etherhelper own the interface.

**Red herrings** (don't waste time here): firewall *stealth mode* (irrelevant);
*pinging the guest* (`.244` is behind MACNAT — never pingable, by design); host SIGILL
crash reports in window code (an unrelated macOS Sequoia + SDL2 GUI bug).

Full write-up: <https://pit.390er.de/applebridge/anatomy-of-a-freeze-macnat-return-path/>

### Connection Drops / Daemon Stops Responding

**Symptoms:**
- Initially connected, then stops responding
- No RX/TX LED activity

**Solutions:**

1. **Check ToolServer is still running**
   - ToolServer can crash during complex operations
   - Restart ToolServer and try again

2. **Check AppleBridge window**
   - Look for error messages in status window
   - RX/TX LEDs show activity (v0.3.0+)
   - Green flash = command received
   - Red flash = response sent

3. **Restart both sides**
   ```
   Mac: Quit AppleBridge, restart ToolServer, relaunch AppleBridge
   Host: host_server.py re-accepts the daemon automatically
   ```

### Chooser finds no AppleShare server (but the bridge works fine)

**Symptom:** the guest's Chooser shows the AppleShare icon with an empty server
list — while `Echo HELLO` over the bridge returns `STATUS:0` and TCP/IP in the
guest reports an address like `10.0.2.15`.

**Cause:** the emulator is running the **slirp** Ethernet backend
(`ether slirp` in `~/.basilisk_ii_prefs`). slirp is a user-mode **IP-only** NAT:
it forwards TCP/UDP over IPv4 and silently drops everything else — including
AppleTalk/EtherTalk frames, which is what the System 7 Chooser uses to find
file servers. So the bridge (plain TCP) keeps working and only AppleTalk dies,
which makes this look like a broken AppleShare extension rather than a network
setting. A second tell: `pgrep -fl etherhelpertool` is empty, because on slirp
no helper is spawned.

**Fix:** switch back to the wired backend and relaunch the emulator.

```bash
sed -i '' 's|^ether slirp$|ether etherhelper/en8|' ~/.basilisk_ii_prefs
host/start_stack.sh          # also puts .154 back on the default-route interface
```

`bridge_doctor` reports this as the `ether_slirp` finding, and flags any drift
from the backend recorded in `~/.basilisk_ii_prefs.netmode`.

---

## Emulator (Basilisk II) Crashes

### BasiliskII crashes (SIGILL in `NSWMWindowCoordinator`) — usually a broken guest app

**Symptoms:**
- Basilisk II quits *completely* (the whole emulator window vanishes), typically
  right after **launching a particular guest app**.
- A macOS crash report appears in `~/Library/Logs/DiagnosticReports/BasiliskII-*.ips`
  with `EXC_BAD_INSTRUCTION (SIGILL)` and a faulting thread topped by
  `-[NSWMWindowCoordinator performTransactionUsingBlock:]`.

**The SIGILL is a red herring — it's a *secondary* crash during shutdown.** The
window-management frame at the top of the faulting thread is *not* where things went
wrong. Read the faulting thread **bottom-to-top** and the real sequence appears:

```
catch_exception_raise → sigsegv_dump_state(...)   ← BAII CAUGHT A FATAL SIGSEGV
  → QuitEmulator() → exit() → __cxa_finalize_ranges  ← orderly shutdown begins
    → SDL2 atexit window teardown → -[NSWindow _doOrderWindowOut...]
      → -[NSWMWindowCoordinator performTransactionUsingBlock:]  ← SIGILL (logged)
```

So the **primary** fault is a **SIGSEGV during 68k execution**: look at **thread 0**
(`com.apple.main-thread`) — it's the CPU, frozen mid-instruction on a `MOVE`
opcode (`op_1158` = `MOVE.B`, `op_20f8` = `MOVE.L`, …) dereferencing an address
BAII can't map. BAII's exception handler deems it fatal and calls `QuitEmulator()`;
during the clean `exit()`, **SDL2 (2.30.11) tripping the macOS Sequoia
`WindowManagement` framework on its way out** is the *secondary* SIGILL that the log
records. The window frame masks the real cause.

**Verified root cause (controlled test, 2026-06-26):** stock GUI apps are fine — a
known-good app (`SimpleText`) launched **4×** with zero crashes. A purpose-built
app (the **assembly** `CounterAsm`) crashed BAII **on launch**, every time, with
this exact signature. The CPU was executing through an uninitialised global → a
wild-pointer `MOVE` → SIGSEGV → quit → SIGILL-on-exit.

`DumpFile -h` pins the defect — the asm binary was linked **without the MPW
runtime startup**:

| | working C app `Counter` | crashing asm `CounterAsm` |
|---|---|---|
| Resource fork | **6904 B** | **719 B** |
| Runtime in `CODE` | `32-bit startup`, `_DataInit`, `%A5Init`, jump-table loader, `_RTInit` | **none** — raw traps + `Main` only |

With no `_DataInit` / `%A5Init`, the **A5 world (globals, QuickDraw globals, jump
table) is never set up**, so the first A5-relative access dereferences garbage.
The C runtime links that bootstrap in automatically; the assembly link omitted it.
The fault is the **assembly *link*, not `Asm`** — and certainly not macOS/SDL/BAII.

**Diagnose correctly — read thread 0 and the shutdown chain, not just frame 0:**
```bash
f=$(ls -t ~/Library/Logs/DiagnosticReports/BasiliskII-*.ips | head -1)
python3 -c "
import json
d=json.loads(open('$f').read().split(chr(10),1)[1])
ft=d['faultingThread']
syms=[(x.get('symbol') or '') for x in d['threads'][ft]['frames']]
chain=all(any(s in y for y in syms) for s in ('sigsegv_dump_state','QuitEmulator','exit'))
print('shutdown-chain (caught SIGSEGV, then SIGILL-on-exit):', chain)
print('CPU was here (thread 0):', (d['threads'][0]['frames'][0].get('symbol') or '?'))
"
# shutdown-chain True + a MOVE op on thread 0 = a guest/app SIGSEGV, NOT a host window bug.
```

**Fix the actual cause (in priority order):**
1. **Fix the guest binary.** This is the real bug almost every time. If it's an
   AppleBridge-built app, suspect the link. `DumpFile app -h` and compare against a
   *working* app of the same kind: the broken one is far smaller and **missing the
   MPW runtime bootstrap** (`32-bit startup`, `_DataInit`, `%A5Init`, jump-table
   loader). For assembly apps this is the usual culprit — link in / set up the A5
   world (`_DataInit`/`%A5Init`) or keep all globals PC-relative. Confirm the
   emulator itself is healthy by launching a *stock* app (`SimpleText`) — if that's
   stable, the fault is your binary, not BAII.
2. **Make the exit graceful (host-side, optional).** The SIGILL-on-exit is a
   Sequoia + SDL2 2.30.11 interaction. We run the latest BasiliskII build
   (Jan 2025, SDL2 **2.30.11**); rebuilding it against **SDL2 2.32.x** may turn the
   ugly SIGILL into a clean quit. **It will not stop the crashes** — the guest
   SIGSEGV is upstream of it. Running full-screen / disabling Stage Manager may
   likewise only affect the exit path, not the fault.

**Verified recipe — a crash-free 68k *assembly* app (POC, 2026-06-27).** The
crashing `CounterAsm` was rebuilt from scratch; each of these was a real defect, and
fixing all of them produced an assembly app that launches repeatedly without
crashing BAII (verified: stock `SimpleText` stable, `MinAsm` launched 4× clean):

1. **Mac line endings.** LF-terminated source makes the MPW assembler emit an
   *empty* object (`DumpObj` shows only First/Last records; warning "END supplied by
   Assembler"). Convert with `host/encoding_convert.py` so it has CR endings.
2. **Link against the runtime, not bare.** Add `"{Libraries}MacRuntime.o"` (and
   `"{Libraries}Interface.o"`) so `%_MAIN`/`_DataInit`/`%A5Init` set up the A5 world
   and call your `main`. A bare-linked asm app has no A5 world → first global/QD
   access faults. Confirm with `DumpFile -h`: the fork should contain `A5Init` /
   `DataInit` / `RTInit` (≈4 KB), not ≈700 B.
3. **Case-exact `main`.** The C runtime calls lowercase `main`; the assembler folds
   to `MAIN` by default. Add `CASE OBJECT` so the export matches (else the link
   fails `-m`/`Error 53 main not found`, or links a dangling entry).
4. **`InitGraf` wants `&thePort`, not the buffer base.** `thePort` is the *last*
   field of QDGlobals (offset `QDSize-4`); the other globals grow downward from it.
   Passing the buffer base puts them below SP where later pushes clobber them →
   crash in the Window Manager. Use `PEA QDSize-4(SP)`.

`Link` succeeding is necessary but **not** sufficient — verify by *launching* and
watching BAII survive (and the crash-report count not increase), not by the link
status. Heavy dialog/Toolbox code can still have its own bugs on top of the above.

**A second, distinct crash mode** also exists: `EXC_BAD_ACCESS` (SIGSEGV) topped by
`video_refresh_window_static()` → `do_video_refresh()` → `redraw_func()`, with **no**
`NSWMWindowCoordinator` and **no** shutdown chain. That's a fault in BAII's own
redraw thread, unrelated to a guest app — diagnose it separately.

**Note:** all of the above is unrelated to the daemon-side "frozen at CONNECTING"
freeze above — that one is a hung *connect* (no crash report, host process alive).
These are real host *crashes* (process gone, crash report present).

---

## Compilation and Linking

### ILink vs Link

**`Link` is the default** — verified and leaner output. **`ILink` is *not* broken.**

The old "ILink crashes Basilisk II" belief was a **misdiagnosis** (corrected
2026-06-26). The committed `BuildIt` used an empty `{LIBS}`, so its library
paths resolved to nothing → a broken binary that crashed *on launch* — not
ILink's fault. With the correct `{CLibraries}`/`{Libraries}` paths, ILink
links, runs, and round-trips commands cleanly.

ILink just yields a slightly larger binary plus a big `.NJ` incremental file,
so `Link` stays the default by preference, not necessity.

```
Link  -model far -o MyApp main.o "{LIBS}Libraries:Interface.o" "{LIBS}Libraries:MacRuntime.o"
ILink -model far -o MyApp main.o "{LIBS}Libraries:Interface.o" "{LIBS}Libraries:MacRuntime.o"
```

### Error -192 (resNotFound) When Launching App

**Symptoms:**
- App links without error
- Launching shows Error -192

**A Data Fork Length of 0 is NORMAL for a 68K app** — the executable code lives
in `CODE` resources in the **resource fork**, not the data fork. Every working
AppleBridge build (Link *and* ILink) has a 0-byte data fork. So an empty data
fork is *not* the symptom; don't chase it.

**Cause:** no usable `CODE`/resources in the binary — almost always **wrong or
empty library paths** (e.g. an empty `{LIBS}`), not the linker itself.

**Diagnose:** `DumpFile MyApp -h` → the **Resource Fork Length** should be
non-trivial (CODE resources present). If it's empty, the link found no real
libraries.

**Solution:** fix the library paths (use `{Libraries}`/`{CLibraries}`, not an
empty `{LIBS}`) and re-link — via ToolServer is fine.

### Undefined Symbol Errors

**Common causes:**

1. **Missing libraries**
   ```
   ### Link: Error: Undefined entry, name: (Error 28) "_InitGraf"
   ```
   **Fix:** Add Interface.o:
   ```
   Link ... "{LIBS}Libraries:Interface.o"
   ```

2. **Using StdCLib functions without library**
   ```
   ### Link: Error: Undefined entry, name: (Error 28) "_printf"
   ```
   **Fix:** Add StdCLib.o (but beware conflicts):
   ```
   Link ... "{LIBS}CLibraries:StdCLib.o"
   ```

3. **Function doesn't exist in libraries**
   - Example: `NumToString` - not in standard libs
   - **Fix:** Implement your own or use different approach

### LIBS Variable Not Set

**Symptoms:**
```
### Link: File not found (OS error -43) ... MacRuntime.o
```

**Cause:** `{LIBS}` MPW variable is undefined or wrong.

**Fix:** Set before running Make or Link:
```
Set LIBS "MeinMac:Interfaces&Libraries:Libraries:"
Make MyApp
```

**Permanent fix:** Add to MPW UserStartup file:
```
Set LIBS "MeinMac:Interfaces&Libraries:Libraries:"
Export LIBS
```

---

## File System and Encoding

### Shared Folder Limitations

**Problem:** Unix volume (host's `/Users/pitforster/Desktop/Share`) is read-only from Mac.

**Symptoms:**
- Can't compile source on `Unix:` volume
- Error -45 (file locked) or -120 (directory not found)

**Solution:** Copy files to Mac local storage first:

```
# Copy entire directory
Duplicate -y Unix:project: MeinMac:MPW:project:

# Or individual files
Duplicate -y Unix:main.c MeinMac:MPW:project:main.c
```

Then compile from local storage:
```
Directory MeinMac:MPW:project:
SC main.c -o main.o
```

### Character Encoding Issues

**Symptoms:**
- Makefile shows garbage characters
- Special MPW characters don't work
- Source code corrupted

**Cause:** Mac uses MacRoman encoding, host uses UTF-8.

**Critical characters:**

| Char | UTF-8 bytes | MacRoman | MPW use |
|------|-------------|----------|---------|
| ∂ | e2 88 82 | 0xB6 | Line continuation |
| ƒ | c6 92 | 0xC4 | Folder/dependency marker |
| ≈ | e2 89 88 | 0xC7 | Wildcard |

**Solution:** Always use encoding_convert.py:

```bash
# TO Mac (UTF-8 → MacRoman, LF → CR):
uv run python encoding_convert.py to-share source.txt

# FROM Mac (MacRoman → UTF-8, CR → LF):
uv run python encoding_convert.py from-mac /Users/pitforster/Desktop/Share/output.txt ./output.txt
```

**Or use MCP tools** - they handle encoding automatically:
- `mac_write_file` - converts to MacRoman
- `mac_read_file` - converts to UTF-8

### "Not a text file" Error (OS Error -31001)

**Symptoms:**
```
### Cannot open "file.c" # Not a text file (OS Error -31001)
```

**Cause:** File has wrong type/creator or wrong line endings.

**Fix:**
```
# Set file type to TEXT
SetFile -t TEXT -c MPS  file.c

# Or recreate with proper encoding
# (Use encoding_convert.py on host first)
```

---

## ToolServer vs MPW Shell

### When to Use Which

| Feature | MPW Shell ('MPS ') | ToolServer ('MPSX') |
|---------|-------------------|---------------------|
| Interactive use | ✅ Excellent | ⚠️ Limited |
| Command output visible | ✅ Worksheet | ❌ Silent |
| Apple Events output | ❌ Empty (Items:0) | ✅ Full (Items:3) |
| Automation via AppleBridge | ❌ Blind | ✅ Full feedback |
| Compile/Link | ✅ Works | ✅ Works (but Link has data fork bug) |
| Both running simultaneously | ✅ Yes | ✅ Yes |

### ToolServer Output Capture - Verified

**Commands that return output via Apple Events:**

| Command | Output |
|---------|--------|
| `Directory` | ✅ Current path |
| `Files`, `Files -l` | ✅ File listings |
| `Echo "text"` | ✅ Text |
| `Catenate file` | ✅ File contents |

**Commands that are silent (check result file):**
- `SC source.c -o source.o` - Check if .o file exists
- `Link ...` - Check if binary exists and has non-zero data fork

### Capturing Compile/Link Errors

**DO NOT use `2>&1`** - crashes MPW Shell!

**Use MPW's `≥` operator instead:**

```bash
# Compile with stderr redirect
SC file.c -o file.o ≥ compile.err

# Check if compile succeeded
Exists file.o
# Success: Returns filename
# Failure: Returns "NoDir:-1701;Empty"

# Read errors/warnings
Catenate compile.err
```

### AppleBridge Daemon Automatically Chooses

The daemon tries ToolServer first, falls back to MPW Shell:

```c
// 1. Try ToolServer (preferred for automation)
err = FindApplicationBySignature('MPSX', &tSpec, &tLaunch);

// 2. Fall back to MPW Shell
if (err != noErr) {
    err = FindApplicationBySignature('MPS ', &tSpec, &tLaunch);
}
```

**Recommendation:** Always run ToolServer for automation.

---

## Synthetic Input

### A Command shortcut reports success but does nothing

`mac_key` / `mac_menu` answer `success: true` whenever the daemon queued the event —
that says the keystroke was *delivered*, never that the app *acted* on it. Verify by
screenshot, not by the return value — the layer that reports is not the layer that
decides.

If the app ignores the shortcut, check what it resolves the shortcut from. A key-down
message packs **two** halves: the low byte is the character the `KCHR` produced, the
next byte the **physical key**. Most apps call `MenuKey` with the character; some read
the key code.

Until PR #86 the MCP surface sent key code `0` for every character, and **code 0 is
the A key** — so key-code-reading apps saw Cmd-A for every shortcut. Symptom:
`mac_key(key="o", modifiers=["command"])` did nothing in Photoshop 2.5 while the same
call worked in ResEdit, SimpleText and Standard File dialogs. `mcp/tools.py` now
derives the physical key from the character.

Remaining cases and what to do:

| Symptom | Cause | Fix |
|---|---|---|
| Shortcut ignored in one app only | that app reads the key code, and yours is wrong | pass `key_code` explicitly (Inside Macintosh: Key Codes) |
| `Y`/`Z` shortcuts hit the wrong item | key codes are *positions*; QWERTZ swaps them | `APPLEBRIDGE_KEY_LAYOUT=de`, or an explicit `key_code` |
| No menu item has a Cmd-key equivalent | nothing to inject | `mac_host_menu` (real mouse, local emulator) or `mac_menu_front` |
| Nothing arrives at all | the daemon's own window took the front | the target app must be frontmost — `MONITOR:0` keeps the Verbose console from stealing clicks |

**Testing trap:** do not verify key injection with **Cmd-A**. `a` *is* key code 0, so a
Cmd-A test passes whether or not the key code is correct — that is exactly why the
original verification (Cmd-A/Cmd-Q in SimpleText) missed the bug. Pick a letter whose
key code is non-zero and an app whose reaction is unmistakable (Cmd-O opening a dialog).

---

## Known Limitations

### 1. Single Connection

AppleBridge daemon supports **one TCP connection at a time**.

**Why:** Simple design, classic Mac memory constraints.

**Impact:** Only one host can control the Mac simultaneously.

**Workaround:** None needed - MCP server manages the single connection.

### 2. Some Tools Don't Capture Output

**Problem:** Some MPW tools write directly to worksheet, bypassing Apple Events.

**Examples:**
- `Make` - Prints commands but doesn't return them
- Some diagnostic tools

**Workaround:**
- Use `Make > script; Execute script` pattern
- Or check result files instead of output

### 3. Reserved Keywords in Code

**Problem:** Struct members named `stdout`/`stderr` cause compiler errors.

**Cause:** MPW C compiler reserves these names.

**Solution:** Use different names:
```c
// Wrong:
struct Response {
    char *stdout;  // Error!
    char *stderr;  // Error!
};

// Correct:
struct Response {
    char *outData;
    char *errData;
};
```

### 4. Memory Constraints

**Classic Mac environment limits:**
- AppleBridge preferred size: 512 KB
- Large file transfers (>100 KB) may fail
- Complex commands can exhaust memory

**Solutions:**
- Keep commands simple
- Transfer large files in chunks
- Restart AppleBridge periodically for long sessions

### 5. Screenshots

The daemon captures the **emulated** screen itself (the main GDevice PixMap,
incl. pixel depth + colour table) and streams the raw pixmap over the bridge;
the host decodes it to PNG with `host/screenshot_decode.py` (pure stdlib, no
Pillow). Trigger it via the `mac_screenshot` MCP tool or by sending the raw
`SCREENSHOT` verb over the control port (`:9001`).

> The old host-side approach (`screencapture -R` of the Basilisk window via
> Quartz) was removed — it captured the wrong screen (the host desktop) and
> couldn't see the emulated framebuffer.

---

## Debug Checklist

When things don't work, check in order:

1. ✅ **Basilisk II running** - Emulator is active
2. ✅ **Network configured** - Mac has IP address
3. ✅ **ToolServer running** - For command output
4. ✅ **AppleBridge running** - Shows "Connected to host!"
5. ✅ **RX/TX LEDs flash** - Activity indicators (v0.3.0+)
6. ✅ **MCP server responds** - host_server.py logs (`/tmp/applebridge_server.log`) show activity
7. ✅ **Encoding correct** - Files converted via encoding_convert.py
8. ✅ **LIBS set** - For Make and Link commands

## Getting Help

**Check these documents first:**
- [ARCHITECTURE.md](ARCHITECTURE.md) - How the system works
- [SETUP.md](docs/SETUP.md) - Installation and configuration
- [README.md](README.md) - Quick start and overview

**Still stuck?**
- Check the host server log: `/tmp/applebridge_server.log`
- Look at the AppleBridge monitor window (or menu-bar LED) on the Mac
- Examine RX/TX LED patterns (v0.3.0+)
- Try the standalone host_server.py for direct testing (`/usr/bin/python3 host_server.py`)

---

**Last Updated:** July 26, 2026
**Version:** AppleBridge 0.8d28 (wire protocol v0.2) — from `mac/vers.r`, the single source
