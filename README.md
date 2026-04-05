# AppleBridge

Connect Claude AI to classic Macintosh MPW shell running in Basilisk II emulator.

## Status: WORKING

- TCP bridge: **Operational**
- Command execution: **Operational** (via Apple Events to MPW Shell)
- Screenshot capture: **Initiated** (data transfer works, image format needs work)

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

Copy `mac/` folder to Mac filesystem (not shared folder - compiling from shared folder fails).

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
| `convert_screenshot.py` | Raw screenshot to PNG converter |

## Technical Notes

### MPW Makefile
- Use **TAB** characters (not spaces)
- Special chars: `ƒ` (Option-f) for dependencies

### Libraries
```
OpenTransport.o OpenTransportApp.o OpenTptInet.o Interface.o MacRuntime.o
```

### Encoding
- MacRoman encoding
- Line endings: `\r` (Mac) - host handles both `\r` and `\n`

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

1. **Screenshot not operational**: Raw screenBits data transfers successfully (24KB), but PNG conversion doesn't produce meaningful images yet - format/dimensions need debugging
2. Single connection only
3. Struct members named `outData`/`errData` (not stdout/stderr - reserved)

## Dependencies Avoided

- `StdCLib.o` - causes undefined symbol errors
- `console.h` - unavailable, using QuickDraw window
- `time.h` - simplified logging without timestamps
