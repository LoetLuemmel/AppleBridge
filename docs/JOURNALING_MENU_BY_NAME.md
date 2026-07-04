# Journaling driver — menu-by-name & modal-dialog mouse (R&D)

Status: **R&D / design complete (2026-07-04)** — the driver-call protocol is now
fully resolved (from Inside Macintosh Vol I); the next step is building the DRVR,
not more research. Design + on-device reconnaissance for Phase 2 of
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

1. ~~Confirm the protocol~~ — ✅ **done** (resolved above from IM Vol I: `Control`
   call, `csCode=16` for playback, journal codes 0–4, driver fills `csParam`'s
   buffer). No longer a blocker.
2. ~~**Playback DRVR skeleton**~~ — ✅ **built & verified 2026-07-04** (`mac/journal/ABJournal.a`).
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
3. **Prove the hook fires in the background** — from the faceless daemon, install
   the DRVR and verify the Event Manager actually calls it (e.g. the driver bumps a
   counter on each `jcGetMouse`, read back over the bridge). This is the **key
   feasibility gate**: does a background-installed journal driver get consulted, and
   does it reach inside a `MenuSelect` opened in the front app?
4. **Scripted menu path** — replace the canned values with a small state machine the
   daemon loads: feed `jcGetMouse`/`jcButton`/`jcEvent` a real sequence —
   `mouseDown` at the menu title's screen point → `jcGetMouse` walking down the item
   column → `mouseUp` on the target item. Positions come from `GetMenuBar`/
   `MenuSelect` geometry or by counting items; start with hardcoded coordinates for
   one known menu.
5. **`MENU:<title>:<item>` wire verb + `mac_menu(byName=…)`** — resolve title/item to
   coordinates, drive the sequence, restore `JournalFlag = 0` (watchdog-guarded).
   Extend to Standard File / modal-dialog mouse.

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

Feasible and the *right* long-term fix (native, works on the remote PowerPC target,
no host calibration), but it is a **multi-session build** gated on two things in
order: (1) the driver-call protocol from IM Vol I, then (2) the step-3 feasibility
gate proving a background-installed playback driver is actually consulted. Until a
concrete workflow needs a shortcut-less menu item or hands-off modal-dialog driving
on the *remote* target, the host-side `cliclick` path remains the pragmatic
stopgap for **local** Basilisk. Pick this up as a dedicated session starting at
step 1.
