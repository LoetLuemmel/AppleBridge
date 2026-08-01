# Journaling driver — menu-by-name & modal-dialog mouse (R&D)

Design and on-device reconnaissance for Phase 2 of
[INPUT_MODIFIERS_AND_MENUS.md](INPUT_MODIFIERS_AND_MENUS.md). See also
[[applebridge-journaling-driver-feasibility]].

## Goal

Drive the front app's UI over the bridge in the two places posted events **cannot** reach:

1. **Menu items without a Command-key equivalent** — a `MENU:<title>:<item>` verb.
   `mac_menu` today only works for items that have a Cmd-key shortcut (it posts the
   shortcut and lets `MenuKey` dispatch it). Shortcut-less items need the menu to be
   physically tracked open.
2. **Modal tracking loops** — `MenuSelect` (menu pulled down) and Standard File /
   modal-dialog mouse tracking. These spin their own `GetNextEvent`/`StillDown` loop
   that a **background daemon is not scheduled inside**, so a posted `mouseDown`/
   `mouseUp` is never seen — the loop polls the *real* (reverted) mouse instead. This
   is the same wall that forced the host-side `cliclick` workaround
   ([[applebridge-real-mouse-cliclick-modal-dialogs]]), which only works on the
   **local** Basilisk and needs host↔guest pixel calibration.

The **journaling mechanism** is the one native path that reaches inside those loops,
because the Event Manager consults the journal driver *regardless of which process or
modal loop is running*. A journaling solution therefore also works on the **remote**
SheepShaver target, with no host mouse and no calibration.

## How journaling reaches inside `MenuSelect` (the mechanism)

The classic Toolbox Event Manager has a built-in playback hook implemented as a
**device driver** (`DRVR`), controlled by two low-memory globals:

| Global | Addr | Meaning |
|--------|------|---------|
| `JournalRef`  | `$08E8` | driver refNum of the installed journaling device |
| `JournalFlag` | `$08DE` | mode: 0 = off, record vs. **playback** otherwise |

When `JournalFlag` selects playback, the ROM Event Manager — inside
`GetNextEvent`, `EventAvail`, `GetOSEvent`, `GetMouse`, `Button`, `StillDown`,
`GetKeys`, `TickCount` — issues a driver **Status/Control** call to `JournalRef`
to *obtain* the next input instead of reading the hardware. Because `MenuSelect`'s
tracking loop and Standard File's `ModalDialog` are built on those very same Event
Manager calls, a playback driver feeds them a synthesized mouse path — menu-bar
title → drag down to the item → release — **from inside the modal loop**. That is
exactly what a posted event cannot do.

So the plan is a **playback-only** journaling `DRVR`: the daemon installs it, hands
it a scripted sequence of mouse/'`what`' records for one `MENU:` request, points
`JournalRef` at it, flips `JournalFlag` to playback, lets `MenuSelect` drain the
sequence, then restores `JournalFlag` to 0. (Recording is not needed — we only
synthesize.)

## On-device reconnaissance (verified 2026-07-04, over the bridge)

Probed the live MPW Universal Interfaces on Basilisk (System 7.6.1):

- **`LowMem.h`** — has `LMGetJournalRef()` / `LMSetJournalRef()` only
  (inline `0x3EB8/0x31DF, 0x08E8` → the `$08E8` word confirmed). **No**
  `LMGet/SetJournalFlag`; `JournalFlag` (`$08DE`) must be poked directly.
- **`Events.h`** — the journaling API is **gone** from the modern Universal
  Headers (no `jcode*`, no `journalPlay`/`journalRecord`, no `JournalRec`).
- **`Events.a` / `Traps.a`** — likewise **no** journal opcode equates; `SysEqu.a`
  and `ToolEqu.a` are **not present** in this MPW at all.

**Conclusion:** the toolchain gives us the `JournalRef` accessor and nothing else.
A driver must **hardcode** `JournalFlag = $08DE` and the journal driver call
protocol (opcodes + record layout) — none of it is surfaced by the SDK. This
matches the feasibility-checkpoint note that the opcodes need Inside Macintosh Vol I.

## The driver-call protocol (RESOLVED — Inside Macintosh Vol I)

Confirmed 2026-07-04 from IM Vol I, *The Toolbox Event Manager* → "The Journal".
The Event Manager talks to the journal driver with a **`Control` call** (not
`Status`), driven entirely by `JournalFlag`:

| `JournalFlag` | `csCode` used | constant | mode |
|---------------|---------------|----------|------|
| negative      | 16 | `jPlayCtl`   | **playback** |
| positive      | 17 | `jRecordCtl` | recording |
| zero          | — (no Control call is made) | | off |

So **enabling playback = set `JournalFlag` to any negative value**; the Event
Manager then issues `Control(JournalRef, csCode=16, …)` on each poll. Restore
`JournalFlag = 0` to disable.

Before each Control call the Event Manager fills the parameter block:

- **`csParam`** (first long, offset 0 of the csParam area) — a **pointer to the
  caller's own buffer**, the place the routine wants the polled data written.
- **`csParam+4`** (next long) — a **journal code** saying which routine is asking:

| Called during | `csParam` → buffer | journal code @ `csParam+4` |
|---------------|--------------------|----------------------------|
| `TickCount`     | long word (ticks)   | `jcTickCount = 0` |
| `GetMouse`      | `Point` (4 bytes)   | `jcGetMouse  = 1` |
| `Button`        | `Boolean` (byte)    | `jcButton    = 2` |
| `GetKeys`       | `KeyMap` (16 bytes) | `jcGetKeys   = 3` |
| `GetNextEvent`  | `EventRecord`       | `jcEvent     = 4` |
| `EventAvail`    | `EventRecord`       | `jcEvent     = 4` |

**What our playback DRVR does** is therefore simple: its `Control` routine, on
`csCode == 16`, reads the long at `csParam+4`, switches on the journal code, and
**writes the synthesized value into the buffer pointed to by `csParam`** — a
`Point` for `jcGetMouse`, a `Boolean` for `jcButton`, an `EventRecord` for
`jcEvent`, etc. It does not define its own record format; it fills the caller's.

To drive a menu we only need three of the five codes: **`jcGetMouse`** (feed the
scripted cursor position), **`jcButton`** (feed the scripted button state), and
**`jcEvent`** (hand back the `mouseDown`/`mouseUp` at the scripted point/time) —
since `MenuSelect`'s tracking loop is built on `GetMouse`/`Button`/`GetNextEvent`.
`jcTickCount`/`jcGetKeys` can return live/neutral values.

(Primary source, if a re-check is ever needed: IM Vol I "The Journal", ≈ p. I-259;
also on the AppleShare dev-docs volume, [[applebridge-appleshare-cdev-source]].)

## The step-3 gate — journaling works on this ROM

The key feasibility gate is **cleared**: a playback journal DRVR *is* consulted by
the Event Manager on Basilisk II (System 7.6.1). Proven with a ToolServer test tool
(`mac/journal/jtest.c`) that installs `ABJournalDRVR`, arms playback, and reads
`Button()`:

```
Button() idle  = 0      (real: no mouse pressed)
Button() armed = 255    (driver injected TRUE via jcButton)
driver Control calls while armed = 1..2   (Event Mgr called us; csCode = 16)
RESULT: PASS - Event Manager consulted the playback journal DRVR
```

End-to-end chain proven: DRVR installs (unit 95, refNum −96) → Open self-registers
`JournalRef` → `JournalFlag < 0` makes the ROM call our Control routine with
`csCode=16` → we write the caller's buffer → `Button()` returns the injected value.

**Three empirical corrections to the paper design (all cost a build cycle):**

1. **The DRVR resource ID *is* the unit-table slot number.** System 7 `OpenDriver`
   installs the driver at `unit = resourceID`; if that ID ≥ `UnitNtryCnt`
   (`$01D2`; **96** on this machine) it returns `badUnitErr (-21)` before ever
   calling Open. Fix: give the DRVR a resource ID that is a *free* unit slot. We
   scan `UTableBase` (`$011C`) for a free high slot and use **95** →
   `Link -rt DRVR=95`. (A production install should pick the free slot at runtime.)
2. **The journal code is a WORD, not a long.** It is `csParam[2]` — a single word at
   param-block **offset 32** (`csParam+4`), value `0x0002` = `jcButton` etc. The
   earlier "long at csParam+4" made every `CMP.L` miss, so the driver never injected.
   Read it with `MOVE.W csParam+4(A0),Dn` + `CMP.W`. The buffer pointer *is* a long at
   `csParam+0` (offset 28) as documented.
3. **The DRVR resource needs `sysheap,locked,preload` attributes** (non-purgeable),
   set at link time with `-ra .ABJournal=$54`; `Link -rt` defaults to `purgeable`.

Verified build recipe for the driver:
```
Asm  ABJournal.a -o ABJournal.a.o
Link -rt DRVR=95 -sn Main=.ABJournal -ra .ABJournal=$54 -o ABJournalDRVR ABJournal.a.o
```
Test tool (MPW tool; note IntEnv.o for the integrated-environment/stdio glue):
```
SC   jtest.c -o jtest.c.o
Link -o jtest -t MPST -c 'MPS ' jtest.c.o "{LIBS}CLibraries:StdCLib.o" \
     "{LIBS}Libraries:IntEnv.o" "{LIBS}Libraries:Interface.o" "{LIBS}Libraries:MacRuntime.o"
```

**Part 2a — daemon-side install.** The
**faceless daemon** now installs the driver from its own process context via a
`JGATE` wire verb (`OpenResFile` `ABJournalDRVR` from the daemon home →
`OpenDriver` → self-register `JournalRef` → arm → `Button()`), verified live:
`jgate resRef=3574 openErr=0 drvRef=-96 jref=-96 idle=0 armed=255 calls=1 PASS`.
So a driver a *background daemon* installs is consulted by the ROM, not just one an
interactive/ToolServer process installs. (Deploy note: `ABJournalDRVR` must sit in
the daemon home, e.g. `MeinMac:AppleBridge:` — the daemon opens it by name from
there. Since 2026-07-31 the **kit and the installer carry it**, so a normal install
puts it in place: `KIT_APPS` in `host/install_bridge.py` picks it up beside the
other binaries, and `installer.c` copies it alongside them. It is **optional** on
both paths — the driver is a separate build step (`mac/journal`), and a payload
without one still yields a working bridge, only without journaling. Before that
date neither shipped it, so an install looked complete while every journal verb
answered `fnfErr`.)

**Part 2b — menu-driving.**
The daemon drives `PopUpMenuSelect` to **any** chosen item via journaling, freeze-safe:
`JMENU:200:128:150` → item 1, `:144` → 2, `:160` → 3, `:176` → 4 (item N at global
`v = 112 + 16*N`). The driver feeds `jcGetMouse` the target `Point` and, after
`<thresh>` playback calls, a synthesized **`mouseUp` `EventRecord`** via `jcEvent` to
end tracking. Two fixes made it work + safe: (1) the state block is **daemon-owned**
(a static, pointed at via `dCtlStorage`) — a self-allocating driver that bailed on a
nil block was the real hard-freeze cause; (2) release is a real `mouseUp` **event**,
not a `jcButton`-up. Reconnaissance: the tracking loop polls **all three** per
iteration (`jcGetMouse`/`jcButton`/`jcEvent`); the `mouseUp` ends it. With a valid
block the in-driver call-count safety works (self-recovers <1s), so no hard freeze —
no Time Manager needed.

**Part 2c — the real menu bar (`JABOUT`).**
The daemon journal-drives its **own** Apple-menu title on the real menu **bar** via
`MenuSelect` → item 1 → `ShowAboutBox`: the About box opened with zero synthetic input.
The trick vs the popup case is feeding a `mouseDown` at the menu **title's** screen point
via `jcEvent` *first* (so the front app enters `MenuSelect`), then tracking down the item
column and releasing with a `mouseUp`. Proves the menu-bar path end-to-end for a
shortcut-less item.

**JSF — modal Standard File driving.**
Progression:
- **d16 (commit 9151b1b) — driver *mode*.** The driver gained a mode selector: mode 0 =
  menu (feed `jcEvent` `null`→`mouseUp` mid-track); **mode 1 = dialog click** (feed a
  `mouseDown` *then* `mouseUp` — a modal button needs a real mouse-DOWN, unlike a
  `MenuSelect` entered mid-track). `JSF` opens a `StandardGetFile` and journal-clicks a point.
- **d17 (commit aef3c95) — deterministic coords via a dlgHook.** `SFGetFile` + an
  `SFCoordHook` that reads the target item's rect (`GetDialogItem`) → `LocalToGlobal` →
  redirects the journal's target to that button's **live guest-global centre** — no
  coordinate guessing. `JSF:<thresh>:<item>` (item 1 = Open, 3 = Cancel). The hook reports
  the resolved coord back in the reply.
- **d18 (commit e348e26) — interrupt-time watchdog (`JSAFE`).** A `ModalDialog` that misses
  its click **spins at 100% CPU without polling the journal**, so the in-driver call-count
  safety can never disarm it — the freeze that forced two hard-kills. Fix: a **Time Manager**
  task (`DisarmTMProc`, primed via `ArmJournalWatchdog(ms)` before arming) zeroes
  `JournalFlag` (`$08DE`) **at interrupt time** regardless of the frozen main loop. It touches
  only the fixed low-mem address (no A5) so it's interrupt-safe. `JSAFE` validated it in
  isolation: `elapsedTicks=91 flagAtExit=0 result=PASS-watchdog-fired`.
- **d19 (froze) → d20 (fixed) — the real blocker: a faceless daemon's modal gets no events.**
  As a background app, the daemon's `SFGetFile` `ModalDialog` receives no events (they route
  to the front Finder), so it stays undismissable — a freeze the journal watchdog can't fix
  because it isn't a journal hang. d19 tried `SetFrontProcess(self)` then entered the modal
  immediately and **still froze** (forcing a hard-kill), because **`SetFrontProcess` is
  asynchronous**: the layer switch only lands when the current front app next yields through
  the Event Manager, but `ModalDialog` busy-loops on `GetNextEvent` (never `WaitNextEvent`)
  and journal playback intercepts event fetch, so the switch never completes and the daemon
  never truly becomes front. (The 100% CPU is normal for *any* `ModalDialog`; the bug is that
  it's undismissable.)

  **d20 fix:** `SetFrontProcess(self)`, then **pump `WaitNextEvent` until `GetFrontProcess`/
  `SameProcess` confirms we ARE the front process** (bounded ~2 s); **only if confirmed front**
  do we arm the journal + open `SFGetFile`; and **bail before the modal if the switch never
  lands** — so a failed foreground can never peg the CPU again. Restore the prior front app
  (Finder) afterwards. The reply gains `front=`. Verified live over the bridge:
  ```
  JSF:200:3 (Cancel) -> front=1 good=0 poll=7 tgtV=262 tgtH=396           -> modal DISMISSED
  JSF:200:1 (Open)   -> front=1 good=1 poll=6 tgtV=237 tgtH=396 file=AB.old4 -> file SELECTED
  ```
  Distinct live button rects (Cancel `v=262`, Open `v=237`, same `h=396`); BAII host CPU
  stayed 6–11 % through **both** drives (never pegged); the daemon returned to the background
  fully responsive. So the daemon now journal-drives a **modal Standard File** to click **any**
  button by item #, freeze-safe. Evidence: `docs/evidence/jsf-foreground-pass.txt`.

**Feasibility spike — JPROBE (0.8d21, commit d7a7a77).** Before building `MENU:<title>:<item>`,
a spike settled whether it could ever reach an **arbitrary front app**. Two blockers, both
confirmed: (1) `MenuSelect` uses the **calling process's** menu list, so a background daemon
calling it only ever drives its **own** menus (the `JABOUT` comment says as much); (2) `JPROBE`
showed that arming journal playback + a background `WaitNextEvent` **does not pump the journal at
all** (`poll=0`, `wneHits=0`) — the journal is only consulted by an active tracking loop
(`MenuSelect`/`ModalDialog`/`Button`) running *in-process*. So the naive cross-process path (arm,
yield, let the front app pick up a journaled `mouseDown`) is unsupported. **Cross-process front-app
menu driving via journaling is a dead end**; the host-side `cliclick` path remains the answer for
foreign apps on *local* Basilisk.

**Follow-on spike — JPROBE2 v4: foreign-context probe (2026-07-05).** The probe sources
(`mac/journal/jgne.a` + `jprobe2.c`) were deliberately **not merged** — the technique they
exercise is unsafe (see below), so shipping it in the tree would only invite reuse. They remain
readable in the [unmerged spike](https://github.com/LoetLuemmel/AppleBridge/pull/71) if the
probe ever needs re-running; these findings are kept here because they are the reason the approach
was abandoned. A second, deeper spike revisited the cross-process question with a real
jGNE filter (`$29A`) that runs *in the calling app's own context* on every `GetNextEvent`. Verbs:
`zones` / `install` / `armwd` (install + prime a Time-Manager unhook watchdog) / `snap` (one-shot
`MenuList` snapshot) / `drive` (inject a menu-bar `mouseDown` + arm playback) / `read` / `disarm` /
`uninstall` / `peek` (read-only hex+ASCII dump). Results, all verified live on Basilisk II /
System 7.6.1:

- **The boot-time `$29A` filter is Apple's own, not a leftover.** A `peek` of the resident head
  (fresh boot `$29A = 0x718xx`) decodes to the **Notification Manager's** GNE hook — a `WWExist`
  (`$8F2`) gate, then a hardcoded chain to a worker that walks **`BNMQHd`** (`$B60`) and fetches
  `'SICN'` for the blinking Apple-menu icon. It is present every boot and **built to be chained
  onto**, so an install guard must refuse only a *stale jprobe2* head (magic `$4A32`), never any
  hook. (This retired the earlier "a foreign filter caused the crash" theory.)
- **Reading a foreign context's menus works (Route A confirmed).** With the filter installed,
  `snap` walks the **front app's** low-mem `MenuList` (`$0A1C`, which the Process Manager swaps
  per process) from *inside that app's context* and copies each menu's ID + title. Captured the
  daemon's own live bar (Apple `128`, Edit `130`, the two system menus) cleanly, bounded and
  crash-free. So **menu *structure* of any foreground app is readable** by this route.
- **`install` + chain and the `armwd` watchdog are safe.** Chaining onto the NM hook counted
  hundreds of GNE calls with no fault; the watchdog fired at interrupt time (`WDfired=1`),
  zeroed `JournalFlag` and restored `$29A` — the recovery mechanism works as designed for a
  *soft spin*.
- **`drive` CRASHES BasiliskII — and the watchdog cannot save it.** Injecting a menu-bar
  `mouseDown` + arming `JournalFlag` playback into the *faceless* daemon (which has no menu-bar
  click handling) makes it perform a window / front-process reorder, and that trips a **host-side
  `SIGILL` / `EXC_BAD_INSTRUCTION` on the AppKit/SDL2 window-management thread**
  (`-[NSWindow _reallyDoOrderWindow]`) — the *same* Sequoia/SDL2 window-teardown crash as quitting
  the daemon. Because the whole **emulator process** dies instantly, the guest's Time Manager never
  runs, so the interrupt-time watchdog is structurally powerless here (it guards *spins*, not host
  crashes). Crash report `BasiliskII-2026-07-05-100241.ips`; host log flagged the `drive` command as
  the prime suspect 4 s before the drop.

**Net:** JPROBE2 independently reconfirms JPROBE's verdict and adds one hard boundary — the `drive`
technique (raw injection + playback) is not merely unsupported but actively *unsafe* on this
Sequoia/SDL2 host, and no guest-side watchdog can make it safe. `drive` stays disabled. The safe
menu-driving routes are unchanged: `MENU:<title>:<item>` / `JABOUT` (which journal-drive
`MenuSelect` on the daemon's own bar **without** a raw menu-bar `mouseDown`/playback), synthetic
`mac_menu` Cmd-key shortcuts, and host `cliclick` for foreign apps on local Basilisk.

**Phase A — `MENU:<title>:<item>` on the daemon's OWN menu bar.** Generalises `JABOUT` from a hardcoded Apple/item-1 to arbitrary
title+item **by name**: it walks the live menu list (`GetMenuBar`; header `lastMenu@0`/`lastRight@2`/
`mbResID@4`, then 6-byte entries `MenuHandle@0`/`menuLeft@4`; `MenuInfo` `menuID@0`/`menuWidth@2`/
title Pascal string `@14`) to match the title (→ its screen X = `menuLeft`), resolves the item to an
index (numeric, or case-insensitive `GetMenuItemText` match), computes the item point (16px rows
below the 20px bar), and journal-drives `MenuSelect(titlePt)` → dispatches via `HandleMenuCommand`.
Add `MENU:` to `host_server.py`'s raw-verb allowlist. Verified live: `MENU:Edit:Copy` → `selItem=1`
and the log landed on the host clipboard (proves **dispatch**, not just selection); `MENU:Edit:Show
details` → `selItem=3` (resolved by name past a separator; the 16px model held). **Hardening (d23):**
d22 froze the guest on **invalid** input (`MENU:Bogus:1` / a typo) — fixed by making resolution
read-only and touching the journal driver / `MenuSelect` **only for a fully-resolved, in-range
target**; valid drives now save/restore the `GrafPort`, install the driver lazily (no per-call
`OpenResFile`), and are watchdog-guarded. A typo is now a safe no-op (`found=0`, CPU ~6 %); repeated
driving is stable. Evidence: `docs/evidence/menu-byname-pass.txt`.

**What remains:** nothing tractable for the *journaling* path — the shipping own-menu verb is done;
cross-process is ruled out (above). Optional polish: a `mac_menu(by_name=…)` MCP wrapper over the
`MENU:` verb, and refining the item-Y model if a menu mixes reduced-height separators. Reaching a
**foreign** app's shortcut-less menu remains host-`cliclick`-only (local Basilisk); the *remote*
PowerPC target has no journaling route to it.

## The DRVR mechanics (RESOLVED — MPW Universal Interfaces 3.4, verified on-device 2026-07-04)

The remaining IM-research blocker — *how to actually write and install the `DRVR`* —
is now pinned to exact constants, pulled from the live headers we compile against
(`{CIncludes}Devices.h`, `{CIncludes}Files.h`), **not** a web paraphrase. (A
WebFetch summary of IM Vol II got three of these wrong: it put `dCtlEnable` at bit 11,
`csCode` as a byte at offset 28, and `dCtlRefNum` at offset 14 — all corrected below.
Use the header values.)

### DRVR header + the mandatory flag (Devices.h)

The Control routine only dispatches if **`dCtlEnableMask = 0x0400`** (bit index
`dCtlEnable = 2` within the flags byte) is set in `drvrFlags`. Full enable/attr table:

| Flag | Bit | Mask |
|------|-----|------|
| `dReadEnable`  | 0 | `0x0100` |
| `dWritEnable`  | 1 | `0x0200` |
| **`dCtlEnable`** | **2** | **`0x0400`** ← required for our Control call |
| `dStatEnable`  | 3 | `0x0800` |
| `dNeedGoodBye` | 4 | `0x1000` |
| `dNeedTime`    | 5 | `0x2000` |
| `dNeedLock`    | 6 | `0x4000` ← set it too: driver is called at arbitrary times |

So our playback DRVR's `drvrFlags` = `dCtlEnableMask | dNeedLockMask` = **`0x4400`**
(no Read/Write/Prime/Status/Time). Header layout (`struct DRVRHeader`, all `short`):

| Offset | Field |
|--------|-------|
| 0  | `drvrFlags`  (→ `0x4400`) |
| 2  | `drvrDelay`  (0) |
| 4  | `drvrEMask`  (0) |
| 6  | `drvrMenu`   (0) |
| 8  | `drvrOpen`   → offset to Open code |
| 10 | `drvrPrime`  (0 — unused) |
| 12 | `drvrCtl`    → offset to Control code |
| 14 | `drvrStatus` (0 — unused) |
| 16 | `drvrClose`  → offset to Close code |
| 18 | `drvrName`   (Pascal string, e.g. `.ABJournal`) |

### Control-routine entry + parameter-block offsets (Files.h `CntrlParam`)

On entry the Device Manager passes **`A0` = ParmBlkPtr**, **`A1` = DCE pointer**.
`csCode` is a **word at offset 26**; `csParam[11]` begins at **offset 28**. So the
journal protocol maps to:

| What | Where in the block |
|------|--------------------|
| `csCode` (word) — `16` = playback | `26(A0)` |
| journal buffer ptr (long) = `csParam[0..1]` | `28(A0)` |
| journal code (long) = `csParam[2..3]` (`jcGetMouse`=1, `jcButton`=2, `jcEvent`=4) | `32(A0)` |

Our Control routine: `MOVE.W 26(A0),D0` → if `16`, `MOVE.L 32(A0),D1` (journal code),
`MOVEA.L 28(A0),A2` (caller's buffer), switch on `D1`, write the synthesized `Point`/
`Boolean`/`EventRecord` into `(A2)`, clear `ioResult` (`16(A0)`), `RTS`.

### Install + self-registering the refNum (Devices.h)

- **Install:** `OpenDriver("\p.ABJournal", &refNum)` loads the DRVR resource, allocates
  a DCE in the unit table, assigns a **negative refNum**, and calls our Open routine.
  (Low-level alt: `DriverInstall(drvrPtr, refNum)` trap `0xA03D` — note the old
  `DrvrInstall` is documented in-header as *"no longer supported… never really worked"*.)
- **Find our own refNum in Open:** read `dCtlRefNum` — a `short` at **offset 24** of the
  DCE (`A1`): `MOVE.W 24(A1),D0`. (DCE layout: `dCtlDriver`@0, `dCtlFlags`@4,
  `dCtlQHdr`@6 [10 bytes], `dCtlPosition`@16, `dCtlStorage`@20, `dCtlRefNum`@24.)
- **Self-register for journaling:** in Open, `LMSetJournalRef(dCtlRefNum)` writes it to
  `JournalRef` (`$08E8`) — per IM Vol I the journal driver registers itself on open. The
  daemon then just pokes `JournalFlag` (`$08DE`) negative to arm playback, 0 to disarm.

**Net:** every constant needed to build the skeleton is now nailed down from the real
toolchain. Nothing IM-side remains open; the next unknown is empirical — step 3's
go/no-go gate (does a faceless-daemon-installed playback DRVR actually get consulted
inside a front-app `MenuSelect`).

### Setup & lifecycle (RESOLVED — IM Vol I)

- **Turn on:** the journaling **driver itself** writes its own driver refNum into
  `JournalRef` **from its `Open` routine** (per IM Vol I: *"the journaling device
  driver should put its reference number in this variable when it's opened"*). So
  the daemon just `OpenDriver`s our DRVR; the driver self-registers. Then set
  **`JournalFlag` to any negative value** to start **playback** (positive = record,
  which we never use).
- **Turn off:** set **`JournalFlag = 0`** — *"the Control call won't be made at
  all."* There is **no end-of-journal opcode** (the code list is only 0–4); playback
  simply stops when `JournalFlag` returns to 0. Our driver can **zero it itself** the
  instant its scripted sequence is exhausted, which also closes the reentrancy-lockup
  risk (a modal loop can't spin forever waiting for input — the driver ends the run).
- **Driver mechanics** are the standard Device Manager `DRVR` (IM Vol **II**, ch. 6);
  a unit-table driver is enough — no need for the desk-accessory packaging (ch. 14).
  IM Vol I notes journaling *"can be accessed only through assembly language"*, which
  is why this is an asm `DRVR`, matching the long-standing "needs 68k asm, not C" note.

## Prototype plan (smallest step first)

1. **Confirm the protocol** — resolved above from IM Vol I: `Control` call,
   `csCode=16` for playback, journal codes 0–4, driver fills `csParam`'s buffer.
2. **Playback DRVR skeleton** (`mac/journal/ABJournal.a`).
   Pure MPW asm; assembles clean and links into a valid `'DRVR' (128, ".ABJournal")`
   resource whose header/code DeRez byte-for-byte to spec (`$4400` flags; Open@30 →
   `dCtlRefNum`→`JournalRef`; Ctl@42 tests `csCode`==16, fills the caller's buffer per
   journal code; Close@116 clears `JournalFlag`). **Build recipe (verified):**
   ```
   Asm  ABJournal.a -o ABJournal.a.o
   Link -rt DRVR=128 -sn Main=.ABJournal -o ABJournalDRVR ABJournal.a.o
   ```
   Two gotchas learned: **(a)** put `STRING ASIS` at the top or the header's Pascal
   name literal gets a *second* auto length byte (`0A 0A 2E…`); **(b)** `-sn Main=.ABJournal`
   renames the segment so the **resource** is named `".ABJournal"` (else it defaults to
   `"Main"` and `OpenDriver(".ABJournal")` can't find it). `-m` names an *entry*, not a
   module — omit it; a single `MAIN`-flagged module is the entry automatically.
   *(Original skeleton spec below, for reference.)*

   A minimal `DRVR` (`drvrFlags = 0x4400`) whose
   `Control` routine reads `csCode` (word `@26(A0)`); on `== 16` it reads the journal
   code (long `@32(A0)`) and the caller's buffer ptr (long `@28(A0)`) and writes a
   **fixed** value there — a canned `Point` for `jcGetMouse (1)`, `FALSE` for
   `jcButton (2)`, a `nullEvent` for `jcEvent (4)` — then clears `ioResult @16(A0)`.
   Build as a code resource (asm or `-model near` C code resource, per the cdev
   gotchas [[applebridge-cdev-build-gotchas]]). Its `Open` routine reads its own
   refNum from `dCtlRefNum @24(A1)` and self-registers into `JournalRef`
   (`LMSetJournalRef`); the daemon `OpenDriver`s it by name, then pokes `JournalFlag`
   (`$08DE`) negative to start and back to 0 to stop. (All offsets header-verified —
   see "The DRVR mechanics" above.)
3. **Prove the hook fires** (see "The step-3 gate" above). A playback DRVR *is*
   consulted by the Event Manager on this ROM: a ToolServer tool armed playback and
   `Button()` returned the driver's injected value; the driver's own Control-call
   counter confirmed the ROM called it (`csCode=16`). The harder cases — driving it
   from the **faceless daemon** and reaching inside a real front-app `MenuSelect` —
   are Parts 2a and 2c above.
4. **Scripted menu path** — replace the canned values with a small state machine the
   daemon loads: feed `jcGetMouse`/`jcButton`/`jcEvent` a real sequence —
   `mouseDown` at the menu title's screen point → `jcGetMouse` walking down the item
   column → `mouseUp` on the target item. Positions come from `GetMenuBar`/
   `MenuSelect` geometry or by counting items; start with hardcoded coordinates for
   one known menu.
5. **`MENU:<title>:<item>` wire verb + `mac_menu(byName=…)`** — resolve title/item to
   coordinates, drive the sequence, restore `JournalFlag = 0` (watchdog-guarded).
   Extending to Standard File / modal-dialog mouse is the `JSF` path
   (`SFGetFile` + dlgHook + foreground-confirm + interrupt watchdog).

## Risks & open questions

- **Background DRVR install** — the daemon is faceless (`backgroundAndForeground`).
  Installing a `DRVR` and owning `JournalRef` from a non-foreground process is
  untried here; step 3 is explicitly the go/no-go.
- **Reentrancy / lockup** — *mitigated:* the driver zeros `JournalFlag` itself when
  its script is exhausted (see Setup & lifecycle), so a modal loop can't spin forever.
  Belt-and-suspenders: the daemon also restores `JournalFlag = 0` on error/timeout
  (watchdog-guarded).
- **Emulator fidelity** — Basilisk II runs a real ROM, so the Event Manager
  journaling path should execute as on hardware; still, confirm the ROM in use
  actually exercises the driver calls (some later paths differ).
- **Coordinate source** — deriving item positions without a cross-process menu read;
  counting/geometry is the fallback (the Phase 2 sketch already assumes this).
- **Interaction with real input** — while playback is armed, real mouse/keys are
  suppressed; keep the armed window as short as the single scripted sequence.

## Recommendation

**Update 2026-07-05:** what read as a speculative multi-session build is now **mostly
built and verified live.** The IM protocol (step 1), the DRVR skeleton (step 2), the
step-3 gate, faceless-daemon install (`JGATE`), popup + real menu-bar driving
(`JMENU`/`JABOUT`), the interrupt-time freeze watchdog (`JSAFE`, 0.8d18), and **modal
Standard File button-driving** (`JSF`, 0.8d20 — foreground-confirm + dlgHook) all work.
The one shipping piece left is the **by-name menu verb surface** (`MENU:<title>:<item>`
+ `mac_menu(byName=…)`) generalising the `JABOUT` path to an arbitrary front app. This
native path is the *right* long-term fix — it carries to the remote PowerPC target,
where the host-side `cliclick` stopgap (pragmatic for **local** Basilisk only) can't
reach.

## Route B — the global `MenuSelect` trap patch (2026-07-05)

The journaling path above reaches the daemon's **own** menus and modal loops. It cannot
reach an **arbitrary front application's** menus: `MenuSelect` uses the *calling
process's* menu list, and a background `WaitNextEvent` never pumps the journal (the
`JPROBE`/`JPROBE2` spikes settled this — see [[applebridge-jgne-spike-state]]). Two
runtime hooks were then investigated for the front-app case. **Route A** — a jGNE filter
at `$29A` — can *read* any front app's menu structure in-context, but *driving* through it
(inject a menu-bar mouse-down + arm playback) crashes the host with a Sequoia/SDL2
window-teardown `SIGILL` that no guest watchdog can catch; `drive` is a dead end. **Route
B — a global `_MenuSelect` ($A93D) trap patch — is the one that works.**

**Mechanism.** A head patch on the `MenuSelect` Toolbox trap. When armed with a target
`(menuID, item)`, the next `MenuSelect` in *any* context returns that value immediately —
**no mouse-tracking loop, no journal, no window reorder** (the Route A crash mechanisms).
A menu-bar mouse-down posted to the front app makes it call `MenuSelect`; the patch
short-circuits the tracking and the app dispatches the chosen command. Pascal trap return:
`MOVE.L (SP)+,A1 / ADDQ.L #4,SP / MOVE.L D0,(SP) / JMP (A1)` (result is the high-byte
Pascal `Boolean` word on the stack, not `D0`). Sources: `mac/journal/mspatch.a` (the
`'MSPT'` patch), `mac/journal/msinit.a` (the boot INIT), `mac/journal/msinstall.c`
(harness), daemon `MSINSTALL`/`MSREAD`/`MSDRIVE`/`MSUNINSTALL` verbs.

**App-installed patches are process-local — the INIT is *required*.** A patch installed by
a running application (even the resident daemon, `NewPtrSys` + `NSetTrapAddress`) is
visible **only in that process**: verified live — the daemon's own `MenuSelect` was
intercepted, but the Finder's was not. This is exactly Inside Macintosh's rule (an
app-heap patch applies to your app only; a **system-extension patch at startup** applies
to all apps). So global reach needs a boot `INIT`. (ToolServer also *reverts* the trap
table around each tool, so a patch made by an MPW tool never persists — the jGNE filter
survived only because `$29A` is a low-memory global, not a trap slot.)

**The boot INIT (`msinit.a`).** A self-contained System 7 extension — `INIT` id **0**,
`sysheap, locked` (matches the working `AppleBridgeMenuLED`) — that **embeds** the 78-byte
patch and installs it with `NewPtrSys` + `BlockMove`-from-self + `Get/SetToolTrapAddress`.
It deliberately does **not** `Get1Resource` a sibling `'MSPT'`: at INIT execution time the
extension's resource fork is not reliably the current res file, so a resource load bails.
Build: `Asm msinit.a` → `Link -rt INIT=0 -m MSInit -ra =resSysHeap,resLocked` → `SetFile
-t INIT -c 'ABmi'` → drop in `System Folder:Extensions:` → reboot. Gotchas: entry is
`PROC EXPORT` (not `MAIN`, else Link Error 53); the embedded length must be a literal
(`kPatchLen EQU 78`; a forward-referenced `PatchEnd-PatchData` is Asm Error 16).

**Verify by heap scan, not the trap head.** After boot, `NGetTrapAddress(_MenuSelect)` did
**not** equal our block ("magic mismatch") — which *looks* like the INIT never ran. It
did. `MenuSelect` is a contended trap: the System and other Apple extensions (which load
after `ABMenuInit` alphabetically) patch it *on top* of ours, chaining down — so our patch
is alive in the chain but not the head. A system-heap scan for the patch signature
(`601A 4D53` = `BRA.S Go` + `'MS'` magic) found the block and proved the INIT executed.
**Trap-patch liveness must be checked by scanning for the patch, not by comparing the trap
vector head.** (This is also why the `AppleBridgeMenuLED` INIT "looked" like it worked and
this one didn't — the LED patches a quiet trap and you *see* the LED; nobody re-patches
over it, so it stays head. Same INIT machinery; different verification.)

**Proven end-to-end.** Armed the scanned block with `(888, 5)`, brought the Finder front,
and press-held its File menu: the block went `calls 1→2, hits 1→2, lastRes = 03780005 =
(888, 5)`. A **foreign front application's `MenuSelect` was intercepted and returned the
injected menu selection, with no tracking loop and no crash** (guest uptime 382 s,
`ERR 0`). This is the front-app menu driving that journaling (own-menu only) and the jGNE
`drive` path (host crash) could not deliver — and unlike Route A it is completely stable.

**Polish done.** The daemon now **adopts** the INIT's global block by scanning the system
heap for its magic (`FindMSPatch`; `MSINSTALL` prefers it over installing its own
process-local copy), and an MCP tool **`mac_menu_front(menu_id, item)`** orchestrates the
full front-app drive — `MSINSTALL` (adopt) → `MSDRIVE` (arm) → host `cliclick` a menu-bar
title (trigger) → `MSREAD` (verify). Numeric ids only (by-name resolution of a *foreign*
app needs reading its menu list — the jGNE Route A read — and is not wired); the trigger is
local-Basilisk (host `cliclick`). Recovery if a boot INIT ever wedges startup: Shift-boot
(extensions off), or delete the file host-side with `hfsutils` while Basilisk is shut down.
See [[applebridge-route-b-menuselect-patch]].
