/*
 * AppleBridge INIT - boot-time presence (Phase 6b).  *** DEFERRED / REFERENCE ***
 *
 * This is the INTENDED logic for a System 7 presence extension ('INIT', creator
 * 'ABin'): at startup, register a Gestalt selector 'ABrg' so software can detect
 * AppleBridge, and draw an icon into the boot screen. It patches nothing.
 *
 * It does NOT currently work as an MPW C code resource, and is kept here as a
 * readable reference and a record of the design. A functional version needs 68k
 * assembly. See README.md for the full diagnosis; the short version:
 *
 *   - A standalone 'INIT' code resource has no A5 world. Taking the C address of
 *     the callback (&ABGestaltProc) makes MPW C emit an A5-relative jump-table
 *     reference, which the linker can't resolve into a code resource (it mangles
 *     the instruction stream). Computing the address from the resource base + a
 *     fixed offset avoids that — but then the linker DEAD-STRIPS the now-
 *     unreferenced callback. Assembly sidesteps both (natural PC-relative LEA, no
 *     strip).
 *   - NewGestalt also requires its callback to live in the SYSTEM HEAP, so the
 *     resource must be linked resSysHeap+resLocked (that part works — see
 *     BuildInit.emu). gestalt_check.c verifies the selector once it registers.
 *
 * The functional goal of Phase 6 — keeping the daemon alive — is delivered by the
 * separate AppleBridgeWatchdog app (../watchdog/), at safe application context.
 * "Is AppleBridge present/running?" is already answerable via process detection
 * (GetNextProcess for 'ABrg'/'ABwd'), so the Gestalt selector here is a nicety.
 *
 * Constraint that IS correct and must be preserved in any rewrite: ABInitMain MUST
 * be the first function (the System jumps to offset 0 of the resource at boot, and
 * the linker keeps source order).
 */

#include <Types.h>
#include <Quickdraw.h>
#include <Windows.h>
#include <Gestalt.h>
#include <Resources.h>
#include <Icons.h>
#include <Memory.h>

#define kABSelector  'ABrg'
#define kIconID      128
#define kABVersion   0x00000100L

static pascal OSErr ABGestaltProc(OSType selector, long *response);
static void         ABDrawIcon(void);

/* Entry point — MUST stay first (System jumps to resource offset 0 at boot). */
void ABInitMain(void)
{
    Handle self;

    self = Get1Resource('INIT', 0);
    if (self != nil) {                       /* keep us resident for the session */
        DetachResource(self);
        HLock(self);
    }
    (void) NewGestalt(kABSelector, (SelectorFunctionUPP) ABGestaltProc);
    ABDrawIcon();
}

/* Gestalt selector callback. No globals (no A5): returns a constant. */
static pascal OSErr ABGestaltProc(OSType selector, long *response)
{
#pragma unused(selector)
    *response = kABVersion;
    return noErr;
}

/* Draw our 'ICN#' into the Window Manager port (the boot screen), bottom-left. */
static void ABDrawIcon(void)
{
    GrafPtr wmgrPort, savePort;
    Handle  icon;
    Rect    r;

    icon = GetResource('ICN#', kIconID);
    if (icon == nil) return;
    GetWMgrPort(&wmgrPort);
    if (wmgrPort == nil) return;
    GetPort(&savePort);
    SetPort(wmgrPort);
    r.bottom = wmgrPort->portRect.bottom - 8;
    r.top    = r.bottom - 32;
    r.left   = wmgrPort->portRect.left + 8;
    r.right  = r.left + 32;
    HLock(icon);
    PlotIcon(&r, icon);
    HUnlock(icon);
    SetPort(savePort);
}
