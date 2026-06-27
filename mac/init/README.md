# AppleBridge INIT (presence extension) — DEFERRED

A System 7 extension (`'INIT'`, creator `'ABin'`) intended to advertise AppleBridge's
presence at boot: register a Gestalt selector `'ABrg'` and draw a startup icon. It
**patches nothing** — purely advisory.

**Status: deferred / non-functional in C.** The functional goal of Phase 6 (keeping
the daemon alive) is delivered by the separate `AppleBridgeWatchdog` app
(`../watchdog/`). This INIT is kept as a documented reference; a working version
needs 68k assembly. The files here (`applebridge_init.c`, `applebridge_init.r`,
`BuildInit.emu`, `gestalt_check.c`) build, and the build is *almost* right — but the
Gestalt callback can't be made to work as an MPW C code resource. The diagnosis below
is the valuable part.

## Why it's deferred — the diagnosis

Getting here took several on-device build/reboot cycles. Each fix exposed the next
constraint of a **standalone `'INIT'` code resource, which has no A5 world**:

1. **Entry order.** The System jumps to **offset 0** of the `'INIT'` resource at boot,
   and the MPW linker lays functions out in source order (`-m` does not reorder). The
   first build had the Gestalt callback first; the System called it with garbage
   Pascal arguments → **boot crash**. Fix: `ABInitMain` must be the first function.

2. **A5-relative vs PC-relative.** Taking the callback's C address (`&ABGestaltProc`)
   makes MPW C (near model) emit an **A5-relative jump-table reference** (`LEA d16(A5)`),
   which is meaningless in a code resource — another boot crash. `-model far` changes
   it to a 32-bit reference, and the linker *does* convert intra-resource references to
   PC-relative — **but** when it shortens the 6-byte absolute `LEA` to a 4-byte
   PC-relative one, it leaves a stray `0000` word that executes as `ORI.B` and **eats
   the following `_NewGestalt` trap word** as its operand. So NewGestalt is silently
   never called.

3. **System heap.** `NewGestalt` returns `gestaltLocationErr` (−5553) unless its
   callback lives in the **system heap**. The resource must be linked
   `resSysHeap,resLocked` (via `Link -ra` — this part works; confirmed with DeRez).

4. **Dead-strip.** Computing the callback as `(*self) + offset` (resource base + a
   fixed byte offset) avoids taking `&ABGestaltProc` and so dodges (2) entirely — the
   disassembly is then clean and `_NewGestalt` is called correctly. **But** with no C
   reference to it, the linker **dead-code-strips** `ABGestaltProc`, so the offset
   points past the end of the resource.

Items (2) and (4) are a vise: referencing the callback by address corrupts the code;
*not* referencing it strips the callback. Both vanish in **assembly**, where a
PC-relative `LEA GestCB(PC),A0` is natural, nothing is stripped, and the byte layout
is explicit. That — plus the already-working `resSysHeap` attribute and "ABInitMain
first" — is the path to a functional INIT, if it's ever worth doing.

## What the INIT would do (the intent)

```c
NewGestalt('ABrg', callback);   /* callback returns version 0x0100 */
PlotIcon(bottomLeftRect, 'ICN#' 128);  /* a box with a "bridge deck" bar */
DetachResource(self); HLock(self);     /* stay resident for the Gestalt callback */
```

`gestalt_check.c` is the companion MPW tool: it queries `Gestalt('ABrg')` and reports
the result as its exit status (`0` present, `2` absent) — the canonical example of how
any program detects AppleBridge. It works; it just reports "absent" until a functional
INIT registers the selector.

## Is the INIT even needed?

Mostly no. "Is AppleBridge present / running?" is already answerable by **process
detection** (`GetNextProcess` for creator `'ABrg'` (daemon) or `'ABwd'` (watchdog)),
which `AppleBridgeConfig` already uses. The Gestalt selector is a nicety; the boot
icon is cosmetic. The keep-alive that actually matters runs in the watchdog app at
safe application context, not here.

## Build (for the record)

```mpw
Directory MeinMac:MPW:AppleBridge:
Execute :init:BuildInit.emu          # SC -model far + Link (-rt INIT=0 -ra resSysHeap,resLocked) + Rez + SetFile -t INIT
```
