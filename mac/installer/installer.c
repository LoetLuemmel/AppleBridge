/*
 * AppleBridge Installer - a preflight-checking 68K setup app.
 *
 * Turns "copy four fork-bearing binaries and hope" into a verified, relocatable
 * install. It:
 *   1. PROBES PRECONDITIONS (Gestalt: System 7, Apple Events, a TCP stack —
 *      Open Transport or MacTCP —, 32-bit addressing, RAM) and refuses to
 *      install a setup that can't work. This is its real value: the daemon
 *      otherwise just hangs silently when the environment is wrong.
 *   2. COPIES the binaries (data + resource forks) from the installer's own
 *      folder to a destination folder on the boot volume.
 *   3. SEEDS the prefs — including the HOME= install location, so the watchdog
 *      and config app find the daemon wherever it landed (relocatable).
 *   4. INSTALLS AUTOSTART (a Startup Items alias to the installed watchdog).
 *
 * Payload model: the binaries (AppleBridge, AppleBridgeWatchdog,
 * AppleBridgeConfig, optional AppleBridgeMenuLED) ship as SIBLINGS of this app
 * in one folder; the installer locates its own folder and copies from there.
 *
 * It PROBES the TCP stack without linking Open Transport: Gestalt for OT, and
 * for classic MacTCP (which need not register the 'mtcp' selector) a Device-
 * Manager OpenDriver(".IPP") — still no OT dependency. Reuses prefs.c +
 * mystring.c from the daemon suite.
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
#include <Memory.h>
#include <Gestalt.h>
#include <Devices.h>     /* OpenDriver -> reliable classic-MacTCP '.IPP' probe */
#include <prefs.h>
#include <mystring.h>

QDGlobals qd;

/* Gestalt selectors (raw four-char codes — avoids header-constant availability). */
#define kGestaltSysVer    'sysv'
#define kGestaltAEAttr    'evnt'
#define kGestaltAddrMode  'addr'
#define kGestaltLRAM      'lram'
#define kGestaltOT        'otan'   /* Open Transport attributes */
#define kGestaltMacTCP    'mtcp'   /* MacTCP version */

#define kInstallerCreator 'ABis'
#define DEST_FOLDER_NAME  "\pAppleBridge"

/* Binary leaf names — same in the payload folder and the destination. */
#define LEAF_DAEMON   "AppleBridge"
#define LEAF_WATCHDOG "AppleBridgeWatchdog"
#define LEAF_CONFIG   "AppleBridgeConfig"

#define ST_FAIL  0
#define ST_WARN  1
#define ST_PASS  2

typedef struct {
    char    label[40];
    char    detail[48];
    short   status;     /* ST_FAIL / ST_WARN / ST_PASS */
    Boolean critical;
} Check;

#define MAX_CHECKS 8

static Boolean      gRunning = true;
static WindowPtr    gWin = NULL;
static AppPrefs     gPrefs;
static ControlHandle gInstallBtn, gQuitBtn;

static Check   gChecks[MAX_CHECKS];
static short   gNumChecks = 0;
static Boolean gCanInstall = false;
static Boolean gHasOT = false, gHasMacTCP = false, gHasSerial = false;
static Boolean gInstalled = false;
static char    gDestPath[PREFS_PATH_LEN];   /* "Vol:AppleBridge:" once known */
static char    gStatus[160];

/* ---- small string/struct helpers --------------------------------------- */

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

/* Append a non-negative decimal to a C string. */
static void CatNum(char *dst, long n)
{
    char tmp[16];
    short i = 0, j;
    long k = mystrlen(dst);
    if (n == 0) { dst[k] = '0'; dst[k + 1] = '\0'; return; }
    while (n > 0) { tmp[i++] = (char)('0' + (n % 10)); n /= 10; }
    for (j = 0; j < i; j++) dst[k + j] = tmp[i - 1 - j];
    dst[k + i] = '\0';
}

static void AddCheck(const char *label, short status, Boolean critical, const char *detail)
{
    if (gNumChecks >= MAX_CHECKS) return;
    mystrncpy(gChecks[gNumChecks].label, label, 39);  gChecks[gNumChecks].label[39] = '\0';
    mystrncpy(gChecks[gNumChecks].detail, detail, 47); gChecks[gNumChecks].detail[47] = '\0';
    gChecks[gNumChecks].status = status;
    gChecks[gNumChecks].critical = critical;
    gNumChecks++;
}

/* ---- preflight --------------------------------------------------------- */

static void RunChecks(void)
{
    long v;
    short i;

    gNumChecks = 0;
    gHasOT = gHasMacTCP = false;

    /* System 7.0+ */
    if (Gestalt(kGestaltSysVer, &v) == noErr) {
        short sv = (short)(v & 0xFFFF);
        char ver[24];
        ver[0] = '\0';
        CatNum(ver, (sv >> 8) & 0xF); mystrcat(ver, ".");
        CatNum(ver, (sv >> 4) & 0xF); mystrcat(ver, ".");
        CatNum(ver, sv & 0xF);
        AddCheck("System 7.0 or later", (sv >= 0x0700) ? ST_PASS : ST_FAIL, true, ver);
    } else {
        AddCheck("System 7.0 or later", ST_FAIL, true, "Gestalt failed");
    }

    /* Apple Events present (daemon needs them, else -903) */
    if (Gestalt(kGestaltAEAttr, &v) == noErr && (v & (1L << 0)))
        AddCheck("Apple Events", ST_PASS, true, "present");
    else
        AddCheck("Apple Events", ST_FAIL, true, "missing");

    /* A TCP stack: Open Transport OR MacTCP */
    gHasOT     = (Gestalt(kGestaltOT, &v) == noErr && v != 0);
    gHasMacTCP = (Gestalt(kGestaltMacTCP, &v) == noErr && v != 0);
    /* Classic MacTCP (e.g. the SE/30's 2.0.4) does NOT register the 'mtcp'
     * Gestalt selector -- only Open Transport's MacTCP-compat shim does, which
     * is why the OT-backed emulator passed preflight while a real machine with
     * stock MacTCP failed it. Fall back to the reliable probe: open the '.IPP'
     * driver (Device Manager only -- no OT linkage), exactly as the daemon's
     * MacTCP backend (transport_mactcp.c) does to bring the stack up. */
    if (!gHasMacTCP) {
        short ippRef;
        if (OpenDriver("\p.IPP", &ippRef) == noErr)
            gHasMacTCP = true;
    }
    /* Serial is always a viable transport on 68k hardware (modem/printer port);
     * probe the .AOut driver to confirm. This lets an Ethernet-less machine
     * (Plus/SE/Classic) pass preflight via the serial backend. */
    {
        short serRef;
        if (OpenDriver("\p.AOut", &serRef) == noErr) {
            gHasSerial = true;
            CloseDriver(serRef);
        }
    }
    /* A transport is required, but any of the three satisfies it. */
    if (gHasOT || gHasMacTCP)
        AddCheck("Network transport", ST_PASS, true, gHasOT ? "Open Transport" : "MacTCP");
    else if (gHasSerial)
        AddCheck("Network transport", ST_PASS, true, "Serial (modem port)");
    else
        AddCheck("Network transport", ST_FAIL, true, "none - need OT/MacTCP or serial");

    /* 32-bit addressing (advisory) */
    if (Gestalt(kGestaltAddrMode, &v) == noErr && (v & (1L << 0)))
        AddCheck("32-bit addressing", ST_PASS, false, "on");
    else
        AddCheck("32-bit addressing", ST_WARN, false, "24-bit - caps usable RAM");

    /* RAM >= 12 MB (daemon partition 8 min / 12 pref) (advisory) */
    if (Gestalt(kGestaltLRAM, &v) == noErr) {
        char mb[24];
        mb[0] = '\0';
        CatNum(mb, v / (1024L * 1024L)); mystrcat(mb, " MB");
        AddCheck("RAM 12 MB or more", (v >= 12L * 1024L * 1024L) ? ST_PASS : ST_WARN, false, mb);
    } else {
        AddCheck("RAM", ST_WARN, false, "unknown");
    }

    /* ToolServer: no Gestalt; located optionally during install (advisory) */
    AddCheck("ToolServer", ST_WARN, false, "locate during install (optional)");

    gCanInstall = true;
    for (i = 0; i < gNumChecks; i++)
        if (gChecks[i].critical && gChecks[i].status == ST_FAIL) gCanInstall = false;
}

/* ---- file-system helpers ----------------------------------------------- */

/* The folder this installer app lives in (its payload siblings are here). */
static OSErr MyFolder(short *vRef, long *dirID)
{
    ProcessSerialNumber psn;
    ProcessInfoRec info;
    FSSpec appSpec;

    GetCurrentProcess(&psn);
    info.processInfoLength = sizeof(info);
    info.processName = NULL;
    info.processAppSpec = &appSpec;
    if (GetProcessInformation(&psn, &info) != noErr) return ioErr;
    *vRef = appSpec.vRefNum;
    *dirID = appSpec.parID;
    return noErr;
}

/* Build a "Vol:dir:...:name" HFS path from an FSSpec by walking parents. */
static void FSSpecToPath(const FSSpec *spec, char *path)
{
    CInfoPBRec pb;
    Str255 nm;
    char nameC[256];
    char acc[512];
    char tmp[512];
    long dirID;

    PtoC(spec->name, acc);
    dirID = spec->parID;
    while (dirID != fsRtParID) {
        pb.dirInfo.ioCompletion = NULL;
        pb.dirInfo.ioNamePtr = nm;
        pb.dirInfo.ioVRefNum = spec->vRefNum;
        pb.dirInfo.ioFDirIndex = -1;
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

/* The destination folder ("AppleBridge" at the boot volume root), creating it
 * if absent. Returns its vRefNum + own dirID. */
static OSErr DestFolder(short *vRef, long *dirID)
{
    OSErr  err;
    short  sysVRef;
    long   sysDir, newDir;
    FSSpec folderSpec;
    CInfoPBRec pb;
    Str255 nm;

    err = FindFolder(kOnSystemDisk, kSystemFolderType, kDontCreateFolder, &sysVRef, &sysDir);
    if (err != noErr) return err;

    err = FSMakeFSSpec(sysVRef, fsRtDirID, DEST_FOLDER_NAME, &folderSpec);
    if (err == fnfErr) {
        err = FSpDirCreate(&folderSpec, 0, &newDir);
        if (err != noErr) return err;
    } else if (err != noErr) {
        return err;
    }

    /* Resolve the folder's own dirID. */
    BlockMoveData(folderSpec.name, nm, folderSpec.name[0] + 1);
    pb.dirInfo.ioCompletion = NULL;
    pb.dirInfo.ioNamePtr = nm;
    pb.dirInfo.ioVRefNum = folderSpec.vRefNum;
    pb.dirInfo.ioFDirIndex = 0;
    pb.dirInfo.ioDrDirID = folderSpec.parID;
    if (PBGetCatInfoSync(&pb) != noErr) return ioErr;
    *vRef = folderSpec.vRefNum;
    *dirID = pb.dirInfo.ioDrDirID;
    return noErr;
}

/* Copy one fork (data or resource) from src to dst. A missing source resource
 * fork is not an error. */
static OSErr CopyOneFork(const FSSpec *src, const FSSpec *dst, Boolean resFork)
{
    short sref, dref;
    OSErr err, e2;
    long  total, done, want;
    Ptr   buf;

    err = resFork ? FSpOpenRF(src, fsRdPerm, &sref) : FSpOpenDF(src, fsRdPerm, &sref);
    if (err != noErr) return resFork ? noErr : err;   /* no rsrc fork is fine */

    err = resFork ? FSpOpenRF(dst, fsRdWrPerm, &dref) : FSpOpenDF(dst, fsRdWrPerm, &dref);
    if (err != noErr) { FSClose(sref); return err; }

    SetEOF(dref, 0L);
    GetEOF(sref, &total);

    buf = NewPtr(16384L);
    if (buf == NULL) { FSClose(sref); FSClose(dref); return memFullErr; }

    done = 0; err = noErr;
    while (done < total) {
        want = total - done;
        if (want > 16384L) want = 16384L;
        e2 = FSRead(sref, &want, buf);
        if ((e2 != noErr && e2 != eofErr) || want == 0) { if (e2 != eofErr) err = e2; break; }
        e2 = FSWrite(dref, &want, buf);
        if (e2 != noErr) { err = e2; break; }
        done += want;
    }

    DisposePtr(buf);
    FSClose(sref);
    FSClose(dref);
    return err;
}

/* Fork-aware copy of one file (both forks + type/creator + Finder flags). */
static OSErr CopyForks(const FSSpec *src, const FSSpec *dst)
{
    FInfo fi;
    OSErr err;

    if (FSpGetFInfo(src, &fi) != noErr) return fnfErr;

    FSpDelete(dst);   /* ignore: may not exist */
    err = FSpCreate(dst, fi.fdCreator, fi.fdType, 0);
    if (err != noErr && err != dupFNErr) return err;
    /* Lay down a proper resource map first (a raw FSpOpenRF write onto a bare
     * FSpCreate'd file corrupts at offset 48 — same gotcha as fileio.c). */
    FSpCreateResFile(dst, fi.fdCreator, fi.fdType, 0);

    err = CopyOneFork(src, dst, false);
    if (err != noErr) return err;
    err = CopyOneFork(src, dst, true);
    if (err != noErr) return err;

    FSpSetFInfo(dst, &fi);   /* carry type/creator/flags */
    return noErr;
}

/* Drop a Startup Items alias to the installed watchdog (same recipe as the
 * config app's InstallAutostart). */
static OSErr InstallAutostart(const FSSpec *watchdog)
{
    FSSpec      aliasFile;
    AliasHandle alias;
    OSErr       err;
    short       vRef, refNum;
    long        dir;
    FInfo       fi;

    err = FindFolder(kOnSystemDisk, kStartupFolderType, kDontCreateFolder, &vRef, &dir);
    if (err != noErr) return err;
    err = FSMakeFSSpec(vRef, dir, "\pAppleBridge Watchdog", &aliasFile);
    /* An installer should point autostart at the watchdog it just installed, so
     * REPLACE any prior alias (unlike the config app, which leaves it alone). */
    if (err == noErr) FSpDelete(&aliasFile);
    else if (err != fnfErr) return err;

    err = NewAlias(NULL, watchdog, &alias);
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

    if (FSpGetFInfo(&aliasFile, &fi) == noErr) {
        fi.fdFlags |= 0x8000;              /* kIsAlias */
        FSpSetFInfo(&aliasFile, &fi);
    }
    return noErr;
}

/* ---- the install ------------------------------------------------------- */

static Boolean CopyBinary(short srcV, long srcD, short dstV, long dstD, const char *leaf)
{
    Str255 pLeaf;
    FSSpec src, dst;
    CtoP(leaf, pLeaf);
    if (FSMakeFSSpec(srcV, srcD, pLeaf, &src) != noErr) return false;  /* not in payload */
    FSMakeFSSpec(dstV, dstD, pLeaf, &dst);
    return CopyForks(&src, &dst) == noErr;
}

static void DoInstall(void)
{
    short  srcV, dstV;
    long   srcD, dstD;
    FSSpec folderSpec, wdSpec;
    Str255 pName;
    StandardFileReply reply;

    if (!gCanInstall) {
        mystrcpy(gStatus, "Cannot install: a required check failed.");
        return;
    }
    if (MyFolder(&srcV, &srcD) != noErr) {
        mystrcpy(gStatus, "Could not find the installer's own folder.");
        return;
    }
    if (DestFolder(&dstV, &dstD) != noErr) {
        mystrcpy(gStatus, "Could not create the destination folder.");
        return;
    }

    /* Copy the three binaries (daemon, watchdog, config). */
    if (!CopyBinary(srcV, srcD, dstV, dstD, LEAF_DAEMON) ||
        !CopyBinary(srcV, srcD, dstV, dstD, LEAF_WATCHDOG) ||
        !CopyBinary(srcV, srcD, dstV, dstD, LEAF_CONFIG)) {
        mystrcpy(gStatus, "Copy failed - are the binaries beside this installer?");
        return;
    }

    /* Build the destination folder path string for HOME=. */
    FSMakeFSSpec(dstV, fsRtDirID, DEST_FOLDER_NAME, &folderSpec);
    FSSpecToPath(&folderSpec, gDestPath);
    mystrcat(gDestPath, ":");

    /* Seed prefs: keep any existing IP, set HOME, pick the detected stack. */
    PrefsDefaults(&gPrefs);
    LoadPrefs(&gPrefs);
    mystrncpy(gPrefs.home, gDestPath, PREFS_PATH_LEN - 1);
    gPrefs.home[PREFS_PATH_LEN - 1] = '\0';
    /* Prefer a detected TCP stack; on an Ethernet-less machine fall to serial
     * (modem port A, 9600), so the daemon can reach the host over a cable. */
    if (gHasOT)
        gPrefs.transport = kTransportOT;
    else if (gHasMacTCP)
        gPrefs.transport = kTransportMacTCP;
    else {
        gPrefs.transport   = kTransportSerial;
        gPrefs.serialPortB = false;   /* modem port A */
        gPrefs.serialBaud  = 9600;
    }

    /* Optional: let the user locate ToolServer -> APP= chain-launch entry. */
    StandardGetFile(NULL, -1, NULL, &reply);
    if (reply.sfGood && gPrefs.appCount < PREFS_MAX_APPS) {
        char tsPath[PREFS_PATH_LEN];
        FSSpecToPath(&reply.sfFile, tsPath);
        if (tsPath[0]) {
            mystrncpy(gPrefs.apps[gPrefs.appCount], tsPath, PREFS_PATH_LEN - 1);
            gPrefs.apps[gPrefs.appCount][PREFS_PATH_LEN - 1] = '\0';
            gPrefs.appCount++;
        }
    }
    SavePrefs(&gPrefs);

    /* Install autostart: alias to the freshly-copied watchdog. */
    CtoP(LEAF_WATCHDOG, pName);
    FSMakeFSSpec(dstV, dstD, pName, &wdSpec);
    InstallAutostart(&wdSpec);

    gInstalled = true;
    gStatus[0] = '\0';
    mystrcat(gStatus, "Installed to ");
    mystrcat(gStatus, gDestPath);
    /* An install with no host address is incomplete, and saying "reboot to start
     * the bridge" would be a promise it cannot keep — the daemon will come up and
     * refuse to dial. Nothing here can derive the address: it belongs to the host.
     * So name the missing step instead of implying there is none (R2). */
    /* Name the file that was written. The preferences do NOT live in the
     * installation folder — they go to the System Folder's Preferences folder —
     * and somebody looking for them beside the binaries finds a template
     * instead, edits it, and changes nothing (R3). */
    mystrcat(gStatus, "; prefs in System Folder:Preferences:AppleBridge Prefs");
    if (gPrefs.ip[0] == '\0')
        mystrcat(gStatus, " - now set the host IP in AppleBridgeConfig.");
    else
        mystrcat(gStatus, " - reboot to start the bridge.");
}

/* ---- UI ---------------------------------------------------------------- */

static void DrawContent(void)
{
    Rect r;
    short i, y;

    SetPort(gWin);
    r = gWin->portRect;
    r.bottom -= 44;
    EraseRect(&r);

    TextFont(0); TextFace(bold); TextSize(12);
    MoveTo(16, 22);
    DrawString("\pAppleBridge Installer");

    TextFace(0); TextSize(10);
    MoveTo(16, 40);
    DrawString("\pPreflight checks:");

    y = 58;
    for (i = 0; i < gNumChecks; i++) {
        Str255 p;
        MoveTo(28, y);
        if (gChecks[i].status == ST_PASS)      DrawString("\pOK  ");
        else if (gChecks[i].status == ST_WARN) DrawString("\p?   ");
        else                                   DrawString("\pX   ");
        CtoP(gChecks[i].label, p); DrawString(p);
        DrawString("\p  -  ");
        CtoP(gChecks[i].detail, p); DrawString(p);
        y += 15;
    }

    y += 6;
    MoveTo(16, y);
    if (!gCanInstall)
        DrawString("\pA required check failed - install is disabled.");
    else if (gInstalled)
        { Str255 p; CtoP(gStatus, p); DrawString(p); }
    else
        DrawString("\pReady to install. Binaries must sit beside this app.");

    if (gCanInstall && !gInstalled && gStatus[0]) {
        Str255 p;
        MoveTo(16, y + 15);
        CtoP(gStatus, p); DrawString(p);
    }

    DrawControls(gWin);
}

static void MakeButtons(void)
{
    Rect r;
    short top = gWin->portRect.bottom - 36;

    SetRect(&r, 16, top, 140, top + 20);
    gInstallBtn = NewControl(gWin, &r, "\pInstall", true, 0, 0, 1, 0, 0);
    SetRect(&r, 380, top, 444, top + 20);
    gQuitBtn = NewControl(gWin, &r, "\pQuit", true, 0, 0, 1, 0, 0);

    HiliteControl(gInstallBtn, gCanInstall ? 0 : 255);   /* 255 = disabled */
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
                if (ctl == gInstallBtn && gCanInstall && !gInstalled) {
                    DoInstall();
                    /* disable only on success; a failed attempt stays retryable */
                    HiliteControl(gInstallBtn, gInstalled ? 255 : 0);
                } else if (ctl == gQuitBtn) {
                    gRunning = false;
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

    gStatus[0] = '\0';
    RunChecks();

    SetRect(&bounds, 40, 60, 500, 340);
    gWin = NewCWindow(NULL, &bounds, "\pAppleBridge Installer", true,
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
