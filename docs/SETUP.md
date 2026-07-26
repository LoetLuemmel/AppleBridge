# AppleBridge Setup Guide

Complete setup instructions for getting AppleBridge running with Claude Code.

## Overview

AppleBridge connects Claude Code to a classic Mac System 7.6.1 environment via four layers:

1. **MCP layer** — Claude Code ↔ `mcp/server.py` (stdio), which exposes the 20 MCP tools.
2. **Control layer** — `mcp/server.py` ↔ `host_server.py` over `localhost:9001`.
3. **Bridge layer** — the Mac daemon ↔ `host_server.py` over `:9000`. The architecture is **NAT-reversed**: the emulator sits behind Basilisk II's MACNAT, so the **daemon dials OUT** to the host; the host never connects in.
4. **Apple Events layer** — the daemon ↔ ToolServer / MPW Shell.

The retired Swift `MacintoshBridgeHost` was replaced long ago by the stdlib-only Python `host_server.py`; if you find references to a Swift app or an Xcode build, they are stale.

## Prerequisites

### Host system (macOS)

- **macOS 12.0+** (developed on Sequoia).
- **Basilisk II** emulator, configured and running.
- **System `python3`** (`/usr/bin/python3`) — the host server is stdlib-only and is deliberately run under system Python, because the macOS firewall blocks the un-allowlisted `uv`/venv binary from accepting connections.
- **uv** — `brew install uv` — used to launch the MCP server (`uv run python -m mcp.server`).
- **Claude Code**, installed and configured.

### Mac emulator (Basilisk II)

- **System** Mac OS 7.6.1 (recommended).
- **Open Transport** 1.1.1 or later (MacTCP also works via the transport seam).
- **MPW** with the SC compiler, and **ToolServer** — ToolServer (`'MPSX'`) is required for command-output capture; MPW Shell (`'MPS '`) runs commands but returns empty AE replies.
- **Memory** 64 MB RAM minimum.

## Part 1: Basilisk II and the host network

### 1.1 Emulator networking (MACNAT, not slirp)

Use Basilisk II's **`etherhelper`** Ethernet backend (MACNAT), not slirp. In `~/.basilisk_ii_prefs`:

```
ether etherhelper/en8
```

The guest sits behind MACNAT: it can only connect **out**, its outbound traffic is NAT'd through the host's **default-route** interface (normally Wi-Fi `en0`), and it is **never pingable**. The daemon dials the hard-coded host address **`192.168.3.154`**.

**The rule that avoids a freeze:** the host's `.154` alias must live on the **same interface the NAT exits** — the default-route interface (`en0`). If `.154` is aliased on a second NIC, the MACNAT return path is split across interfaces, the daemon's connect blocks, and the emulator freezes at 100 % CPU (it looks like a crash; it isn't). Do **not** pre-create a bridge — `etherhelpertool` owns the wired interface directly.

`host/start_stack.sh` sets all of this up for you (see Part 2). Background: <https://pit.390er.de/applebridge/anatomy-of-a-freeze-macnat-return-path/>.

> A **slirp** backend was benchmarked as an alternative but rejected: it improves latency but regresses bulk throughput ~80 % on this Basilisk build's legacy slirp. `etherhelper/en8` stays the default. See the transport benchmark notes.

### 1.2 Shared folder

Enable the Basilisk II Unix volume so files can move between host and guest:

```
Unix Root: /Users/pitforster/Desktop/Share
```

It appears as the `Unix:` volume on the Mac and is **read-only** from the Mac side, so source is copied to local storage before compiling.

### 1.3 Install MPW and ToolServer

1. Copy the MPW folder to the Mac's hard drive (e.g. `MeinMac:MPW:`).
2. Verify MPW Shell launches (a worksheet window appears).
3. Launch **ToolServer** — it runs in the background with no window, and **must** be running for command output to come back over the bridge.
4. Verify the libraries are present: `Files "MeinMac:Interfaces&Libraries:Libraries:Libraries:"` should list `Interface.o`, `MacRuntime.o`, `OpenTransport.o`, etc.

With autostart installed (Part 3), the daemon **chain-launches ToolServer** itself on boot, so this becomes a one-time verification.

## Part 2: The host server and MCP

There is no host app to build — the host is Python.

### 2.1 Start the stack

```bash
cd host
./start_stack.sh
```

`start_stack.sh` aliases `.154` onto the default-route interface (the freeze-avoidance rule above, via one admin prompt), (re)starts `host_server.py`, and launches Basilisk II. The host server also **auto-starts via launchd** on login, so on a configured machine the bridge comes up on its own.

> **Startup order does not matter.** If the daemon comes up first it simply finds
> nothing listening, logs the reason in its Verbose console, and connects on its
> next retry — at most ~40 s later (a 10 s bounded connect plus 30 s of backoff).
> Nothing needs restarting or rebooting.
>
> This used to be documented as a hard rule ("the daemon's early connects foul the
> socket"). No socket was ever fouled: a blocking `accept()` on `:9000` sat ahead of
> the control-port loop, so while no daemon was connected the server never serviced
> `:9001` and *every* tool hung — which looked like damage caused by the startup
> order. Fixed in PR #75; see the
> [root-cause write-up](https://pit.390er.de/applebridge/blocking-accept-disables-diagnostic-channel/).
> `mac_status` now answers whether or not the daemon is up, so it is the right first
> question when the bridge seems dead.

Server log: `/tmp/applebridge_server.log`. Smoke test once the daemon is connected:

```bash
cd host && /usr/bin/python3 send_command.py 'Echo HELLO'   # -> STATUS:0 ... HELLO
```

### 2.2 Register the MCP server

The repo ships a project `.mcp.json` that registers the server over stdio:

```json
{
  "mcpServers": {
    "applebridge": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "python", "-m", "mcp.server"],
      "env": {}
    }
  }
}
```

Verify Claude Code sees it:

```bash
claude mcp list      # -> applebridge (30 tools)
```

The 30 tools cover driving builds and reading output (`mpw_execute`, `mac_compile`, `mac_build`, `mac_read_file`, `mac_list_files`, `mac_send_apple_event`), moving bytes and interacting (`mac_put_file` / `mac_get_file`, `mac_write_file`, `launch_app`, `mac_screenshot`, `mac_type` / `mac_key` / `mac_menu` / `mac_menu_front` / `mac_click`, `mac_clipboard_get` / `mac_clipboard_set`), driving the guest's real mouse for menus and modal dialogs (`mac_host_click`, `mac_host_menu`, `mac_host_screenshot`), network discovery (`mac_appletalk_browse`), and lifecycle/liveness (`mac_status`, `bridge_doctor`, `mac_verbose_log`, `mac_reboot`, `mac_shutdown`, `mac_restart_toolserver`, `mac_update_daemon`, `run_applescript`). New tools register on the next MCP-server restart.

## Part 3: Build and deploy the Mac daemon

### 3.1 Transfer the source

Convert the daemon source from host UTF-8/LF to Mac MacRoman/CR and copy it over:

```bash
cd host
uv run python encoding_convert.py to-mac ../mac/src/     /Users/pitforster/Desktop/Share/src/
uv run python encoding_convert.py to-mac ../mac/include/ /Users/pitforster/Desktop/Share/include/
```

On the Mac, copy from the read-only `Unix:` volume to local storage (the `mac_put_file` MCP tool can also stream files directly, type `TEXT` / creator `'MPS '`):

```
Duplicate -y Unix:src:     MeinMac:MPW:AppleBridge:src:
Duplicate -y Unix:include: MeinMac:MPW:AppleBridge:include:
```

### 3.2 Host IP

The host IP is read from the **`AppleBridge Prefs`** file (`IP=192.168.3.154`), editable in-place or via the **AppleBridgeConfig** control app — no source edit needed. A compiled-in fallback (`.154`) keeps a fresh build reachable if the prefs file is missing.

### 3.3 Build

In MPW / ToolServer, with the library path set:

```
Set LIBS "MeinMac:Interfaces&Libraries:Libraries:"
Directory MeinMac:MPW:AppleBridge:
Make -f Makefile.68k > BuildIt; BuildIt
```

`Make` only *prints* the build commands, so write them to `BuildIt` and execute it. The Makefile compiles each source (`main.c`, the `transport.c` / `transport_ot.c` / `transport_mactcp.c` seam that replaced the old `network.c`, `protocol.c`, `command.c`, `fileio.c`, `events.c`, `auth.c`, …), links with `Link -model far`, merges the `SIZE` resource with `Rez AppleBridge_res.r` (required for Apple Events — without it every command fails with `-903`), and sets the type/creator (`APPL` / `'ABrg'`). Result: `:bin:AppleBridge`.

> Build off the running daemon: link to `:bin:AppleBridge.new` and swap it in, rather than overwriting a running binary.

### 3.4 Deploy and autostart

Run the **AppleBridge Installer** to fork-aware-copy the binary to its deployed home (`HOME=` in prefs, e.g. `MeinMac:AppleBridge:`) and install autostart. On a cold boot the **watchdog** (in Startup Items) then launches the daemon, which chain-launches ToolServer — the whole bridge comes up invisibly. See `mac/config/README.md`.

## Part 4: Test with Claude Code

Ask Claude to run a command:

```
You: Run 'Directory' on the Mac.
```

Claude calls `mcp__applebridge__mpw_execute`, which reaches the daemon and returns `MeinMac:MPW:AppleBridge:`. On the daemon's monitor window (or menu-bar LED) you'll see RX/TX activity. Then try a build:

```
You: Build a Hello World app and run it on System 7.6.1.
```

Claude writes the C, compiles (`SC`), links, sets the file type, and launches it — a classic Mac window appears in the emulator.

Check liveness at any time with the `mac_status` tool, or:

```bash
cd host && /usr/bin/python3 send_command.py 'Echo HELLO'
```

## Reference

### MPW libraries

The daemon links against:

| Library | Purpose |
|---------|---------|
| `OpenTransport.o` | TCP/IP networking (OT backend) |
| `OpenTransportApp.o` | OT application support |
| `OpenTptInet.o` | Internet protocols |
| `Interface.o` | Toolbox trap definitions (also resolves MacTCP driver calls) |
| `MacRuntime.o` | C runtime initialization |
| `StdCLib.o` | Standard C library (from `CLibraries:`) |

Folder layout: `MeinMac:Interfaces&Libraries:Libraries:` contains `Libraries:` (`Interface.o`, `MacRuntime.o`, `OpenTransport*.o`, …) and `CLibraries:` (`StdCLib.o`, …). Reference them as `"{LIBS}Libraries:Interface.o"` and `"{LIBS}CLibraries:StdCLib.o"` with `Set LIBS "MeinMac:Interfaces&Libraries:Libraries:"`.

### Wire protocol (v0.1 core)

**Request (host → daemon)** — length-framed:

```
COMMAND:<length>\n<command_text>
```

**Response (daemon → host)** — read by declared length; CR-terminated fields:

```
STATUS:<code>\rSTDOUT:<len>\r<stdout>\rSTDERR:<len>\r<stderr>\r\r
```

Since v0.2 the session opens with a `HELLO:` version-negotiation handshake (and optional token authentication); a v0.1 peer that doesn't understand it falls back to legacy cleanly. Full detail in [PROTOCOL_v0.2.md](PROTOCOL_v0.2.md).

**Screenshot** — request `SCREENSHOT`; the daemon streams the raw main-GDevice pixmap, which the host decodes to PNG:

```
IMAGE:<width>:<height>:<depth>:<rowBytes>:<clutCount>:<dataSize>\n<CLUT><pixels>
```

### Character encoding

Files crossing between host and Mac are converted by `host/encoding_convert.py` (UTF-8↔MacRoman, LF↔CR):

| Character | UTF-8 | MacRoman | Usage |
|-----------|-------|----------|-------|
| ∂ (partial) | E2 88 82 | B6 | MPW line continuation |
| ƒ (florin) | C6 92 | C4 | MPW dependency marker / folder |
| ≈ (approx) | E2 89 88 | C5 | MPW wildcard |
| … (ellipsis) | E2 80 A6 | C9 | Option-; |

Line endings: Mac Classic uses CR (`\r`), Unix/macOS uses LF (`\n`).

### MPW Makefile syntax

- **Dependency marker:** `ƒ` (Option-F), not `::`.
- **Line continuation:** `∂` (Option-D).
- **Variables:** `Set LIBS "…"`, referenced as `"{LIBS}Libraries:Interface.o"`.
- Use **TAB** for command indentation, not spaces.
- `Make` prints commands; run them with `Make -f Makefile.68k > BuildIt; BuildIt`.

## Advanced

### Standalone / interactive host server

For debugging without MCP, run the server from a TTY (system Python):

```bash
cd host && /usr/bin/python3 host_server.py
Command> Directory
```

### Screenshot over the control port

```bash
printf 'screenshot\n\n' | nc localhost 9001   # returns a base64-PNG frame
```

`host/screenshot_decode.py` (pure stdlib) decodes the raw pixmap to PNG.

### Serial transport (experimental — reaches Ethernet-less Macs)

For a Mac with no Ethernet (Plus, SE, Classic), the daemon can run over a serial
line instead of TCP. Set `NET=Serial` (optionally `PORT=A|B`, `BAUD=`) in the
daemon prefs, and start the host server in serial mode:

```bash
APPLEBRIDGE_SERIAL=/dev/tty.usbserial-XXXX APPLEBRIDGE_BAUD=57600 /usr/bin/python3 host/host_server.py
```

To test it with **no hardware**, `host/serial_harness.py` creates a lossless pty
bridge: point Basilisk's `seriala` at one path it prints and
`APPLEBRIDGE_SERIAL` at the other. Full design, framing, and the reliability
caveats for real hardware are in [SERIAL_TRANSPORT.md](SERIAL_TRANSPORT.md).

## See also

- [README.md](../README.md) — intro and examples
- [ARCHITECTURE.md](../ARCHITECTURE.md) — full design
- [PROTOCOL_v0.2.md](PROTOCOL_v0.2.md) — the wire protocol and its v0.2 revision
- [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) — failure modes and fixes
- [ASSEMBLY_TEMPLATE.md](../ASSEMBLY_TEMPLATE.md) — 68k assembly guide
- Build recipes, trap defs, encoding tables — the user's global `~/.claude/CLAUDE.md`

---

**Last Updated:** July 2, 2026
**Target:** AppleBridge v0.7.0+ (protocol v0.2), Python host via launchd, 20 MCP tools
