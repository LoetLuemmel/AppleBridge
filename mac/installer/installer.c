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
#include <Shutdown.h>       /* ShutDwnStart - the post-install restart */
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
#define kDaemonCreator    'ABrg'
#define kConfigCreator    'ABcf'
#define kWatchdogCreator  'ABwd'

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
static ControlHandle gInstallBtn, gQuitBtn, gRestartBtn;
static short   gLogoFrame = 0;
static long    gLogoNext  = 0;    /* TickCount of the next frame */
static Rect    gLogoRect;

static Check   gChecks[MAX_CHECKS];
static short   gNumChecks = 0;
static Boolean gCanInstall = false;
static Boolean gHasOT = false, gHasMacTCP = false, gHasSerial = false;
static Boolean gInstalled = false;
static char    gDestPath[PREFS_PATH_LEN];   /* "Vol:AppleBridge:" once known */
static char    gStatus[160];    /* line 1: what happened */
static char    gStatus2[160];   /* line 2: where the prefs went */
static char    gStatus3[160];   /* line 3: what is still needed */
static char    gStatus4[160];   /* line 4: the optional extra */

/* ---- animated logo, top right ------------------------------------------
   Ported verbatim from mac/claudeapp (the About-box party GIF), because that
   code is already proven on this guest: 'GFin' info + 'clut' + PackBits'd
   'Gfrm' frames, unpacked into one buffer and CopyBits'd through an offscreen
   PixMap. Sized 104x39 x 10 frames here — 20 KB of resources on a 33 KB app,
   which a 2 MB kit will not notice.

   The animation ticks on the installer's existing WaitNextEvent loop and never
   spins: a busy loop here would starve the daemon and freeze the bridge, which
   is the rule that governs every guest program in this project. */
#define kLogoBase 200
/* ---- animated About-box logo (the party GIF) -------------------------- */

typedef struct {
    short count;    /* number of frames            */
    short w;        /* frame width  (== rowBytes)  */
    short h;        /* frame height                */
    short baseID;   /* 'Gfrm' id of frame 0        */
    short delay;    /* ticks between frames        */
    short packed;   /* 1 => rows are PackBits'd    */
} GifInfo;

static GifInfo    gGif;
static Boolean    gGifReady = false;
static CTabHandle gClut     = 0L;
static Ptr        gFrameBuf = 0L;      /* one unpacked frame, w*h bytes */
static PixMap     gSrcPM;              /* source pixmap over gFrameBuf   */
static Handle     gFrames[64];         /* the 'Gfrm' resources           */

static void GifLoad(void)
{
    Handle info;
    short  i;

    if (gGifReady) return;

    info = GetResource('GFin', kLogoBase);
    if (info == 0L) return;
    BlockMove(*info, &gGif, (long)sizeof(GifInfo));
    if (gGif.count <= 0 || gGif.count > 64) return;

    gClut = (CTabHandle) GetResource('clut', kLogoBase);
    if (gClut == 0L) return;
    HNoPurge((Handle)gClut);

    for (i = 0; i < gGif.count; i++) {
        gFrames[i] = GetResource('Gfrm', gGif.baseID + i);
        if (gFrames[i] == 0L) return;
        HNoPurge(gFrames[i]);
    }

    gFrameBuf = NewPtr((long)gGif.w * (long)gGif.h);
    if (gFrameBuf == 0L) return;

    gSrcPM.baseAddr   = gFrameBuf;
    gSrcPM.rowBytes   = (short)(gGif.w | 0x8000);   /* high bit => PixMap */
    SetRect(&gSrcPM.bounds, 0, 0, gGif.w, gGif.h);
    gSrcPM.pmVersion  = 0;
    gSrcPM.packType   = 0;
    gSrcPM.packSize   = 0;
    gSrcPM.hRes       = 0x00480000L;                /* 72 dpi */
    gSrcPM.vRes       = 0x00480000L;
    gSrcPM.pixelType  = 0;                          /* chunky */
    gSrcPM.pixelSize  = 8;
    gSrcPM.cmpCount   = 1;
    gSrcPM.cmpSize    = 8;
    gSrcPM.planeBytes = 0;
    gSrcPM.pmTable    = gClut;
    gSrcPM.pmReserved = 0;

    gGifReady = true;
}

static void GifDrawFrame(WindowPtr w, short idx, Rect *dst)
{
    Handle   h;
    Ptr      src, dp;
    short    row;
    RGBColor savedFore, savedBack;

    h = gFrames[idx];
    if (h == 0L) return;

    HLock(h);
    src = *h;
    dp  = gFrameBuf;
    if (gGif.packed) {
        for (row = 0; row < gGif.h; row++)
            UnpackBits(&src, &dp, gGif.w);
    } else {
        BlockMove(src, gFrameBuf, (long)gGif.w * (long)gGif.h);
    }
    HUnlock(h);

    GetForeColor(&savedFore);
    GetBackColor(&savedBack);
    ForeColor(blackColor);
    BackColor(whiteColor);
    CopyBits((BitMap *)&gSrcPM,
             (BitMap *)*(((CGrafPtr)w)->portPixMap),
             &gSrcPM.bounds, dst, srcCopy, 0L);
    RGBForeColor(&savedFore);
    RGBBackColor(&savedBack);
}


static void DrawWrapped(const char *text, short left, short *y,
                        short right);

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
    /* Says what happens, not what used to. The installer stopped opening a
       picker for this (a file dialog nobody asked for), so a preflight line
       promising to "locate during install" would be describing a step that no
       longer exists — the kind of stale claim that sends somebody looking for
       a dialog that never comes. */
    AddCheck("ToolServer", ST_WARN, false, "optional - add later in the config panel");

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
/* "<name> old", clamped to HFS's 31-character limit. */
static void AsideName(ConstStr255Param base, Str255 out)
{
    const char *suffix = " old";
    short n = base[0], i;

    if (n > 31 - 4) n = 31 - 4;
    for (i = 1; i <= n; i++) out[i] = base[i];
    for (i = 0; i < 4; i++) out[n + 1 + i] = (unsigned char)suffix[i];
    out[0] = (unsigned char)(n + 4);
}

/* Make `dst` replaceable. -> noErr when the name is free to be re-created.
 *
 * The old code called FSpDelete and IGNORED the result, which is wrong in the
 * one case that matters. Installing over an existing AppleBridge means the
 * destination is the RUNNING daemon: FSpDelete fails with fBsyErr, FSpCreate
 * then returns dupFNErr — which was treated as success — and the copy went on
 * to write into the open application's forks. It happened to fail at the first
 * fork open rather than corrupting a running binary, which is luck, not design.
 *
 * Renaming an open file IS allowed: it edits the catalog entry, not the forks.
 * That is exactly the trick `SWAPSELF` uses to let the daemon replace itself
 * (docs/SELF_UPDATE.md), proven on System 7 and Mac OS 9. Reusing it here is
 * what lets the installer upgrade a machine without stopping its bridge first —
 * and a bridge cannot be stopped by the thing driving the install, because
 * stopping it is what takes the driver away.
 */
static OSErr ClearDestination(const FSSpec *dst)
{
    FInfo  fi;
    OSErr  err;
    Str255 aside;
    FSSpec asideSpec;

    if (FSpGetFInfo(dst, &fi) != noErr) return noErr;   /* nothing in the way */

    err = FSpDelete(dst);
    if (err == noErr) return noErr;
    if (err != fBsyErr && err != opWrErr && err != permErr && err != fLckdErr)
        return err;                                      /* a real failure */

    AsideName(dst->name, aside);
    if (FSMakeFSSpec(dst->vRefNum, dst->parID, aside, &asideSpec) == noErr)
        FSpDelete(&asideSpec);      /* a previous aside; if it is open too, the
                                     * rename below fails and we report that */
    return FSpRename(dst, aside);
}

static OSErr CopyForks(const FSSpec *src, const FSSpec *dst)
{
    FInfo fi;
    OSErr err;

    if (FSpGetFInfo(src, &fi) != noErr) return fnfErr;

    err = ClearDestination(dst);
    if (err != noErr) return err;
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

/* -> noErr, or fnfErr when the file is not beside the installer, or whatever
 * the copy itself failed with. Returning a Boolean merged those two into one
 * verdict, and the caller then printed "are the binaries beside this
 * installer?" for BOTH — advice that points at the one thing which is not the
 * problem when the real cause is a busy destination. Measured on the SE/30,
 * 2026-07-28: the binaries were demonstrably beside it. */
static OSErr CopyBinary(short srcV, long srcD, short dstV, long dstD, const char *leaf)
{
    Str255 pLeaf;
    FSSpec src, dst;
    CtoP(leaf, pLeaf);
    if (FSMakeFSSpec(srcV, srcD, pLeaf, &src) != noErr) return fnfErr;
    FSMakeFSSpec(dstV, dstD, pLeaf, &dst);
    return CopyForks(&src, &dst);
}

static void DoInstall(void)
{
    short  srcV, dstV;
    long   srcD, dstD;
    FSSpec folderSpec, wdSpec;
    Str255 pName;

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

    /* Copy the three binaries (daemon, watchdog, config), naming whichever one
     * fails and why. One message for every cause is how "are the binaries
     * beside this installer?" came to be printed about a busy destination. */
    {
        static const char *kLeaves[3] = { LEAF_DAEMON, LEAF_WATCHDOG, LEAF_CONFIG };
        short i;
        for (i = 0; i < 3; i++) {
            OSErr cerr = CopyBinary(srcV, srcD, dstV, dstD, kLeaves[i]);
            if (cerr == noErr) continue;
            gStatus[0] = '\0';
            mystrcat(gStatus, "Copy failed for ");
            mystrcat(gStatus, kLeaves[i]);
            if (cerr == fnfErr) {
                mystrcat(gStatus, " - it is not beside this installer.");
            } else {
                mystrcat(gStatus, " - error ");
                if (cerr < 0) { mystrcat(gStatus, "-"); CatNum(gStatus, -(long)cerr); }
                else          { CatNum(gStatus, (long)cerr); }
                if (cerr == fBsyErr || cerr == opWrErr || cerr == permErr)
                    mystrcat(gStatus, " (in use, and could not be renamed aside)");
                mystrcat(gStatus, ".");
            }
            return;
        }
    }

    /* Build the destination folder path string for HOME=. */
    FSMakeFSSpec(dstV, fsRtDirID, DEST_FOLDER_NAME, &folderSpec);
    FSSpecToPath(&folderSpec, gDestPath);
    mystrcat(gDestPath, ":");

    /* Seed prefs in three LAYERS, weakest first. The order is the whole point.
     *
     *   1. PrefsDefaults   — compiled-in fallbacks.
     *   2. the kit's prefs — shipped beside this installer by the host, which
     *      is the only party that knows which address the guest should dial.
     *   3. the machine's own prefs — whatever is already in the Preferences
     *      folder, which must WIN.
     *
     * Layer 2 is new (2026-07-28) and it closes an R2 hole. Until now nothing
     * read the kit's prefs file at all: LoadPrefs resolves its path through
     * FindFolder and looks nowhere else, so a fresh machine took its `IP=`
     * from the installer's COMPILED-IN default. That default was one
     * developer's address, so the install looked perfect on that LAN and
     * would have pointed a stranger's daemon at a stranger's computer — while
     * reporting full health, which is why nobody would have caught it.
     *
     * Layer 3 must come last, or reinstalling from a kit built on another host
     * would silently repoint a working daemon and drop its `APP=` chain-launch
     * list. A kit supplies what a machine does not know; it does not overrule
     * a machine that already knows. LoadPrefs resets `appCount` only after it
     * has opened a file, so a machine with no prefs keeps the kit's list.
     */
    PrefsDefaults(&gPrefs);
    {
        FSSpec kitPrefs;
        if (FSMakeFSSpec(srcV, srcD, "\pAppleBridge Prefs", &kitPrefs) == noErr)
            (void)LoadPrefsFrom(&gPrefs, &kitPrefs);   /* absent is fine */
    }
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

    /* NO helper picker here, deliberately (operator's call, 2026-07-29).
       This used to open Standard File in the middle of the install, and a file
       dialog nobody asked for is confusing exactly because it is unexpected —
       a first-time user cannot know it wants ToolServer, and adding an alert to
       explain it was treating the symptom. Helpers are an OPTIONAL extra, so
       they belong where somebody goes looking for them: the Add Helper App
       button in AppleBridgeConfig (and the control panel), which is where the
       guidance and the guards live. The status line below points there, and
       README/docs/SETUP.md carry it too. */

    SavePrefs(&gPrefs);

    /* Install autostart: alias to the freshly-copied watchdog. */
    CtoP(LEAF_WATCHDOG, pName);
    FSMakeFSSpec(dstV, dstD, pName, &wdSpec);
    InstallAutostart(&wdSpec);

    /* Leave a one-shot marker for the daemon: it means "this next boot is the
     * first one after an install", and the daemon shows its welcome window
     * once and deletes the file. It lives beside the binaries rather than in
     * the prefs because it is a fact about ONE boot, not configuration, and
     * because deleting a file is a cleaner one-shot than rewriting prefs the
     * daemon may re-read at any time. */
    {
        FSSpec markSpec;
        CtoP("AppleBridge Welcome", pName);
        if (FSMakeFSSpec(dstV, dstD, pName, &markSpec) == fnfErr)
            (void)FSpCreate(&markSpec, kDaemonCreator, 'TEXT', 0);   /* 0 = system script */
    }

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
    /* "the Preferences folder", not a spelled-out English path: the folder is
     * located with FindFolder and is called whatever this System calls it. On
     * the German System 7.5 of the SE/30 it is `Systemordner:Preferences:`,
     * and printing `System Folder:…` sent a reader looking for a folder that
     * does not exist on their machine (2026-07-28). */
    /* ONE FACT PER LINE. This was a single sentence drawn with one DrawString
       at x=16 in a 460-pixel window, and it ran off the right edge: the reader
       saw "...prefs in the Preferences folder (AppleB" and could not discover
       how it ended. Reported 2026-07-29 by someone reading the window instead
       of the source. The destination path is machine-specific and unbounded,
       so no single line can be guaranteed to fit — splitting it is what makes
       the layout predictable rather than lucky. */
    gStatus2[0] = '\0';
    mystrcat(gStatus2, "Prefs in the Preferences folder (AppleBridge Prefs).");

    gStatus3[0] = '\0';
    if (gPrefs.ip[0] == '\0')
        mystrcat(gStatus3, "Now set the host IP in the AppleBridge config panel.");
    else
        mystrcat(gStatus3, "Restart to start the bridge.");

    /* Name the optional extra rather than performing it uninvited. */
    gStatus4[0] = '\0';
    mystrcat(gStatus4, "Helper apps (ToolServer): add them in the AppleBridge config panel.");
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
    if (!gCanInstall) {
        MoveTo(16, y);
        DrawString("\pA required check failed - install is disabled.");
    } else if (gInstalled) {
        DrawWrapped(gStatus,  16, &y, gWin->portRect.right - 16);
        DrawWrapped(gStatus2, 16, &y, gWin->portRect.right - 16);
        DrawWrapped(gStatus3, 16, &y, gWin->portRect.right - 16);
        DrawWrapped(gStatus4, 16, &y, gWin->portRect.right - 16);
    } else {
        MoveTo(16, y);
        DrawString("\pReady to install. Binaries must sit beside this app.");
        y += 15;
        if (gStatus[0])
            DrawWrapped(gStatus, 16, &y, gWin->portRect.right - 16);
    }

    /* Credit line, just above the button strip. Small and grey so it reads as
       a footer rather than as one more thing to act on. The characters are
       real MacRoman -- (C) and (R) are typewriter substitutes for glyphs this
       machine has had since 1984: © is $A9, ® is $A8, the em-dash $D1. */
    TextSize(9);
    TextFace(0);
    MoveTo(16, gWin->portRect.bottom - 44);
    if ((**((CGrafPtr)gWin)->portPixMap).pixelSize > 1) {
        RGBColor grey;
        grey.red = grey.green = grey.blue = 0x7777;
        RGBForeColor(&grey);
    }
    DrawString("\p© 2026 Pit Förster — the Loetluemmel ® — and Claude, his friend");
    ForeColor(blackColor);
    TextSize(12);

    DrawControls(gWin);
    if (gGifReady) GifDrawFrame(gWin, gLogoFrame, &gLogoRect);
}

/* Draw `text` from `left` to `right`, wrapping on word boundaries and
   advancing *y per line.

   The status line used to be one DrawString at x=16 in a 460-pixel window,
   and it says things like "Installed to <path>; prefs in the Preferences
   folder (AppleBridge Prefs) - now set the host IP in AppleBridgeConfig."
   That runs past the right edge and the tail is simply unreachable — the
   operator sees a sentence stop mid-word and cannot find out how it ends.
   Reported 2026-07-29 by somebody reading the window rather than the source.
   The destination path is machine-specific and unbounded, so no fixed window
   width can be "wide enough": it has to wrap. */
static void DrawWrapped(const char *text, short left, short *y, short right)
{
    Str255 line, test;
    short  i = 0, w, k, n;
    char   word[80];

    line[0] = 0;
    while (text[i]) {
        w = 0;
        while (text[i] && text[i] != ' ' && w < 78) word[w++] = text[i++];
        while (text[i] == ' '            && w < 78) word[w++] = text[i++];

        n = line[0];
        for (k = 1; k <= n; k++) test[k] = line[k];
        for (k = 0; k < w && n < 254; k++) test[++n] = word[k];
        test[0] = (unsigned char) n;

        if (line[0] > 0 && StringWidth(test) > right - left) {
            MoveTo(left, *y);
            DrawString(line);
            *y += 15;
            n = 0;
            for (k = 0; k < w && n < 254; k++) line[++n] = word[k];
            line[0] = (unsigned char) n;
        } else {
            for (k = 0; k <= n; k++) line[k] = test[k];
            line[0] = (unsigned char) n;
        }
    }
    if (line[0]) {
        MoveTo(left, *y);
        DrawString(line);
        *y += 15;
    }
}

static void MakeButtons(void)
{
    Rect r;
    short top = gWin->portRect.bottom - 36;

    SetRect(&r, 16, top, 140, top + 20);
    gInstallBtn = NewControl(gWin, &r, "\pInstall", true, 0, 0, 1, 0, 0);
    /* Hidden until there is something to reboot INTO; shown by DoInstall. */
    SetRect(&r, 268, top, 364, top + 20);
    gRestartBtn = NewControl(gWin, &r, "\pRestart", false, 0, 0, 1, 0, 0);
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
                    /* Once the install has happened, Restart is the only
                       action that leads anywhere: the daemon comes up through
                       Startup Items and nothing else starts it, so an installed
                       machine that is merely quit out of is an install that
                       does not work yet. Operator's call (2026-07-29): show
                       Restart, take Quit away. The cost is stated rather than
                       hidden — somebody who wants out without restarting has to
                       quit the emulator instead. */
                    if (gInstalled) {
                        ShowControl(gRestartBtn);
                        HideControl(gQuitBtn);
                    }
                } else if (ctl == gRestartBtn) {
                    ShutDwnStart();   /* restart; the bridge comes up */
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

    SetRect(&bounds, 40, 60, 500, 372);   /* +32: the status is 3 lines now */
    gWin = NewCWindow(NULL, &bounds, "\pAppleBridge Installer", true,
                      documentProc, (WindowPtr)-1L, true, 0);
    SetPort(gWin);
    MakeButtons();

    /* Top right, inside the window, clear of the check list. */
    GifLoad();
    if (gGifReady) {
        short right = gWin->portRect.right - 12;
        SetRect(&gLogoRect, right - gGif.w, 12, right, 12 + gGif.h);
        gLogoNext = TickCount();
    }
    DrawContent();

    while (gRunning) {
        /* One frame per delay, driven by the event loop's own idle. The sleep
           below is 30 ticks, so ask for a shorter one while animating. */
        if (gGifReady && TickCount() >= gLogoNext) {
            GifDrawFrame(gWin, gLogoFrame, &gLogoRect);
            gLogoFrame = (short)((gLogoFrame + 1) % gGif.count);
            gLogoNext  = TickCount() + gGif.delay;
        }
        if (WaitNextEvent(everyEvent, &ev, gGifReady ? 2L : 30L, NULL)) {
            switch (ev.what) {
                case mouseDown:
                    HandleClick(&ev);
                    break;
                case keyDown:
                case autoKey:
                    if (ev.modifiers & cmdKey) {
                        char k = (char)(ev.message & charCodeMask);
                        /* Cmd-W as well as Cmd-Q, and both cases of each. The
                         * window has a close box, so a Mac user reaches for
                         * Cmd-W first and it did nothing; Cmd-Q matched only
                         * lowercase, so Caps Lock defeated it too.
                         *
                         * Gated on !gInstalled deliberately, mirroring the Quit
                         * BUTTON, which is hidden once the install succeeds: the
                         * bridge does not run until the machine restarts, so
                         * leaving by any door would leave a machine that looks
                         * installed and is not. The keyboard equivalent exists
                         * exactly when the button does — no hidden escape, and
                         * no key that silently disagrees with the UI. */
                        if ((k == 'q' || k == 'Q' || k == 'w' || k == 'W') &&
                            !gInstalled)
                            gRunning = false;
                    }
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
