/*
 * AppleBridgeConfig - control panel for the faceless AppleBridge daemon
 *
 * The daemon (creator 'ABrg') is onlyBackground and has no UI, so this normal
 * foreground app is where a human:
 *   - sees whether the daemon is running,
 *   - launches / stops it,
 *   - picks helper apps (e.g. ToolServer) to chain-launch, via Standard File,
 *   - reviews the prefs (host IP + helper list).
 * It shares prefs.c with the daemon and talks to the daemon only through the
 * prefs file + a quit Apple Event — no direct linkage.
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
#include <prefs.h>
#include <mystring.h>

QDGlobals qd;

#define kDaemonCreator  'ABrg'
#define DAEMON_PATH     "MeinMac:MPW:AppleBridge:bin:AppleBridge"

static Boolean      gRunning = true;
static WindowPtr    gWin = NULL;
static AppPrefs     gPrefs;
static ControlHandle gLaunchBtn, gStopBtn, gAddBtn, gQuitBtn;

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

/* Find the daemon's PSN; returns true if found. */
static Boolean FindDaemon(ProcessSerialNumber *out)
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
            if (info.processSignature == kDaemonCreator) { *out = psn; return true; }
        }
    }
    return false;
}

static OSErr LaunchDaemon(void)
{
    Str255 pPath;
    FSSpec spec;
    LaunchParamBlockRec lpb;
    OSErr err;

    CtoP(DAEMON_PATH, pPath);
    err = FSMakeFSSpec(0, 0, pPath, &spec);
    if (err != noErr) return err;

    lpb.launchBlockID = extendedBlock;
    lpb.launchEPBLength = extendedBlockLen;
    lpb.launchFileFlags = 0;
    lpb.launchControlFlags = launchContinue | launchNoFileFlags;
    lpb.launchAppSpec = &spec;
    lpb.launchAppParameters = NULL;
    return LaunchApplication(&lpb);
}

/* Stop the daemon with a kAEQuitApplication Apple Event (its handler quits). */
static OSErr StopDaemon(void)
{
    ProcessSerialNumber psn;
    AEAddressDesc target;
    AppleEvent event, reply;
    OSErr err;

    if (!FindDaemon(&psn)) return procNotFound;

    err = AECreateDesc(typeProcessSerialNumber, (Ptr)&psn, sizeof(psn), &target);
    if (err != noErr) return err;
    err = AECreateAppleEvent(kCoreEventClass, kAEQuitApplication, &target,
                             kAutoGenerateReturnID, kAnyTransactionID, &event);
    AEDisposeDesc(&target);
    if (err != noErr) return err;
    err = AESend(&event, &reply, kAENoReply, kAENormalPriority,
                 kAEDefaultTimeout, NULL, NULL);
    AEDisposeDesc(&event);
    return err;
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

    SetRect(&r, 12, top, 112, top + 20);
    gLaunchBtn = NewControl(gWin, &r, "\pLaunch Daemon", true, 0, 0, 1, 0 /*pushButProc*/, 0);
    SetRect(&r, 120, top, 200, top + 20);
    gStopBtn = NewControl(gWin, &r, "\pStop Daemon", true, 0, 0, 1, 0 /*pushButProc*/, 0);
    SetRect(&r, 208, top, 320, top + 20);
    gAddBtn = NewControl(gWin, &r, "\pAdd Helper App...", true, 0, 0, 1, 0 /*pushButProc*/, 0);
    SetRect(&r, 328, top, 388, top + 20);
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
                if (ctl == gLaunchBtn)      { LaunchDaemon(); }
                else if (ctl == gStopBtn)   { StopDaemon(); }
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

    SetRect(&bounds, 40, 60, 440, 320);
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
