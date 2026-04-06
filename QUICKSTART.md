# Quick Start Guide for Your Basilisk II Setup

Since you already have Basilisk II with System 7.6.1 and TCP/IP configured, here's the fastest path to getting AppleBridge running.

## Prerequisites Check

You have:
- ✓ Basilisk II emulator
- ✓ System 7.6.1
- ✓ TCP/IP configured

You need:
- [ ] MPW installed in Basilisk II
- [ ] Python 3.9+ on host
- [ ] Claude API key

## Step 1: Install MPW (if not already installed)

### IMPORTANT: MPW Version Requirement

**System 7.6.1 requires MPW 3.5 or later** (MPW Golden Master)

- ✗ MPW 3.1, 3.2 - TOO OLD, won't work
- ✓ MPW 3.5, Golden Master, MPW Pro - These work!

### Option A: Use Your MPW CD-ROM

You have `MPW CD-ROM.iso` in your shared folder! This should contain a compatible version.

1. **Mount in Basilisk II**:
   - Add `MPW CD-ROM.iso` as CD-ROM in Basilisk II preferences
   - Or mount on host and copy MPW folder to shared folder

2. **See detailed instructions**:
   - Check `MPW_INSTALL_INSTRUCTIONS.txt` in your shared folder

### Option B: Download MPW Golden Master

1. **Download MPW**:
   - MPW Golden Master: https://macintoshgarden.org/apps/macintosh-programmers-workshop
   - Look for version 3.5 or "Golden Master"

2. **Transfer to Basilisk II**:
   - Mount the downloaded disk image in Basilisk II
   - Copy MPW folder to your hard drive (e.g., `HD:MPW:`)

3. **Test MPW**:
   - Double-click MPW Shell
   - Type: `echo "Hello MPW"`
   - If you see "Hello MPW" output, you're good!

## Step 2: Transfer AppleBridge Mac Code

You need to get the `mac/` directory contents into Basilisk II:

### Method 1: Shared Folder (Recommended)
You have a shared folder configured at `/Users/pitforster/Desktop/Share`

1. **Copy files to shared folder** (automated):
   ```bash
   ./copy_to_basilisk.sh
   ```

   Or manually:
   ```bash
   cp -r mac/* /Users/pitforster/Desktop/Share/AppleBridge/
   ```

2. **In Basilisk II**, the shared folder should appear on the desktop
3. Copy from shared folder to `HD:MPW:AppleBridge:`

### Method 2: Disk Image
1. Create a disk image and copy files into it
2. Mount in Basilisk II
3. Copy to `HD:MPW:AppleBridge:`

### Method 3: FTP/Web Server
1. Start a simple web server on host:
   ```bash
   cd mac/
   python3 -m http.server 8080
   ```
2. In Basilisk II, use browser/FTP client to download files

**Target structure in Basilisk II**:
```
HD:MPW:AppleBridge:
  :include:
    applebridge.h
  :src:
    main.c
    network.c
    protocol.c
    command.c
    screenshot.c
    utils.c
  Makefile
```

## Step 3: Check Open Transport

System 7.6.1 should have Open Transport, but let's verify:

1. In Basilisk II: Apple Menu → About This Macintosh → Check for "Open Transport"
2. Or look in Extensions folder for "Open Transport" files

**If you only have MacTCP** (older TCP/IP):
- The code templates use Open Transport
- You'll need to either:
  - Install Open Transport 1.1.2 or later for System 7.6.1, OR
  - Modify the code to use MacTCP instead (more work)

## Step 4: Build the Mac Daemon

1. **Open MPW Shell** in Basilisk II

2. **Navigate to project**:
   ```
   Directory HD:MPW:AppleBridge:
   ```

3. **Create build directories**:
   ```
   NewFolder :obj:
   NewFolder :bin:
   ```

4. **Attempt build**:
   ```
   make
   ```

5. **Expect compilation errors on first try!**
   - This is normal - the code is a template
   - Common issues:
     - Missing header paths
     - `system()` function not available
     - Time functions compatibility

## Step 5: Fix Command Execution (Critical)

The biggest issue will be in `command.c`. The template uses a placeholder.

**Quick fix for testing**: Replace the `ExecuteCommand` function in `command.c` with this minimal version:

```c
BridgeResult ExecuteCommand(const char *command, CommandResult *result)
{
    LogMessage("Executing command:");
    LogMessage(command);

    result->exitCode = 0;

    /* For now, just echo back the command */
    strcpy(result->stdout, "Command received: ");
    strncat(result->stdout, command, MAX_RESPONSE_LENGTH - 100);
    result->stderr[0] = '\0';

    LogMessage("Command completed (echo mode)");
    return kBridgeNoErr;
}
```

This will let you test the TCP connection without implementing full MPW integration yet.

## Step 6: Run the Mac Daemon

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

3. **Leave it running!**

## Step 7: Set Up Host Side

1. **Install dependencies**:
   ```bash
   cd host/
   uv pip install anthropic pillow
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   ```

   Edit `.env`:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-your-key-here
   APPLEBRIDGE_MAC_HOST=localhost  # or Mac IP if different
   APPLEBRIDGE_MAC_PORT=9000
   ```

## Step 8: Test Connection

```bash
uv run tests/test_connection.py
```

**Expected**: Should connect and receive echoed command

**If connection refused**:
- Check Mac daemon is running
- Verify port forwarding in Basilisk II settings
- Try using Mac's actual IP instead of localhost
- Check firewall settings

## Step 9: Test with Claude

```bash
export ANTHROPIC_API_KEY=your-key-here  # or set in .env
uv run main.py --test
```

If connection test passes:
```bash
uv run main.py
```

Try:
```
You: Hello! Can you execute the command "Directory"?
```

Claude should use the tool, and you'll see activity in the Mac daemon window!

## Troubleshooting

### "Failed to initialize Open Transport"
- System 7.6.1 should have it, but check Extensions folder
- May need to install Open Transport 1.1.2+

### "Connection refused" from host
1. Get Mac IP: TCP/IP Control Panel
2. Test direct connection:
   ```bash
   telnet <mac_ip> 9000
   ```
3. If telnet connects, the daemon is working!

### Compilation errors in MPW
- Check that MrC compiler is available: `which MrC`
- Verify libraries are in correct paths
- Check MPW version (needs PowerPC tools for Basilisk II 68k)
  - Actually, Basilisk II is 68k, so you might need SC/SCpp instead of MrC!

**IMPORTANT**: Basilisk II emulates 68k, not PowerPC!
- Change compiler in Makefile from `MrC` to `SC` or `SCpp`
- Change linker from `PPCLink` to `Link`
- Adjust library paths for 68k

## Next Steps After Basic Test Works

1. Implement proper MPW command execution (see NEXT_STEPS.md)
2. Test screenshot capture
3. Optimize protocol
4. Have fun with Claude + vintage Mac!

## Need Help?

- Check full NEXT_STEPS.md for detailed implementation guidance
- Review SETUP.md for comprehensive setup
- Check protocol/PROTOCOL.md for protocol details
