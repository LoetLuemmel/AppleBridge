# AppleBridge Setup Guide

Complete setup instructions for getting AppleBridge running with Claude Code.

## Overview

AppleBridge connects Claude Code to a classic Mac System 7.6.1 environment via three layers:
1. **MCP Layer** - Claude Code ↔ MacintoshBridgeHost (port 9001)
2. **TCP/OpenTransport Layer** - Mac daemon ↔ MacintoshBridgeHost (port 9000)
3. **Apple Events Layer** - AppleBridge ↔ ToolServer/MPW Shell

## Prerequisites

### Host System (macOS)

- **Operating System**: macOS 12.0+ (tested on Sequoia)
- **Basilisk II emulator**: Configured and running
- **Xcode**: For building MacintoshBridgeHost Swift app
- **Python 3.9+**: For encoding converter and utilities
- **uv**: Package manager (recommended) - `brew install uv`
- **Claude Code**: Installed and configured

### Mac Emulator (Basilisk II)

- **System**: Mac OS 7.6.1 (recommended)
- **OpenTransport**: Version 1.3 or later
- **MPW**: Golden Master with SC compiler
- **ToolServer**: Required for command output capture
- **Networking**: TCP/IP configured (DHCP or manual)
- **Memory**: 64 MB RAM minimum

## Part 1: Basilisk II Setup

### 1.1 Emulator Configuration

**Networking** - Use SLIRP (NAT) mode:
```
Basilisk II Preferences:
- Ethernet: slirp
- Network Interface: en0 (or your primary interface)
```

**Shared Folder** - Enable Unix volume:
```
Basilisk II Preferences:
- Unix Root: /Users/pitforster/Desktop/Share
```

This appears as `Unix:` volume on the Mac (read-only).

**Memory** - Allocate sufficient RAM:
```
Basilisk II Preferences:
- Mac Memory: 64 MB (minimum)
```

### 1.2 TCP/IP Control Panel

Boot into System 7.6.1 and configure networking:

1. **Open TCP/IP Control Panel**
   - Apple Menu → Control Panels → TCP/IP

2. **Configure settings:**
   ```
   Connect via: Ethernet
   Configure: Using DHCP Server

   # After DHCP assigns address:
   IP Address: 192.168.x.x (note this!)
   Subnet mask: 255.255.255.0
   Router: 192.168.x.1
   ```

3. **Test connectivity:**
   - Note the Mac's IP address
   - From host: `ping <mac_ip>` should work

### 1.3 Install MPW and ToolServer

1. **Install MPW Golden Master**
   - Copy MPW folder to Mac hard drive
   - Recommended location: `MeinMac:MPW:`

2. **Verify MPW Shell launches**
   - Double-click MPW Shell
   - You should see a worksheet window

3. **Launch ToolServer** (critical for automation!)
   - Double-click ToolServer
   - It runs in background (no visible window)
   - **Must be running for command output capture**

4. **Verify libraries are present:**
   ```
   Files "MeinMac:Interfaces&Libraries:Libraries:"
   ```
   Should show: Interface.o, MacRuntime.o, etc.

## Part 2: Build MacintoshBridgeHost (Swift)

### 2.1 Build the MCP Bridge

```bash
cd /Users/pitforster/Documents/Dev/AppleBridge_Working/MacintoshBridgeHost
open MacintoshBridgeHost.xcodeproj
```

In Xcode:
1. Select scheme: MacintoshBridgeHost
2. Product → Build
3. Product → Archive (for production)

**Binary location:**
```
Debug: ~/Library/Developer/Xcode/DerivedData/.../Build/Products/Debug/MacintoshBridgeHost.app
Release: Archive and export
```

### 2.2 Configure MCP in Claude Code

Edit `~/.claude/.mcp.json` or project `.mcp.json`:

```json
{
  "mcpServers": {
    "applebridge": {
      "command": "/Users/pitforster/Documents/Dev/AppleBridge_Working/MacintoshBridgeHost/build/MacintoshBridgeHost.app/Contents/MacOS/MacintoshBridgeHost",
      "args": []
    }
  }
}
```

Verify with:
```bash
claude mcp list
```

Should show: `applebridge` with 6 tools.

## Part 3: Build AppleBridge Mac Daemon

### 3.1 Character Encoding Setup

**Critical:** Files must be converted from UTF-8 (host) to MacRoman (Mac).

Install encoding converter:
```bash
cd /Users/pitforster/Documents/Dev/AppleBridge_Working/host
uv venv
source .venv/bin/activate
uv pip install chardet
```

### 3.2 Convert and Transfer Source

Convert Mac daemon source to MacRoman:
```bash
cd /Users/pitforster/Documents/Dev/AppleBridge_Working/host
uv run python encoding_convert.py to-share ../mac/
```

**What this does:**
- Converts UTF-8 → MacRoman encoding
- Converts LF → CR line endings
- Handles special MPW characters: `∂` (continuation), `ƒ` (dependency)

**Result:** Files appear in `/Users/pitforster/Desktop/Share/mac/`

### 3.3 Copy to Mac Local Storage

In Basilisk II (via MPW Shell or Finder):

```
# Create project directory
NewFolder MeinMac:MPW:AppleBridge

# Copy from shared Unix volume to local storage
Duplicate -y Unix:mac: MeinMac:MPW:AppleBridge:

# Verify files copied
Files MeinMac:MPW:AppleBridge:src:
```

**Why local storage?** The Unix: volume is read-only from Mac side. Compilation requires write access.

### 3.4 Configure Host IP

Edit `MeinMac:MPW:AppleBridge:src:main.c`:

```c
char hostIPStr[] = "192.168.3.154";  // Change to YOUR host IP
```

**To find host IP:**
```bash
# On macOS:
ipconfig getifaddr en0

# Should match Mac's gateway/router address
```

### 3.5 Build the Daemon

In MPW Shell:

```
# Navigate to project
Directory MeinMac:MPW:AppleBridge:

# Set library path (CRITICAL!)
Set LIBS "MeinMac:Interfaces&Libraries:Libraries:"
Export LIBS

# Build
Make -f Makefile.68k
```

**Expected output:**
```
# Compiling...
SC src:main.c -o obj:main.c.o
SC src:network.c -o obj:network.c.o
...

# Linking...
Link -model far ...

# Creating resource fork...
Rez AppleBridge.r -a -o bin:AppleBridge

# Build complete
```

**Result:** `MeinMac:MPW:AppleBridge:bin:AppleBridge`

### 3.6 Verify Build

```
# Check file exists
Files bin:

# Check file type
GetFileInfo bin:AppleBridge
```

Should show:
- Type: APPL
- Creator: Ptfr (or similar)

## Part 4: Launch and Test

### 4.1 Start the System

**On Mac (in order):**
1. Start ToolServer (double-click, runs in background)
2. Start MPW Shell (optional, for interactive use)
3. Launch AppleBridge (double-click bin:AppleBridge)

**AppleBridge window should show:**
```
AppleBridge v0.3.0

Status: Connecting to host...
      ↓
Status: Connected to host!

RX:0 TX:0
[  ] [  ]  ← RX/TX LED indicators
```

**On Host:**
MacintoshBridgeHost starts automatically when Claude Code loads MCP.

**Check MacintoshBridgeHost logs:**
```bash
# If running from terminal:
/path/to/MacintoshBridgeHost.app/Contents/MacOS/MacintoshBridgeHost

# Look for:
MCP server listening on port 9001
TCP server listening on port 9000
Mac client connected from 192.168.x.x
```

### 4.2 Test with Claude Code

Open Claude Code and test:

```
You: Can you execute 'Directory' on the Mac?
```

Claude should:
1. Use `mcp__applebridge__mpw_execute` tool
2. Send command to MacintoshBridgeHost
3. Forward to Mac via TCP
4. Receive response: `MeinMac:MPW:AppleBridge:`

**Watch for:**
- AppleBridge RX LED flashes GREEN (command received)
- AppleBridge TX LED flashes RED (response sent)
- Counter increments: `RX:1 TX:1`

### 4.3 Test Compilation

```
You: Create a simple Hello World program in MeinMac:MPW:Test
```

Claude should:
1. Create source file via `mac_write_file`
2. Compile via `mpw_execute("SC ...")`
3. Link via `mpw_execute("Link ...")`
4. Set file type via `mpw_execute("SetFile ...")`
5. Launch the app

**Result:** Mac dialog showing "Hello, World!"

## Part 5: Technical Configuration

### 5.1 MPW Libraries

AppleBridge daemon requires these libraries:

| Library | Purpose |
|---------|---------|
| **OpenTransport.o** | TCP/IP networking |
| **OpenTransportApp.o** | OT application support |
| **OpenTptInet.o** | Internet protocols |
| **Interface.o** | Toolbox trap definitions |
| **MacRuntime.o** | C runtime initialization |

**Library paths (set LIBS variable):**
```
MeinMac:Interfaces&Libraries:Libraries:
  Libraries:
    Interface.o
    MacRuntime.o
    ...
  CLibraries:
    StdCLib.o (optional, can cause conflicts)
```

**In Makefile.68k:**
```makefile
LIBS = "{LIBS}Libraries:"

Link ... ∂
    "{LIBS}OpenTransport.o" ∂
    "{LIBS}OpenTransportApp.o" ∂
    "{LIBS}OpenTptInet.o" ∂
    "{LIBS}Interface.o" ∂
    "{LIBS}MacRuntime.o" ∂
    -o bin:AppleBridge
```

### 5.2 Protocol Specification

**Request format (Host → Mac):**
```
COMMAND:<length>\n
<command_text>
```

Example:
```
COMMAND:9\n
Directory
```

**Response format (Mac → Host):**
```
STATUS:<exit_code>\n
STDOUT:<length>\n
<stdout_data>
STDERR:<length>\n
<stderr_data>
\n
```

Example:
```
STATUS:0\n
STDOUT:25\n
MeinMac:MPW:AppleBridge:\n
STDERR:0\n
\n
```

**Screenshot request:**
```
SCREENSHOT
```

**Screenshot response:**
```
IMAGE:<width>:<height>:BMP:<size>\r
<binary_bitmap_data>
```

### 5.3 Character Encoding Reference

**MacRoman ↔ UTF-8 conversions:**

| Character | UTF-8 | MacRoman | Usage |
|-----------|-------|----------|-------|
| ∂ (partial) | E2 88 82 | B6 | MPW line continuation |
| ƒ (florin) | C6 92 | C4 | MPW dependency marker |
| ≈ (approx) | E2 89 88 | C7 | MPW wildcard |
| • (bullet) | E2 80 A2 | A5 | Option-8 |
| … (ellipsis) | E2 80 A6 | C9 | Option-; |

**Line endings:**
- **Mac Classic**: CR (0x0D, `\r`)
- **Unix/macOS**: LF (0x0A, `\n`)
- **Windows**: CR+LF (`\r\n`)

**Converter script handles all of this automatically.**

### 5.4 MPW Makefile Syntax

**Dependency marker:** `ƒ` (Option-F)
```makefile
main.o  ƒ  main.c
    SC main.c -o main.o
```

**Line continuation:** `∂` (Option-D)
```makefile
Link -o MyApp ∂
    main.o ∂
    "{LIBS}Interface.o" ∂
    -t APPL
```

**Variables:**
```makefile
Set LIBS "MeinMac:Interfaces&Libraries:Libraries:"
OBJS = main.o network.o

# Reference:
"{LIBS}Interface.o"
{OBJS}
```

**Critical:** Use TAB characters for command indentation, not spaces!

## Troubleshooting

For common issues and solutions, see **[TROUBLESHOOTING.md](../TROUBLESHOOTING.md)**:

- Apple Events errors
- Connection problems
- Compilation/linking issues
- Encoding problems
- ToolServer vs MPW Shell

## Advanced Configuration

### Host-Side Screenshot Capture

System 7.6.1 can't capture screenshots natively. Use host-side script:

```bash
python3 /Users/pitforster/Documents/Dev/AppleBridge_Working/host/screenshot.py output.png
```

**How it works:**
1. Uses macOS Quartz to find Basilisk II window
2. Captures window bounds
3. Executes `screencapture -R x y w h output.png`

### Standalone TCP Server (Development)

For testing without MCP:

```bash
cd host/
python3 host_server.py
```

Interactive mode allows direct command testing:
```
Command> Directory
Response:
STATUS:0
STDOUT:25
MeinMac:MPW:AppleBridge:
```

### Python MCP Server Alternative

Alternative to MacintoshBridgeHost Swift app:

```bash
cd mcp/
uv run python server.py
```

Configure in `.mcp.json`:
```json
{
  "mcpServers": {
    "applebridge": {
      "command": "uv",
      "args": ["run", "python", "mcp/server.py"]
    }
  }
}
```

## Next Steps

- **[README.md](../README.md)** - Quick start and overview
- **[ARCHITECTURE.md](../ARCHITECTURE.md)** - System design and paradigms
- **[TROUBLESHOOTING.md](../TROUBLESHOOTING.md)** - Problem solving
- **[ASSEMBLY_TEMPLATE.md](../ASSEMBLY_TEMPLATE.md)** - 68k assembly guide

## Production Checklist

Before using for serious work:

- ✅ Basilisk II networking configured and tested
- ✅ OpenTransport installed on Mac
- ✅ MPW and ToolServer installed
- ✅ MacintoshBridgeHost built and MCP configured
- ✅ AppleBridge daemon built with correct host IP
- ✅ ToolServer running (for output capture)
- ✅ AppleBridge shows "Connected to host!"
- ✅ RX/TX LEDs flash when testing commands
- ✅ Test compilation and linking work
- ✅ encoding_convert.py available for file transfers

---

**Setup Version:** 1.0
**Last Updated:** April 12, 2026
**Target:** AppleBridge 0.3.0 with MCP
