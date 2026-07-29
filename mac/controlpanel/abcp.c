/*
 * AppleBridge Control Panel - cdev.
 *
 * Steps 1-3 proved the cdev mechanics; step 4 ported AppleBridgeConfig's real
 * logic (live daemon status via the Process Manager, live autostart status via
 * the Folder Manager, the helper-app list + Add Helper App). This file is the
 * POLISH pass:
 *   - the helper list is now a real, self-drawn userItem (multi-row, click to
 *     select a row) instead of a one-line "Helpers: a, b" statText;
 *   - a "Remove Helper" button deletes the selected helper's APP= line from the
 *     shared prefs file;
 *   - list drawing is driven by updateDev / a dirty flag (no per-poll flicker).
 *
 * Why self-drawn and not the List Manager: LNew/LClick/... are GLUE in this MPW
 * (no ONEWORDINLINE in Lists.h), and A4-dependent glue would fault a code
 * resource -> crash the whole Control Panel host. The list is therefore rendered
 * with inline QuickDraw traps only (the DrawLED bank), and clicks are hit-tested
 * geometrically with PtInRect - exactly the discipline the verbose console
 * scrollbar used. See README.
 *
 * Discipline unchanged: A4-free (state in the cdevValue handle; no globals; no
 * string literals - labels/prefs-name built on the stack from char constants,
 * helper paths come from the file system and the prefs file; QuickDraw colours
 * set from immediate constants); only inline Toolbox traps (glue fails the LINK,
 * caught before install); CDevMain FIRST (offset 0); cdevValue re-dereferenced
 * AFTER any heap-moving trap.
 */

#include <Types.h>
#include <Quickdraw.h>
#include <Windows.h>
#include <Dialogs.h>
#include <Events.h>
#include <Memory.h>
#include <Processes.h>      /* GetNextProcess / GetProcessInformation */
#include <Files.h>          /* FSSpec, FSMakeFSSpec, FSpOpenDF/Create, FSRead/Write, SetEOF, PBGetCatInfo */
#include <Folders.h>        /* FindFolder, kStartupFolderType, kPreferencesFolderType */
#include <StandardFile.h>   /* SFGetFile, SFReply (inline-trap form) */
#include <Resources.h>      /* CurResFile / UseResFile - inline traps */

#define macDev    8
#define initDev   0
#define hitDev    1
#define closeDev  2
#define nulDev    3
#define updateDev 4
#define activDev  5

#define kDaemonCreator    'ABrg'
#define kConfigCreator    'ABcf'
#define kWatchdogCreator  'ABwd'
#define kInstallerCreator 'ABis'
#define kPrefsBufSize     512
#define kPathBufSize      256

/* Resource ids live in the cdev's OWN range, which runs UPWARD from -4064 to
   -4033 — so the free ids are kCdevBase + n, not - n. Outside that range the
   Control Panel's merged resource chain can collide with another panel's
   numbering and put somebody else's dialog on screen; inside it, every id is
   ours to spend. Allocated from a base with the occupancy written down, because
   the next person to add a dialog needs to know what is already taken without
   reading abcp.r. (Resource TYPES have separate id spaces, which is why four
   different types can all sit on the base id.)

   Scoping the LOOKUP still matters more than the id — see UseResFile in
   AddHelper. The range keeps a collision unlikely; UseResFile makes it
   impossible. */
#define kCdevBase         (-4064)   /* taken: panel DITL, nrct, mach, ICN# */
#define kAddHelperAlert   (kCdevBase + 1)   /* ALRT -4063 -> DITL -4063 */
#define kOwnSuiteAlert    (kCdevBase + 2)   /* ALRT -4062 -> DITL -4062 */
#define kNotAnAppAlert    (kCdevBase + 3)   /* ALRT -4061 -> DITL -4061 */
/*      next free                    kCdevBase + 4  (-4060) ... -4033 */

#define kMaxHelpers       10        /* cached helper leaves */
#define kLeafMax          31        /* max chars per leaf name */
#define kRowHeight        13        /* list row height, pixels */

/* our DITL items, 1-based within our own DITL */
enum { kLabel = 1, kStatus, kAutostart, kIP, kHelperList, kAddBtn, kRemoveBtn };

/* per-instance state, kept in the cdevValue handle (no globals). */
typedef struct {
    short cdevRes;                          /* OUR resource file (see initDev) */
    short lastDaemon;                       /* -1 unknown; 0/1 last shown */
    short lastAuto;
    short helpersShown;                     /* 0 = re-read helper leaves from prefs */
    short listDirty;                        /* 1 = redraw the list from cache */
    short helperCount;                      /* cached leaves in use */
    short selRow;                           /* selected row, -1 = none */
    char  leaf[kMaxHelpers][kLeafMax + 1];  /* NUL-terminated leaf names */
} CPState;

/* CRITICAL: the host calls a cdev by jumping to OFFSET 0 of the 'cdev' resource,
 * so CDevMain MUST be first. Define it first; forward-declare the helpers. */
static Boolean DaemonRunning(void);
static Boolean AutostartInstalled(void);
static OSErr   PrefsSpec(FSSpec *spec);
static void    FSSpecToPath(const FSSpec *spec, char *path);
static void    AddHelper(short cdevRes);
static void    RemoveSelectedHelper(Handle h);
static void    DaemonString(Str255 d, Boolean running);
static void    AutoString(Str255 d, Boolean installed);
static void    ShowText(DialogPtr cpDialog, short numItems, short whichItem,
                        ConstStr255Param text);
static void    DrawLED(DialogPtr cpDialog, const Rect *box, Boolean good);
static void    ReadPrefs(DialogPtr cpDialog, short numItems, Handle h);
static void    DrawHelperList(DialogPtr cpDialog, short numItems, Handle h);
static void    SelectHelperRow(DialogPtr cpDialog, short numItems, Handle h,
                               EventRecord *event);
static void    PollAndShow(long cdevValue, short numItems, DialogPtr cpDialog);

pascal long CDevMain(short message, short item, short numItems, short rsrcID,
                     EventRecord *event, long cdevValue, DialogPtr cpDialog)
{
#pragma unused(rsrcID)
    switch (message) {
        case macDev:
            return 1;                              /* appear on this machine */

        case initDev: {
            Handle h = NewHandle(sizeof(CPState));
            if (h) {
                CPState *st = (CPState *)(*h);
                /* The host has made OUR resource file current for initDev,
                   and only for initDev. Hold on to it: by the time a click
                   arrives the chain's current file is whatever the Control
                   Panel last used, so an unqualified Alert() would search
                   from there -- missing our ALRT, or finding another
                   panel's resource of the same id. */
                st->cdevRes      = CurResFile();
                st->lastDaemon   = -1;             /* force first poll to draw */
                st->lastAuto     = -1;
                st->helpersShown = 0;              /* re-read helpers */
                st->listDirty    = 1;              /* draw the list */
                st->helperCount  = 0;
                st->selRow       = -1;
            }
            return (long) h;
        }

        case hitDev:                               /* a click landed in one of our items */
            if (cdevValue) {
                short which = item - numItems;
                if (which == kAddBtn) {
                    AddHelper(((CPState *)(*(Handle)cdevValue))->cdevRes);
                    ((CPState *)(*(Handle)cdevValue))->helpersShown = 0;   /* re-read */
                } else if (which == kRemoveBtn) {
                    RemoveSelectedHelper((Handle) cdevValue);   /* drop selected APP= line */
                    ((CPState *)(*(Handle)cdevValue))->helpersShown = 0;
                } else if (which == kHelperList && event) {
                    SelectHelperRow(cpDialog, numItems, (Handle) cdevValue, event);
                }
                ((CPState *)(*(Handle)cdevValue))->listDirty = 1;
                PollAndShow(cdevValue, numItems, cpDialog);
            }
            return cdevValue;

        case updateDev:                            /* window needs redraw: list + LEDs */
            if (cdevValue) ((CPState *)(*(Handle)cdevValue))->listDirty = 1;
            PollAndShow(cdevValue, numItems, cpDialog);
            return cdevValue;

        case activDev:                             /* panel came to front: refresh all */
            if (cdevValue) {
                CPState *st = (CPState *)(*(Handle)cdevValue);
                st->lastDaemon   = -1;
                st->lastAuto     = -1;
                st->helpersShown = 0;
                st->listDirty    = 1;
            }
            /* fall through to poll-and-show */
        case nulDev:                               /* idle: poll status; draw list if dirty */
            PollAndShow(cdevValue, numItems, cpDialog);
            return cdevValue;

        case closeDev:
            if (cdevValue) DisposeHandle((Handle) cdevValue);
            return 0;

        default:
            return cdevValue;
    }
}

/* Is the faceless daemon (creator 'ABrg') currently a running process? */
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

/* Is the watchdog autostart alias present in Startup Items? (read-only) */
static Boolean AutostartInstalled(void)
{
    Str255 nm;
    FSSpec spec;
    short  vRefNum, i = 0;
    long   dirID;

    nm[++i]='A';nm[++i]='p';nm[++i]='p';nm[++i]='l';nm[++i]='e';
    nm[++i]='B';nm[++i]='r';nm[++i]='i';nm[++i]='d';nm[++i]='g';nm[++i]='e';
    nm[++i]=' ';
    nm[++i]='W';nm[++i]='a';nm[++i]='t';nm[++i]='c';nm[++i]='h';nm[++i]='d';nm[++i]='o';nm[++i]='g';
    nm[0] = (unsigned char) i;

    if (FindFolder(kOnSystemDisk, kStartupFolderType, kDontCreateFolder,
                   &vRefNum, &dirID) != noErr)
        return false;
    return (FSMakeFSSpec(vRefNum, dirID, nm, &spec) == noErr);
}

/* FSSpec of the shared prefs file "AppleBridge Prefs" in the Preferences folder
 * (name built on the stack). Returns noErr if it exists, fnfErr if not yet
 * (spec is still filled, ready for FSpCreate), else the FindFolder error. */
static OSErr PrefsSpec(FSSpec *spec)
{
    Str255 nm;
    OSErr  err;
    short  vRefNum, i = 0;
    long   dirID;

    nm[++i]='A';nm[++i]='p';nm[++i]='p';nm[++i]='l';nm[++i]='e';
    nm[++i]='B';nm[++i]='r';nm[++i]='i';nm[++i]='d';nm[++i]='g';nm[++i]='e';
    nm[++i]=' ';
    nm[++i]='P';nm[++i]='r';nm[++i]='e';nm[++i]='f';nm[++i]='s';
    nm[0] = (unsigned char) i;

    err = FindFolder(kOnSystemDisk, kPreferencesFolderType, kDontCreateFolder,
                     &vRefNum, &dirID);
    if (err != noErr) return err;
    return FSMakeFSSpec(vRefNum, dirID, nm, spec);
}

/* Build "Vol:dir:...:leaf" for an FSSpec by walking parent dirs with
 * PBGetCatInfo. All bytes come from the file system, so it stays A4-free
 * (inline string loops, no glue strcpy/strcat). */
static void FSSpecToPath(const FSSpec *spec, char *path)
{
    CInfoPBRec pb;
    Str255     nm;
    char       acc[kPathBufSize], tmp[kPathBufSize];
    long       dirID, t, s;
    short      m, k;

    m = spec->name[0];                          /* leaf name -> acc */
    for (k = 0; k < m; k++) acc[k] = spec->name[k + 1];
    acc[m] = '\0';

    dirID = spec->parID;
    while (dirID != fsRtParID) {                /* fsRtParID (1) is above the root */
        pb.dirInfo.ioCompletion = NULL;
        pb.dirInfo.ioNamePtr    = nm;
        pb.dirInfo.ioVRefNum    = spec->vRefNum;
        pb.dirInfo.ioFDirIndex  = -1;           /* info about dirID itself */
        pb.dirInfo.ioDrDirID    = dirID;
        if (PBGetCatInfoSync(&pb) != noErr) break;

        t = 0;                                  /* tmp = nm + ":" + acc */
        for (k = 1; k <= nm[0] && t < kPathBufSize - 2; k++) tmp[t++] = nm[k];
        tmp[t++] = ':';
        for (s = 0; acc[s] && t < kPathBufSize - 1; s++) tmp[t++] = acc[s];
        tmp[t] = '\0';
        for (s = 0; s <= t; s++) acc[s] = tmp[s];    /* acc = tmp (incl. NUL) */

        dirID = pb.dirInfo.ioDrParID;
    }
    for (s = 0; acc[s]; s++) path[s] = acc[s];
    path[s] = '\0';
}

/* Standard File picker -> append an "APP=<path>" line to the prefs file. */
static void AddHelper(short cdevRes)
{
    Point       where;
    Str255      prompt, line;
    SFReply     reply;
    SFTypeList  types;
    FSSpec      spec;
    CInfoPBRec  cpb;
    OSErr       err;
    short       refNum, li = 0, k, saved;
    long        count;
    char        path[kPathBufSize];

    where.v = 90;  where.h = 100;
    /* Say what the picker wants BEFORE opening it. The prompt stays empty and
       that is NOT the omission it looks like: the Standard File package IGNORES
       SFGetFile's prompt (Inside Macintosh — only SFPutFile displays one), so a
       string here would change nothing on screen while looking like guidance. */
    prompt[0] = 0;
    saved = CurResFile();
    UseResFile(cdevRes);
    NoteAlert(kAddHelperAlert, (ModalFilterProcPtr) 0);
    UseResFile(saved);                      /* leave the chain as we found it */

    /* Refuse a choice that cannot work and hand the picker straight back, so
       the operator stays in the task instead of being dropped out of it with a
       bad entry saved. Both refusals fail LATER and expensively if accepted:
       one of AppleBridge's own applications is circular (the watchdog already
       owns the daemon's lifecycle, and an APP= naming the daemon has it launch
       itself every boot), and a non-'APPL' is rejected by the daemon's LAUNCH
       verb at chain-launch, long after anyone remembers picking it. */
    for (;;) {
        SFGetFile(where, prompt, (FileFilterProcPtr) 0,
                  -1, types, (DlgHookProcPtr) 0, &reply);
        if (!reply.good) return;            /* Cancel: add nothing, quietly */

        /* SFReply gives a WDRefNum + name; FSMakeFSSpec resolves it. */
        if (FSMakeFSSpec(reply.vRefNum, 0, reply.fName, &spec) != noErr) return;

        /* Finder info via PBGetCatInfo, not FSpGetFInfo: the FSSpec convenience
           call is GLUE and glue fails this link (see the header note) — the
           same reason the list is self-drawn instead of using the List Manager.
           This file already walks directories with PBGetCatInfo. */
        cpb.hFileInfo.ioNamePtr   = spec.name;
        cpb.hFileInfo.ioVRefNum   = spec.vRefNum;
        cpb.hFileInfo.ioDirID     = spec.parID;
        cpb.hFileInfo.ioFDirIndex = 0;
        if (PBGetCatInfo(&cpb, false) != noErr) return;

        if (cpb.hFileInfo.ioFlFndrInfo.fdCreator == kDaemonCreator
            || cpb.hFileInfo.ioFlFndrInfo.fdCreator == kConfigCreator
            || cpb.hFileInfo.ioFlFndrInfo.fdCreator == kWatchdogCreator
            || cpb.hFileInfo.ioFlFndrInfo.fdCreator == kInstallerCreator) {
            saved = CurResFile();
            UseResFile(cdevRes);
            NoteAlert(kOwnSuiteAlert, (ModalFilterProcPtr) 0);
            UseResFile(saved);
            continue;
        }
        if (cpb.hFileInfo.ioFlFndrInfo.fdType != 'APPL') {
            saved = CurResFile();
            UseResFile(cdevRes);
            NoteAlert(kNotAnAppAlert, (ModalFilterProcPtr) 0);
            UseResFile(saved);
            continue;
        }
        break;
    }

    FSSpecToPath(&spec, path);
    if (path[0] == '\0') return;

    line[++li]='A'; line[++li]='P'; line[++li]='P'; line[++li]='=';   /* "APP=" */
    for (k = 0; path[k] && li < 253; k++) line[++li] = path[k];
    line[++li] = '\r';                          /* Mac line ending */
    line[0] = (unsigned char) li;

    err = PrefsSpec(&spec);                      /* spec filled even if fnfErr */
    if (err == fnfErr)
        FSpCreate(&spec, kDaemonCreator, 'TEXT', 0);   /* daemon owns its prefs */
    else if (err != noErr)
        return;

    if (FSpOpenDF(&spec, fsRdWrPerm, &refNum) != noErr) return;
    SetFPos(refNum, fsFromLEOF, 0);             /* append at end (thin trap-wrapper glue) */
    count = li;
    FSWrite(refNum, &count, &line[1]);          /* the chars, after the length byte */
    FSClose(refNum);
}

/* Remove the selected helper: rewrite the prefs file omitting the Nth APP= line
 * (N = selRow). Reads the file into a stack buffer, compacts out the target line
 * in place (write pointer never overtakes the read pointer), writes it back and
 * truncates with SetEOF. selRow captured to a local up front, so the handle may
 * move under the file traps; re-dereferenced afterwards to clear the selection. */
static void RemoveSelectedHelper(Handle h)
{
    FSSpec   spec;
    CPState *st;
    short    refNum, target;
    long     len = 0, r, wp;
    short    appIdx;
    char     buf[kPrefsBufSize];

    st = (CPState *)(*h);
    target = st->selRow;
    if (target < 0) return;

    if (PrefsSpec(&spec) != noErr) return;
    if (FSpOpenDF(&spec, fsRdWrPerm, &refNum) != noErr) return;
    len = kPrefsBufSize;
    FSRead(refNum, &len, buf);                   /* len <- bytes read */

    r = 0; wp = 0; appIdx = 0;
    while (r < len) {
        long ls = r, le, k;
        Boolean isApp, drop = false;
        while (r < len && buf[r] != '\r' && buf[r] != '\n') r++;   /* to EOL */
        le = r;
        while (r < len && (buf[r] == '\r' || buf[r] == '\n')) r++;  /* past EOL */
        isApp = (le - ls >= 4 &&
                 buf[ls]=='A' && buf[ls+1]=='P' && buf[ls+2]=='P' && buf[ls+3]=='=');
        if (isApp) { if (appIdx == target) drop = true; appIdx++; }
        if (!drop)
            for (k = ls; k < r; k++) buf[wp++] = buf[k];   /* keep whole line incl. EOL */
    }

    SetFPos(refNum, fsFromStart, 0);
    len = wp;
    FSWrite(refNum, &len, buf);                  /* overwrite from start */
    SetEOF(refNum, wp);                          /* truncate the tail */
    FSClose(refNum);

    st = (CPState *)(*h);                         /* re-deref after the file traps */
    st->selRow = -1;
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

/* Set a statText (the windowKind juggle a cdev needs). */
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

/* Draw a status LED just LEFT of a status line: light-green = good, light-red =
 * bad. A4-free: RGBColor fields set with immediate constants (no initialised-data
 * globals), and only inline Color-QuickDraw traps (RGBForeColor/PaintOval/FrameOval). */
static void DrawLED(DialogPtr cpDialog, const Rect *box, Boolean good)
{
    GrafPtr  savePort;
    RGBColor saveFore, c;
    Rect     led;

    GetPort(&savePort);
    SetPort((GrafPtr) cpDialog);
    GetForeColor(&saveFore);

    led.top    = (short)(box->top + 2);
    led.left   = 94;
    led.bottom = (short)(box->top + 14);
    led.right  = 106;

    if (good) { c.red = 0x2000; c.green = 0xD000; c.blue = 0x2000; }  /* hellgruen */
    else      { c.red = 0xF000; c.green = 0x3000; c.blue = 0x3000; }  /* hellrot  */
    RGBForeColor(&c);
    PaintOval(&led);

    c.red = c.green = c.blue = 0;            /* black ring for definition */
    RGBForeColor(&c);
    FrameOval(&led);

    RGBForeColor(&saveFore);                 /* restore caller's colour + port */
    SetPort(savePort);
}

/* Read the prefs file ONCE: show the host IP ("Host IP: <value>" from the IP=
 * line) and cache each APP= line's leaf name for the list. A4-free: char
 * constants + file bytes. The IP statText is set BEFORE the handle is
 * dereferenced (SetDialogItemText may move memory); the leaf cache is then
 * filled with no intervening moving traps. */
static void ReadPrefs(DialogPtr cpDialog, short numItems, Handle h)
{
    FSSpec   spec;
    CPState *st;
    short    refNum, oi;
    long     len = 0, j;
    char     buf[kPrefsBufSize];
    Str255   out;
    Boolean  haveFile = false;

    if (PrefsSpec(&spec) == noErr && FSpOpenDF(&spec, fsRdPerm, &refNum) == noErr) {
        len = kPrefsBufSize;                      /* FSRead/FSClose: thin trap-wrapper */
        FSRead(refNum, &len, buf);                /* glue (A4-safe), len <- bytes read */
        FSClose(refNum);
        haveFile = true;
    }

    /* --- "Host IP: <value>" from the first IP= line (statText; may move) --- */
    oi = 0;
    out[++oi]='H'; out[++oi]='o'; out[++oi]='s'; out[++oi]='t'; out[++oi]=' ';
    out[++oi]='I'; out[++oi]='P'; out[++oi]=':'; out[++oi]=' ';
    if (haveFile) {
        j = 0;
        while (j < len) {
            if (j + 3 <= len && buf[j]=='I' && buf[j+1]=='P' && buf[j+2]=='=') {
                long v = j + 3, k;
                while (v < len && buf[v] != '\r' && buf[v] != '\n') v++;
                for (k = j + 3; k < v && oi < 250; k++) out[++oi] = buf[k];
                break;
            }
            while (j < len && buf[j] != '\r' && buf[j] != '\n') j++;
            while (j < len && (buf[j] == '\r' || buf[j] == '\n')) j++;
        }
    }
    out[0] = (unsigned char) oi;
    ShowText(cpDialog, numItems, kIP, out);       /* <- last possible memory move */

    /* --- cache the APP= leaves (no moving traps below: safe to hold st) --- */
    st = (CPState *)(*h);
    st->helperCount = 0;
    if (haveFile) {
        j = 0;
        while (j < len && st->helperCount < kMaxHelpers) {
            if (j + 4 <= len &&
                buf[j]=='A' && buf[j+1]=='P' && buf[j+2]=='P' && buf[j+3]=='=') {
                long v = j + 4, leaf = j + 4, k;
                short n = 0;
                while (v < len && buf[v] != '\r' && buf[v] != '\n') v++;
                for (k = j + 4; k < v; k++) if (buf[k] == ':') leaf = k + 1;
                for (k = leaf; k < v && n < kLeafMax; k++)
                    st->leaf[st->helperCount][n++] = buf[k];
                st->leaf[st->helperCount][n] = '\0';
                st->helperCount++;
            }
            while (j < len && buf[j] != '\r' && buf[j] != '\n') j++;   /* to EOL */
            while (j < len && (buf[j] == '\r' || buf[j] == '\n')) j++;  /* past it */
        }
    }
    if (st->selRow >= st->helperCount) st->selRow = st->helperCount - 1;
    if (st->helperCount == 0)          st->selRow = -1;
}

/* Draw the helper list into its userItem box: a frame, one leaf per row, the
 * selected row on a light-blue background, empty slots cleared. Idempotent and
 * flicker-free (no InvertRect toggling). Inline QuickDraw only; A4-free. Called
 * from PollAndShow only when the list is dirty. */
static void DrawHelperList(DialogPtr cpDialog, short numItems, Handle h)
{
    GrafPtr   savePort;
    RGBColor  saveFore, c;
    RgnHandle saveClip;
    CPState  *st;
    short     type, i, n, top;
    Handle    ih;
    Rect      box, row;
    Str255    s;

    GetDialogItem(cpDialog, numItems + kHelperList, &type, &ih, &box);
    GetPort(&savePort);
    SetPort((GrafPtr) cpDialog);
    GetForeColor(&saveFore);
    saveClip = NewRgn();                          /* allocates: before we hold st */
    GetClip(saveClip);
    ClipRect(&box);

    c.red = c.green = c.blue = 0;                 /* black frame */
    RGBForeColor(&c);
    FrameRect(&box);

    TextFont(3);                                  /* Geneva */
    TextSize(9);

    /* Walk rows by ADDING kRowHeight each step - no divide/multiply, which would
     * pull the LDIVT/LMUL runtime helpers (unreachable in a code resource). */
    st = (CPState *)(*h);                          /* no moving traps past here */
    top = (short)(box.top + 1);
    for (i = 0; ; i++) {
        row.left   = (short)(box.left + 1);
        row.right  = (short)(box.right - 1);
        row.top    = top;
        row.bottom = (short)(top + kRowHeight);
        if (row.bottom > box.bottom - 1) break;
        top = (short)(top + kRowHeight);

        if (i < st->helperCount) {
            if (i == st->selRow) {                /* selected: light-blue bg */
                c.red = 0xC000; c.green = 0xD800; c.blue = 0xFF00;
                RGBForeColor(&c);
                PaintRect(&row);
            } else {
                EraseRect(&row);                  /* white */
            }
            c.red = c.green = c.blue = 0;         /* black text */
            RGBForeColor(&c);
            n = 0;
            while (st->leaf[i][n]) n++;
            MoveTo((short)(box.left + 4), (short)(row.top + 10));
            DrawText(st->leaf[i], 0, n);
        } else {
            EraseRect(&row);                      /* empty slot */
        }
    }

    if (st->helperCount == 0) {                    /* "(none)" hint, grey */
        c.red = c.green = c.blue = 0x8000;
        RGBForeColor(&c);
        s[0]=6; s[1]='('; s[2]='n'; s[3]='o'; s[4]='n'; s[5]='e'; s[6]=')';
        MoveTo((short)(box.left + 4), (short)(box.top + 11));
        DrawString(s);
    }

    RGBForeColor(&saveFore);
    TextFont(0);                                  /* restore system font */
    TextSize(0);
    SetClip(saveClip);
    DisposeRgn(saveClip);
    SetPort(savePort);
}

/* A click in the list userItem: map the event point to a row and select it. */
static void SelectHelperRow(DialogPtr cpDialog, short numItems, Handle h,
                            EventRecord *event)
{
    GrafPtr  savePort;
    CPState *st;
    short    type, r, top;
    Handle   ih;
    Rect     box;
    Point    pt;

    GetDialogItem(cpDialog, numItems + kHelperList, &type, &ih, &box);
    GetPort(&savePort);
    SetPort((GrafPtr) cpDialog);
    pt = event->where;
    GlobalToLocal(&pt);                           /* to the panel's local coords */
    SetPort(savePort);

    if (!PtInRect(pt, &box)) return;
    /* find the row containing pt.v by ADDING kRowHeight (no divide -> no LDIVT). */
    r = 0;
    top = (short)(box.top + 1);
    if (pt.v < top) return;
    while (pt.v >= (short)(top + kRowHeight)) { r++; top = (short)(top + kRowHeight); }
    st = (CPState *)(*h);
    if (r >= 0 && r < st->helperCount) st->selRow = r;
}

/* Poll daemon + autostart (redraw on change), re-read the prefs when helpersShown
 * was cleared, and redraw the list only when dirty. Memory-safe: deref cdevValue
 * AFTER the heap-moving poll traps, and write all state before any moving draw. */
static void PollAndShow(long cdevValue, short numItems, DialogPtr cpDialog)
{
    Handle   h = (Handle) cdevValue;
    short    dnow, anow;
    Boolean  drawD, drawA, drawH, dirty;
    CPState *st;

    if (!h) return;
    dnow = DaemonRunning()      ? 1 : 0;     /* <- may move memory */
    anow = AutostartInstalled() ? 1 : 0;     /* <- may move memory */

    st    = (CPState *)(*h);                  /* deref AFTER the move-risk calls */
    drawD = (dnow != st->lastDaemon);
    drawA = (anow != st->lastAuto);
    drawH = (st->helpersShown == 0);
    st->lastDaemon   = dnow;                   /* write all state before drawing */
    st->lastAuto     = anow;
    st->helpersShown = 1;
    if (drawH) st->listDirty = 1;              /* a re-read forces a redraw */
    dirty = (st->listDirty != 0);
    st->listDirty = 0;

    if (drawD) { Str255 b; DaemonString(b, dnow); ShowText(cpDialog, numItems, kStatus, b); }
    if (drawA) { Str255 b; AutoString(b, anow);   ShowText(cpDialog, numItems, kAutostart, b); }
    if (drawH) ReadPrefs(cpDialog, numItems, h);     /* host IP statText + leaf cache */
    if (dirty) DrawHelperList(cpDialog, numItems, h);/* self-drawn userItem list */

    /* LEDs: redraw EVERY poll (not just on change) so they survive update events,
     * using the item boxes only for vertical alignment. Two small ovals. */
    {
        short  type;
        Handle ih;
        Rect   box;
        GetDialogItem(cpDialog, numItems + kStatus, &type, &ih, &box);
        DrawLED(cpDialog, &box, (Boolean) dnow);
        GetDialogItem(cpDialog, numItems + kAutostart, &type, &ih, &box);
        DrawLED(cpDialog, &box, (Boolean) anow);
    }
}
