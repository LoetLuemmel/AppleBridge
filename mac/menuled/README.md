# AppleBridgeMenuLED — a menu-bar activity LED for the faceless daemon

A System 7 extension (`'INIT'`, creator `'ABin'`) that draws a small **activity LED
in the menu bar**, top-right, just left of the system Help / Application icons:

- **dark green** — the bridge is up but idle (passive),
- **bright green** — RX/TX traffic right now (active; reverts after ~⅔ s).

AppleBridge is a faceless background service whose only face is its Control Panel.
This adds an always-visible, across-all-apps heartbeat so you can see the bridge
living without opening anything. It is the menu-bar counterpart of the daemon's old
in-window RX/TX LEDs.

## Why assembly (not C)

A standalone `'INIT'` code resource has **no A5 world**, and a menu-bar drawer must be
a resident, trap-patching system extension. MPW C cannot form a working PC-relative
reference to its own resident routines in a code resource (the A5/dead-strip "vise" —
see `../init/README.md`), and a Gestalt selector callback must live in the system heap
and be reachable PC-relative. In 68k assembly all of that is natural: `LEA label(PC),An`,
explicit byte layout, nothing dead-stripped. So this is hand-written 68k
(`applebridge_menuled.a`).

## Architecture — three parts, one shared cell

```
daemon (C)  --Gestalt('ABrg')-->  &gLastTick (resident cell in the INIT)
            --on each RX: *cell = TickCount()-->
INIT (asm)  owns gLastTick; patches DrawMenuBar + SystemTask; draws the LED,
            colour chosen from (now - gLastTick) < ~40 ticks
```

- **The INIT** (`applebridge_menuled.a`):
  - **`ABInitMain`** (must be the first routine — the System jumps to resource offset 0):
    stays resident (`DetachResource` + `HLock`, plus `resSysHeap,resLocked` from the
    linker), installs the patches, and registers the Gestalt selector.
  - **`DrawMenuBar` tail-patch** — repaint the LED right after any menu-bar redraw, so
    it survives app switches and menu changes.
  - **`SystemTask` tail-patch (throttled ~10 Hz)** — the *live* redraw: every app's event
    loop calls `SystemTask` constantly at safe app time, so the LED tracks activity
    between bar redraws.
  - **`DrawDot`** — anchors the LED to the **Help menu** and paints it (see below).
  - **`ABGestaltProc`** — Gestalt `'ABrg'` selector returning `&gLastTick`.
- **The daemon** (`../src/main.c`): at startup `Gestalt('ABrg', &addr)` caches the cell;
  on each RX it writes `*addr = TickCount()`. No extension installed → Gestalt fails →
  the write is a harmless no-op.

## Positioning — anchored to the Help menu (structure-free)

System 7's right-justified menus (Help, Keyboard, Application) are **system menus**;
there is no API for a third party to add one, so we can't let the system lay out a
"slot" for us. Instead `DrawDot`:

1. `GetMenuHandle(kHMHelpMenuID = -16490)` → the Help menu handle (the Help menu is the
   **leftmost** right-justified menu),
2. scans the current menu-bar list block (low-mem `MenuList` `$0A1C`, bounded by
   `GetHandleSize`) for that exact 4-byte handle and reads the `menuLeft` word right
   after it,
3. draws the LED `kGap` px to its left.

This needs only the documented `{menuHandle:long, menuLeft:word}` entry layout — no
guessing the internal right-group structure — and reflows automatically as icons come
and go. If the Help menu isn't present yet (early boot, or the daemon's own context),
`DrawDot` **draws nothing**, so the LED fades in only once the right-justified group
exists and never appears alone at a fixed spot.

## Three 68k gotchas solved on the way

1. **`NewGestalt` is register-based** (`D0` = selector, `A0` = ProcPtr), not a stack
   Pascal trap — the C glue hid this. Pushing params on the stack left it unbalanced and
   **wedged the boot**. Fixed to the register convention.
2. **Colour needs the colour WMgr port.** `GetWMgrPort` returns the *basic* port, where
   `RGBForeColor` snaps to the 8 classic QuickDraw colours and dark green collapses to
   black. `GetCWMgrPort` (`$AA48`) gives true 8-bit colour.
3. **Redraw starvation.** The throttle (`gLastDraw`) must be reset only on a *real* draw.
   Resetting it on every `SystemTask` — including the faceless daemon's frequent calls,
   where `DrawDot` draws nothing — starved the foreground redraw and froze the LED
   bright. `DrawDot` now resets `gLastDraw` only when it actually paints, and bails
   cheaply (one `GetMenuHandle`) in no-Help contexts.

## Build

```mpw
Directory MeinMac:MPW:AppleBridge:
Asm -model far :src:menuled.a -o :obj:menuled.a.o
Link -rt INIT=0 -m ABInitMain -ra =resSysHeap,resLocked :obj:menuled.a.o -o :bin:AppleBridgeMenuLED
SetFile -t INIT -c 'ABin' :bin:AppleBridgeMenuLED
```

No `Interface.o` / `MacRuntime.o`: every trap is declared inline with `OPWORD`, so there
are no external references and no A5 world is needed. `-rt INIT=0` makes an `'INIT'`
id-0 code resource; `-m ABInitMain` is the boot entry (must be source-first);
`-ra =resSysHeap,resLocked` loads it into the system heap, locked, so the code and its
data cells (`gLastTick`, `gLastDraw`, `oldDMB`, `oldSysTask`) stay valid all session.

Install: copy `:bin:AppleBridgeMenuLED` into `System Folder:Extensions:` and reboot.
For the daemon side, rebuild `../src/main.c` into `:bin:AppleBridge` and reboot.

## Recovery (a trap-patching INIT can wedge the boot)

1. Hold **Shift** while booting → System 7 skips extensions → remove/replace the file.
2. If Basilisk won't boot at all: **quit Basilisk**, then delete the file from the disk
   image host-side with hfsutils (this path was exercised live):

   ```bash
   hmount "/path/to/System.dmg"
   hdel ":System Folder:Extensions:AppleBridgeMenuLED"
   humount
   ```

   hfsutils `hrename` also does a fork-preserving in-image swap of the daemon binary
   while Basilisk is down (its running file is busy and can't be overwritten live).

## Files

- `applebridge_menuled.a` — the INIT (68k assembly).
- `BuildMenuLED.emu` — the build recipe.
- `../src/main.c` — the daemon-side activity stamp (`gMenuLED`, Gestalt cache, RX write).
