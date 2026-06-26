# AppleBridge Troubleshooting

This document contains solutions to common issues, historical fixes, and known limitations.

## Table of Contents

- [Apple Events Issues](#apple-events-issues)
- [Network and Connection](#network-and-connection)
- [Emulator (Basilisk II) Crashes](#emulator-basilisk-ii-crashes)
- [Compilation and Linking](#compilation-and-linking)
- [File System and Encoding](#file-system-and-encoding)
- [ToolServer vs MPW Shell](#toolserver-vs-mpw-shell)
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

3. **Verify Mac can reach host**
   - From Mac: Use MacTCP Ping or similar utility
   - From host: Check Basilisk II networking mode (SLIRP vs bridged)

4. **Check firewall on host**
   ```bash
   # macOS: Allow incoming on port 9000
   sudo /usr/libexec/ApplicationFirewall/socketfilterfw --add /path/to/MacintoshBridgeHost.app
   ```

5. **Verify correct IP in src/main.c**
   ```c
   char hostIPStr[] = "192.168.3.154";  // Must match host IP
   ```

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
   Host: MacintoshBridgeHost will reconnect automatically
   ```

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
6. ✅ **MCP server responds** - MacintoshBridgeHost logs show activity
7. ✅ **Encoding correct** - Files converted via encoding_convert.py
8. ✅ **LIBS set** - For Make and Link commands

## Getting Help

**Check these documents first:**
- [ARCHITECTURE.md](ARCHITECTURE.md) - How the system works
- [SETUP.md](docs/SETUP.md) - Installation and configuration
- [README.md](README.md) - Quick start and overview

**Still stuck?**
- Check MacintoshBridgeHost console logs
- Look at AppleBridge status window on Mac
- Examine RX/TX LED patterns (v0.3.0+)
- Try the standalone host_server.py for direct testing

---

**Last Updated:** April 12, 2026
**Version:** AppleBridge 0.3.0
