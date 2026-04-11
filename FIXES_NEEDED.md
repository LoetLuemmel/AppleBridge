# AppleBridge System - Issues and Proposed Fixes

**Date:** 2026-04-11
**Status:** Mac daemon working, host-side response forwarding needs implementation

## Summary

The AppleBridge Mac daemon (client) is working correctly and can execute MPW commands via Apple Events to ToolServer. However, **MacintoshBridgeHost is not forwarding responses back to MCP clients**.

---

## Issues Found

### 1. LocalControlServer Not Using CommandHandler

**File:** `MacintoshBridgeHost/MacintoshBridgeHost/LocalControlServer.swift`
**Location:** Lines 268-310 (forwardToMacDaemon method)

**Problem:**
- LocalControlServer sends commands directly to TCPServer using `macServer.send()`
- It does NOT use `CommandHandler.shared.sendCommand()`
- This bypasses the response handling infrastructure
- Responses from Mac daemon are never forwarded back to MCP clients

**Current Code (line 292):**
```swift
macServer.send(data: packet) { error in
    if let error = error {
        self.sendResponse("ERROR:Failed to send to Mac: \(error.localizedDescription)\n\n", to: connection)
    } else {
        // Response will come back via Mac daemon connection
        // We need to capture it and forward back to MCP client
        // For now, send a simple acknowledgment
        // TODO: Implement proper response forwarding
        print("Command forwarded to Mac daemon, waiting for response...")
    }
}
```

**Proposed Fix:**
```swift
// Store reference to control connection for response forwarding
let controlConnection = connection

CommandHandler.shared.sendCommand(command) { response in
    // Forward the response back to the MCP client
    self.sendResponse(response, to: controlConnection)
}
```

---

### 2. Character Encoding in MCP Connection

**File:** `mcp/mac_connection.py`
**Location:** Line 88

**Problem:**
- Commands were being sent as UTF-8 instead of MacRoman
- Mac expects MacRoman encoding

**Status:** ✅ **FIXED**
- Changed from `.encode('utf-8')` to `.encode('mac_roman')`

---

### 3. Debug Logging Flooding UI

**File:** `mac/src/command.c` (original debug version)

**Problem:**
- Excessive `StatusMessage()` and `DebugLog()` calls
- Flooded AppleBridge Client UI window
- Made the app unresponsive

**Status:** ✅ **FIXED**
- Created clean production version with zero debug logging
- All debug calls removed
- File ready at: `mac/src/command.c`

---

### 4. Struct Member Name Mismatch

**File:** `mac/src/command.c`

**Problem:**
- Used `result->stdout` and `result->stderr`
- Actual struct members are `result->outData` and `result->errData`

**Status:** ✅ **FIXED**
- Updated to use correct member names
- File ready at: `mac/src/command.c`

---

## Architecture Overview

### Current Flow (Working Parts)

```
MCP Client
    ↓ (TCP 9001)
LocalControlServer ← ← ← ← ← [BROKEN: No response forwarding]
    ↓
    ↓ (direct send)
TCPServer (port 9000)
    ↓ (TCP)
Mac Daemon (AppleBridge)
    ↓ (Apple Events)
ToolServer
    ↓ (executes command)
    ↓ (returns output via AE)
Mac Daemon
    ↓ (formats response: STATUS/STDOUT/STDERR)
    ↓ (sends back)
TCPServer
    ↓ (receives response)
CommandHandler.handleMacDaemonResponse() ← Response arrives here but...
    ↓
[DEAD END: No handler because LocalControlServer didn't use CommandHandler]
```

### Correct Flow (After Fix)

```
MCP Client
    ↓ (TCP 9001)
LocalControlServer
    ↓
CommandHandler.sendCommand(command, completionHandler) ← Stores handler!
    ↓
TCPServer (port 9000)
    ↓ (TCP)
Mac Daemon (AppleBridge)
    ↓ (Apple Events)
ToolServer
    ↓ (executes command)
    ↓ (returns output via AE)
Mac Daemon
    ↓ (formats response: STATUS/STDOUT/STDERR)
    ↓ (sends back)
TCPServer
    ↓ (receives response)
CommandHandler.handleMacDaemonResponse()
    ↓ (calls stored completionHandler)
LocalControlServer
    ↓ (sends response to MCP client)
MCP Client ← Response received! ✓
```

---

## Implementation Details

### CommandHandler Infrastructure (Already Exists!)

The CommandHandler already has all the necessary infrastructure:

**File:** `MacintoshBridgeHost/MacintoshBridgeHost/CommandHandler.swift`

**Key Components:**
1. **Pending response handler storage** (line 90):
   ```swift
   pendingResponseHandler = completion
   ```

2. **Response reception** (line 120):
   ```swift
   func handleMacDaemonResponse(_ response: String) {
       responseQueue.sync {
           if let handler = pendingResponseHandler {
               handler(response)  // ← Calls the completion handler!
               pendingResponseHandler = nil
           }
       }
   }
   ```

3. **Timeout handling** (30 seconds)

All that's needed is for LocalControlServer to **use** this infrastructure!

---

## Proposed Changes

### LocalControlServer.swift

**Method:** `forwardToMacDaemon(_ command:from:)`

**Replace lines 268-310 with:**

```swift
private func forwardToMacDaemon(_ command: String, from connection: NWConnection) {
    guard let macServer = macDaemonServer else {
        sendResponse("ERROR:Mac daemon not connected\n\n", to: connection)
        return
    }

    // Check if Mac is connected
    guard macServer.connection != nil else {
        sendResponse("ERROR:Mac daemon not connected\n\n", to: connection)
        return
    }

    // Use CommandHandler to send command and handle response
    CommandHandler.shared.sendCommand(command) { [weak self] response in
        guard let self = self else { return }

        // Forward Mac daemon's response back to MCP client
        self.sendResponse(response, to: connection)
    }
}
```

**That's it!** The entire fix is just using CommandHandler instead of direct sending.

---

## Files Ready for Mac Build

### Fixed Files (in Share/AppleBridge copy 2/):

1. **mystring.h** - Fixed size_t types (unsigned int)
2. **mystring.c** - Fixed size_t types
3. **main.c** - Added HiWord/LoWord macros
4. **command.c** - Production version (no debug logging, correct struct members)
5. **network.c** - CLIENT mode with ConnectToHost & ParseIPAddress
6. **Makefile.68k** - Fixed tabs, single-line variables, explicit file list for clean

### Build Commands (MPW):

```mpw
Directory Unix:AppleBridge copy 2:
Set LIBS "MeinMac:Interfaces&Libraries:Libraries:"

# Clean
Delete -i :obj:main.c.o :obj:network.c.o :obj:protocol.c.o :obj:command.c.o :obj:screenshot.c.o :obj:utils.c.o :obj:mystring.c.o || Set Status 0

# Compile all
SC -i :include: :src:main.c -o :obj:main.c.o
SC -i :include: :src:network.c -o :obj:network.c.o
SC -i :include: :src:protocol.c -o :obj:protocol.c.o
SC -i :include: :src:command.c -o :obj:command.c.o
SC -i :include: :src:screenshot.c -o :obj:screenshot.c.o
SC -i :include: :src:utils.c -o :obj:utils.c.o
SC -i :include: :src:mystring.c -o :obj:mystring.c.o

# Link
Link -model far -o :bin:AppleBridge :obj:main.c.o :obj:network.c.o :obj:protocol.c.o :obj:command.c.o :obj:screenshot.c.o :obj:utils.c.o :obj:mystring.c.o "{LIBS}CLibraries:StdCLib.o" "{LIBS}Libraries:OpenTransport.o" "{LIBS}Libraries:OpenTransportApp.o" "{LIBS}Libraries:OpenTptInet.o" "{LIBS}Libraries:Interface.o" "{LIBS}Libraries:MacRuntime.o"

# Add resources (CRITICAL for Apple Events!)
Rez AppleBridge_res.r -a -o :bin:AppleBridge

# Set file type
SetFile -t APPL -c '????' :bin:AppleBridge
```

---

## Testing After Fix

1. **Rebuild AppleBridge on Mac** (commands above)
2. **Restart AppleBridge Client**
3. **Rebuild MacintoshBridgeHost in Xcode** (with LocalControlServer fix)
4. **Restart MacintoshBridgeHost**
5. **Test MCP command:**
   ```python
   from mcp.tools import mpw_execute
   result = mpw_execute("Echo 'Test 123'")
   print(result)
   ```

**Expected Result:**
```python
{
    'success': True,
    'status': 0,
    'output': 'Test 123',
    'error': None
}
```

---

## Notes

- The Mac daemon (AppleBridge) is working correctly
- ToolServer integration is working correctly
- Character encoding is fixed
- Clean production code is ready
- **Only MacintoshBridgeHost Swift code needs the one-line fix**
- This is a minimal, surgical fix to an existing, well-architected system

---

## Priority

**HIGH** - This blocks all MPW command execution via MCP. However, screenshot functionality works fine since it's host-side only.
