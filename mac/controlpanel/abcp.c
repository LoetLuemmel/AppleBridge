/*
 * AppleBridge Control Panel - cdev proof of concept.
 *
 * Steps 1-3 proved the cdev mechanics (appear/open, hitDev dispatch + handle
 * state, modal Standard File from hitDev). Step 4 ports AppleBridgeConfig's
 * actual logic into the cdev, in on-device-proven sub-steps:
 *
 *   4a: LIVE DAEMON STATUS via the Process Manager (proven).
 *   4b-detect (this file): LIVE AUTOSTART STATUS. Also report whether the
 *      watchdog autostart alias exists in the System Folder's Startup Items,
 *      using the FOLDER MANAGER (FindFolder + FSMakeFSSpec). This probes the
 *      next unknown of the port - do those traps link and run from a code
 *      resource? - read-only, so a glue symbol fails the LINK rather than
 *      crashing at run time. (4b-install then adds Install/Remove buttons.)
 *
 * Discipline unchanged: A4-free (state in the cdevValue handle; no globals; no
 * string literals in code - every UI string is built on the stack from char
 * constants, which compile to immediate moves; the alias name likewise);
 * only inline Toolbox traps; CDevMain FIRST so it sits at offset 0; and the
 * handle is re-dereferenced AFTER any heap-moving trap, never held across one.
 */

#include <Types.h>
#include <Quickdraw.h>
#include <Windows.h>
#include <Dialogs.h>
#include <Events.h>
#include <Memory.h>
#include <Processes.h>      /* GetNextProcess / GetProcessInformation */
#include <Files.h>          /* FSSpec, FSMakeFSSpec, fnfErr */
#include <Folders.h>        /* FindFolder, kStartupFolderType */

#define macDev    8
#define initDev   0
#define hitDev    1
#define closeDev  2
#define nulDev    3
#define activDev  5

#define kDaemonCreator  'ABrg'

/* our DITL items, 1-based within our own DITL */
enum { kLabel = 1, kStatus, kAutostart };

/* per-instance state, kept in the cdevValue handle (no globals).
 * -1 = unknown (forces the first poll to draw); 0/1 = last shown value. */
typedef struct {
    short lastDaemon;
    short lastAuto;
} CPState;

/* CRITICAL: the Control Panel host calls a cdev by jumping to OFFSET 0 of the
 * 'cdev' resource, so CDevMain MUST be the first code emitted. We therefore
 * DEFINE it first and forward-declare the helpers below it. (Defining helpers
 * first put CDevMain at a non-zero offset -> the host jumped into a helper with
 * the wrong arguments -> instant fault that takes the emulator down with it.) */
static Boolean DaemonRunning(void);
static Boolean AutostartInstalled(void);
static void    DaemonString(Str255 d, Boolean running);
static void    AutoString(Str255 d, Boolean installed);
static void    ShowText(DialogPtr cpDialog, short numItems, short whichItem,
                        ConstStr255Param text);
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
            if (h) {
                CPState *st = (CPState *)(*h);
                st->lastDaemon = -1;               /* force first poll to draw */
                st->lastAuto   = -1;
            }
            return (long) h;                       /* becomes cdevValue */
        }

        case activDev:                             /* panel came to front: refresh */
            if (cdevValue) {
                CPState *st = (CPState *)(*(Handle)cdevValue);
                st->lastDaemon = -1;
                st->lastAuto   = -1;
            }
            /* fall through to poll-and-show */
        case nulDev:                               /* idle: poll daemon + autostart */
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
 * A4-free: only locals + immediate OSType constants; inline Process Mgr traps. */
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

/* Is the watchdog autostart alias present in Startup Items? Read-only: just
 * FindFolder(Startup Items) + FSMakeFSSpec(name) -> noErr means it exists.
 * The alias name is built on the stack (no string literal in the code). */
static Boolean AutostartInstalled(void)
{
    Str255 nm;
    FSSpec spec;
    short  vRefNum, i = 0;
    long   dirID;

    nm[++i]='A'; nm[++i]='p'; nm[++i]='p'; nm[++i]='l'; nm[++i]='e';
    nm[++i]='B'; nm[++i]='r'; nm[++i]='i'; nm[++i]='d'; nm[++i]='g'; nm[++i]='e';
    nm[++i]=' ';
    nm[++i]='W'; nm[++i]='a'; nm[++i]='t'; nm[++i]='c'; nm[++i]='h';
    nm[++i]='d'; nm[++i]='o'; nm[++i]='g';
    nm[0] = (unsigned char) i;

    if (FindFolder(kOnSystemDisk, kStartupFolderType, kDontCreateFolder,
                   &vRefNum, &dirID) != noErr)
        return false;
    return (FSMakeFSSpec(vRefNum, dirID, nm, &spec) == noErr);  /* noErr = exists */
}

/* "Daemon: RUNNING" / "Daemon: stopped" - char constants, no string literal. */
static void DaemonString(Str255 d, Boolean running)
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

/* "Autostart: installed" / "Autostart: none" - char constants, no literal. */
static void AutoString(Str255 d, Boolean installed)
{
    short i = 0;
    d[++i]='A'; d[++i]='u'; d[++i]='t'; d[++i]='o'; d[++i]='s'; d[++i]='t';
    d[++i]='a'; d[++i]='r'; d[++i]='t'; d[++i]=':'; d[++i]=' ';
    if (installed) { d[++i]='i'; d[++i]='n'; d[++i]='s'; d[++i]='t';
                     d[++i]='a'; d[++i]='l'; d[++i]='l'; d[++i]='e'; d[++i]='d'; }
    else           { d[++i]='n'; d[++i]='o'; d[++i]='n'; d[++i]='e'; }
    d[0] = (unsigned char) i;
}

/* Set a statText. A cdev's window carries the Control Panel's windowKind, not
 * dialogKind, so SetDialogItemText (which draws immediately) needs a brief juggle. */
static void ShowText(DialogPtr cpDialog, short numItems, short whichItem,
                     ConstStr255Param text)
{
    short  type, saveKind;
    Handle ih;
    Rect   box;

    GetDialogItem(cpDialog, numItems + whichItem, &type, &ih, &box);
    saveKind = ((WindowPeek) cpDialog)->windowKind;
    ((WindowPeek) cpDialog)->windowKind = dialogKind;
    SetDialogItemText(ih, text);
    ((WindowPeek) cpDialog)->windowKind = saveKind;
}

/* Poll daemon + autostart and redraw each line only on change. Memory-safe:
 * DaemonRunning()/AutostartInstalled() may move the heap, so we deref cdevValue
 * AFTER them, decide what changed, write BOTH state fields, THEN draw (drawing
 * may move the heap too, but we don't touch the handle pointer after writing). */
static void PollAndShow(long cdevValue, short numItems, DialogPtr cpDialog)
{
    Handle   h = (Handle) cdevValue;
    short    dnow, anow;
    Boolean  drawD, drawA;
    CPState *st;

    if (!h) return;
    dnow = DaemonRunning()      ? 1 : 0;     /* <- may move memory */
    anow = AutostartInstalled() ? 1 : 0;     /* <- may move memory */

    st    = (CPState *)(*h);                  /* deref AFTER the move-risk calls */
    drawD = (dnow != st->lastDaemon);
    drawA = (anow != st->lastAuto);
    st->lastDaemon = dnow;                     /* write both before any drawing */
    st->lastAuto   = anow;

    if (drawD) { Str255 b; DaemonString(b, dnow); ShowText(cpDialog, numItems, kStatus, b); }
    if (drawA) { Str255 b; AutoString(b, anow);   ShowText(cpDialog, numItems, kAutostart, b); }
}
