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
#define kConfigCreator  'ABcf'
#define kWatchdogCreator 'ABwd'
#define kInstallerCreator 'ABis'
#define kAddHelperAlert 300     /* config_res.r — what the Add picker wants */
#define kOwnSuiteAlert  301     /* ...and what it will not accept */
#define kNotAnAppAlert  302

/* Is this file one of AppleBridge's own? Chain-launching the suite from the
   suite is circular: the watchdog already owns the daemon's lifecycle, and an
   APP= line naming the daemon has it launch itself at every boot. */
static Boolean IsOwnSuite(OSType creator)
{
    return creator == kDaemonCreator || creator == kConfigCreator
        || creator == kWatchdogCreator || creator == kInstallerCreator;
}
#define DAEMON_PATH     "MeinMac:MPW:AppleBridge:bin:AppleBridge"
#define WATCHDOG_PATH   "MeinMac:MPW:AppleBridge:bin:AppleBridgeWatchdog"

static Boolean      gRunning = true;
static WindowPtr    gWin = NULL;
static AppPrefs     gPrefs;
static ControlHandle gInstallBtn, gRemoveBtn, gAddBtn, gQuitBtn;
static ControlHandle gOTRadio, gMacTCPRadio, gSerialRadio;  /* networking-service selector */
static ControlHandle gPortARadio, gPortBRadio;              /* serial port: modem (A) / printer (B) */
static ControlHandle gBaudRadio[4];                         /* 9600 / 19200 / 38400 / 57600 */
static const long    kBaudVals[4] = { 9600L, 19200L, 38400L, 57600L };

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

/* Build {HOME}<leaf> when the prefs carry an install location (set by the
 * installer), else the legacy compiled-in path — so autostart points at the
 * watchdog wherever it actually lives. HOME is a folder; ensure a trailing ':'. */
static void HomePath(const char *leaf, const char *legacy, char *out)
{
    if (gPrefs.home[0]) {
        short n;
        mystrcpy(out, gPrefs.home);
        n = (short)mystrlen(out);
        if (n > 0 && out[n - 1] != ':') { out[n] = ':'; out[n + 1] = '\0'; }
        mystrcat(out, leaf);
    } else {
        mystrcpy(out, legacy);
    }
}

/* FSSpec of the binary we want launched at boot — the watchdog. */
static OSErr WatchdogSpec(FSSpec *spec)
{
    char   path[PREFS_PATH_LEN + 24];
    Str255 pPath;
    HomePath("AppleBridgeWatchdog", WATCHDOG_PATH, path);
    CtoP(path, pPath);
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
    FInfo             finfo;
    char path[PREFS_PATH_LEN];

    /* Say what the picker wants BEFORE opening it. A bare Standard File dialog
       told the operator nothing at all, and the answer is not guessable: it
       wants an application to chain-launch beside the daemon, ToolServer first.
       The alert is not laziness about a prompt string — StandardGetFile has no
       prompt parameter, and SFGetFile's is ignored by the Standard File
       package, so there is no string to fill in. */
    NoteAlert(kAddHelperAlert, (ModalFilterProcPtr) 0);

    /* Refuse a choice that cannot work, and hand the picker straight back
       rather than adding it or dropping the operator out of the task. Two
       things are refused, and both fail LATER and EXPENSIVELY if accepted:
       one of AppleBridge's own applications (circular — see IsOwnSuite), and
       anything that is not an 'APPL' at all, which the daemon's LAUNCH verb
       refuses at chain-launch time (R16), by which point the connection
       between the freeze and the file somebody picked days ago is gone. */
    for (;;) {
        StandardGetFile(NULL, -1, NULL, &reply);
        if (!reply.sfGood) return;              /* Cancel: add nothing, quietly */
        if (FSpGetFInfo(&reply.sfFile, &finfo) != noErr) return;
        if (IsOwnSuite(finfo.fdCreator)) {
            NoteAlert(kOwnSuiteAlert, (ModalFilterProcPtr) 0);
            continue;
        }
        if (finfo.fdType != 'APPL') {
            NoteAlert(kNotAnAppAlert, (ModalFilterProcPtr) 0);
            continue;
        }
        break;
    }

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

    /* Networking service selector (the two radio controls sit just below). */
    MoveTo(16, 84);
    DrawString("\pNetworking service:");
    MoveTo(16, 130);
    DrawString("\pSerial port:");
    MoveTo(16, 154);
    DrawString("\pBaud:");
    MoveTo(16, 176);
    DrawString("\p(takes effect on the next daemon launch / reboot)");

    MoveTo(16, 200);
    DrawString("\pHelper apps (chain-launched):");
    y = 216;
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

/* Reflect gPrefs into the transport + serial radios, and dim the serial
 * sub-options unless Serial is selected. */
static void SyncTransportRadios(void)
{
    short i, hi;
    SetControlValue(gOTRadio,     gPrefs.transport == kTransportOT     ? 1 : 0);
    SetControlValue(gMacTCPRadio, gPrefs.transport == kTransportMacTCP ? 1 : 0);
    SetControlValue(gSerialRadio, gPrefs.transport == kTransportSerial ? 1 : 0);
    SetControlValue(gPortARadio,  gPrefs.serialPortB ? 0 : 1);
    SetControlValue(gPortBRadio,  gPrefs.serialPortB ? 1 : 0);
    for (i = 0; i < 4; i++)
        SetControlValue(gBaudRadio[i], gPrefs.serialBaud == kBaudVals[i] ? 1 : 0);
    hi = (gPrefs.transport == kTransportSerial) ? 0 : 255;
    HiliteControl(gPortARadio, hi);
    HiliteControl(gPortBRadio, hi);
    for (i = 0; i < 4; i++) HiliteControl(gBaudRadio[i], hi);
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

    /* Networking-service radio group (just under the "Networking service:" label).
     * radioButProc == 2. The pair is mutually exclusive — clicks in HandleClick
     * set one to 1 and the other to 0 and persist the choice to prefs. */
    SetRect(&r, 28, 92, 150, 108);
    gOTRadio = NewControl(gWin, &r, "\pOpen Transport", true, 0, 0, 1, 2 /*radioButProc*/, 0);
    SetRect(&r, 156, 92, 250, 108);
    gMacTCPRadio = NewControl(gWin, &r, "\pMacTCP", true, 0, 0, 1, 2 /*radioButProc*/, 0);
    SetRect(&r, 256, 92, 360, 108);
    gSerialRadio = NewControl(gWin, &r, "\pSerial", true, 0, 0, 1, 2 /*radioButProc*/, 0);

    /* Serial sub-options (dimmed unless Serial is the active transport). */
    SetRect(&r, 110, 118, 205, 134);
    gPortARadio = NewControl(gWin, &r, "\pModem (A)", true, 0, 0, 1, 2 /*radioButProc*/, 0);
    SetRect(&r, 210, 118, 320, 134);
    gPortBRadio = NewControl(gWin, &r, "\pPrinter (B)", true, 0, 0, 1, 2 /*radioButProc*/, 0);
    {
        static unsigned char *baudLbl[4] = { "\p9600", "\p19200", "\p38400", "\p57600" };
        short bx[4], i;
        bx[0] = 60; bx[1] = 146; bx[2] = 242; bx[3] = 338;
        for (i = 0; i < 4; i++) {
            SetRect(&r, bx[i], 142, bx[i] + 86, 158);
            gBaudRadio[i] = NewControl(gWin, &r, baudLbl[i], true, 0, 0, 1, 2 /*radioButProc*/, 0);
        }
    }

    SyncTransportRadios();
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
                else if (ctl == gOTRadio || ctl == gMacTCPRadio ||
                         ctl == gSerialRadio) {
                    /* Pick a networking service, reflect it in the radios, and
                     * persist it. The daemon reads NET= at its next launch. */
                    gPrefs.transport = (ctl == gMacTCPRadio) ? kTransportMacTCP
                                     : (ctl == gSerialRadio) ? kTransportSerial
                                     : kTransportOT;
                    SyncTransportRadios();
                    SavePrefs(&gPrefs);
                }
                else if (ctl == gPortARadio || ctl == gPortBRadio) {
                    gPrefs.serialPortB = (ctl == gPortBRadio);
                    SyncTransportRadios();
                    SavePrefs(&gPrefs);
                }
                else {
                    short i;
                    for (i = 0; i < 4; i++) {
                        if (ctl == gBaudRadio[i]) {
                            gPrefs.serialBaud = kBaudVals[i];
                            SyncTransportRadios();
                            SavePrefs(&gPrefs);
                            break;
                        }
                    }
                }
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

    SetRect(&bounds, 40, 60, 500, 420);
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
