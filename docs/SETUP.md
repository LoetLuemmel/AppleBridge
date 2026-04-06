# AppleBridge Setup Guide

Complete setup instructions for getting AppleBridge running.

## Prerequisites

### Host System

- Python 3.9 or later
- `uv` package manager (recommended) or `pip`
- Network access to Mac emulator

### Mac Emulator

- SheepShaver or Basilisk II emulator
- Mac OS 7.5.3 or later (for Open Transport)
- MPW (Macintosh Programmer's Workshop) installed
- TCP/IP networking configured

## Part 1: Mac Emulator Setup

### 1.1 Install and Configure Emulator

**SheepShaver** (recommended for PowerPC):
- Download from https://sheepshaver.cebix.net/
- Create a Mac OS 9 disk image
- Install Mac OS 8.1 or later

**Basilisk II** (for 68k):
- Download from https://basilisk.cebix.net/
- Create a System 7.5.3+ disk image

### 1.2 Configure Networking

1. In emulator preferences, enable networking:
   - **SheepShaver**: Enable "Enable Ethernet" in preferences
   - **Basilisk II**: Set "Ethernet Interface" to "slirp"

2. Boot the emulated Mac

3. Open TCP/IP Control Panel:
   - Apple Menu → Control Panels → TCP/IP
   - Connect via: Ethernet
   - Configure: Using DHCP Server (or manually if needed)
   - Note the IP address (usually 10.0.2.15 with slirp)

4. Test connectivity:
   - From Mac: Try to ping host (if ping tool available)
   - From host: `ping <mac_ip_address>`

### 1.3 Install MPW

1. Download MPW from:
   - Apple's legacy software site
   - https://macintoshgarden.org/

2. Install MPW to your Mac hard drive
   - Recommended: `HD:MPW:`

3. Launch MPW and verify it works:
   ```
   echo "MPW is working"
   ```

## Part 2: Build Mac Daemon

### 2.1 Transfer Source Code to Mac

1. Copy the `mac/` directory contents to emulated Mac
   - Use shared folders (if emulator supports it)
   - Or use FTP/HTTP server
   - Or mount disk image and copy files

2. Place files in MPW directory structure:
   ```
   HD:MPW:AppleBridge:src:
   HD:MPW:AppleBridge:include:
   HD:MPW:AppleBridge:Makefile
   ```

### 2.2 Compile the Daemon

1. Open MPW Shell

2. Set directory:
   ```
   Directory HD:MPW:AppleBridge:
   ```

3. Create build directories:
   ```
   make dirs
   ```

4. Build the daemon:
   ```
   make
   ```

5. Check for errors. If successful:
   ```
   HD:MPW:AppleBridge:bin:AppleBridge
   ```
   should exist

### 2.3 Run the Daemon

1. In MPW Shell:
   ```
   bin:AppleBridge
   ```

2. You should see:
   ```
   === AppleBridge Mac Daemon ===
   Version 0.1.0
   Listening on port 9000

   Server ready. Waiting for connections...
   ```

3. Leave it running

## Part 3: Host Setup

### 3.1 Install Python Dependencies

Using `uv` (recommended):
```bash
cd host/
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -e .
```

Or using pip:
```bash
cd host/
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3.2 Configure Environment

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-xxx  # Your Claude API key
   APPLEBRIDGE_MAC_HOST=localhost  # Or Mac IP if different
   APPLEBRIDGE_MAC_PORT=9000
   ```

3. Load environment:
   ```bash
   source .env  # Or use direnv
   ```

### 3.3 Test Connection

```bash
uv run tests/test_connection.py
```

Expected output:
```
Testing TCP connection to localhost:9000...
Connecting...
Connected! Sending test command...
Waiting for response...

Response received:
STATUS:0
STDOUT:16
Connection test

STDERR:0


✓ Connection test PASSED
```

## Part 4: Run AppleBridge

### 4.1 Test Single Command

```bash
cd host/
uv run main.py --command "echo 'Hello from MPW'"
```

### 4.2 Interactive Mode

```bash
uv run main.py
```

You'll see:
```
=== Claude + MPW Bridge ===
Type your messages to Claude. Commands:
  /screenshot - Include screenshot with next message
  /quit - Exit session

You: _
```

### 4.3 Try It Out

```
You: Can you list the files in the current directory?
```

Claude will use the `execute_mpw_command` tool to run the MPW command and show you the results!

## Troubleshooting

### Mac Daemon Won't Start

**Error: "Failed to initialize Open Transport"**
- Ensure you're running System 7.5.2 or later
- Check that Open Transport is installed
- Try rebooting the emulated Mac

**Error: "Failed to bind socket"**
- Port 9000 may be in use
- Change `BRIDGE_PORT` in applebridge.h
- Recompile

### Connection Refused from Host

1. Verify Mac daemon is running
2. Check emulator networking:
   - Try pinging Mac from host
   - Check firewall settings
3. Verify port forwarding if using NAT

### Commands Not Executing

1. Check MPW is in the path
2. Verify command syntax for MPW (not Unix)
3. Check daemon logs for errors

### Screenshot Fails

Screenshots require:
- QuickDraw properly initialized
- Sufficient memory
- Valid screen device

If screenshots fail, basic command execution should still work.

## Next Steps

- Read PROTOCOL.md for protocol details
- See USAGE.md for usage examples
- Check DEVELOPMENT.md for contributing

## Getting Help

- Check logs on both host and Mac
- Enable debug mode: `APPLEBRIDGE_DEBUG=true`
- Review emulator documentation
- Consult MPW documentation for command syntax
