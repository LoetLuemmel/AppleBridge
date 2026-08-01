# AppleBridge Architecture: OpenTransport + MCP Integration

**Date:** 2026-04-11
**Status:** Production - Fully Operational

> Why OpenTransport *and* MCP is not a doubling of the same layer:
> [docs/ARCHITECTURE_LAYERS.md](docs/ARCHITECTURE_LAYERS.md).

## Overview

AppleBridge combines two distinct technologies to create a unique capability: **AI-powered development for classic 68k Macintosh systems**. This document explains why both OpenTransport and MCP are essential, and how they work together.

---

## The Complete Stack

```
Claude Code (AI/LLM)
    ↓ [MCP Protocol] - AI tool interface
MCP server + host_server.py (Python, modern macOS)
    ↓ [Open Transport, MacTCP or Serial] - Network bridge
AppleBridge Daemon (C, 68k Mac in Basilisk II emulator)
    ↓ [Apple Events] - Classic Mac IPC
ToolServer/MPW Shell (classic Mac OS System 7.6.1)
```

---

## Why Both Technologies Are Needed

### OpenTransport (Classic Mac Networking)

**Purpose:** Gets the 68k Mac online with modern TCP/IP

**What it provides:**
- 🌐 Native TCP/IP networking for System 7
- 📡 Outbound connection capability from Mac
- 🔄 Reversed architecture (Mac connects OUT to host)
- 💡 Solves NAT/emulator isolation problem

**Without OpenTransport:**
- Mac would be isolated in emulator
- No way to send commands remotely
- Manual keyboard/mouse interaction only

### MCP (Model Context Protocol)

**Purpose:** Standardized AI tool interface for classic Mac operations

**What it provides:**
- 🤖 AI integration (Claude Code can control the Mac)
- 🛠️ Structured tools (`mpw_execute`, `mac_screenshot`, etc.)
- 🔌 Plugin architecture (any MCP client can use it)
- 📝 Declarative interface with schemas and validation

**Without MCP:**
- No standardized way for AI to interact
- Raw socket programming required
- No error handling or validation
- Not accessible to AI assistants

---

## They Are NOT Duplicated - They're Complementary

### Different Layers, Different Problems Solved

| Technology | Layer | Problem Solved |
|------------|-------|----------------|
| **OpenTransport** | Mac Network Stack | "How does a 68k Mac talk TCP/IP?" |
| **MCP** | AI Tool Interface | "How does AI control the Mac?" |

### Unique Benefits of the Combination

#### 1. **AI-Powered Retro Development** 🚀

**Without this combo:**
- Manual typing in MPW Shell
- Copy/paste between systems
- No automation possible
- Documentation from 1990s only

**With OpenTransport + MCP:**
- Claude writes code remotely
- Compiles on authentic 68k toolchain
- Captures results automatically
- Full development workflow from natural language
- AI can debug classic Mac code

#### 2. **Breaking the Emulator Barrier** 🎯

**The Problem:**
Basilisk II emulator is behind NAT and can't accept inbound connections. Traditional client-server won't work.

**The Solution:**
- **OpenTransport:** Mac initiates outbound connection to host
- **MCP:** Host forwards AI commands to connected Mac
- **Result:** Reversed TCP architecture solves NAT issue

#### 3. **Time Travel Development** ⏰

You can now:
1. Ask Claude: "Build me a Mac app that counts to 20"
2. Claude creates source code
3. Code compiles on 1990s Mac toolchain (SC compiler)
4. App runs on System 7.6.1
5. All controlled from 2026 AI chat interface

**Example workflow:**
```
User: "Create a counter app in MeinMac:MPW:OurTest"
Claude: [writes counter.c via MCP]
Claude: [compiles: SC counter.c -o counter.o]
Claude: [links: Link -o Counter counter.o Interface.o MacRuntime.o]
Claude: [launches: Counter]
Result: Classic Mac app running, counting 0-20 in a dialog
```

#### 4. **Documentation & Discovery** 📚

The MCP layer provides:
- Self-documenting tools with schemas
- Input validation and error messages
- Proper encoding handling (MacRoman ↔ UTF-8)
- Makes 30-year-old tech accessible to modern AI

---

## What's NOT Possible Without Both

❌ **OpenTransport Only:**
- Mac can network, but no AI integration
- Still requires manual command entry
- No structured tool interface

❌ **MCP Only:**
- AI has tools defined, but can't reach the Mac
- No way to execute commands
- Dead end at the host bridge

✅ **Both Together:**
- AI can develop for classic Mac remotely
- Full feedback loop: command → execute → results
- Authentic 68k environment with modern assistance

---

## Real-World Impact

### Before AppleBridge

**Workflow:**
1. Boot Basilisk II emulator
2. Type commands manually in MPW Shell
3. Copy error messages by hand
4. Search 1990s documentation (if available)
5. Fix code manually
6. Repeat entire process

**Time:** Hours for simple tasks
**Friction:** Extreme

### After AppleBridge

**Workflow:**
1. Tell Claude what you want to build
2. Claude writes, compiles, tests, and debugs
3. On authentic 68k Mac environment
4. With modern AI assistance and instant feedback

**Time:** Minutes
**Friction:** Minimal

**Example:**
```
User: "Can you build a Counter app in OurTest?"
Claude: [Creates counter.c, compiles, links, tests]
Claude: "Counter app built and tested successfully!"
```

---

## Technical Details

### Transport on Mac Side

**Files:** `mac/src/transport.c` (backend dispatcher + fallback ladder),
`mac/src/transport_ot.c` (Open Transport), `mac/src/transport_mactcp.c` (MacTCP),
`mac/src/transport_serial.c` (Serial — an RS-422 line instead of a network, which
is what reaches a Macintosh with no Ethernet at all).
The backend is selected by the `NET=` prefs key. (`network.c` was split into these
in the v0.7.0 transport-seam refactor.)

- CLIENT mode (connects OUT to host)
- Implements `ConnectToHost()`, `SendData()`, `ReceiveData()` behind an opaque `ABConn`
- Handles reconnection after failures
- 30-second retry intervals

### MCP on Host Side

**Files:** `mcp/server.py`, `mcp/tools.py`, `mcp/mac_connection.py`

**MCP Tools Provided:** 30 tools across two surfaces plus lifecycle — driving a
build and reading output (`mpw_execute`, `mac_compile`, `mac_build`,
`mac_read_file`, `mac_list_files`, `mac_send_apple_event`), moving bytes /
running / observing / interacting (`mac_put_file`, `mac_get_file`,
`mac_write_file`, `launch_app`, `mac_screenshot`, `mac_type`, `mac_key`,
`mac_menu`, `mac_menu_front`, `mac_click`, `mac_clipboard_get`,
`mac_clipboard_set`), driving the guest's real mouse (`mac_host_click`,
`mac_host_menu`, `mac_host_screenshot`), network discovery
(`mac_appletalk_browse`), and liveness/lifecycle (`mac_status`, `bridge_doctor`,
`mac_verbose_log`, `mac_reboot`, `mac_shutdown`, `mac_restart_toolserver`,
`mac_update_daemon`, `run_applescript`). The count is `len(TOOLS)` in
`mcp/tools.py`; see `CLAUDE.md` for the authoritative, current list.

### The Bridge (host_server.py)

**File:** `host/host_server.py` (Python, stdlib-only, run under `/usr/bin/python3`).
The retired Swift `MacintoshBridgeHost` was replaced by this Python server.

- TCP server on port 9001 (control / MCP commands)
- TCP server on port 9000 that the Mac daemon connects OUT to (NAT-reversed)
- Routes commands: MCP client → Mac daemon
- Routes responses: Mac daemon → MCP client
- Length-framed reads, flow control, heap-streams responses > 64 KB

---

## Communication Flow

### Successful Command Execution

```
1. Claude Code
   ↓ MCP tool call: mpw_execute("Echo 'Hello'")
2. host_server.py (control server)
   ↓ Forwards command to Mac daemon via TCP
3. AppleBridge Daemon
   ↓ Sends Apple Event to ToolServer
4. ToolServer
   ↓ Executes command, returns output via AE
5. AppleBridge Daemon
   ↓ Formats response: STATUS:0, STDOUT:<data>
6. host_server.py
   ↓ Forwards response back to MCP client
7. Claude Code
   ↓ Receives: {success: true, output: "Hello"}
```

**Total round-trip:** ~100-500ms

---

## Key Learnings

### ToolServer vs MPW Shell

**ToolServer ('MPSX'):**
- ✅ Returns command output via Apple Events
- ✅ Full automation support
- **Use for:** Remote automation via AppleBridge

**MPW Shell ('MPS '):**
- ❌ Returns empty Apple Events (STDOUT:0, STDERR:0)
- ✅ Output visible in Worksheet window
- **Use for:** Interactive development on Mac

**Both can run simultaneously** - AppleBridge prefers ToolServer when available.

### Encoding Is Critical

- **Host:** UTF-8, LF line endings
- **Mac:** MacRoman, CR line endings
- **Conversion:** Required at every boundary
- **Implementation:** `encoding_convert.py`, MCP tools handle automatically

### Apple Events Requirements

The Mac daemon requires:
- SIZE resource with `isHighLevelEventAware` flag
- `WaitNextEvent()` event loop (not `GetNextEvent()`)
- Resource file: `AppleBridge.r`

---

## Unique Achievement

**This is the only known system that enables:**

1. ✅ AI-driven development on classic 68k Mac hardware
2. ✅ Full feedback loop (command → execution → results)
3. ✅ Authentic 1990s toolchain (SC compiler, Link, etc.)
4. ✅ Remote operation through emulator NAT
5. ✅ Natural language to working Mac app

**No other system combines:**
- Classic Mac emulation
- Modern networking (OpenTransport)
- AI tool interface (MCP)
- Bidirectional communication
- Full automation capability

---

## Future Possibilities

With this foundation, you could:

- **Automated testing:** AI writes and runs test suites on classic Mac
- **Code archaeology:** AI explores and documents vintage Mac software
- **Educational tools:** Learn 68k assembly/Mac programming with AI tutor
- **Preservation:** Automate building/testing of historic Mac software
- **Cross-era development:** Modern AI + authentic retro toolchain

---

## Credits

**Built by:** Pit with love for 68K and Claude
**AI Assistant:** Claude Sonnet 4.5 (Anthropic)
**Technology:** OpenTransport, MCP, Apple Events, System 7.6.1
**Platform:** Basilisk II emulator, macOS Sequoia

**"Connecting classic Mac to the future"** ✨

---

## References

- [OpenTransport Documentation](https://developer.apple.com/legacy/library/documentation/mac/OpenTptIntro/OpenTptIntro-2.html)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [Apple Events Programming Guide](https://developer.apple.com/library/archive/documentation/AppleScript/Conceptual/AppleEvents/)
- [MPW Shell Documentation](https://developer.apple.com/legacy/library/documentation/mac/IAC/IAC-2.html)

---

**Last Updated:** April 11, 2026
**Status:** Production Ready ✅
