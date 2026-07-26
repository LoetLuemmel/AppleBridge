# AppleBridge RX/TX LED Health Monitoring

**Date:** 2026-04-11
**Status:** Implemented and Working

## Overview

Enhanced AppleBridge Client with visual RX/TX activity indicators to diagnose communication breakdowns between the host server and the Mac daemon.

## Problem Statement

AppleBridge communication would fail silently with no visibility into:
- Whether commands were reaching the Mac daemon
- Whether responses were being sent back
- Where in the communication chain failures occurred

The host server logs showed:
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
- **Diagnosis:** Network connection lost or the host server not forwarding

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

## Telemetry footer (0.8d6, 2026-07-04)

> The window shown above has since evolved: the two-LED pair became a single
> green **Active** indicator on the top bar (RX and TX always move together), the
> body became a scrolling **Verbose** console, and the previously-empty 15px band
> below the log is now a **telemetry footer** that turns the monitor into a
> diagnostic instrument. Most of the "Future Enhancements" below shipped here.

The footer (drawn in `DrawTelemetry()`, in the band `MonitorBodyRect` already
reserves, so no layout change) reads:

```
RX 35  TX 35  ERR 1  last 83ms ▮  err:launch
```

- **RX / TX / ERR** — running totals. `ERR` counts **bridge/verb-level** error
  responses (`STATUS != 0`): a failed `LAUNCH`, an auth rejection, a malformed
  request, a non-zero command `exitCode`. A guest command that itself fails (e.g.
  MPW "command not found") is **not** an error here — it is a successful bridge
  round-trip that returned the guest's output.
- **last `<n>`ms** — the last **real** command's round-trip latency (daemon
  `RX → TX`), a number *and* a colour-coded **analog health bar**: green
  (< 200 ms), amber (< 1 s), red (≥ 1 s); the bar length scales with latency
  (capped). Heartbeats are excluded (see below) so the figure reflects real work.
- **err:`<tag>`** — a short identifier for the most recent error
  (`auth` / `launch` / `quit` / `clipboard` / `badreq` / `swap` / `cmd fail` / …),
  set by a `NoteErr(tag)` helper at each error site — so the monitor says *what*
  failed, not just how often.

**Heartbeat gating (a subtle bug, twice).** The host PINGs every ~10 s and STATs
on demand; both are ~0-tick round-trips. Measuring "last latency" naively lets
those clobber the real figure to 0. The fix captures latency in `ProcessRequest`
only when the *previous* request was a real command (`gLastWasReal`), and — the
second half — `DrawTelemetry()` must only **read** `gLastLat`, never recompute it
(it runs 8×/sec off the *latest*, i.e. heartbeat, timestamps). Both were caught in
live on-device verification.

**Also exposed off-screen.** The `STAT` verb reports `err=` / `lat=` / `lasterr=`,
and `mac_status` surfaces `err_count` / `last_latency_ms` / `last_error` — so the
same telemetry is available without opening the window.

## Status and history

Not here. What is built, what is open, and when each piece landed are the
[roadmap ledger's](https://pit.390er.de/applebridge/applebridge-roadmap-ledger-progress-and-status-tracker/)
job, and `git log` is the changelog. This document explains the *mechanism* — why
the indicators exist, what each one means, and how to read them — which is the
part that stays true.

## Credits

**Built by:** Pit with Claude
**Purpose:** Diagnose communication failures in AppleBridge
**Result:** Successfully identified ToolServer crashes vs network issues

---

**"Now we can see when the bridge breaks"** 🚦
