/*
 * AppleBridge Control Panel - cdev proof of concept.
 *
 * Steps 1-3 proved the cdev mechanics. Step 4 ports AppleBridgeConfig's real
 * logic into the cdev, in on-device-proven sub-steps:
 *   4a:        live DAEMON status via the Process Manager.
 *   4b-detect: live AUTOSTART status via the Folder Manager.
 *   4c (this file): the HELPER-APP list + an "Add Helper App..." button. The
 *      button pops Standard File (SFGetFile, from hitDev - proven in step 3),
 *      turns the choice into an HFS path (FSMakeFSSpec + walking parents with
 *      PBGetCatInfo), and APPENDS an "APP=" line to the shared prefs file; the
 *      list then re-reads and shows the helper leaf names. (Autostart
 *      Install/Remove buttons are intentionally dropped - that toggle is the
 *      Finder's job via Startup Items - so autostart is shown read-only.)
 *
 * Discipline unchanged: A4-free (state in the cdevValue handle; no globals; no
 * string literals - labels/prefs-name built on the stack from char constants,
 * helper paths come from the file system and the prefs file); only inline
 * Toolbox traps (glue fails the LINK, caught before install); CDevMain FIRST
 * (offset 0); cdevValue re-dereferenced AFTER any heap-moving trap.
 */

#include <Types.h>
#include <Quickdraw.h>
#include <Windows.h>
#include <Dialogs.h>
#include <Events.h>
#include <Memory.h>
#include <Processes.h>      /* GetNextProcess / GetProcessInformation */
#include <Files.h>          /* FSSpec, FSMakeFSSpec, FSpOpenDF/Create, FSRead/Write, PBGetCatInfo */
#include <Folders.h>        /* FindFolder, kStartupFolderType, kPreferencesFolderType */
#include <StandardFile.h>   /* SFGetFile, SFReply (inline-trap form) */

#define macDev    8
#define initDev   0
#define hitDev    1
#define closeDev  2
#define nulDev    3
#define activDev  5

#define kDaemonCreator    'ABrg'
#define kPrefsBufSize     512
#define kPathBufSize      256

/* our DITL items, 1-based within our own DITL */
enum { kLabel = 1, kStatus, kAutostart, kIP, kHelpers, kAddBtn };

/* per-instance state, kept in the cdevValue handle (no globals). */
typedef struct {
    short lastDaemon;     /* -1 unknown; 0/1 last shown */
    short lastAuto;
    short helpersShown;   /* 0 = (re)read the helper list on next poll */
} CPState;

/* CRITICAL: the host calls a cdev by jumping to OFFSET 0 of the 'cdev' resource,
 * so CDevMain MUST be first. Define it first; forward-declare the helpers. */
static Boolean DaemonRunning(void);
static Boolean AutostartInstalled(void);
static OSErr   PrefsSpec(FSSpec *spec);
static void    FSSpecToPath(const FSSpec *spec, char *path);
static void    AddHelper(void);
static void    DaemonString(Str255 d, Boolean running);
static void    AutoString(Str255 d, Boolean installed);
static void    ShowText(DialogPtr cpDialog, short numItems, short whichItem,
                        ConstStr255Param text);
static void    DrawLED(DialogPtr cpDialog, const Rect *box, Boolean good);
static void    ShowPrefsLines(DialogPtr cpDialog, short numItems);
static void    PollAndShow(long cdevValue, short numItems, DialogPtr cpDialog);

pascal long CDevMain(short message, short item, short numItems, short rsrcID,
                     EventRecord *event, long cdevValue, DialogPtr cpDialog)
{
#pragma unused(rsrcID, event)
    switch (message) {
        case macDev:
            return 1;                              /* appear on this machine */

        case initDev: {
            Handle h = NewHandle(sizeof(CPState));
            if (h) {
                CPState *st = (CPState *)(*h);
                st->lastDaemon   = -1;             /* force first poll to draw */
                st->lastAuto     = -1;
                st->helpersShown = 0;
            }
            return (long) h;
        }

        case hitDev:                               /* Add Helper App button */
            if (item - numItems == kAddBtn) {
                AddHelper();                       /* SFGetFile -> append APP= to prefs */
                if (cdevValue)                     /* force the helper list to re-read */
                    ((CPState *)(*(Handle)cdevValue))->helpersShown = 0;
                PollAndShow(cdevValue, numItems, cpDialog);
            }
            return cdevValue;

        case activDev:                             /* panel came to front: refresh all */
            if (cdevValue) {
                CPState *st = (CPState *)(*(Handle)cdevValue);
                st->lastDaemon   = -1;
                st->lastAuto     = -1;
                st->helpersShown = 0;
            }
            /* fall through to poll-and-show */
        case nulDev:                               /* idle: poll status (+ helpers once) */
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
static void AddHelper(void)
{
    Point       where;
    Str255      prompt, line;
    SFReply     reply;
    SFTypeList  types;
    FSSpec      spec;
    OSErr       err;
    short       refNum, li = 0, k;
    long        count;
    char        path[kPathBufSize];

    where.v = 90;  where.h = 100;
    prompt[0] = 0;                               /* empty prompt, built on stack */
    SFGetFile(where, prompt, (FileFilterProcPtr) 0,
              -1, types, (DlgHookProcPtr) 0, &reply);
    if (!reply.good) return;

    /* SFReply gives a WDRefNum + name; FSMakeFSSpec resolves it to an FSSpec. */
    if (FSMakeFSSpec(reply.vRefNum, 0, reply.fName, &spec) != noErr) return;
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
 * globals), and only inline Color-QuickDraw traps (RGBForeColor/PaintOval/FrameOval).
 * The LED sits at a fixed x (94..106) inside the panel but outside any DITL item
 * box (the status items start at left=114), so SetDialogItemText never erases it.
 * `box` is the status item's display rect, used only for vertical alignment. */
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

    if (good) { c.red = 0x2000; c.green = 0xD000; c.blue = 0x2000; }  /* hellgrün */
    else      { c.red = 0xF000; c.green = 0x3000; c.blue = 0x3000; }  /* hellrot  */
    RGBForeColor(&c);
    PaintOval(&led);

    c.red = c.green = c.blue = 0;            /* black ring for definition */
    RGBForeColor(&c);
    FrameOval(&led);

    RGBForeColor(&saveFore);                 /* restore caller's colour + port */
    SetPort(savePort);
}

/* Read the prefs file ONCE and show two lines from it: the host IP ("Host IP:
 * <value>" from the IP= line) and the helper list ("Helpers: name1, name2" from
 * each APP= line's leaf, or "Helpers: none"). A4-free: char constants + file bytes. */
static void ShowPrefsLines(DialogPtr cpDialog, short numItems)
{
    FSSpec  spec;
    short   refNum;
    long    len = 0, j;
    char    buf[kPrefsBufSize];
    Str255  out;
    short   oi, n;
    Boolean haveFile = false;

    if (PrefsSpec(&spec) == noErr && FSpOpenDF(&spec, fsRdPerm, &refNum) == noErr) {
        len = kPrefsBufSize;                      /* FSRead/FSClose: thin trap-wrapper */
        FSRead(refNum, &len, buf);                /* glue (A4-safe), len <- bytes read */
        FSClose(refNum);
        haveFile = true;
    }

    /* --- "Host IP: <value>" from the first IP= line --- */
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
    ShowText(cpDialog, numItems, kIP, out);

    /* --- "Helpers: leaf1, leaf2" from the APP= lines --- */
    oi = 0; n = 0;
    out[++oi]='H'; out[++oi]='e'; out[++oi]='l'; out[++oi]='p'; out[++oi]='e';
    out[++oi]='r'; out[++oi]='s'; out[++oi]=':'; out[++oi]=' ';
    if (haveFile) {
        j = 0;
        while (j < len) {
            if (j + 4 <= len &&
                buf[j]=='A' && buf[j+1]=='P' && buf[j+2]=='P' && buf[j+3]=='=') {
                long v = j + 4, leaf = j + 4, k;
                while (v < len && buf[v] != '\r' && buf[v] != '\n') v++;
                for (k = j + 4; k < v; k++) if (buf[k] == ':') leaf = k + 1;
                if (n > 0 && oi < 250) { out[++oi]=','; out[++oi]=' '; }
                for (k = leaf; k < v && oi < 250; k++) out[++oi] = buf[k];
                n++;
            }
            while (j < len && buf[j] != '\r' && buf[j] != '\n') j++;   /* to EOL */
            while (j < len && (buf[j] == '\r' || buf[j] == '\n')) j++;  /* past it */
        }
    }
    if (n == 0) { out[++oi]='n'; out[++oi]='o'; out[++oi]='n'; out[++oi]='e'; }
    out[0] = (unsigned char) oi;
    ShowText(cpDialog, numItems, kHelpers, out);
}

/* Poll daemon + autostart (redraw on change), and read+show the helper list once
 * per activation / after an Add. Memory-safe: deref cdevValue AFTER the heap-moving
 * poll traps, write state before drawing. */
static void PollAndShow(long cdevValue, short numItems, DialogPtr cpDialog)
{
    Handle   h = (Handle) cdevValue;
    short    dnow, anow;
    Boolean  drawD, drawA, drawH;
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

    if (drawD) { Str255 b; DaemonString(b, dnow); ShowText(cpDialog, numItems, kStatus, b); }
    if (drawA) { Str255 b; AutoString(b, anow);   ShowText(cpDialog, numItems, kAutostart, b); }
    if (drawH) ShowPrefsLines(cpDialog, numItems);   /* host IP + helper list */

    /* LEDs: redraw EVERY poll (not just on change) so they survive update events,
     * using the item boxes only for vertical alignment. Two small ovals/tick. */
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
