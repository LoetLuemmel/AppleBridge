# AppleBridge RX/TX LED Health Monitoring

**Date:** 2026-04-11
**Status:** Implemented and Working

## Overview

Enhanced AppleBridge Client with visual RX/TX activity indicators to diagnose communication breakdowns between MacintoshBridgeHost and the Mac daemon.

## Problem Statement

AppleBridge communication would fail silently with no visibility into:
- Whether commands were reaching the Mac daemon
- Whether responses were being sent back
- Where in the communication chain failures occurred

MacintoshBridgeHost logs showed:
```
Command forwarded to Mac daemon, waiting for response...
Control connection cancelled
Response timeout - no response from Mac daemon
```

But we couldn't tell if the Mac side was receiving commands or if execution was stalling.

## Solution: Visual RX/TX LEDs

Added LED-style indicators to the AppleBridge Client status window:

### Features

**Visual Indicators:**
- **RX LED (left)**: Flashes black/green when command received
- **TX LED (right)**: Flashes dark gray/red when response sent
- **Counters**: Shows "RX:n TX:n" with total counts
- **Flash Duration**: ~0.5 seconds (30 ticks)
- **Inactive State**: Light gray

**Activity Tracking:**
- Tracks timestamp of last RX/TX activity
- Increments counters on each event
- Updates every 0.5 seconds via `ShowAlive()`

**Integration Points:**
- RX marked in `ProcessRequest()` when command arrives
- TX marked after `SendData()` completes
- LEDs drawn at top of status window (preserved during updates)

## Implementation

### Modified Files

**mac/src/main.c** - Enhanced with LED code:
- Added global variables: `gLastRX`, `gLastTX`, `gRXCount`, `gTXCount`
- New function: `DrawLEDs()` - Renders LED indicators
- Modified: `ProcessRequest()` - Marks RX/TX activity
- Modified: `ShowAlive()` - Calls `DrawLEDs()` to update display
- Modified: `CheckUserAbort()` - Redraws LEDs on update events

### Key Code Sections

**LED Drawing (main.c:76-147):**
```c
void DrawLEDs(void)
{
    Rect rxLED, txLED;
    long now = TickCount();
    Boolean rxActive, txActive;

    // RX LED - Green when active
    rxActive = (now - gLastRX) < LED_FLASH_DURATION;
    if (rxActive) {
        PenPat((Pattern *)&qd.black);  // Solid black = green
    } else {
        PenPat((Pattern *)&qd.ltGray);
    }
    PaintRect(&rxLED);

    // TX LED - Red when active
    txActive = (now - gLastTX) < LED_FLASH_DURATION;
    if (txActive) {
        PenPat((Pattern *)&qd.dkGray);  // Dark gray = red
    } else {
        PenPat((Pattern *)&qd.ltGray);
    }
    PaintRect(&txLED);

    // Draw "RX:n TX:n" counter
}
```

**Activity Marking (main.c:413-481):**
```c
void ProcessRequest(EndpointRef endpoint, char *request, long requestLen)
{
    // Mark RX activity
    gLastRX = TickCount();
    gRXCount++;

    // ... process command ...

    // Mark TX activity
    gLastTX = TickCount();
    gTXCount++;
}
```

## Build Instructions

**From host:**
```bash
# Convert source to MacRoman
uv run python host/encoding_convert.py to-mac main_leds.c main_mac.c

# Copy to Mac
# (Via shared folder Unix:main_mac.c)
```

**On Mac (MPW Shell):**
```
Duplicate -y Unix:main_mac.c MeinMac:MPW:AppleBridge:src:main.c
Directory MeinMac:MPW:AppleBridge:
Make -f Makefile.68k > BuildIt
BuildIt
```

## Diagnostic Value

### Communication Breakdown Detection

**Scenario 1: Command reaches Mac, no response**
- RX LED flashes green ✓
- TX LED stays gray ✗
- **Diagnosis:** ToolServer crashed or command execution stalled

**Scenario 2: No command reaches Mac**
- RX LED stays gray ✗
- TX LED stays gray ✗
- **Diagnosis:** Network connection lost or MacintoshBridgeHost not forwarding

**Scenario 3: Normal operation**
- RX LED flashes green ✓
- TX LED flashes red ✓
- **Diagnosis:** Communication working normally

### Real-World Usage

During testing on 2026-04-11, the LEDs revealed:
1. Commands **were** reaching the Mac daemon (RX flashing)
2. Responses **were not** being sent (TX staying gray)
3. Root cause: ToolServer had crashed, not network issues

This saved significant debugging time by pinpointing the exact failure point.

## Future Enhancements

Possible improvements:
1. **Heartbeat/Ping**: Periodic health check command
2. **Error counter**: Track failed commands separately
3. **Last command display**: Show most recent command text
4. **Response time**: Display average command latency
5. **Connection quality meter**: Visual indicator of health

## Version History

- **v0.3.0** (2026-04-11): Initial RX/TX LED implementation
  - Visual activity indicators
  - RX/TX counters
  - Color-coded LEDs (green/red simulation)

## Credits

**Built by:** Pit with Claude
**Purpose:** Diagnose communication failures in AppleBridge
**Result:** Successfully identified ToolServer crashes vs network issues

---

**"Now we can see when the bridge breaks"** 🚦
