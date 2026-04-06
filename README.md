# AppleBridge

Connect Claude AI to classic Macintosh MPW shell running in Basilisk II emulator.

## Current Status: IN PROGRESS

- TCP bridge: **WORKING** - Mac daemon connects to host
- Encoding conversion: **WORKING** - UTF-8 ↔ MacRoman converter
- Apple Events: **DEBUGGING** - Returns -903 error (Script Editor works, daemon doesn't)
- Screenshot capture: **WORKING** (host-side, captures Basilisk II window)

## Architecture

```
┌─────────────────┐
│   Claude AI     │
└────────┬────────┘
         │
┌────────▼────────────────┐
│  Host Server            │  (Python)
│  host_server.py         │
│  Listens on port 9000   │
└────────▲────────────────┘
         │ TCP Socket (Mac connects OUT)
         │ Through NAT/MACNAT
┌────────┴────────────────┐
│  Mac TCP Daemon         │  (C/MPW 68k)
│  AppleBridge            │
│  Connects to host       │
└────────┬────────────────┘
         │ Apple Events ('misc'/'dosc')
┌────────▼────────────────┐
│    MPW Shell            │
│  Creator: 'MPS '        │
│  Execute commands       │
└─────────────────────────┘
```

**Key Design**: Mac connects OUT to host (reversed client-server) because Basilisk II uses MACNAT - incoming connections are blocked, outgoing work.

## Quick Start

### 1. Host Side

Start the server first:
```bash
cd host/
python3 host_server.py
```

Output:
```
=== AppleBridge Host Server ===
Listening on port 9000
Configure Mac to connect to: 192.168.x.x:9000
Waiting for Mac to connect...
```

### 2. Mac Side (Basilisk II)

Requirements:
- System 7.6.1 with Open Transport
- MPW Golden Master
- 68k Compiler: SC, Linker: ILink with `-model far`

**IMPORTANT**: Convert files before copying to Mac:
```bash
cd host/
uv run python encoding_convert.py to-share ../mac/
```

Edit `src/main.c` to set your host IP:
```c
char hostIPStr[] = "192.168.3.154";  /* Your host IP */
```

Build in MPW:
```
Directory AppleBridge:
Make -f Makefile.68k
```

### 3. Launch

1. Start MPW Shell first (required for Apple Events)
2. Double-click AppleBridge application
3. Status window shows: "Connecting to host..."
4. On success: "Connected to host!"

### 4. Test

In host_server.py interactive mode:
```
Command> Echo "Hello from Claude"
Response:
STATUS:0
STDOUT:17
Hello from Claude
STDERR:0
```

## Character Encoding

**CRITICAL**: Files must be converted between host (UTF-8) and Mac (MacRoman):

```bash
# Use the encoding converter script:
cd host/
uv run python encoding_convert.py to-mac source.txt ~/Desktop/Share/dest.txt
uv run python encoding_convert.py from-mac ~/Desktop/Share/file.txt ./file.txt

# Shortcut to Share folder:
uv run python encoding_convert.py to-share Makefile.68k
```

**Key character mappings:**
| Char | UTF-8 bytes | MacRoman | MPW use |
|------|-------------|----------|---------|
| ∂ | e2 88 82 | b6 | Line continuation |
| ƒ | c6 92 | c4 | Folder in paths |
| ≈ | e2 89 88 | c7 | Wildcard |

**Line endings:**
- Mac uses CR (`\r`, 0x0D)
- Host uses LF (`\n`, 0x0A)
- Converter handles both directions automatically

## Files

### mac/

| File | Description |
|------|-------------|
| `src/main.c` | Main daemon, event loop, status window |
| `src/network.c` | Open Transport TCP client |
| `src/command.c` | Apple Events to MPW Shell |
| `src/screenshot.c` | ScreenBits capture |
| `src/protocol.c` | Protocol parsing |
| `src/mystring.c` | Custom string functions (no StdCLib) |
| `include/applebridge.h` | Main header |
| `include/mystring.h` | String function prototypes |
| `Makefile.68k` | MPW Makefile |

### host/

| File | Description |
|------|-------------|
| `host_server.py` | TCP server with interactive mode |
| `encoding_convert.py` | UTF-8 ↔ MacRoman converter with line ending conversion |
| `test_commands.py` | Automated test script for MPW commands |
| `screenshot.py` | Capture Basilisk II window (host-side, uses Quartz) |

## Technical Notes

### MPW Makefile
- Use **TAB** characters (not spaces)
- Special chars: `ƒ` (Option-f) for dependencies, `∂` (Option-d) for continuation

### Libraries
```
OpenTransport.o OpenTransportApp.o OpenTptInet.o Interface.o MacRuntime.o
```

**Note**: StdCLib.o removed - causes undefined symbol errors.

### Apple Events
Finds MPW Shell (`'MPS '`) or ToolServer (`'MPSX'`), sends `'misc'/'dosc'` event.

### Protocol

Request: `COMMAND:<length>\r<command>`

Response:
```
STATUS:<code>
STDOUT:<length>
<data>
STDERR:<length>
<data>

```

Screenshot: `SCREENSHOT` -> `IMAGE:<w>:<h>:BMP:<size>\r<data>`

## Known Issues

1. **Apple Events -903 error**: Daemon fails to send AE to MPW Shell, but Script Editor succeeds
   - Problem likely in daemon's SIZE resource or AE addressing method
   - Script Editor uses OSA/AppleScript which may have different code path
2. **Single connection**: Only one Mac client at a time
3. Struct members named `outData`/`errData` (not stdout/stderr - reserved in MPW)

## Screenshot

Screenshots are captured from the **host side** (not Mac side) - System 7.6.1 lacks screenshot capabilities.

```bash
python3 host/screenshot.py [output.png]
```

Uses macOS Quartz to find Basilisk II window and capture via `screencapture -R`.

## License

Educational and development purposes.
