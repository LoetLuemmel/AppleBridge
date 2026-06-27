/*
 * AppleBridgeConfig - control panel for the faceless AppleBridge daemon
 *
 * The daemon (creator 'ABrg') is onlyBackground and has no UI, so this normal
 * foreground app is where a human:
 *   - sees whether the daemon is running + whether autostart is installed,
 *   - installs / removes autostart (an alias to the watchdog in Startup Items;
 *     at boot the watchdog launches the daemon and keeps it alive),
 *   - picks helper apps (e.g. ToolServer) to chain-launch, via Standard File,
 *   - reviews the prefs (host IP + helper list).
 * It shares prefs.c with the daemon and talks to it only through the prefs file
 * and the Startup Items alias — no direct linkage. The daemon is meant to run
 * continuously, so there are no Launch/Stop buttons (use autostart + the Finder).
 */

#include <Quickdraw.h>
#include <Fonts.h>
#include <Windows.h>
#include <Menus.h>
#include <Events.h>
#include <Controls.h>
#include <Dialogs.h>
#include <TextEdit.h>
#include <StandardFile.h>
#include <Processes.h>
#include <AppleEvents.h>
#include <Files.h>
#include <Folders.h>
#include <Aliases.h>
#include <Resources.h>
#include <prefs.h>
#include <mystring.h>

QDGlobals qd;

#define kDaemonCreator  'ABrg'
#define DAEMON_PATH     "MeinMac:MPW:AppleBridge:bin:AppleBridge"
#define WATCHDOG_PATH   "MeinMac:MPW:AppleBridge:bin:AppleBridgeWatchdog"

static Boolean      gRunning = true;
static WindowPtr    gWin = NULL;
static AppPrefs     gPrefs;
static ControlHandle gInstallBtn, gRemoveBtn, gAddBtn, gQuitBtn;

/* ---- small helpers ---------------------------------------------------- */

static void CtoP(const char *c, Str255 p)
{
    short i = 0;
    while (c[i] && i < 255) { p[i + 1] = c[i]; i++; }
    p[0] = (unsigned char)i;
}

static void PtoC(const unsigned char *p, char *c)
{
    short i, n = p[0];
    for (i = 0; i < n; i++) c[i] = p[i + 1];
    c[n] = '\0';
}

/* Is the daemon (creator 'ABrg') currently running? */
static Boolean DaemonRunning(void)
{
    ProcessSerialNumber psn;
    ProcessInfoRec info;

    psn.highLongOfPSN = 0;
    psn.lowLongOfPSN = kNoProcess;
    while (GetNextProcess(&psn) == noErr) {
        info.processInfoLength = sizeof(info);
        info.processName = NULL;
        info.processAppSpec = NULL;
        if (GetProcessInformation(&psn, &info) == noErr) {
            if (info.processSignature == kDaemonCreator) return true;
        }
    }
    return false;
}

/* ---- autostart (a watchdog alias in the System Folder's Startup Items) ----
 * Autostart installs an alias to the *watchdog*, not the daemon: at boot the
 * watchdog comes up, launches the daemon (which chain-launches ToolServer), and
 * then keeps the daemon alive. One Startup Items entry owns the whole service. */

#define kStartupAliasName "\pAppleBridge Watchdog"

/* FSSpec of the binary we want launched at boot — the watchdog. */
static OSErr WatchdogSpec(FSSpec *spec)
{
    Str255 pPath;
    CtoP(WATCHDOG_PATH, pPath);
    return FSMakeFSSpec(0, 0, pPath, spec);
}

/* FSSpec of our alias file inside Startup Items (fnfErr if not present). */
static OSErr StartupAliasSpec(FSSpec *spec)
{
    OSErr err;
    short vRefNum;
    long  dirID;

    err = FindFolder(kOnSystemDisk, kStartupFolderType, kDontCreateFolder,
                     &vRefNum, &dirID);
    if (err != noErr) return err;
    return FSMakeFSSpec(vRefNum, dirID, kStartupAliasName, spec);
}

static Boolean AutostartInstalled(void)
{
    FSSpec spec;
    return (StartupAliasSpec(&spec) == noErr);   /* noErr == file exists */
}

/* Drop an alias to the watchdog into Startup Items so it launches at boot. */
static OSErr InstallAutostart(void)
{
    FSSpec      target, aliasFile;
    AliasHandle alias;
    OSErr       err;
    short       refNum;
    FInfo       fi;

    err = WatchdogSpec(&target);
    if (err != noErr) return err;                 /* watchdog binary not found */
    err = StartupAliasSpec(&aliasFile);
    if (err == noErr) return noErr;               /* already installed */
    if (err != fnfErr) return err;

    err = NewAlias(NULL, &target, &alias);        /* absolute alias */
    if (err != noErr) return err;

    FSpCreateResFile(&aliasFile, 'ABwd', 'APPL', 0);
    err = ResError();
    if (err != noErr && err != dupFNErr) { DisposeHandle((Handle)alias); return err; }

    refNum = FSpOpenResFile(&aliasFile, fsRdWrPerm);
    if (refNum == -1) { DisposeHandle((Handle)alias); return ResError(); }
    UseResFile(refNum);
    AddResource((Handle)alias, 'alis', 0, aliasFile.name);
    if (ResError() == noErr) WriteResource((Handle)alias);
    CloseResFile(refNum);

    /* Mark it as an alias so the Finder resolves the target at startup. */
    if (FSpGetFInfo(&aliasFile, &fi) == noErr) {
        fi.fdFlags |= 0x8000;                     /* kIsAlias */
        FSpSetFInfo(&aliasFile, &fi);
    }
    return noErr;
}

static OSErr RemoveAutostart(void)
{
    FSSpec aliasFile;
    OSErr  err = StartupAliasSpec(&aliasFile);
    if (err != noErr) return err;                 /* not installed */
    return FSpDelete(&aliasFile);
}

/* Build a full "Vol:dir:...:name" HFS path from an FSSpec by walking parents. */
static void FSSpecToPath(const FSSpec *spec, char *path)
{
    CInfoPBRec pb;
    Str255 nm;
    char nameC[256];                /* NB: 'comp' is a reserved MPW type */
    char acc[512];
    char tmp[512];
    long dirID;

    PtoC(spec->name, acc);          /* leaf name */

    dirID = spec->parID;
    while (dirID != fsRtParID) {    /* fsRtParID (1) is "above" the root */
        pb.dirInfo.ioCompletion = NULL;
        pb.dirInfo.ioNamePtr = nm;
        pb.dirInfo.ioVRefNum = spec->vRefNum;
        pb.dirInfo.ioFDirIndex = -1;        /* info about dirID itself */
        pb.dirInfo.ioDrDirID = dirID;
        if (PBGetCatInfoSync(&pb) != noErr) break;
        PtoC(nm, nameC);
        mystrcpy(tmp, nameC);
        mystrcat(tmp, ":");
        mystrcat(tmp, acc);
        mystrcpy(acc, tmp);
        dirID = pb.dirInfo.ioDrParID;
    }
    mystrcpy(path, acc);
}

/* Standard File picker -> append an APP= path to prefs, save, reload. */
static void AddHelperApp(void)
{
    StandardFileReply reply;
    char path[PREFS_PATH_LEN];

    StandardGetFile(NULL, -1, NULL, &reply);
    if (!reply.sfGood) return;

    FSSpecToPath(&reply.sfFile, path);
    if (path[0] && gPrefs.appCount < PREFS_MAX_APPS) {
        mystrncpy(gPrefs.apps[gPrefs.appCount], path, PREFS_PATH_LEN - 1);
        gPrefs.apps[gPrefs.appCount][PREFS_PATH_LEN - 1] = '\0';
        gPrefs.appCount++;
        SavePrefs(&gPrefs);
    }
    LoadPrefs(&gPrefs);
}

/* ---- UI ---------------------------------------------------------------- */

static void DrawContent(void)
{
    Rect r;
    short i, y;

    SetPort(gWin);
    r = gWin->portRect;
    r.bottom -= 44;             /* leave the button strip */
    EraseRect(&r);

    TextFont(0); TextFace(bold); TextSize(12);
    MoveTo(16, 22);
    DrawString("\pAppleBridge Config");

    TextFace(0); TextSize(10);
    MoveTo(16, 44);
    if (DaemonRunning())
        DrawString("\pDaemon: RUNNING");
    else
        DrawString("\pDaemon: stopped");

    MoveTo(160, 44);
    if (AutostartInstalled())
        DrawString("\pAutostart: installed");
    else
        DrawString("\pAutostart: not installed");

    MoveTo(16, 62);
    DrawString("\pHost IP: ");
    { Str255 p; CtoP(gPrefs.ip, p); DrawString(p); }

    MoveTo(16, 84);
    DrawString("\pHelper apps (chain-launched):");
    y = 100;
    for (i = 0; i < gPrefs.appCount; i++) {
        Str255 p;
        MoveTo(28, y);
        CtoP(gPrefs.apps[i], p);
        DrawString(p);
        y += 14;
    }
    if (gPrefs.appCount == 0) {
        MoveTo(28, y);
        DrawString("\p(none - use Add Helper App...)");
    }

    DrawControls(gWin);
}

static void MakeButtons(void)
{
    Rect r;
    short top = gWin->portRect.bottom - 36;

    SetRect(&r, 12, top, 124, top + 20);
    gInstallBtn = NewControl(gWin, &r, "\pInstall Autostart", true, 0, 0, 1, 0 /*pushButProc*/, 0);
    SetRect(&r, 132, top, 256, top + 20);
    gRemoveBtn = NewControl(gWin, &r, "\pRemove Autostart", true, 0, 0, 1, 0 /*pushButProc*/, 0);
    SetRect(&r, 264, top, 376, top + 20);
    gAddBtn = NewControl(gWin, &r, "\pAdd Helper App...", true, 0, 0, 1, 0 /*pushButProc*/, 0);
    SetRect(&r, 384, top, 444, top + 20);
    gQuitBtn = NewControl(gWin, &r, "\pQuit", true, 0, 0, 1, 0 /*pushButProc*/, 0);
}

static void HandleClick(EventRecord *ev)
{
    WindowPtr w;
    ControlHandle ctl;
    short part;
    Point pt;

    part = FindWindow(ev->where, &w);
    if (part == inContent && w == gWin) {
        pt = ev->where;
        GlobalToLocal(&pt);
        if (FindControl(pt, gWin, &ctl)) {
            if (TrackControl(ctl, pt, NULL)) {
                if (ctl == gInstallBtn)     { InstallAutostart(); }
                else if (ctl == gRemoveBtn) { RemoveAutostart(); }
                else if (ctl == gAddBtn)    { AddHelperApp(); }
                else if (ctl == gQuitBtn)   { gRunning = false; }
                DrawContent();
            }
        }
    } else if (part == inDrag && w == gWin) {
        Rect b; SetRect(&b, 4, 24, qd.screenBits.bounds.right - 4,
                        qd.screenBits.bounds.bottom - 4);
        DragWindow(w, ev->where, &b);
    } else if (part == inGoAway && w == gWin) {
        if (TrackGoAway(w, ev->where)) gRunning = false;
    }
}

int main(void)
{
    EventRecord ev;
    Rect bounds;

    InitGraf(&qd.thePort);
    InitFonts();
    InitWindows();
    InitMenus();
    TEInit();
    InitDialogs(NULL);
    InitCursor();

    PrefsDefaults(&gPrefs);
    LoadPrefs(&gPrefs);

    SetRect(&bounds, 40, 60, 500, 320);
    gWin = NewCWindow(NULL, &bounds, "\pAppleBridge Config", true,
                      documentProc, (WindowPtr)-1L, true, 0);
    SetPort(gWin);
    MakeButtons();
    DrawContent();

    while (gRunning) {
        if (WaitNextEvent(everyEvent, &ev, 30L, NULL)) {
            switch (ev.what) {
                case mouseDown:
                    HandleClick(&ev);
                    break;
                case keyDown:
                case autoKey:
                    if ((ev.modifiers & cmdKey) &&
                        (ev.message & charCodeMask) == 'q') gRunning = false;
                    break;
                case updateEvt:
                    BeginUpdate((WindowPtr)ev.message);
                    DrawContent();
                    EndUpdate((WindowPtr)ev.message);
                    break;
            }
        }
    }

    if (gWin) DisposeWindow(gWin);
    return 0;
}
