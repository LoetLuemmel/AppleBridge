/*
 * AppleBridge Control Panel - cdev proof of concept.
 *
 * Steps 1-3 proved the cdev mechanics (appear/open, hitDev dispatch + handle
 * state, modal Standard File from hitDev). Step 4 ports AppleBridgeConfig's
 * actual logic into the cdev, in on-device-proven sub-steps:
 *
 *   4a (this file): LIVE DAEMON STATUS. Poll the Process Manager for the
 *      faceless daemon (creator 'ABrg') and show RUNNING/stopped in a statText.
 *      We poll on BOTH nulDev (the idle message) and activDev (panel brought to
 *      front), redrawing only when the state changes. This probes the one real
 *      unknown of the port: do the Process Manager traps even link and run from
 *      a code resource? (Step 4b adds the Folder/Alias/Resource Manager autostart
 *      actions; 4c the helper list + Add-Helper via SFGetFile.)
 *
 * Memory-move discipline: GetNextProcess / GetProcessInformation (and the Dialog
 * Manager) may move the heap, so we never hold a dereferenced handle pointer
 * across them - we re-dereference cdevValue AFTER the move-risk call.
 *
 * Still strictly A4-free, as a code resource must be:
 *   - per-instance state in the cdevValue handle, no globals/statics;
 *   - NO string literals in code - the status words are built on the stack from
 *     char constants (immediate moves), not a "\p..." constant in the code's data;
 *   - only inline Toolbox traps + our own same-segment helpers.
 */

#include <Types.h>
#include <Quickdraw.h>
#include <Windows.h>
#include <Dialogs.h>
#include <Events.h>
#include <Memory.h>
#include <Processes.h>      /* GetNextProcess / GetProcessInformation */

#define macDev    8
#define initDev   0
#define hitDev    1
#define closeDev  2
#define nulDev    3
#define activDev  5

#define kDaemonCreator  'ABrg'

/* our DITL items, 1-based within our own DITL */
enum { kLabel = 1, kStatus };

/* per-instance state, kept in the cdevValue handle (no globals) */
typedef struct {
    short lastState;   /* -1 unknown, 0 stopped, 1 running - gates redraw */
} CPState;

/* CRITICAL: the Control Panel host calls a cdev by jumping to OFFSET 0 of the
 * 'cdev' resource, so CDevMain MUST be the first code emitted. We therefore
 * DEFINE it first and forward-declare the helpers below it. (Defining helpers
 * first put CDevMain at a non-zero offset -> the host jumped into a helper with
 * the wrong arguments -> instant fault that takes the emulator down with it.) */
static Boolean DaemonRunning(void);
static void    StatusString(Str255 d, Boolean running);
static void    ShowStatus(DialogPtr cpDialog, short numItems, Boolean running);
static void    PollAndShow(long cdevValue, short numItems, DialogPtr cpDialog);

pascal long CDevMain(short message, short item, short numItems, short rsrcID,
                     EventRecord *event, long cdevValue, DialogPtr cpDialog)
{
#pragma unused(rsrcID, event, item)
    switch (message) {
        case macDev:
            return 1;                              /* appear on this machine */

        case initDev: {
            Handle h = NewHandle(sizeof(CPState));
            if (h) ((CPState *)(*h))->lastState = -1;  /* force first poll to draw */
            return (long) h;                       /* becomes cdevValue */
        }

        case activDev:                             /* panel came to front: refresh */
            if (cdevValue) ((CPState *)(*(Handle)cdevValue))->lastState = -1;
            /* fall through to poll-and-show */
        case nulDev:                               /* idle: poll daemon status */
            PollAndShow(cdevValue, numItems, cpDialog);
            return cdevValue;

        case closeDev:
            if (cdevValue) DisposeHandle((Handle) cdevValue);
            return 0;

        default:
            return cdevValue;
    }
}

/* Is the faceless daemon (creator 'ABrg') currently a running process?
 * Same enumeration AppleBridgeConfig uses, but it must link as inline traps
 * here. A4-free: only locals + immediate OSType constants. */
static Boolean DaemonRunning(void)
{
    ProcessSerialNumber psn;
    ProcessInfoRec      info;

    psn.highLongOfPSN = 0;
    psn.lowLongOfPSN  = kNoProcess;
    while (GetNextProcess(&psn) == noErr) {
        info.processInfoLength = sizeof(info);
        info.processName       = NULL;
        info.processAppSpec    = NULL;
        if (GetProcessInformation(&psn, &info) == noErr)
            if (info.processSignature == kDaemonCreator) return true;
    }
    return false;
}

/* Build "Daemon: RUNNING" / "Daemon: stopped" with no string literals - each
 * char is an immediate constant, so the code holds no A4-relative data ref. */
static void StatusString(Str255 d, Boolean running)
{
    short i = 0;
    d[++i]='D'; d[++i]='a'; d[++i]='e'; d[++i]='m'; d[++i]='o'; d[++i]='n';
    d[++i]=':'; d[++i]=' ';
    if (running) { d[++i]='R'; d[++i]='U'; d[++i]='N'; d[++i]='N';
                   d[++i]='I'; d[++i]='N'; d[++i]='G'; }
    else         { d[++i]='s'; d[++i]='t'; d[++i]='o'; d[++i]='p';
                   d[++i]='p'; d[++i]='e'; d[++i]='d'; }
    d[0] = (unsigned char) i;
}

/* Set our status statText. A cdev's window carries the Control Panel's
 * windowKind, not dialogKind, so SetDialogItemText needs a brief juggle.
 * (SetDialogItemText draws the new text immediately when the window is visible.) */
static void ShowStatus(DialogPtr cpDialog, short numItems, Boolean running)
{
    Str255 buf;
    short  type, saveKind;
    Handle ih;
    Rect   box;

    StatusString(buf, running);
    GetDialogItem(cpDialog, numItems + kStatus, &type, &ih, &box);
    saveKind = ((WindowPeek) cpDialog)->windowKind;
    ((WindowPeek) cpDialog)->windowKind = dialogKind;
    SetDialogItemText(ih, buf);
    ((WindowPeek) cpDialog)->windowKind = saveKind;
}

/* Poll the daemon and, only if its state changed since last shown, redraw.
 * Memory-safe: DaemonRunning() may move the heap, so cdevValue is dereferenced
 * AFTER it returns, not before. */
static void PollAndShow(long cdevValue, short numItems, DialogPtr cpDialog)
{
    Handle  h = (Handle) cdevValue;
    short   now;
    CPState *st;

    if (!h) return;
    now = DaemonRunning() ? 1 : 0;          /* <- may move memory */
    st  = (CPState *)(*h);                   /* deref AFTER the move-risk call */
    if (now != st->lastState) {
        st->lastState = now;
        ShowStatus(cpDialog, numItems, now); /* st unused after this point */
    }
}
