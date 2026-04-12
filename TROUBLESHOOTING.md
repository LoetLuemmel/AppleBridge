# AppleBridge Troubleshooting

This document contains solutions to common issues, historical fixes, and known limitations.

## Table of Contents

- [Apple Events Issues](#apple-events-issues)
- [Network and Connection](#network-and-connection)
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

## Compilation and Linking

### ILink vs Link - Emulator Crashes

**Problem:** ILink (incremental linker) frequently crashes Basilisk II emulator.

**Solution:** Use Link (classic linker) instead.

**Wrong:**
```
ILink -model far -o MyApp main.o Interface.o
```

**Correct:**
```
Link -model far -o MyApp main.o "{LIBS}Libraries:Interface.o" "{LIBS}Libraries:MacRuntime.o"
```

**Note:** Link is slower but stable on Basilisk II. ILink works on real hardware but not reliably in emulation.

### Error -192 (resNotFound) When Launching App

**Symptoms:**
- App links without error
- Launching shows Error -192
- `DumpFile MyApp` shows Data Fork Length: 0

**Cause:** ToolServer Link command sometimes creates executables with empty data forks.

**Solution:** Link via MPW Shell instead:

```
# Instead of linking via ToolServer automation:
# Use MPW Shell interactively or via AppleBridge to MPW Shell
```

**Workaround if must use ToolServer:**
1. Link creates binary
2. Check data fork: `DumpFile -l MyApp`
3. If Data Fork Length is 0, link failed silently
4. Try linking again via MPW Shell

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

### 5. Screenshot Limitations

**Mac side can't capture screenshots** - System 7.6.1 lacks built-in capability.

**Solution:** Host-side capture of Basilisk II window:

```bash
python3 host/screenshot.py [output.png]
```

Uses macOS Quartz to find window and `screencapture -R` to capture region.

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
