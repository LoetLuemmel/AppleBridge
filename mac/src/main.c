/*
 * AppleBridge - Main Daemon (Client Mode) with RX/TX LEDs
 * Connects OUT to host server
 */

#include <applebridge.h>
#include <mystring.h>
#include <Quickdraw.h>
#include <Fonts.h>
#include <Windows.h>
#include <Controls.h>
#include <Events.h>
#include <Menus.h>
#include <TextEdit.h>
#include <Scrap.h>
#include <Dialogs.h>
#include <StandardFile.h>
#include <Files.h>
#include <Resources.h>
#include <Devices.h>
#include <LowMem.h>
#include <Processes.h>
#include <ToolUtils.h>
#include <AppleEvents.h>
#include <Gestalt.h>
#include <Shutdown.h>
#include <OSUtils.h>
#include <Timer.h>
#include <Patches.h>
#include <Traps.h>
#include <prefs.h>
#include <auth.h>

QDGlobals qd;

/* Control Manager part codes / scrollbar procID — this SDK's <Controls.h> does
 * not export them; the values are standard and stable. */
#ifndef scrollBarProc
#define scrollBarProc 16
#endif
#ifndef inUpButton
#define inUpButton    20
#endif
#ifndef inDownButton
#define inDownButton  21
#endif
#ifndef inPageUp
#define inPageUp      22
#endif
#ifndef inPageDown
#define inPageDown    23
#endif
#ifndef inThumb
#define inThumb      129
#endif

/* Loaded from "AppleBridge Prefs" at startup (host IP + chain-launch list).
 * File-scope (not on main's stack) since AppPrefs is ~2 KB. */
static AppPrefs gPrefs;

/* Per-connection auth state (protocol v0.2, see docs/PROTOCOL_v0.2.md). Auth is
 * opt-in: it engages only when THIS session's HELLO carried a host nonce AND we
 * hold a TOKEN=. gAuthed gates command flow — true (open) unless auth is needed
 * and AUTH2 has not yet proven the host. Reset on every (re)connect. */
static Boolean gNeedAuth = false;   /* this session negotiated auth */
static Boolean gAuthed   = true;    /* command flow permitted */
static char    gDaemonNonceHex[17] = "";  /* nonce we handed the host; AUTH2 proves over it */

/* Menu IDs */
#define APPLE_MENU_ID   128
#define FILE_MENU_ID    129
#define EDIT_MENU_ID    130

/* Monaco's well-known fixed font ID (not in this older Fonts.h). Monospace, so
 * command/response columns line up in the monitor log. */
#define kLogFontID      4

/* Menu items */
#define ABOUT_ITEM      1
#define QUIT_ITEM       1
#define COPY_ITEM       1
#define DETAILS_ITEM    3      /* Edit: Copy(1), (divider 2), Show details(3) */

static Boolean gRunning = true;
/* The on-demand "Mitlesen" live-traffic monitor window. The daemon is faceless;
 * this window only exists between a Mitlesen pick (LED click) and its close. All
 * the drawing functions are no-ops while it is NULL, so the daemon stays
 * invisible until asked. It is resizable; close != quit (DisposeWindow, the loop
 * keeps running). */
static WindowPtr gStatusWindow = NULL;

/* One-shot post-install greeting. Shown on the first boot after an install and
 * never again; the installer leaves an "AppleBridge Welcome" marker beside the
 * binaries and this window's appearance consumes it.
 *
 * A plain window, deliberately NOT an alert. A modal blocks the daemon's event
 * loop, so the BRIDGE would be down for as long as the dialog sat on screen --
 * unbounded, since it waits for a human. This just draws and can be ignored.
 *
 * What it says is the other half of the design: it does not offer to remove the
 * kit volume, it TELLS the reader to drag it to the Trash. Unmounting
 * programmatically returns -47 (the Finder holds the volume's desktop database
 * open, the same reason AFPUNMOUNT fails on a share), and closing the window
 * achieves nothing because the Finder reopens it on every mount -- measured by
 * closing it by hand, restarting, and watching it come back. Put Away is the
 * gesture that always works, so the window names it. */
static WindowPtr gWelcomeWindow = NULL;
static Boolean   gWelcomeChecked = false;   /* the marker is looked for once */
static void DrawWelcome(void);
static void ShowWelcomeIfFresh(void);
static Boolean gMenuInstalled = false;   /* minimal Apple menu, installed lazily */
#define MON_MIN_W 240
#define MON_MIN_H 140
/* Scrolling log as a ring buffer of the last LOG_LINES lines, redrawn whole on
 * each message (robust: always in-window, survives redraws). Small 9pt font.
 * The window reflows to its live size, showing the last lines that fit. */
/* Keep LOG_LINES*LOG_W (gLog) + the same-size gTEBuf comfortably under the 32 KB
 * A5 near-data limit: SC compiles with the near data model (the Link's -model far
 * is code-only), so total globals over 32 KB corrupt A5 addressing and crash on
 * launch. 60*160*2 = ~19 KB of log buffers leaves headroom; 120 did not. */
#define LOG_LINES 60
#define LOG_W     160
/* Max command-output lines echoed to the Verbose console per command. The
 * rolling log keeps only the last LOG_LINES, but cap the work so a multi-MB
 * Catenate can't spend forever line-splitting into a 60-line buffer. */
#define CONSOLE_MAX_LINES 120
static char  gLog[LOG_LINES][LOG_W];
/* Per-line kind: 0 = primary ("> command" + its output), 1 = detail (the AE
 * trace). Both are always stored; the monitor can collapse the detail lines. */
static unsigned char gLogKind[LOG_LINES];
static Boolean gShowDetails = false;   /* collapsed by default (clean log) */
static short gLogHead = 0;   /* next slot to write */
static short gLogN    = 0;   /* lines currently stored */
/* The body is a real TextEdit field so the user can mouse-select and copy log
 * text to the clipboard. It mirrors the ring (rebuilt from it on each dirty
 * sync). NULL while the window is closed. gTEBuf is the off-stack scratch the
 * ring is flattened into for TESetText (LOG_LINES*LOG_W ~ under TE's 32 KB cap). */
static TEHandle gLogTE = NULL;
static ControlHandle gScroll = NULL;   /* vertical scrollbar for the log */
static short gLineHeight = 11;          /* log line height; a STYLED TERec reports
                                        * lineHeight as -1, so we keep our own. */
static char     gTEBuf[LOG_LINES * LOG_W];
static Boolean gLogDirty = false;  /* redraw the body from ShowAlive's good
                                    * context (drawing from ProcessRequest, right
                                    * after an OT receive, doesn't render) */
static long gTickCounter = 0;

/* Address of the menu-bar LED's activity cell, from Gestalt 'ABrg' (the
 * AppleBridgeMenuLED INIT); NULL if that extension isn't installed. We stamp
 * TickCount() here on each RX so the menu-bar LED flashes on traffic. */
static long *gMenuLED = NULL;
/* &gMonReq, the second long in the shared 'ABrg' block (gMenuLED+1). The MenuLED
 * INIT bumps it when the user picks "Mitlesen" on the menu-bar LED; we poll it
 * and open the monitor window. NULL if the INIT isn't installed. */
static long *gMonReqCell = NULL;
static long  gMonReqSeen = 0;
static long gStartTick = 0;   /* daemon launch tick (for Alive uptime) */
static MenuHandle gAppleMenu;
static MenuHandle gFileMenu;
static MenuHandle gEditMenu;

/* JSF: the SFGetFile dlgHook redirects the journal to a dialog item's real
 * GUEST-global center, so we never guess coordinates. gSFBlk is the journal
 * state block to steer; gSFItem is the SFGetFile item to target (1=Open,3=Cancel). */
static long  *gSFBlk = NULL;
static short  gSFItem = 3;

/* RX/TX Activity tracking */
static long gLastRX = 0;     /* Tick count of last receive */
static long gLastTX = 0;     /* Tick count of last transmit */
static long gRXCount = 0;    /* Total commands received */
static long gTXCount = 0;    /* Total responses sent */
static long gErrCount = 0;   /* Total error responses (STATUS != 0) */
static long gLastLat = 0;    /* Last REAL command's RX->TX latency, in ticks */
static Boolean gLastWasReal = false;  /* was the last request a real command (not a
                                       * PING/STAT heartbeat)? gates the latency capture
                                       * so ~0-tick heartbeats don't clobber gLastLat. */
static char gLastErr[48] = "";        /* short tag of the most recent error (for id) */

/* Route B: the installed global _MenuSelect patch block (system heap, layout in
 * mac/journal/mspatch.a). 0 = not installed. The RESIDENT daemon installs it so
 * it persists (ToolServer reverts trap patches an MPW tool makes on exit). */
static Ptr gMSPatch = 0L;

/* Current activity shown on the top bar next to the green "Active" LED
 * (the command/verb being processed). */
static char gActivity[256] = "ready";

/* LED flash duration in ticks (~0.66 seconds, long enough to be seen) */
#define LED_FLASH_DURATION  40

/* Footer telemetry line (defined after the StatDec/StatStr number helpers it
 * uses; called from ShowAlive above them). */
static void DrawTelemetry(void);

/*
 * HOST IP - Change this to your host's IP address!
 */
#define DEFAULT_HOST_IP "192.168.1.100"

/*
 * Top bar: one round green "Active" LED + the current activity (the command/verb
 * being processed) on a single line. RX/TX always move together, so a single
 * Active indicator says all the old two-LED pair did.
 */
void DrawLEDs(void)
{
    Rect statusArea, led;
    Str255 pstr;
    short i, w;
    long now;
    Boolean active;
    RGBColor ledBright = { 0x1000, 0xE000, 0x1000 };  /* bright green: data moving */
    RGBColor ledIdle   = { 0x0000, 0x5800, 0x0000 };  /* dim green: connected, idle */
    RGBColor cBlack = { 0, 0, 0 };
    RGBColor cWhite = { 0xFFFF, 0xFFFF, 0xFFFF };

    if (gStatusWindow == NULL) return;

    SetPort(gStatusWindow);
    PenNormal();
    w = gStatusWindow->portRect.right - gStatusWindow->portRect.left;

    /* Clear the top bar (white background, framed) — full window width */
    SetRect(&statusArea, 0, 0, w, 18);
    RGBBackColor(&cWhite);
    RGBForeColor(&cBlack);
    EraseRect(&statusArea);
    FrameRect(&statusArea);

    /* LED brightness = traffic: bright green while a command was received or a
     * response sent within LED_FLASH_DURATION ticks, dim green when connected
     * but idle. ShowAlive's ~8x/sec refresh catches the flash and reverts it,
     * so the dot now shows data actually flowing instead of being always lit. */
    now = TickCount();
    active = (now - gLastRX < LED_FLASH_DURATION) ||
             (now - gLastTX < LED_FLASH_DURATION);
    SetRect(&led, 8, 3, 22, 17);
    RGBForeColor(active ? &ledBright : &ledIdle);
    PaintOval(&led);
    RGBForeColor(&cBlack);
    FrameOval(&led);

    /* state label + the live activity text, same line */
    TextSize(9);
    MoveTo(28, 13);
    DrawString(active ? "\pActive  " : "\pIdle    ");

    for (i = 0; gActivity[i] && i < 250; i++) pstr[i + 1] = gActivity[i];
    pstr[0] = (unsigned char)i;
    DrawString(pstr);

    TextSize(12);
}

/*
 * Set the current activity shown on the top bar. Strips the "COMMAND:<len>\n"
 * wire header so the real MPW command shows (verbs like SCREENSHOT/LAUNCH: are
 * passed through), and stops at the first line end. Redraws the bar.
 * (Classic-Mac C: '\r' is byte 0x0A, '\n' is 0x0D — both line ends are caught.)
 */
void SetActivity(const char *msg)
{
    short i;
    const char *p = msg;

    if (msg[0]=='C' && msg[1]=='O' && msg[2]=='M' && msg[3]=='M' &&
        msg[4]=='A' && msg[5]=='N' && msg[6]=='D' && msg[7]==':') {
        p = msg + 8;
        while (*p && *p != '\r' && *p != '\n') p++;   /* skip past <len> */
        if (*p) p++;                                  /* skip the separator */
    }

    for (i = 0; p[i] && i < (short)sizeof(gActivity) - 1; i++) {
        if (p[i] == '\r' || p[i] == '\n') break;
        gActivity[i] = p[i];
    }
    gActivity[i] = '\0';
    if (i == 0) { gActivity[0] = 'i'; gActivity[1] = 'd'; gActivity[2] = 'l';
                  gActivity[3] = 'e'; gActivity[4] = '\0'; }
    DrawLEDs();
}

/* The log body's rectangle in the window's local coords: full width, between the
 * top activity bar (18px) and the Alive line + grow box (16px). Reflows with the
 * window. Inset 2px so text doesn't touch the frame. */
void MonitorBodyRect(Rect *r)
{
    short w = gStatusWindow->portRect.right - gStatusWindow->portRect.left;
    short h = gStatusWindow->portRect.bottom - gStatusWindow->portRect.top;
    /* Leave 15px on the right for the scrollbar and 15px at the bottom for the
     * Alive line + grow box. */
    SetRect(r, 2, 20, w - 15, h - 15);
}

/* Scroll the log so display line `topLine` sits at the top of the view. */
static void ScrollLogTo(short topLine)
{
    short curTop, delta;
    if (gLogTE == NULL) return;
    curTop = (short)(((**gLogTE).viewRect.top - (**gLogTE).destRect.top) / gLineHeight);
    delta = (short)((curTop - topLine) * gLineHeight);
    if (delta != 0) TEScroll(0, delta, gLogTE);
}

/* Max scrollbar value = number of lines that don't fit in the view. */
static short LogMaxScroll(void)
{
    short vis, n;
    if (gLogTE == NULL) return 0;
    vis = (short)(((**gLogTE).viewRect.bottom - (**gLogTE).viewRect.top) / gLineHeight);
    n = (**gLogTE).nLines;
    return (n - vis < 0) ? 0 : (short)(n - vis);
}

/* Flatten the ring into the TextEdit field so its text mirrors the log, then
 * pin the view to the bottom (newest visible). Rebuilding wholesale resets any
 * in-progress mouse selection, but only fires on NEW traffic (gLogDirty) — while
 * the bridge is idle (the usual time you read/copy a result) the selection is
 * stable. */
void SyncLogTE(void)
{
    short line, idx, k;
    long n = 0;
    long cmdS[LOG_LINES], cmdE[LOG_LINES];        /* char ranges of command lines */
    short nCmd = 0;
    Boolean wasAtBottom;

    if (gLogTE == NULL) return;

    /* If the user has scrolled up to read history, DON'T yank them to the bottom
     * on new traffic; only auto-follow when they're already at the end. */
    wasAtBottom = (gScroll == NULL) ||
                  (GetControlValue(gScroll) >= GetControlMaximum(gScroll));

    for (line = 0; line < gLogN; line++) {
        long lineStart;
        idx = (gLogHead - gLogN + line + 2 * LOG_LINES) % LOG_LINES;
        if (!gShowDetails && gLogKind[idx] == 1) continue;  /* collapsed: hide details only */
        lineStart = n;
        for (k = 0; gLog[idx][k] && k < LOG_W - 1; k++) gTEBuf[n++] = gLog[idx][k];
        if (gLogKind[idx] == 2 && nCmd < LOG_LINES) {       /* a command line -> bold */
            cmdS[nCmd] = lineStart; cmdE[nCmd] = n; nCmd++;
        }
        gTEBuf[n++] = '\n';                       /* TE line break: MPW C '\n' is
                                                   * 0x0D (CR), the char TextEdit
                                                   * breaks on; '\r' is 0x0A (LF),
                                                   * which TE draws as a box glyph. */
    }
    TESetText(gTEBuf, n, gLogTE);

    /* Style: everything plain, then bold each command line. */
    {
        TextStyle ts;
        short c;
        ts.tsFace = 0;
        TESetSelect(0, n, gLogTE);
        TESetStyle(doFace, &ts, false, gLogTE);
        ts.tsFace = bold;
        for (c = 0; c < nCmd; c++) {
            TESetSelect(cmdS[c], cmdE[c], gLogTE);
            TESetStyle(doFace, &ts, false, gLogTE);
        }
    }

    /* Match the scrollbar to the new line count, then either follow the bottom
     * (TESelView) or restore the user's scrolled-up position. */
    {
        short mx = LogMaxScroll();
        if (gScroll != NULL) SetControlMaximum(gScroll, mx);
        if (wasAtBottom) {
            TESetSelect(n, n, gLogTE);
            TESelView(gLogTE);
            if (gScroll != NULL) SetControlValue(gScroll, mx);
        } else if (gScroll != NULL) {
            ScrollLogTo(GetControlValue(gScroll));
        }
    }

    /* TESetText updates the record but does NOT redraw — repaint the body here
     * (we run from ShowAlive's render-safe context). Without this only the very
     * first line (drawn via OpenMonitor's InvalRect) would ever appear. */
    {
        Rect body;
        RGBColor cWhite = { 0xFFFF, 0xFFFF, 0xFFFF };
        MonitorBodyRect(&body);
        RGBBackColor(&cWhite);
        EraseRect(&body);
        TEUpdate(&body, gLogTE);
    }
}

/* Redraw the log body. With the TE field present (window open) this is a
 * TEUpdate; the old ring-DrawString path is kept only as a guard. */
void RedrawLog(void)
{
    Rect body;
    RGBColor cWhite = { 0xFFFF, 0xFFFF, 0xFFFF };

    if (gStatusWindow == NULL || gLogTE == NULL) return;
    SetPort(gStatusWindow);
    MonitorBodyRect(&body);
    RGBBackColor(&cWhite);
    EraseRect(&body);
    TEUpdate(&body, gLogTE);
}

/* Copy the current selection to the clipboard so it can be pasted into any app.
 * An empty selection means "copy everything" — a quick grab of the whole log. */
void DoCopyLog(void)
{
    if (gLogTE == NULL) return;
    if ((**gLogTE).selStart == (**gLogTE).selEnd) {
        TESetSelect(0, (**gLogTE).teLength, gLogTE);
    }
    TECopy(gLogTE);
    ZeroScrap();
    TEToScrap();
}

/* Append one log line of the given kind (0=primary, 1=detail) to the ring. */
static void AddLogLine(const char *msg, short kind)
{
    short k;
    if (gStatusWindow == NULL) return;

    for (k = 0; msg[k] && k < LOG_W - 1; k++) gLog[gLogHead][k] = msg[k];
    gLog[gLogHead][k] = '\0';
    gLogKind[gLogHead] = (unsigned char)kind;
    gLogHead = (short)((gLogHead + 1) % LOG_LINES);
    if (gLogN < LOG_LINES) gLogN++;

    gLogDirty = true;   /* ShowAlive redraws the body from a context that renders */
}

/* Primary line (command + output): always shown. */
void StatusMessage(const char *msg) { AddLogLine(msg, 0); }

/* Detail line (AE trace): stored always, hidden when details are collapsed.
 * Called from command.c's Trace(). */
void StatusDetail(const char *msg) { AddLogLine(msg, 1); }

/* Show alive indicator with LEDs */
/* Periodic monitor-window refresh (kept the name for its call sites): flashes
 * the LEDs and syncs the log TE. The old "Alive: <uptime>" footer was removed —
 * the live log and mac_status already convey liveness. */
void ShowAlive(void)
{
    long ticks;

    if (gStatusWindow == NULL) return;

    SetPort(gStatusWindow);

    /* Refresh ~8x/sec so the LED flash is caught and reverts promptly */
    ticks = TickCount();
    if (ticks - gTickCounter < 8) return;
    gTickCounter = ticks;

    DrawLEDs();       /* top bar (activity + RX/TX LEDs) */
    DrawTelemetry();  /* footer strip (RX/TX/ERR counters + last latency) */

    /* Sync the TE field from the ring if new lines arrived (from this good
     * context — TESetText draws, which doesn't render in the OT-receive path). */
    if (gLogDirty) { SyncLogTE(); gLogDirty = false; }
}

/* About-box logo: PICT 128 (the AppleBridge suspension-bridge icon) with data
 * packets animated across the deck -- the "bridge" analog of MacNetScan's radar
 * sweep. The static base is repainted each beat so the packets leave no trail. */
#define rABLogoPICT    128
#define AB_NPKT          3
#define AB_PHASES       24
#define AB_STEP_TICKS    4

static void DrawABLogo(const Rect *logo, short phase)
{
    PicHandle pic;
    short w   = logo->right  - logo->left;
    short hh  = logo->bottom - logo->top;
    short x0  = logo->left + (short)((long)w  * 25 / 100);   /* deck span left  */
    short x1  = logo->left + (short)((long)w  * 74 / 100);   /* deck span right */
    short y   = logo->top  + (short)((long)hh * 60 / 100);   /* just above deck */
    short span = x1 - x0;
    short sz  = (short)((long)w * 7 / 100);
    short i, px, off;
    RGBColor green  = { 0x0000, 0xC000, 0x3000 };
    RGBColor orange = { 0xF000, 0x9000, 0x0000 };
    Rect r;
    if (sz < 3) sz = 3;

    pic = GetPicture(rABLogoPICT);
    if (pic) DrawPicture(pic, logo);            /* repaint base each beat */
    for (i = 0; i < AB_NPKT; i++) {
        off = (short)(((long)span * ((phase + i * AB_PHASES / AB_NPKT) % AB_PHASES)) / AB_PHASES);
        px  = x0 + off;
        RGBForeColor((i & 1) ? &orange : &green);
        SetRect(&r, px, (short)(y - sz / 2), (short)(px + sz), (short)(y + sz / 2));
        PaintRect(&r);
    }
    ForeColor(blackColor);
}

/*
 * INTERRUPT-driven journaling watchdog. While the journal is armed, a modal loop
 * that stops polling the journal (e.g. StandardGetFile on a missed click) spins at
 * 100% CPU and no in-driver safety can fire -- but the Time Manager runs at
 * INTERRUPT time regardless, so this task zeroes JournalFlag ($08DE) after a delay,
 * unfreezing the guest. DisarmTMProc references only a fixed low-mem address (no A5,
 * no globals) so it is safe to run at interrupt time.
 */
static TMTask gDisarmTask;
static Boolean gDisarmActive = false;

static void DisarmTMProc(void)
{
    *(volatile short *)0x08DEL = 0;     /* JournalFlag = 0 (disarm) */
}

/* Prime the watchdog to disarm journaling in <ms> milliseconds. Call BEFORE arming. */
static void ArmJournalWatchdog(long ms)
{
    gDisarmTask.tmAddr     = (TimerUPP)DisarmTMProc;
    gDisarmTask.tmWakeUp   = 0;
    gDisarmTask.tmReserved = 0;
    InsTime((QElemPtr)&gDisarmTask);
    PrimeTime((QElemPtr)&gDisarmTask, ms);
    gDisarmActive = true;
}

/* Cancel/remove the watchdog after the armed section completes (safe if it fired). */
static void CancelJournalWatchdog(void)
{
    if (gDisarmActive) {
        RmvTime((QElemPtr)&gDisarmTask);
        gDisarmActive = false;
    }
}

/*
 * SFGetFile dlgHook (JSF): each call, read the target item's rect from the LIVE
 * dialog, convert to global, and point the journal's target at that button's
 * centre -- so the driver clicks the real button wherever SFGetFile placed it.
 * No coordinate guessing. Returns the item unchanged (does not itself dismiss).
 */
pascal short SFCoordHook(short item, DialogPtr dlg)
{
    if (gSFBlk != NULL && dlg != NULL) {
        short   itype;
        Handle  ihandle;
        Rect    r;
        GrafPtr save;
        Point   c;
        GetPort(&save);
        SetPort((GrafPtr)dlg);
        GetDialogItem(dlg, gSFItem, &itype, &ihandle, &r);
        c.v = (short)((r.top + r.bottom) / 2);
        c.h = (short)((r.left + r.right) / 2);
        LocalToGlobal(&c);
        SetPort(save);
        gSFBlk[0] = ((long)c.v << 16) | ((long)c.h & 0xFFFF);
    }
    return item;
}

/* True iff the main screen is 1-bit monochrome (or there is no Color QuickDraw
 * at all). The color About box uses RGBForeColor + a colour PICT in a basic
 * GrafPort, which faults on a real 1-bit device (e.g. a Macintosh SE/30), so we
 * pick a plain black-and-white About there instead. */
static Boolean ScreenIsMono(void)
{
    long         qd;
    GDHandle     gd;
    PixMapHandle pm;

    if (Gestalt(gestaltQuickdrawVersion, &qd) != noErr || qd < gestalt8BitQD)
        return true;                        /* no Color QuickDraw -> monochrome */
    gd = GetMainDevice();
    if (gd == NULL) return true;
    pm = (**gd).gdPMap;
    if (pm == NULL) return true;
    return (Boolean)((**pm).pixelSize <= 1);
}

/* Plain monochrome About box: text + a hand-drawn 1-bit suspension bridge, all
 * basic (1-bit-safe) QuickDraw -- no RGBForeColor, no colour PICT, no animation. */
static void ShowAboutBoxMono(void)
{
    DialogPtr dialog;
    Rect      bounds, logo;

    SetRect(&bounds, 76, 84, 468, 264);            /* wider so the tagline doesn't clip; fits 512x342 */
    dialog = NewDialog(NULL, &bounds, "\p", true, dBoxProc,
                       (WindowPtr)-1L, false, 0, NULL);
    if (dialog == NULL) return;

    SetPort(dialog);
    SetRect(&logo, 24, 30, 128, 118);
    FrameRect(&logo);                              /* logo frame */
    MoveTo(52, 108); LineTo(52, 46);               /* left tower  */
    MoveTo(100,108); LineTo(100,46);               /* right tower */
    MoveTo(28, 96);  LineTo(124, 96);              /* deck        */
    MoveTo(28, 96);  LineTo(52, 46);               /* main cable  */
    LineTo(100,46);  LineTo(124,96);

    MoveTo(148, 46); TextSize(14); TextFace(bold);
    DrawString("\pAppleBridge v0.7.0");
    MoveTo(148, 70); TextSize(10); TextFace(0);
    DrawString("\pBuilt by Pit with Love");
    MoveTo(148, 86);
    DrawString("\pfor 68K and Claude");
    MoveTo(148, 110); TextFace(italic);
    DrawString("\p\"Connecting classic Mac to the future\"");
    MoveTo(148, 134); TextFace(bold);
    DrawString("\pMonochrome Edition");
    MoveTo(148, 158); TextFace(0);
    DrawString("\pClick to close...");

    while (!Button()) SystemTask();
    while (Button()) {}
    DisposeDialog(dialog);
}

/*
 * Show About dialog. Case on screen depth: the animated colour bridge on a
 * colour machine, a plain monochrome box on a 1-bit screen (SE/30 & friends).
 */
void ShowAboutBox(void)
{
    DialogPtr dialog;
    Rect bounds, logo;
    short phase = 0;
    long  nextTick = 0;

    if (ScreenIsMono()) {                          /* the do-case split */
        ShowAboutBoxMono();
        return;
    }

    SetRect(&bounds, 90, 80, 480, 280);
    dialog = NewDialog(NULL, &bounds, "\p", true, dBoxProc,
                       (WindowPtr)-1L, false, 0, NULL);

    if (dialog != NULL) {
        SetPort(dialog);
        SetRect(&logo, 20, 45, 120, 145);       /* 100x100 logo on the left */

        MoveTo(130, 30); TextSize(14); TextFace(bold);
        DrawString("\pAppleBridge v0.7.0");
        MoveTo(130, 55); TextSize(10); TextFace(0);
        DrawString("\pBuilt by Pit with Love");
        MoveTo(130, 75);
        DrawString("\pfor 68K and Claude");
        MoveTo(130, 100); TextFace(italic);
        DrawString("\p\"Connecting classic Mac to the future\"");
        MoveTo(130, 122); TextFace(bold);
        DrawString("\pActive + Console Edition");
        MoveTo(130, 145); TextFace(0);
        DrawString("\pClick to close...");

        while (!Button()) {
            if (TickCount() >= nextTick) {
                DrawABLogo(&logo, phase);
                phase = (short)((phase + 1) % AB_PHASES);
                nextTick = TickCount() + AB_STEP_TICKS;
            }
            SystemTask();
        }
        while (Button()) {}

        DisposeDialog(dialog);
    }
}

/*
 * Handle menu selection
 */
void HandleMenuCommand(long menuResult)
{
    short menuID, menuItem;

    menuID = HiWord(menuResult);
    menuItem = LoWord(menuResult);

    switch (menuID) {
        case APPLE_MENU_ID:
            if (menuItem == ABOUT_ITEM) {
                ShowAboutBox();
            } else {
                /* Everything below the separator was added by AppendResMenu:
                 * desk accessories, control panels, whatever lives in Apple Menu
                 * Items. An application must hand those to OpenDeskAcc itself —
                 * without this the entries are drawn but picking one does
                 * NOTHING, silently (reported 2026-07-25: "with AppleBridge
                 * frontmost you cannot open a control panel"; the same trap made
                 * the Chooser unreachable from this app earlier that day). */
                Str255 daName;
                GetMenuItemText(gAppleMenu, menuItem, daName);
                if (daName[0] > 0) OpenDeskAcc(daName);
            }
            break;

        case FILE_MENU_ID:
            if (menuItem == QUIT_ITEM) {
                gRunning = false;
            }
            break;

        case EDIT_MENU_ID:
            if (menuItem == COPY_ITEM) {
                DoCopyLog();
            } else if (menuItem == DETAILS_ITEM) {
                gShowDetails = !gShowDetails;
                CheckItem(gEditMenu, DETAILS_ITEM, gShowDetails);
                gLogDirty = true;   /* re-sync to expand/collapse detail lines */
            }
            break;
    }

    HiliteMenu(0);
}

/*
 * Initialize menus
 */
void InitMenuBar(void)
{
    gAppleMenu = NewMenu(APPLE_MENU_ID, "\p\024");
    AppendMenu(gAppleMenu, "\pAbout AppleBridge...;(-");
    AppendResMenu(gAppleMenu, 'DRVR');
    InsertMenu(gAppleMenu, 0);

    gFileMenu = NewMenu(FILE_MENU_ID, "\pFile");
    AppendMenu(gFileMenu, "\pQuit/Q");
    InsertMenu(gFileMenu, 0);

    DrawMenuBar();
}

/*
 * Inbound kAEQuitApplication handler. A faceless app has no window close box
 * or Quit menu, so this is how the Finder / a system shutdown stops it cleanly
 * (otherwise an invisible app could stall shutdown). Just signals the loop.
 */
static pascal OSErr HandleQuitApp(const AppleEvent *evt, AppleEvent *reply, long refcon)
{
#pragma unused(evt, reply, refcon)
    gRunning = false;
    return noErr;
}

/*
 * Initialize the Toolbox for a FACELESS background service.
 *
 * v0.6.0: no status window and no menu bar — the daemon runs invisibly
 * (onlyBackground in the SIZE resource). gStatusWindow stays NULL, so every
 * drawing function (DrawLEDs / RedrawLog / StatusMessage / ShowAlive) is a
 * no-op. The debug UI now lives in the separate AppleBridgeConfig app.
 * InitGraf is still required (qd.screenBits + the screenshot path use it); the
 * other inits are harmless and kept for any transient Toolbox use.
 */
void InitApp(void)
{
    long aeAttr;

    InitGraf(&qd.thePort);
    InitFonts();
    InitWindows();
    InitMenus();
    TEInit();
    InitDialogs(NULL);
    InitCursor();

    /* Quit cleanly when the Finder / system asks (no close box exists). */
    if (Gestalt(gestaltAppleEventsAttr, &aeAttr) == noErr &&
        (aeAttr & (1L << gestaltAppleEventsPresent))) {
        AEInstallEventHandler(kCoreEventClass, kAEQuitApplication,
                              NewAEEventHandlerUPP(HandleQuitApp), 0, false);
    }
}

/*
 * Open the on-demand "Mitlesen" live-traffic monitor window (resizable). Called
 * when the menu-bar LED's "Mitlesen" item is picked (gMonReq bumped). The daemon
 * is otherwise faceless; this is its only window. A minimal Apple menu is
 * installed lazily so a foregrounded daemon has a sane menu bar — deliberately NO
 * Quit item (quitting tears down Open Transport and crashes the SDL2 host).
 */
/* Fill *r with the Verbose window's content rect: the saved bounds from prefs if
 * set AND on-screen, otherwise a default clamped to the screen so the footer
 * stays visible even on a 512x342 SE/30. */
/* A compact Mac (SE/30, Plus, Classic: 512x342) has no room for the roomy
 * default — a 480x296 console buries the entire desktop, which is exactly how it
 * looked on the SE/30 (2026-07-26). Cap the monitor there, and cap a RESTORED
 * rect too: a rect saved on a big screen, or by an earlier build, is still
 * "valid" on 342 lines and would otherwise keep covering everything. Stays well
 * above MON_MIN_W/MON_MIN_H. */
#define COMPACT_SCREEN_H 400
#define COMPACT_MON_W    440
#define COMPACT_MON_H    190

static void ClampForCompactScreen(Rect *r, const Rect *scr)
{
    if ((short)(scr->bottom - scr->top) > COMPACT_SCREEN_H) return;   /* roomy display */
    if ((short)(r->right - r->left) > COMPACT_MON_W)
        r->right = (short)(r->left + COMPACT_MON_W);
    if ((short)(r->bottom - r->top) > COMPACT_MON_H)
        r->bottom = (short)(r->top + COMPACT_MON_H);
}

static void ComputeMonitorRect(Rect *r)
{
    Rect  scr = qd.screenBits.bounds;
    short mbar = GetMBarHeight();
    short usableTop = (short)(scr.top + mbar + 22);   /* +22 so the title bar clears the menu bar */
    short w, h;

    if (gPrefs.winB > gPrefs.winT && gPrefs.winR > gPrefs.winL &&
        gPrefs.winT >= scr.top + mbar && gPrefs.winL >= scr.left &&
        gPrefs.winB <= scr.bottom && gPrefs.winR <= scr.right) {
        SetRect(r, gPrefs.winL, gPrefs.winT, gPrefs.winR, gPrefs.winB);
        /* The saved rect is the CONTENT rect; the title bar sits ABOVE it. The
         * validity test above only keeps the content below the menu bar, so a
         * window saved near the top reopens with its title bar hidden UNDER the
         * menu bar — undraggable and unclosable by hand. Push it down instead of
         * rejecting the position, keeping the user's size. (Seen 2026-07-25 as a
         * "missing title bar" after a hide/show cycle; the same trap applies to
         * the close-box reopen, so this repairs a stale prefs value too.) */
        if (r->top < usableTop) {
            short d = (short)(usableTop - r->top);
            r->top = (short)(r->top + d);
            r->bottom = (short)(r->bottom + d);
        }
        ClampForCompactScreen(r, &scr);
        return;
    }
    w = (short)(scr.right - scr.left - 8);    if (w > 480) w = 480;
    h = (short)(scr.bottom - usableTop - 4);  if (h > 360) h = 360;
    SetRect(r, (short)(scr.left + 4), usableTop,
              (short)(scr.left + 4 + w), (short)(usableTop + h));
    ClampForCompactScreen(r, &scr);
}

/* Snapshot the Verbose window's current global content rect into prefs and
 * persist it, so it reopens at the same size/place next launch. */
static void SaveMonitorBounds(void)
{
    GrafPtr save;
    Rect    pr;
    Point   tl;
    if (gStatusWindow == NULL) return;
    GetPort(&save);
    SetPort(gStatusWindow);
    pr = gStatusWindow->portRect;          /* local content rect */
    tl.v = pr.top; tl.h = pr.left;         /* (0,0) */
    LocalToGlobal(&tl);                    /* global top-left of the content */
    SetPort(save);
    gPrefs.winT = tl.v;
    gPrefs.winL = tl.h;
    gPrefs.winB = (short)(tl.v + (pr.bottom - pr.top));
    gPrefs.winR = (short)(tl.h + (pr.right - pr.left));
    SavePrefs(&gPrefs);
}

static void DrawWelcome(void)
{
    GrafPtr save;
    Rect    r;

    if (gWelcomeWindow == NULL) return;
    GetPort(&save);
    SetPort(gWelcomeWindow);
    r = gWelcomeWindow->portRect;
    EraseRect(&r);

    TextFont(0); TextSize(12); TextFace(bold);
    MoveTo(16, 26);
    DrawString("\pAppleBridge is installed and running.");

    TextFace(0);
    MoveTo(16, 50);
    DrawString("\pThe bridge starts by itself every time this Mac");
    MoveTo(16, 66);
    DrawString("\pboots. Nothing else needs launching.");

    MoveTo(16, 92);
    DrawString("\pYou can put the AppleBridge Kit disk away now:");
    MoveTo(16, 108);
    DrawString("\pdrag it to the Trash. It is no longer needed.");

    TextSize(9);
    MoveTo(16, 132);
    DrawString("\pClose this window when you are done - it appears only once.");
    TextSize(12);

    SetPort(save);
}

/* Show the greeting if the installer left its marker, then consume the marker
 * so this is genuinely once. Consumed BEFORE the window opens: if anything
 * below fails, the user misses a greeting, which is better than meeting it on
 * every boot with no way to stop it. */
static void ShowWelcomeIfFresh(void)
{
    FSSpec      spec;
    Str255      pPath;
    Rect        r;
    short       i, n = 0;
    const char *fn = "AppleBridge Welcome";

    if (gWelcomeWindow != NULL) return;
    if (gPrefs.home[0] == '\0') return;      /* legacy setup: no known folder */

    /* Same full-path idiom the journal driver and SWAPSELF use: HOME= already
     * ends in a colon, so the leaf appends directly. */
    for (i = 0; gPrefs.home[i] && n < 254; i++) pPath[1 + n++] = gPrefs.home[i];
    for (i = 0; fn[i] && n < 254; i++)          pPath[1 + n++] = fn[i];
    pPath[0] = (unsigned char)n;

    if (FSMakeFSSpec(0, 0L, pPath, &spec) != noErr) return;   /* no marker */
    (void)FSpDelete(&spec);

    /* Centred on BOTH axes, not near the top. The daemon is a background app,
     * so this window cannot come to the front (SelectWindow does not activate
     * one) -- and the window it would sit behind is the kit's own Finder
     * window in the top-left corner, i.e. exactly the thing this text is
     * asking the reader to put away. Measured 2026-07-29: half the sentence
     * was hidden behind it. */
    SetRect(&r, 0, 0, 420, 150);
    OffsetRect(&r,
               (short)((qd.screenBits.bounds.right - 420) / 2),
               (short)((qd.screenBits.bounds.bottom - 150) / 2));
    gWelcomeWindow = NewCWindow(NULL, &r, "\pAppleBridge", true,
                                noGrowDocProc, (WindowPtr)-1L, true, 0);
    if (gWelcomeWindow != NULL) SelectWindow(gWelcomeWindow);
}

void OpenMonitor(void)
{
    Rect r;

    if (gStatusWindow != NULL) {      /* already open: just bring it forward */
        SelectWindow(gStatusWindow);
        return;
    }

    ComputeMonitorRect(&r);           /* saved bounds, or a screen-fitted default */
    gStatusWindow = NewCWindow(NULL, &r, "\pAppleBridge - Verbose", true,
                               zoomDocProc, (WindowPtr)-1L, true, 0);
    if (gStatusWindow == NULL) return;

    /* Zoom box "standard state" = fill the usable screen, so a click on it on a
     * big display expands the window and toggles back to the user size. */
    {
        WStateDataHandle wsd =
            (WStateDataHandle)((WindowPeek)gStatusWindow)->dataHandle;
        if (wsd != NULL) {
            Rect  scr2 = qd.screenBits.bounds;
            short mbar2 = GetMBarHeight();
            (**wsd).userState = r;
            SetRect(&(**wsd).stdState, (short)(scr2.left + 4),
                    (short)(scr2.top + mbar2 + 22),
                    (short)(scr2.right - 4), (short)(scr2.bottom - 4));
        }
    }

    if (!gMenuInstalled) {
        gAppleMenu = NewMenu(APPLE_MENU_ID, "\p\024");
        AppendMenu(gAppleMenu, "\pAbout AppleBridge...;(-");
        AppendResMenu(gAppleMenu, 'DRVR');
        InsertMenu(gAppleMenu, 0);
        /* Edit menu so Copy (Cmd-C) is discoverable; the system also routes the
         * standard Cut/Copy/Paste keys here. We only act on Copy. */
        gEditMenu = NewMenu(EDIT_MENU_ID, "\pEdit");
        AppendMenu(gEditMenu, "\pCopy/C;(-;Show details/D");
        CheckItem(gEditMenu, DETAILS_ITEM, gShowDetails);
        InsertMenu(gEditMenu, 0);
        DrawMenuBar();
        gMenuInstalled = true;
    }

    SelectWindow(gStatusWindow);
    SetPort(gStatusWindow);

    /* The body is a TextEdit field (monospace 9pt) so the log is mouse-
     * selectable and copyable. Monaco reads well for command/response text. */
    {
        Rect body;
        MonitorBodyRect(&body);
        TextFont(kLogFontID);
        TextSize(9);
        TextFace(0);                      /* default run = plain; commands get bold per-line */
        {
            FontInfo fi;
            GetFontInfo(&fi);             /* fixed line height (styled TERec reports -1) */
            gLineHeight = fi.ascent + fi.descent + fi.leading;
            if (gLineHeight < 1) gLineHeight = 11;
        }
        gLogTE = TEStyleNew(&body, &body); /* styled record so command lines can be bold */
        if (gLogTE != NULL) {
            TEAutoView(true, gLogTE);     /* let TESelView scroll to the bottom */
            TEActivate(gLogTE);           /* show selection highlight */
        }
        TextSize(12);
    }

    /* Vertical scrollbar down the right edge, between the activity bar and the
     * grow box, so the log history can be scrolled back through. */
    {
        Rect sb;
        short w = gStatusWindow->portRect.right - gStatusWindow->portRect.left;
        short h = gStatusWindow->portRect.bottom - gStatusWindow->portRect.top;
        SetRect(&sb, w - 15, 19, w + 1, h - 14);
        gScroll = NewControl(gStatusWindow, &sb, "\p", true, 0, 0, 0,
                             scrollBarProc, 0);
    }

    StatusMessage("--- Verbose: live bridge traffic ---");
    gLogDirty = true;
    gTickCounter = 0;                 /* force the next ShowAlive to redraw */
    InvalRect(&gStatusWindow->portRect);
}

/*
 * Poll the shared 'ABrg' block for a Mitlesen request from the menu-bar LED and
 * open (or re-foreground) the monitor window when it changes. Cheap: one deref
 * and compare. No-op when the MenuLED INIT isn't installed (gMonReqCell NULL).
 */
void PollMonitorRequest(void)
{
    if (gMonReqCell == NULL) return;
    if (*gMonReqCell != gMonReqSeen) {
        gMonReqSeen = *gMonReqCell;
        OpenMonitor();
    }
}

/*
 * Check for user interrupt and process events
 */
Boolean CheckUserAbort(void)
{
    EventRecord event;
    WindowPtr window;
    short part;

    /* One-shot post-install greeting, from the event pump rather than from
     * startup. By the time this runs the daemon is past its own init and
     * pumping events, so a fault here costs a window, not the boot. */
    if (!gWelcomeChecked) {
        gWelcomeChecked = true;
        ShowWelcomeIfFresh();
    }

    /* WaitNextEvent (not GetNextEvent) yields the CPU for up to 'sleep' ticks
     * per call, so the connect poll and reconnect wait IDLE instead of spinning
     * at 100% — Basilisk's idlewait then throttles host CPU and the Finder stays
     * reachable. It also delivers our window/menu events like GetNextEvent did. */
    if (WaitNextEvent(everyEvent, &event, 2L, NULL)) {
        switch (event.what) {
            case kHighLevelEvent:
                /* Faceless quit path: dispatch kAEQuitApplication -> HandleQuitApp.
                 * (The mouse/menu cases below are inert with no window/menu.) */
                AEProcessAppleEvent(&event);
                break;

            case mouseDown:
                part = FindWindow(event.where, &window);
                switch (part) {
                    case inMenuBar:
                        HandleMenuCommand(MenuSelect(event.where));
                        break;
                    case inDrag:
                        if (window == gWelcomeWindow) {
                            Rect dr;
                            SetRect(&dr, 4, 24,
                                    qd.screenBits.bounds.right - 4,
                                    qd.screenBits.bounds.bottom - 4);
                            DragWindow(window, event.where, &dr);
                            break;
                        }
                        if (window == gStatusWindow) {
                            Rect dragRect;
                            SetRect(&dragRect, 4, 24,
                                    qd.screenBits.bounds.right - 4,
                                    qd.screenBits.bounds.bottom - 4);
                            DragWindow(window, event.where, &dragRect);
                            SaveMonitorBounds();
                        }
                        break;
                    case inGoAway:
                        if (window == gWelcomeWindow) {
                            if (TrackGoAway(window, event.where)) {
                                DisposeWindow(window);
                                gWelcomeWindow = NULL;
                            }
                            break;
                        }
                        /* Close != quit: tear down the TE field + window, the
                         * daemon keeps running (gRunning stays true). */
                        if (window == gStatusWindow) {
                            if (TrackGoAway(window, event.where)) {
                                if (gLogTE != NULL) {
                                    TEDispose(gLogTE);
                                    gLogTE = NULL;
                                }
                                DisposeWindow(window);   /* also disposes gScroll */
                                gScroll = NULL;
                                gStatusWindow = NULL;
                            }
                        }
                        break;
                    case inGrow:
                        if (window == gStatusWindow) {
                            Rect limits;
                            long newSize;
                            SetRect(&limits, MON_MIN_W, MON_MIN_H,
                                    qd.screenBits.bounds.right,
                                    qd.screenBits.bounds.bottom);
                            newSize = GrowWindow(window, event.where, &limits);
                            if (newSize != 0) {
                                SizeWindow(window, LoWord(newSize),
                                           HiWord(newSize), true);
                                /* Move the scrollbar to the new right edge/height. */
                                if (gScroll != NULL) {
                                    short w2 = window->portRect.right -
                                               window->portRect.left;
                                    short h2 = window->portRect.bottom -
                                               window->portRect.top;
                                    MoveControl(gScroll, w2 - 15, 19);
                                    SizeControl(gScroll, 16, (h2 - 14) - 19);
                                }
                                /* Reflow the TE field to the new body; the next
                                 * ShowAlive re-wraps + re-pins to the bottom. */
                                if (gLogTE != NULL) {
                                    Rect body;
                                    MonitorBodyRect(&body);
                                    (**gLogTE).viewRect = body;
                                    (**gLogTE).destRect = body;
                                    gLogDirty = true;
                                }
                                InvalRect(&window->portRect);
                                SaveMonitorBounds();
                            }
                        }
                        break;
                    case inZoomIn:
                    case inZoomOut:
                        if (window == gStatusWindow &&
                            TrackBox(window, event.where, part)) {
                            SetPort(window);
                            EraseRect(&window->portRect);
                            ZoomWindow(window, part, true);
                            if (gScroll != NULL) {
                                short w2 = window->portRect.right -
                                           window->portRect.left;
                                short h2 = window->portRect.bottom -
                                           window->portRect.top;
                                MoveControl(gScroll, w2 - 15, 19);
                                SizeControl(gScroll, 16, (h2 - 14) - 19);
                            }
                            if (gLogTE != NULL) {
                                Rect body;
                                MonitorBodyRect(&body);
                                (**gLogTE).viewRect = body;
                                (**gLogTE).destRect = body;
                                gLogDirty = true;
                            }
                            InvalRect(&window->portRect);
                            SaveMonitorBounds();
                        }
                        break;
                    case inContent:
                        /* In our window: a click brings it front (if needed) or,
                         * when already front, drives the scrollbar or starts a TE
                         * text selection. */
                        if (window == gStatusWindow) {
                            Point pt = event.where;
                            Rect sbR;
                            short w = window->portRect.right - window->portRect.left;
                            short h = window->portRect.bottom - window->portRect.top;
                            SetPort(window);
                            GlobalToLocal(&pt);
                            SetRect(&sbR, w - 15, 19, w + 1, h - 14);
                            /* Hit-test the scrollbar GEOMETRICALLY (not FindControl/
                             * TrackControl, which don't engage for this faceless
                             * app's window). Top/bottom 16px = arrows (±1 line); the
                             * rest of the track = proportional jump to that spot. */
                            if (gScroll != NULL && PtInRect(pt, &sbR)) {
                                short v    = GetControlValue(gScroll);
                                short mx   = GetControlMaximum(gScroll);
                                short relY = pt.v - sbR.top;
                                short hgt  = sbR.bottom - sbR.top;
                                if (relY < 16) {
                                    v -= 1;
                                } else if (relY > hgt - 16) {
                                    v += 1;
                                } else {
                                    short trackY = relY - 16;
                                    short trackH = hgt - 32;
                                    if (trackH > 0)
                                        v = (short)(((long)trackY * mx) / trackH);
                                }
                                if (v < 0) v = 0;
                                if (v > mx) v = mx;
                                SetControlValue(gScroll, v);
                                ScrollLogTo(v);
                            } else if (window != FrontWindow()) {
                                SelectWindow(window);
                            } else if (gLogTE != NULL) {
                                Rect body;
                                MonitorBodyRect(&body);
                                if (PtInRect(pt, &body)) {
                                    TEClick(pt, (event.modifiers & shiftKey) != 0,
                                            gLogTE);
                                }
                            }
                        } else {
                            SelectWindow(window);
                        }
                        break;
                }
                break;

            case keyDown:
            case autoKey:
                if (event.modifiers & cmdKey) {
                    char key = event.message & charCodeMask;
                    if (key == '.') {
                        return true;
                    }
                    HandleMenuCommand(MenuKey(key));
                }
                break;

            case activateEvt:
                if ((WindowPtr)event.message == gStatusWindow) {
                    Boolean act = (event.modifiers & activeFlag) != 0;
                    if (gLogTE != NULL) { if (act) TEActivate(gLogTE);
                                          else      TEDeactivate(gLogTE); }
                    /* Keep the scrollbar always active (hilite 0) so it remains
                     * clickable even while this background app's window is not the
                     * system-frontmost — a deactivated control is skipped by
                     * FindControl, which made clicks do nothing. */
                    if (gScroll != NULL) HiliteControl(gScroll, 0);
                }
                break;

            case updateEvt:
                BeginUpdate((WindowPtr)event.message);
                /* Guarded by window. DrawLEDs/RedrawLog paint the MONITOR's
                 * contents; they used to run for any update event, which was
                 * harmless only while the daemon had exactly one window. The
                 * welcome window made that assumption false. */
                if ((WindowPtr)event.message == gStatusWindow) {
                    DrawLEDs();    /* top bar (activity) */
                    RedrawLog();   /* console body (TEUpdate) — else updates wipe it */
                    DrawControls(gStatusWindow);   /* the scrollbar */
                    DrawGrowIcon(gStatusWindow);   /* size box / bottom frame */
                } else if ((WindowPtr)event.message == gWelcomeWindow) {
                    DrawWelcome();
                }
                EndUpdate((WindowPtr)event.message);
                break;
        }
    }

    return !gRunning;
}

/*
 * Launch a GUI application at a Mac path and bring it to the foreground.
 * Used by the LAUNCH: verb (ToolServer cannot foreground a GUI app).
 */
static OSErr LaunchAppAtPath(const char *macPath)
{
    Str255 pPath;
    FSSpec spec;
    LaunchParamBlockRec lpb;
    OSErr err;
    short i;

    /* C string -> Pascal string (full HFS path) */
    for (i = 0; macPath[i] && i < 255; i++) {
        pPath[i + 1] = macPath[i];
    }
    pPath[0] = i;

    err = FSMakeFSSpec(0, 0, pPath, &spec);
    if (err != noErr) return err;

    /* Refuse anything that is not an application. `launchNoFileFlags` below
     * tells the Launch Manager NOT to check the file's flags, so if we do not
     * check, nobody does — and handing it a document is not merely an error:
     * on 2026-07-27 a LAUNCH of a THINK C project file ('PROJ') took the whole
     * emulator down. A verb reachable from the host must not be able to do
     * that, so the guard belongs here rather than in the caller. */
    {
        FInfo fi;
        err = FSpGetFInfo(&spec, &fi);
        if (err != noErr) return err;
        if (fi.fdType != 'APPL') return errAENotAnObjSpec;   /* -1727: not launchable */
    }

    lpb.launchBlockID = extendedBlock;
    lpb.launchEPBLength = extendedBlockLen;
    lpb.launchFileFlags = 0;
    lpb.launchControlFlags = launchContinue | launchNoFileFlags;
    lpb.launchAppSpec = &spec;
    lpb.launchAppParameters = NULL;

    return LaunchApplication(&lpb);
}

/*
 * Trigger a clean System 7 restart via the Shutdown Manager, in-process. Used by
 * the REBOOT verb so the host can re-activate a freshly swapped daemon without a
 * manual reboot. A full OS restart is safe; only quitting the daemon while the
 * OS keeps running (QUITDAEMON) trips the host OT-teardown crash.
 *
 * History: this used to send the Finder ('MACS') a 'rest' Apple Event, but a
 * faceless background app cannot reliably make the Finder honour it — verified
 * 2026-06-29 the guest never restarted (ToolServer kept answering through a
 * REBOOT). ShutDwnStart() runs the shutdown procs then restarts the machine
 * directly, with no dependency on the Finder. It does not return if it fires.
 */
static OSErr RebootMac(void)
{
    unsigned long dummy;

    /* The REBOOT ack was already ABSend()'d by the caller; give Open Transport
     * a moment to flush it to the host before the machine restarts (otherwise the
     * host may never see that the reboot fired). ~0.5s (30 ticks). */
    SystemTask();
    Delay(30L, &dummy);

    /* NOTE: do NOT quit ToolServer first — if the restart somehow doesn't fire we
     * want the bridge still usable. ShutDwnStart tears everything down itself. */
    ShutDwnStart();          /* restarts the machine; does not return on success */
    return noErr;            /* only reached if the restart failed to fire */
}

/*
 * Trigger a clean System 7 power-off via the Shutdown Manager, in-process. Used by
 * the SHUTDOWN verb — the SAFE way to stop the guest (and, under Basilisk II, let the
 * emulator quit) WITHOUT hard-killing the host process. A hard kill leaves the guest
 * HFS volume unflushed -> a corrupted disk image; ShutDwnPower() runs the shutdown
 * procs, flushes the volumes, then powers the machine off. Unlike REBOOT, nothing
 * brings the daemon back — the machine is off. It does not return if it fires.
 */
static OSErr ShutdownMac(void)
{
    unsigned long dummy;

    /* The SHUTDOWN ack was already ABSend()'d by the caller; give Open Transport a
     * moment to flush it to the host before the machine powers off. ~0.5s (30 ticks). */
    SystemTask();
    Delay(30L, &dummy);

    ShutDwnPower();          /* powers the machine off; does not return on success */
    return noErr;            /* only reached if the power-off failed to fire */
}

/* Minimal decimal / string appenders for building the STAT verb payload
 * without sprintf or NumToString. Each returns the pointer past what it wrote. */
static char *StatDec(char *p, long v)
{
    char tmp[16];
    short n = 0;
    if (v < 0) { *p++ = '-'; v = -v; }
    if (v == 0) { *p++ = '0'; return p; }
    while (v > 0) { tmp[n++] = (char)('0' + (v % 10)); v /= 10; }
    while (n > 0) *p++ = tmp[--n];
    return p;
}
static char *StatStr(char *p, const char *s)
{
    while (*s) *p++ = *s++;
    return p;
}
static char *StatHex(char *p, unsigned long v)
{
    short i;
    for (i = 28; i >= 0; i -= 4) {
        short nyb = (short)((v >> i) & 0xF);
        *p++ = (char)(nyb < 10 ? '0' + nyb : 'A' + nyb - 10);
    }
    return p;
}

/* Route B: find the boot INIT's global MenuSelect patch by scanning the system
 * heap for its block header (word 0 = 0x601A `BRA.S Go`, word 2 = 0x4D53 'MS').
 * NGetTrapAddress can't find it -- MenuSelect is contended, so a later patch is
 * the $A93D head and ours is chained below it; the block is still resident and
 * armable. Returns the block ptr or 0. */
static Ptr FindMSPatch(void)
{
    unsigned char *p   = (unsigned char *)(*(unsigned long *)0x02A6L); /* SysZone  */
    unsigned char *end = (unsigned char *)(*(unsigned long *)0x02AAL); /* ApplZone */
    for (; p + 4 < end; p += 2) {
        if (*(unsigned short *)p == 0x601A && *(unsigned short *)(p + 2) == 0x4D53)
            return (Ptr)p;
    }
    return 0L;
}

/* Record an error: bump the counter AND remember a short identifying tag, so the
 * monitor/STAT can say WHAT failed, not just how often. Called at each error site. */
static void NoteErr(const char *tag)
{
    short i;
    gErrCount++;
    for (i = 0; tag[i] && i < (short)sizeof(gLastErr) - 1; i++) gLastErr[i] = tag[i];
    gLastErr[i] = '\0';
}

/* The same, with the failing code appended: "AESEND -1712" instead of "cmd fail".
 *
 * Three verbs used to share the literal tag "cmd fail", which is the least
 * useful thing a counter can remember: the footer showed ERR 2 and the operator
 * could not tell a compile error from an Apple Event timeout from a dropped
 * ToolServer without reproducing it. The number is the part that identifies the
 * failure, so it belongs in the tag. */
static void NoteErrCode(const char *tag, long code)
{
    short i = 0, j;
    char  nb[16];
    long  n = code;
    Boolean neg = false;

    gErrCount++;
    while (tag[i] && i < (short)sizeof(gLastErr) - 8) { gLastErr[i] = tag[i]; i++; }
    gLastErr[i++] = ' ';

    if (n < 0) { neg = true; n = -n; }
    j = 0;
    if (n == 0) nb[j++] = '0';
    while (n > 0) { nb[j++] = (char)('0' + (n % 10)); n /= 10; }
    if (neg) gLastErr[i++] = '-';
    while (j > 0 && i < (short)sizeof(gLastErr) - 1) gLastErr[i++] = nb[--j];
    gLastErr[i] = '\0';
}

/*
 * Footer telemetry: a one-line diagnostic strip drawn in the empty 15px band
 * below the log body (MonitorBodyRect reserves it) and left of the grow box —
 * so it needs no layout change. Shows the running RX / TX / error totals, the
 * last real command's round-trip latency (RX->TX) as a number AND a colour-coded
 * analog health bar, and the last error's tag. gLastLat is maintained in
 * ProcessRequest (heartbeat-gated), so this only READS it — recomputing here would
 * clobber it with the ~0-tick heartbeat latency on every 8x/sec refresh.
 * (Full application, not a code resource — divide/format/GetPen glue is fine.)
 */
static void DrawTelemetry(void)
{
    Rect     foot, bar;
    Str255   s;
    char     buf[96];
    char    *b = buf;
    short    w, h, i;
    long     ms, barw;
    Point    pen;
    RGBColor cBlack = { 0, 0, 0 };
    RGBColor cWhite = { 0xFFFF, 0xFFFF, 0xFFFF };
    RGBColor saveF, cHealth;

    if (gStatusWindow == NULL) return;
    SetPort(gStatusWindow);
    GetForeColor(&saveF);
    w = gStatusWindow->portRect.right - gStatusWindow->portRect.left;
    h = gStatusWindow->portRect.bottom - gStatusWindow->portRect.top;

    ms = gLastLat * 1000 / 60;      /* ticks -> ms (read-only; set in ProcessRequest) */

    SetRect(&foot, 0, (short)(h - 15), (short)(w - 15), h);
    RGBBackColor(&cWhite);
    RGBForeColor(&cBlack);
    EraseRect(&foot);

    /* Rule between the log body and the footer. Without it the last log line
     * runs straight into the telemetry strip, which reads as a clipped window
     * rather than as two areas (noticed 2026-07-25). */
    MoveTo(0, (short)(h - 16));
    LineTo((short)(w - 15), (short)(h - 16));

    /* The grow box shares the bottom-right corner with the footer, so redraw it
     * here too — otherwise the corner stays blank after anything that repaints
     * this strip without a full window update. */
    DrawGrowIcon(gStatusWindow);

    /* --- active transport + counters + latency number --- */
    b = StatStr(b, "NET ");    b = StatStr(b, ABTransportName());  b = StatStr(b, "  ");
    b = StatStr(b, "RX ");     b = StatDec(b, gRXCount);
    b = StatStr(b, "  TX ");   b = StatDec(b, gTXCount);
    b = StatStr(b, "  ERR ");  b = StatDec(b, gErrCount);
    b = StatStr(b, "  last "); b = StatDec(b, ms);
    b = StatStr(b, "ms ");
    *b = '\0';
    for (i = 0; buf[i] && i < 250; i++) s[i + 1] = buf[i];
    s[0] = (unsigned char)i;
    TextSize(9);
    MoveTo(6, (short)(h - 4));
    DrawString(s);
    GetPen(&pen);                   /* where the text ended -> bar starts here */

    /* --- analog health bar: colour by latency band, length scaled + capped --- */
    if (ms < 200)       { cHealth.red = 0x1000; cHealth.green = 0xC000; cHealth.blue = 0x1000; } /* green: healthy */
    else if (ms < 1000) { cHealth.red = 0xF000; cHealth.green = 0xB000; cHealth.blue = 0x0000; } /* amber: slow    */
    else                { cHealth.red = 0xE000; cHealth.green = 0x1000; cHealth.blue = 0x1000; } /* red: unhealthy */
    barw = ms / 25;                 /* 1200ms -> 48px full scale */
    if (barw > 48) barw = 48;
    if (barw < 3)  barw = 3;        /* a small stub even at ~0ms (idle = green/healthy) */
    bar.left   = (short)(pen.h + 2);
    bar.top    = (short)(h - 12);
    bar.right  = (short)(pen.h + 2 + barw);
    bar.bottom = (short)(h - 4);
    RGBForeColor(&cHealth);
    PaintRect(&bar);
    RGBForeColor(&cBlack);
    FrameRect(&bar);

    /* --- last error tag (identification), only once something has failed --- */
    if (gErrCount > 0 && gLastErr[0]) {
        char  eb[80];
        char *ep = eb;
        ep = StatStr(ep, "  err:");
        ep = StatStr(ep, gLastErr);
        *ep = '\0';
        for (i = 0; eb[i] && i < 250; i++) s[i + 1] = eb[i];
        s[0] = (unsigned char)i;
        MoveTo((short)(bar.right + 4), (short)(h - 4));
        DrawString(s);
    }

    RGBForeColor(&saveF);
    TextSize(12);
}

/* Parse exactly 8 hex digits into an OSType (4 bytes), for the AESEND verb. */
static OSType ParseHexType(const char *s)
{
    OSType v = 0;
    short k;
    for (k = 0; k < 8; k++) {
        char c = s[k];
        short d;
        if (c >= '0' && c <= '9') d = c - '0';
        else if (c >= 'a' && c <= 'f') d = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') d = c - 'A' + 10;
        else d = 0;
        v = (v << 4) | d;
    }
    return v;
}

/*
 * Process a request from the host
 */
/* Returns true if the connection is still healthy, false if a response send
 * failed partway (the wire is then desynced and the caller must reconnect). */

/*
 * MONITOR:0|1 — hide or show the Verbose console over the bridge.
 *
 * The window covers the desktop, which is exactly wrong while the guest's GUI
 * is being driven: clicking a disk icon or pulling a menu means aiming around
 * it first. Its close box already exists, but a human closing a window is no
 * help to an automated sequence.
 *
 * Hiding uses HideWindow rather than the close box's DisposeWindow: the log ring
 * and the scroll position survive, so showing it again continues the same
 * session instead of starting an empty one. If the window was closed outright
 * (or never opened), MONITOR:1 opens a fresh one.
 */
Boolean MonitorVerb(ABConn *conn, char *request, long requestLen)
{
    short         i = (short)strlen(PROTO_MONITOR);
    Boolean       show = true;
    CommandResult res;
    Handle        h;
    const char   *state;
    short         len, k;

    res.exitCode = -1;
    res.outData  = NULL;
    res.outLen   = 0;
    res.errData[0] = '\0';

    if (i < requestLen && request[i] == ':') i++;
    if (i < requestLen && (request[i] == '0' || request[i] == 'h' || request[i] == 'H'))
        show = false;

    if (show) {
        if (gStatusWindow == NULL) {
            OpenMonitor();                 /* was closed outright: build a new one */
        } else {
            ShowWindow(gStatusWindow);
            SelectWindow(gStatusWindow);
            /* ShowHide brings the window back but leaves its title bar
             * unpainted, so it returns blank. Two calls finish the job, in this
             * order: HiliteWindow paints the bar — with false, because a
             * background application's window is legitimately INACTIVE and
             * painting the active state left a hybrid the Window Manager never
             * completed (stripes but no text) — and SetWTitle then fills in the
             * title, which must come second or the hilite overpaints it.
             *
             * Do NOT repaint by moving the window instead: MoveWindow takes the
             * STRUCTURE origin while the port gives the CONTENT origin, so a
             * "move it back where it already is" nudge walks the window UP by a
             * title-bar height each time until the bar vanishes under the menu
             * bar. That was the actual cause of the disappearing title bar
             * (diagnosed 2026-07-25), not a missing frame. */
            HiliteWindow(gStatusWindow, false);
            SetWTitle(gStatusWindow, "\pAppleBridge - Verbose");
            SetPort(gStatusWindow);
            InvalRect(&gStatusWindow->portRect);
            gLogDirty = true;
            gTickCounter = 0;              /* force the next ShowAlive to redraw */
        }
        state = (gStatusWindow != NULL) ? "shown" : "could not open";
    } else {
        if (gStatusWindow != NULL) HideWindow(gStatusWindow);
        state = "hidden";
    }

    len = (short)strlen(state);
    h = NewHandle(len + 1);
    if (h != NULL) {
        HLock(h);
        for (k = 0; k < len; k++) (*h)[k] = state[k];
        (*h)[len] = '\r';
        HUnlock(h);
    }
    res.exitCode = 0;
    res.outData  = h;
    res.outLen   = (h != NULL) ? (len + 1) : 0;
    SendCommandResult(conn, &res);
    if (h != NULL) DisposeHandle(h);
    return true;
}


/* ---- MACUITREE helpers: append to a growable Handle, always-valid ASCII JSON.
 * PtrAndHand grows the handle and copies, so the tree size is bounded only by
 * heap (dialogs are tiny). sprintf-free, like the rest of the daemon. */
static char gJHex[] = "0123456789abcdef";
static DialogPtr gTestDlg = NULL;   /* DLGTEST: an in-process DITL dialog to
                                     * validate the mac_ui_tree/DLGTREE walk. */
/* DLGSELFMODAL filter: dismiss on the FIRST pass — even a null event — so a modal
 * driven from the background daemon returns without ever waiting for an event and
 * therefore cannot spin (see the SFGetFile note below on background modals). The
 * dlgpatch head runs at trap ENTRY, before this loop, so generation still bumps. */
static pascal Boolean DlgSelfFilter(DialogPtr dp, EventRecord *ev, short *item)
{
#pragma unused(dp, ev)
    *item = 1;      /* pretend item 1 (OK) hit */
    return true;    /* true => ModalDialog returns immediately with *item */
}
static void JPut(Handle h, const char *s, long n)
{
    (void)PtrAndHand((Ptr)s, h, n);
}
static void JStr(Handle h, const char *s)
{
    JPut(h, s, (long)strlen(s));
}
static void JNum(Handle h, long v)
{
    char t[16];
    char *e = StatDec(t, v);
    JPut(h, t, (long)(e - t));
}
/* Append a Pascal string as a JSON string BODY (caller writes the quotes),
 * escaping " \ and any byte <0x20 or >=0x7F as \u00XX so the JSON stays valid
 * ASCII no matter what MacRoman the label holds. */
static void JPStr(Handle h, const unsigned char *ps)
{
    short i, len;
    unsigned char c;
    char esc[8];
    len = ps[0];
    for (i = 1; i <= len; i++) {
        c = ps[i];
        if (c == '"' || c == '\\') { esc[0] = '\\'; esc[1] = (char)c; JPut(h, esc, 2); }
        else if (c >= 0x20 && c < 0x7F) { esc[0] = (char)c; JPut(h, esc, 1); }
        else {
            esc[0] = '\\'; esc[1] = 'u'; esc[2] = '0'; esc[3] = '0';
            esc[4] = gJHex[(c >> 4) & 0x0F]; esc[5] = gJHex[c & 0x0F];
            JPut(h, esc, 6);
        }
    }
}

/* dlgpatch shared-block offsets — must match dlgpatch.a and dlgwalk.c. */
#define oDP_Real     6
#define oDP_Armed    10
#define oDP_OneShot  12
#define oDP_Up       14
#define oDP_Gen      16
#define oDP_Cnt      18
#define oDP_Trunc    20
#define oDP_Rect     22
#define oDP_Recs     30
#define DP_RECSIZE   48

/* Find the dlgpatch block by scanning the system heap for word0=$6000 (BRA.W)
 * and word@+4=$4450 ('DP') — the ModalDialog trap is contended, so like the
 * MenuSelect patch we locate our block by signature, not NGetTrapAddress.
 * (Clone of FindMSPatch.) */
/* Return the INSTALLED + ACTIVE dlgpatch block, or NULL. A trap patch has two
 * separate facts — the code is RESIDENT, and the trap POINTS AT IT — and neither
 * check sees both. Require the CONJUNCTION (see the "Presence is not
 * installation" operating note):
 *   (1) the heap scan finds a resident 'DP' block (the pristine 'DPAT' RESOURCE
 *       is =resSysHeap,resLocked, so a bare signature scan matches it too);
 *   (2) the _ModalDialog trap vector equals that block's entry (entry is at +0)
 *       — a signature-only match reports the un-hooked resource as installed;
 *   (3) its Real (+6) is non-zero AND not the block itself — an "adopted"/un-
 *       hooked copy has 0 there. (Trap-head ALONE is also wrong: ToolServer
 *       restores the trap table around every tool run, so the head reads absent
 *       over an intact patch — which is why the scan stays part of the test.) */
static Ptr FindDlgPatch(void)
{
    unsigned char *p    = (unsigned char *)(*(unsigned long *)0x02A6L); /* SysZone  */
    unsigned char *end  = (unsigned char *)(*(unsigned long *)0x02AAL); /* ApplZone */
    Ptr            trap = (Ptr)NGetTrapAddress(_ModalDialog, ToolTrap);
    unsigned long  real;
    for (; p + 10 < end; p += 2) {
        if (*(unsigned short *)p == 0x6000                 /* (1) resident 'DP'    */
            && *(unsigned short *)(p + 4) == 0x4450
            && (Ptr)p == trap                              /* (2) trap points here */
            && (real = *(unsigned long *)(p + 6)) != 0L
            && real != (unsigned long)p)                   /* (3) chained, not adopted */
            return (Ptr)p;
    }
    return 0L;
}

/* ---- P4: honest dialog_up — clear it when a dialog is CLOSED/DISPOSED --------
 * dlgpatch SETS DialogUp(+oDP_Up) on _ModalDialog entry, but nothing CLEARS it,
 * so DLGTREE kept reporting a dismissed dialog as up — a stale snapshot (caught
 * 2026-08-02: DLGTREE said dialog_up:true over an empty screen). Route B (daemon-
 * side, no boot INIT, no reboot): install tiny PIC head-patches on _CloseDialog /
 * _DisposDialog in the system heap that zero DialogUp and chain to the real trap.
 * Installed idempotently on DLGARM, restored on DLGUNINSTALL.
 *
 * REACH (measured 2026-08-03, corrected): a RUNTIME trap patch is PROCESS-LOCAL
 * here — unlike the boot-installed dlgpatch, these stubs fire ONLY in the daemon's
 * OWN process. They clear DialogUp for a dialog the DAEMON itself disposes (which
 * is why DLGSELFMODAL's own-process self-test sees dialog_up drop), but NEVER for a
 * foreign app's dialog: a cross-app dialog_up stays stale until the next walk bumps
 * generation. Do NOT read "system heap" as "global" — installation site, not heap
 * location, decides reach (OPERATING_NOTES, the trap-table note). Kept only because
 * DLGSELFMODAL needs it; this is NOT a cross-app dialog_up honesty mechanism. */
#define kCloseDialogTrap   0xA982
#define kDisposDialogTrap  0xA983
#define kCDMagic           0x43445054L   /* 'CDPT' */
#define oCD_DlgBlk    4
#define oCD_RealClose 8
#define oCD_RealDisp  12
#define oCD_CloseStub 16
#define oCD_DispStub  34
#define kCD_Size      52

/* Two PIC head-patch stubs, hand-assembled and verified byte-for-byte:
 *   CloseStub (+16):  LEA CDBase(PC),A0 ; A1=[DlgBlk] ; CLR.W oDP_Up(A1) ;
 *                     A0=[RealClose] ; JMP (A0)
 *   DispStub  (+34):  identical, chaining [RealDisp]
 * DlgBlk/RealClose/RealDisp (+4/+8/+12) are filled by the daemon after the move;
 * the code is PC-relative so it survives BlockMove into the system heap. The
 * CLR.W target 14(A1) is oDP_Up in the DPAT block. */
static const unsigned char kCDTemplate[kCD_Size] = {
    0x43,0x44,0x50,0x54, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00,
    0x41,0xFA,0xFF,0xEE, 0x22,0x68,0x00,0x04, 0x42,0x69,0x00,0x0E, 0x20,0x68,0x00,0x08, 0x4E,0xD0,
    0x41,0xFA,0xFF,0xDC, 0x22,0x68,0x00,0x04, 0x42,0x69,0x00,0x0E, 0x20,0x68,0x00,0x0C, 0x4E,0xD0
};

/* The system-heap 'CDPT' stub currently head-patching _CloseDialog, or NULL —
 * read from the LIVE trap vector, so a daemon hot-swap (SWAPSELF) that lost its
 * globals still finds the resident stub. */
static Ptr FindClosePatch(void)
{
    unsigned long ct = (unsigned long)NGetTrapAddress(kCloseDialogTrap, ToolTrap);
    if (ct != 0L && *(unsigned long *)(ct - oCD_CloseStub) == kCDMagic)
        return (Ptr)(ct - oCD_CloseStub);
    return 0L;
}

/* Install (or adopt) the close/dispose honesty patch, pointed at dlgblk. */
static Ptr InstallDlgClosePatch(Ptr dlgblk)
{
    Ptr s;
    if (dlgblk == NULL) return NULL;
    s = FindClosePatch();
    if (s != NULL) {                                    /* adopt a resident stub */
        *(unsigned long *)((char *)s + oCD_DlgBlk) = (unsigned long)dlgblk;
        return s;
    }
    s = NewPtrSys((Size)kCD_Size);
    if (s == NULL) return NULL;
    BlockMove((Ptr)kCDTemplate, s, (Size)kCD_Size);
    *(unsigned long *)((char *)s + oCD_DlgBlk)    = (unsigned long)dlgblk;
    *(unsigned long *)((char *)s + oCD_RealClose) = (unsigned long)NGetTrapAddress(kCloseDialogTrap, ToolTrap);
    *(unsigned long *)((char *)s + oCD_RealDisp)  = (unsigned long)NGetTrapAddress(kDisposDialogTrap, ToolTrap);
    NSetTrapAddress((UniversalProcPtr)((char *)s + oCD_CloseStub), kCloseDialogTrap, ToolTrap);
    NSetTrapAddress((UniversalProcPtr)((char *)s + oCD_DispStub),  kDisposDialogTrap, ToolTrap);
    return s;
}

/* ---- Counter-probe: do the _GetNextEvent/_WaitNextEvent TRAPS fire while a modal
 * alert stands? -------------------------------------------------------------------
 * The question that decides the uncaptured-dialog perception (2026-08-03): the
 * Event Manager provably RUNS during a standing alert (a background daemon got time),
 * but does the alert's loop fetch events through the A970/A860 TRAPS (patchable) or
 * through a ROM-internal path (needs a jGNE filter, which sits inside the Event
 * Manager, below the trap layer)? Arm these two counters, hold an alert up, and see
 * if they climb. A970/A860 are the HOTTEST traps in the system, so each stub is a
 * PURE counter — touch only A0 (scratch) + CCR, bump one long, chain — exactly
 * dlgpatch's pass-through shape. It ships DISARMED (Armed=0 -> transparent JMP, zero
 * cost) and is armed only for a ~2s window, so a fault costs a reboot, not data.
 * Route B: a runtime system-heap block, no boot INIT. */
#define kGNETrap    0xA970          /* _GetNextEvent  */
#define kWNETrap    0xA860          /* _WaitNextEvent */
/* 'CPR2', not 'CPRB': the layout changed (PrevA5), so a NEW daemon must not adopt
 * an OLD block via FindCounterProbe and then write PrevA5 past its end. A changed
 * magic makes that impossible instead of unlikely; the stale block simply stays in
 * the chain as a disarmed pass-through. */
#define kCPMagic    0x43505232L     /* 'CPR2' */
#define oCP_Armed    4              /* word */
#define oCP_CntGNE   8              /* long */
#define oCP_CntWNE   12             /* long */
#define oCP_RealGNE  16             /* long */
#define oCP_RealWNE  20             /* long */
#define oCP_LastA5   24             /* long: CurrentA5 of the last FOREGROUND (non-self) caller */
#define oCP_SelfA5   28             /* long: the daemon's OWN CurrentA5 (set at install) */
#define oCP_OtherCnt 32             /* long: count of non-self (foreground) armed calls */
#define oCP_PrevA5   36             /* long: the PREVIOUS DISTINCT non-self A5 (see below) */
#define oCP_GNEStub  40
#define oCP_WNEStub  90
#define oCP_jReal    140            /* long: old jGNE filter (or &jRTS if there was none) */
#define oCP_jCnt     144            /* long: jGNE armed call count */
#define oCP_jStub    148            /* the jGNE-filter counter stub (0x29A hook) */
#define oCP_jRTS     206            /* a bare RTS: so the chain never JMPs to 0 */
#define kCP_Size     208
#define kCurrentA5   0x0904L        /* low-mem: Process Manager swaps it per process */
#define kJGNEFilter  0x029AL        /* low-mem: the GetNextEvent filter ProcPtr (SINGLE slot) */

/* Two PIC head-counter stubs, hand-assembled and verified byte-for-byte. Each ARMED
 * call bumps the per-trap counter, loads CurrentA5, and — only if it is NOT the daemon's
 * own A5 (SelfA5) — bumps OtherCount and records the caller. The SELF-FILTER is essential:
 * the daemon pumps GetNextEvent/WaitNextEvent so fast that an unfiltered LastA5 is almost
 * always the daemon (measured 10/10 self).
 *
 * ---- why there are TWO recorded slots (2026-08-04) --------------------------------
 * One slot was not enough, and the reason is measured: with the daemon filtered out, a
 * SECOND, FOREIGN process still polled the traps ~59x/second (A5 107480968), while the
 * foreground app's caret blink calls about 2x/second. A single LastA5 is therefore
 * overwritten by the poller almost immediately, and the foreground's call — the whole
 * question — vanished in 0 of ~130 samples.
 *
 * The recorded closure for this was "a second self-filter: skip that process's A5 too".
 * It cannot be built as stated: the poller was first attributed to the health watchdog
 * and that attribution was REFUTED in source (watchdog.c sleeps ~60 ticks => ~1 call/s,
 * not 59), so it has no name — and its A5 is a heap address that no reboot preserves,
 * so there is no constant to compile in either.
 *
 * What is built instead needs no identity at all: a repeat of the A5 already in LastA5
 * does NOT shift. A process polling at any rate therefore occupies exactly ONE slot,
 * and the foreground's rare call parks in the other and STAYS there until a THIRD
 * distinct A5 appears. Reading LastA5 *or* PrevA5 answers "did the foreground enter this
 * trap during the window?" without knowing who the noisy neighbour is — and it names the
 * neighbour as a side effect, which is what the identification still owes.
 *
 * Per counter stub:
 *   LEA Base(PC),A0 ; TST.W Armed(A0) ; BEQ.S p ; ADDQ.L #1,Cnt(A0) ;
 *   MOVE.L ($0904).W,D0 ; CMP.L SelfA5(A0),D0 ; BEQ.S p ; ADDQ.L #1,OtherCount(A0) ;
 *   CMP.L LastA5(A0),D0 ; BEQ.S p ; MOVE.L LastA5(A0),PrevA5(A0) ; MOVE.L D0,LastA5(A0) ;
 *   p: MOVE.L Real(A0),A0 ; JMP (A0)
 * Still straight-line — no loop in the system's hottest trap — and still only D0 + A0
 * (both scratch) + CCR. Generated and label-resolved rather than typed, then verified by
 * an INDEPENDENT disassembler (capstone), because hand-computed PC-relative displacements
 * are what crashed the earlier spike. */
static const unsigned char kCPTemplate[kCP_Size] = {
    'C','P','R','2', 0x00,0x00, 0x00,0x00, 0x00,0x00,0x00,0x00,    /* magic,armed,pad,cntGNE */
    0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, /* cntWNE,realGNE,realWNE */
    0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00, /* LastA5,SelfA5,OtherCount */
    0x00,0x00,0x00,0x00,                                           /* +36 PrevA5 */
    /* +40 GNEStub */
    0x41,0xFA,0xFF,0xD6, 0x4A,0x68,0x00,0x04, 0x67,0x22, 0x52,0xA8,0x00,0x08,
    0x20,0x38,0x09,0x04, 0xB0,0xA8,0x00,0x1C, 0x67,0x14, 0x52,0xA8,0x00,0x20,
    0xB0,0xA8,0x00,0x18, 0x67,0x0A, 0x21,0x68,0x00,0x18,0x00,0x24, 0x21,0x40,0x00,0x18,
    0x20,0x68,0x00,0x10, 0x4E,0xD0,
    /* +90 WNEStub */
    0x41,0xFA,0xFF,0xA4, 0x4A,0x68,0x00,0x04, 0x67,0x22, 0x52,0xA8,0x00,0x0C,
    0x20,0x38,0x09,0x04, 0xB0,0xA8,0x00,0x1C, 0x67,0x14, 0x52,0xA8,0x00,0x20,
    0xB0,0xA8,0x00,0x18, 0x67,0x0A, 0x21,0x68,0x00,0x18,0x00,0x24, 0x21,0x40,0x00,0x18,
    0x20,0x68,0x00,0x14, 0x4E,0xD0,
    /* +140 jReal, +144 jCnt */
    0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x00,
    /* +148 jGNE-filter counter stub. Preserves A1/D0 (the Event Manager's event ptr +
     * result). Saves D1-D2 in the ARMED branch only (the jGNE caller's scratch is an
     * assumption, not a guarantee — this is what crashed the earlier spike). Disarmed =
     * LEA/TST/BEQ/MOVE/JMP, A0-only, the system's hottest path kept minimal. Chains via
     * jReal, which is never 0 (install points it at jRTS when 0x29A had no old filter):
     *   LEA jBase(PC),A0 ; TST.W Armed(A0) ; BEQ.S .chain ;
     *   MOVEM.L D1-D2,-(SP) ; ADDQ.L #1,jCnt(A0) ; MOVE.L ($0904).W,D1 ;
     *   CMP.L SelfA5(A0),D1 ; BEQ.S .norec ; ADDQ.L #1,OtherCount(A0) ;
     *   CMP.L LastA5(A0),D1 ; BEQ.S .norec ; MOVE.L LastA5(A0),PrevA5(A0) ;
     *   MOVE.L D1,LastA5(A0) ;
     *   .norec: MOVEM.L (SP)+,D1-D2 ; .chain: MOVE.L jReal(A0),A0 ; JMP (A0) */
    0x41,0xFA,0xFF,0x6A, 0x4A,0x68,0x00,0x04, 0x67,0x2A, 0x48,0xE7,0x60,0x00,
    0x52,0xA8,0x00,0x90, 0x22,0x38,0x09,0x04, 0xB2,0xA8,0x00,0x1C, 0x67,0x14,
    0x52,0xA8,0x00,0x20, 0xB2,0xA8,0x00,0x18, 0x67,0x0A, 0x21,0x68,0x00,0x18,0x00,0x24,
    0x21,0x41,0x00,0x18, 0x4C,0xDF,0x00,0x06, 0x20,0x68,0x00,0x8C,
    0x4E,0xD0,
    /* +206 jRTS */
    0x4E,0x75
};

static Ptr FindCounterProbe(void)
{
    unsigned long gt = (unsigned long)NGetTrapAddress(kGNETrap, ToolTrap);
    if (gt != 0L && *(unsigned long *)(gt - oCP_GNEStub) == kCPMagic)
        return (Ptr)(gt - oCP_GNEStub);
    return 0L;
}

static Ptr InstallCounterProbe(void)
{
    Ptr s = FindCounterProbe();
    if (s != NULL) return s;                        /* idempotent / adopt across swap */
    s = NewPtrSys((Size)kCP_Size);
    if (s == NULL) return NULL;
    BlockMove((Ptr)kCPTemplate, s, (Size)kCP_Size);
    *(short *)((char *)s + oCP_Armed) = 0;          /* DISARMED by default */
    *(unsigned long *)((char *)s + oCP_RealGNE) = (unsigned long)NGetTrapAddress(kGNETrap, ToolTrap);
    *(unsigned long *)((char *)s + oCP_RealWNE) = (unsigned long)NGetTrapAddress(kWNETrap, ToolTrap);
    *(unsigned long *)((char *)s + oCP_SelfA5)  = *(unsigned long *)kCurrentA5;  /* the daemon's own A5 */
    *(long *)((char *)s + oCP_OtherCnt) = 0L;
    *(long *)((char *)s + oCP_PrevA5)   = 0L;   /* 0 = nothing recorded; no A5 is ever 0 */
    NSetTrapAddress((UniversalProcPtr)((char *)s + oCP_GNEStub), kGNETrap, ToolTrap);
    NSetTrapAddress((UniversalProcPtr)((char *)s + oCP_WNEStub), kWNETrap, ToolTrap);
    return s;
}

/* ---- jgnepatch: runtime jGNE ($029A) one-shot walk-on-request ---------------------
 * A SECOND trigger for the SAME DlgWalk as dlgpatch -- a BYTE-IDENTICAL copy: the
 * jgnepatch resource is linked from the very same dlgwalk.c.o. Fired from the jGNE
 * filter ($029A), which the Event Manager JSRs from inside every event fetch, so while
 * a modal STANDS the foreground app's own ModalDialog loop pumps it and this stub runs
 * in THAT app's context -- FrontWindow/the DITL valid. It captures the one case
 * _ModalDialog cannot: an ALREADY-STANDING dialog (that trap is entered once per dialog,
 * before anything can be armed). Writes dlgpatch's DP block (jDPBlock = FindDlgPatch());
 * DLGTREE reads that one block. Cross-trigger safety is a DAEMON PROTOCOL RULE -- DLGWALK
 * and DLGARM are mutually exclusive -- NOT a shared 68k flag (the 2026-08-03 owner review
 * retracted the flag when it turned out to cost a dlgpatch rebuild + reboot). Runtime only
 * (SWAPSELF, no reboot); a reboot clears $029A and the sysheap block. NOTE: dialogKind
 * means "a dialog window", not "a MODAL dialog" -- a modeless foreground dialog would also
 * be walked; no cheap modality test exists (documented, not a bug). */
#define kJGMagic      0x4A47L
#define oJG_Magic     4
#define oJG_jReal     6                /* long: old $029A filter (0 -> the stub RTSs) */
#define oJG_jArmed    10               /* word: daemon-owned gate; template ships DISARMED */
#define oJG_jOneShot  12               /* word */
#define oJG_jBusy     14               /* word: LOCAL re-entrancy guard */
#define oJG_jTries    16               /* word: armed-fire count (bounded arm) */
#define oJG_jMaxTries 18               /* word: self-disarm backstop */
#define oJG_jTargetA5 20               /* long: the process whose dialog to walk */
#define oJG_jDPBlock  24               /* long: = FindDlgPatch(); DlgWalk writes here */
#define kJG_Size      740
#define kJG_DefTries  10000            /* ~60s at ~150 fires/s: a backstop; the client
                                        * disarms on a short timeout well before this */

static const unsigned char kJGTemplate[kJG_Size] = {
    0x60,0x00,0x00,0x1A,0x4A,0x47,0x00,0x00,0x00,0x00,0x00,0x00,
    0x00,0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x41,0xFA,0xFF,0xE2,0x4A,0x68,0x00,0x0A,
    0x67,0x68,0x48,0xE7,0xFF,0xFE,0x41,0xFA,0xFF,0xD4,0x20,0x38,
    0x09,0x04,0xB0,0xA8,0x00,0x14,0x66,0x4E,0x4A,0x68,0x00,0x0E,
    0x66,0x48,0x52,0x68,0x00,0x10,0x30,0x28,0x00,0x10,0xB0,0x68,
    0x00,0x12,0x63,0x06,0x42,0x68,0x00,0x0A,0x60,0x34,0x31,0x7C,
    0x00,0x01,0x00,0x0E,0x2F,0x28,0x00,0x18,0x61,0x00,0x00,0x3E,
    0x58,0x8F,0x41,0xFA,0xFF,0x9C,0x22,0x68,0x00,0x18,0x4A,0x69,
    0x00,0x0E,0x67,0x0E,0x52,0x69,0x00,0x10,0x4A,0x68,0x00,0x0C,
    0x67,0x04,0x42,0x68,0x00,0x0A,0x41,0xFA,0xFF,0x80,0x42,0x68,
    0x00,0x0E,0x4C,0xDF,0x7F,0xFF,0x41,0xFA,0xFF,0x74,0x4A,0xA8,
    0x00,0x06,0x67,0x06,0x20,0x68,0x00,0x06,0x4E,0xD0,0x4E,0x75,
    0x4E,0x56,0xFE,0xC0,0x48,0xE7,0x1F,0x38,0x26,0x6E,0x00,0x08,
    0x24,0x4B,0x42,0x6A,0x00,0x12,0x42,0x6A,0x00,0x0E,0x42,0x6A,
    0x00,0x14,0x59,0x4F,0xA9,0x24,0x20,0x1F,0x28,0x40,0x4A,0x80,
    0x66,0x04,0x4E,0xFA,0x02,0x0E,0x20,0x0C,0x2D,0x40,0xFE,0xC0,
    0x20,0x40,0x30,0x28,0x00,0x6C,0x72,0x02,0xB0,0x41,0x67,0x04,
    0x4E,0xFA,0x01,0xF8,0x20,0x0C,0x2D,0x40,0xFE,0xC4,0x20,0x40,
    0x3D,0x68,0x00,0xA8,0xFF,0xF0,0x48,0x6E,0xFE,0xC8,0xA8,0x74,
    0x2F,0x2E,0xFE,0xC4,0xA8,0x73,0x24,0x6E,0xFE,0xC0,0x4A,0xAA,
    0x00,0x76,0x67,0x1A,0x20,0x6A,0x00,0x76,0x4A,0x90,0x67,0x12,
    0x20,0x6A,0x00,0x76,0x20,0x50,0x54,0x48,0x43,0xEE,0xFE,0xD4,
    0x22,0xD8,0x22,0x90,0x60,0x0E,0x20,0x4C,0x70,0x10,0xD1,0xC0,
    0x43,0xEE,0xFE,0xD4,0x22,0xD8,0x22,0x90,0x24,0x4B,0x35,0x6E,
    0xFE,0xD4,0x00,0x16,0x35,0x6E,0xFE,0xD6,0x00,0x18,0x35,0x6E,
    0xFE,0xD8,0x00,0x1A,0x35,0x6E,0xFE,0xDA,0x00,0x1C,0x20,0x6E,
    0xFE,0xC4,0x20,0x28,0x00,0x9C,0x2D,0x40,0xFF,0xF8,0x67,0x12,
    0x20,0x40,0x4A,0x90,0x67,0x0C,0x20,0x40,0x20,0x50,0x30,0x10,
    0x52,0x40,0x48,0xC0,0x60,0x02,0x70,0x00,0x3A,0x00,0x72,0x18,
    0xB0,0x41,0x6F,0x08,0x35,0x7C,0x00,0x01,0x00,0x14,0x7A,0x18,
    0x42,0x6E,0xFF,0xEA,0x20,0x4B,0x70,0x1E,0xD1,0xC0,0x2D,0x48,
    0xFF,0xF4,0x7C,0x01,0x36,0x06,0xB6,0x45,0x6E,0x00,0x01,0x34,
    0x2F,0x2E,0xFE,0xC4,0x3F,0x03,0x48,0x6E,0xFF,0xE8,0x48,0x6E,
    0xFE,0xE4,0x48,0x6E,0xFE,0xCC,0xA9,0x8D,0x30,0x2E,0xFF,0xE8,
    0x72,0x7F,0xC0,0x41,0x3E,0x00,0x3D,0x6E,0xFE,0xCE,0xFE,0xDE,
    0x3D,0x6E,0xFE,0xCC,0xFE,0xDC,0x48,0x6E,0xFE,0xDC,0xA8,0x70,
    0x3D,0x6E,0xFE,0xD2,0xFE,0xE2,0x3D,0x6E,0xFE,0xD0,0xFE,0xE0,
    0x48,0x6E,0xFE,0xE0,0xA8,0x70,0x42,0x2E,0xFE,0xE8,0x0C,0x47,
    0x00,0x04,0x6D,0x18,0x0C,0x47,0x00,0x07,0x6E,0x12,0x20,0x2E,
    0xFE,0xE4,0x67,0x2A,0x2F,0x2E,0xFE,0xE4,0x48,0x6E,0xFE,0xE8,
    0xA9,0x5E,0x60,0x1E,0x36,0x07,0x70,0x08,0xB6,0x40,0x67,0x06,
    0x70,0x10,0xB6,0x40,0x66,0x10,0x20,0x2E,0xFE,0xE4,0x67,0x0A,
    0x2F,0x2E,0xFE,0xE4,0x48,0x6E,0xFE,0xE8,0xA9,0x90,0x36,0x06,
    0x24,0x6E,0xFF,0xF4,0x34,0x83,0x35,0x47,0x00,0x02,0x35,0x6E,
    0xFE,0xDC,0x00,0x04,0x35,0x6E,0xFE,0xDE,0x00,0x06,0x35,0x6E,
    0xFE,0xE0,0x00,0x08,0x35,0x6E,0xFE,0xE2,0x00,0x0A,0x30,0x2E,
    0xFF,0xE8,0x48,0xC0,0x02,0x80,0x00,0x00,0x00,0x80,0x67,0x04,
    0x70,0x00,0x60,0x02,0x70,0x01,0xB6,0x6E,0xFF,0xF0,0x66,0x04,
    0x72,0x02,0x60,0x02,0x72,0x00,0x80,0x41,0x35,0x40,0x00,0x0C,
    0x70,0x00,0x10,0x2E,0xFE,0xE8,0x3D,0x40,0xFF,0xEE,0x72,0x1F,
    0xB0,0x41,0x6F,0x06,0x3D,0x7C,0x00,0x1F,0xFF,0xEE,0x20,0x6E,
    0xFF,0xF4,0x31,0x6E,0xFF,0xEE,0x00,0x0E,0x42,0x6E,0xFF,0xEC,
    0x36,0x2E,0xFF,0xEC,0xB6,0x6E,0xFF,0xEE,0x6C,0x28,0x38,0x03,
    0x48,0xC4,0x2D,0x44,0xFF,0xFC,0x52,0x84,0x41,0xEE,0xFE,0xE8,
    0x10,0x30,0x48,0x00,0x22,0x2E,0xFF,0xFC,0x74,0x10,0xD2,0x82,
    0x20,0x41,0xD1,0xEE,0xFF,0xF4,0x10,0x80,0x52,0x6E,0xFF,0xEC,
    0x60,0xCE,0x70,0x30,0xD1,0xAE,0xFF,0xF4,0x52,0x6E,0xFF,0xEA,
    0x52,0x46,0x60,0x00,0xFE,0xC8,0x24,0x4B,0x35,0x6E,0xFF,0xEA,
    0x00,0x12,0x35,0x7C,0x00,0x01,0x00,0x0E,0x2F,0x2E,0xFE,0xC8,
    0xA8,0x73,0x4C,0xDF,0x1C,0xF8,0x4E,0x5E,0x4E,0x75,0x87,0x44,
    0x6C,0x67,0x57,0x61,0x6C,0x6B,0x00,0x00
};

/* Scan the system heap for the jgne block by signature (word0=$6000 BRA.W, word@+4='JG').
 * Unlike FindDlgPatch there is no boot resource to confuse it with -- the only 'JG' block
 * in the heap is one this daemon installed at runtime. */
/* Track the jgne block by a daemon GLOBAL, NOT a heap signature scan. The block
 * lives only within one daemon boot: a reboot clears both $029A and the sysheap,
 * and activating a swapped daemon REQUIRES a reboot -- so there is never an existing
 * block to adopt across instances (unlike boot-resident dlgpatch). A word0=$6000 +
 * word@+4='JG' scan is far too weak: BRA.W is a common opcode, and on a fresh boot it
 * matched the kJGTemplate CONSTANT itself -- which DLGWALK would then wire live with a
 * NULL jDPBlock and crash. The global resets on the daemon restart a reboot forces,
 * exactly when the block also vanishes; the magic recheck guards a stale pointer. */
static Ptr gJGBlk = 0L;

static Ptr FindJGProbe(void)
{
    if (gJGBlk != NULL
        && *(unsigned short *)((char *)gJGBlk + oJG_Magic) == (unsigned short)kJGMagic)
        return gJGBlk;
    return 0L;
}

static Ptr InstallJGProbe(void)        /* idempotent within a daemon lifetime */
{
    Ptr s = FindJGProbe();
    if (s != NULL) return s;
    s = NewPtrSys((Size)kJG_Size);
    if (s == NULL) return NULL;
    BlockMove((Ptr)kJGTemplate, s, (Size)kJG_Size);
    *(short *)((char *)s + oJG_jArmed) = 0;      /* DISARMED until wired + armed */
    gJGBlk = s;
    return s;
}

/* MENUWALK MB block — MenuWalk (in the rebuilt jGNE resource) writes here; MENUTREE
 * reads it. Unlike the DP block (a boot resource found by FindDlgPatch), the MB block
 * is daemon-allocated and tracked by a global. Magic 'MB' at +4 so the jGNE stub's
 * Walk() dispatcher routes to MenuWalk. Offsets +14/+16 ARE the stub's oDP_Up/oDP_Gen.
 * Layout MUST match menuwalk.c (test_dlgpatch_contract guards it). */
#define kMBMagic       0x4D42L
#define oMB_Magic      4
#define oMB_Up         14      /* = oDP_Up  : MenuWalk sets 1 on capture (stub reads) */
#define oMB_Gen        16      /* = oDP_Gen : stub bumps once per fresh capture */
#define oMB_MenuCount  18
#define oMB_Trunc      20
#define oMB_ItemCount  22
#define oMB_MBarH      24
#define oMB_Menus      30
#define kMB_MENU_REC   40
#define kMB_MAX_MENUS  16
#define oMB_Items      (oMB_Menus + kMB_MAX_MENUS * kMB_MENU_REC)   /* 670 */
#define kMB_ITEM_REC   32
#define kMB_MAX_ITEMS  128
#define kMB_Size       (oMB_Items + kMB_MAX_ITEMS * kMB_ITEM_REC)   /* 4766 */

/* ITEM_REC flags (canonical, three-way contract with menuwalk.c + this MENUTREE
 * emitter + docs/MENUWALK_DESIGN.md): bit0 enabled (0 when bit4), bit1 separator,
 * bit2 text-truncated, bit3 RESERVED, bit4 enabled-UNKNOWN -> emit enabled:null. */
#define kMBI_Enabled   1
#define kMBI_Separator 2
#define kMBI_TextTrunc 4
#define kMBI_EnUnknown 16
/* MENU_REC flags: bit0 menu-enabled, bit1 item-points-valid (menuHeight matched). */
#define kMBM_Enabled   1
#define kMBM_PtsValid  2

static Ptr gMBBlk = 0L;

static Ptr FindMBBlk(void)
{
    if (gMBBlk != NULL
        && *(unsigned short *)((char *)gMBBlk + oMB_Magic) == (unsigned short)kMBMagic)
        return gMBBlk;
    return 0L;
}

static Ptr InstallMBBlk(void)          /* idempotent within a daemon lifetime */
{
    Ptr  s = FindMBBlk();
    long i;
    if (s != NULL) return s;
    s = NewPtrSys((Size)kMB_Size);
    if (s == NULL) return NULL;
    for (i = 0; i < kMB_Size; i++) ((char *)s)[i] = 0;
    *(unsigned short *)((char *)s + oMB_Magic) = (unsigned short)kMBMagic;   /* 'MB' */
    gMBBlk = s;
    return s;
}

Boolean ProcessRequest(ABConn *conn, char *request, long requestLen)
{
    char responseBuffer[RESP_SCRATCH];   /* small: fixed verb/error strings only (was 64 KB on the stack) */
    char command[MAX_COMMAND_LENGTH];
    long commandLength;
    BridgeResult result;
    CommandResult cmdResult;
    OSStatus err;

    /* Capture the just-finished command's latency (its RX->TX) before this new RX
     * overwrites gLastRX, so `lat` is right even with the monitor closed. Only for
     * REAL commands: heartbeats (PING/STAT) fire every few seconds with ~0-tick
     * round-trips and would otherwise clobber the last real command's figure.
     * (gLastTX < gLastRX = a command mid-flight, so the last value holds.) */
    if (gLastWasReal && gLastTX >= gLastRX) gLastLat = gLastTX - gLastRX;

    /* Mark RX activity */
    gLastRX = TickCount();
    gRXCount++;
    gLastWasReal = (strncmp(request, "PING", 4) != 0 &&
                    strncmp(request, PROTO_STAT, strlen(PROTO_STAT)) != 0);
    if (gMenuLED) *gMenuLED = gLastRX;   /* flash the menu-bar LED (if installed) */
    DrawLEDs();   /* light RX immediately */

    request[requestLen] = '\0';

    /* --- Verbose visibility: one choke point ----------------------------
     * Echo EVERY incoming verb to the console BODY, so the whole Surface-B
     * surface (input injection, AE, clipboard, file I/O, listdir, launch,
     * reboot, ...) leaves a persistent trace -- not just the ephemeral status
     * bar, which only ever shows the LAST verb. Two carve-outs:
     *   - COMMAND (MPW/ToolServer) has its own richer "> cmd" + per-line
     *     output logging further down, so skip it here (avoid a double line).
     *   - Heartbeats (PING / STAT) fire constantly, so log them as kind 1
     *     (detail), which the console already hides unless gShowDetails is on
     *     -- full coverage, no flood. Everything else is kind 2 (bold verb),
     *     matching COMMAND's bold-input style. Logs the first request line
     *     only (the "VERB:args" header), so payloads stay out of the log. */
    if (strncmp(request, PROTO_COMMAND, strlen(PROTO_COMMAND)) != 0) {
        char  vt[LOG_W];
        short vk;
        /* AFPMOUNT's 5th field is a password. The console keeps a scrollback,
         * so a logged password outlives the call — keep zone/server/volume and
         * mask everything from the user field on. */
        short mask = (strncmp(request, PROTO_AFPMOUNT,
                              strlen(PROTO_AFPMOUNT)) == 0) ? 3 : -1;
        short colons = 0;
        for (vk = 0; vk < LOG_W - 5 && request[vk] &&
                     request[vk] != '\r' && request[vk] != '\n'; vk++) {
            if (mask >= 0 && request[vk] == ':' && colons++ == mask) break;
            vt[vk] = request[vk];
        }
        vt[vk] = '\0';
        if (mask >= 0 && colons > mask) strcat(vt, ":***");
        if (strncmp(request, "PING", 4) == 0 ||
            strncmp(request, PROTO_STAT, strlen(PROTO_STAT)) == 0)
            AddLogLine(vt, 1);   /* heartbeat -> detail (hidden when collapsed) */
        else
            AddLogLine(vt, 2);   /* verb -> bold, like COMMAND's "> ..." */
    }

    /* --- Protocol v0.2: version negotiation + auth handshake -------------
     * HELLO/AUTH2 are handled first and always pass the auth gate below (as
     * does PING, the heartbeat). A v0.1 host never sends HELLO, so a v0.2
     * daemon facing an old host never sets gNeedAuth and behaves as legacy.
     * See docs/PROTOCOL_v0.2.md. */
    if (strncmp(request, PROTO_HELLO, strlen(PROTO_HELLO)) == 0) {
        char frame[256], body[160];
        char *bp = body, *fp = frame;
        long p = (long)strlen(PROTO_HELLO);
        char hostNonce[64];
        short hn = 0;

        SetActivity("HELLO");
        /* HELLO:<ver>:<hostNonceHex> — skip the version digits, take the nonce. */
        while (request[p] >= '0' && request[p] <= '9') p++;
        if (request[p] == ':') p++;
        while (request[p] && request[p] != '\r' && request[p] != '\n' && hn < 63)
            hostNonce[hn++] = request[p++];
        hostNonce[hn] = '\0';

        /* Auth engages only when the host supplied a nonce AND we hold a token
         * (the opt-in "both sides" rule); else this is a version-only HELLO. */
        gNeedAuth = (hn > 0 && gPrefs.token[0] != '\0');
        gAuthed = !gNeedAuth;
        gDaemonNonceHex[0] = '\0';

        bp = StatStr(bp, "ABVERSION:");
        bp = StatDec(bp, AB_PROTOCOL_VERSION);
        if (gNeedAuth) {
            char proof[17];
            ABMakeNonce(gDaemonNonceHex);   /* our nonce; host proves over it via AUTH2 */
            ABDigestHex((const unsigned char *)hostNonce, (long)hn,
                        gPrefs.token, (long)strlen(gPrefs.token), proof);
            bp = StatStr(bp, ";FEAT=auth;NONCE=");
            bp = StatStr(bp, gDaemonNonceHex);
            bp = StatStr(bp, ";PROOF=");
            bp = StatStr(bp, proof);
        } else {
            bp = StatStr(bp, ";FEAT=;NONCE=;PROOF=");
        }
        *bp = '\0';

        fp = StatStr(fp, "STATUS:0\rSTDOUT:");
        fp = StatDec(fp, (long)(bp - body));
        *fp++ = '\r';
        fp = StatStr(fp, body);
        fp = StatStr(fp, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(fp - frame));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* AUTH2:<hostProofHex> — the host proves it knows the token over OUR nonce. */
    if (strncmp(request, PROTO_AUTH2, strlen(PROTO_AUTH2)) == 0) {
        long p = (long)strlen(PROTO_AUTH2);
        char hostProof[64], want[17];
        short hp = 0;

        SetActivity("AUTH2");
        while (request[p] && request[p] != '\r' && request[p] != '\n' && hp < 63)
            hostProof[hp++] = request[p++];
        hostProof[hp] = '\0';

        ABDigestHex((const unsigned char *)gDaemonNonceHex,
                    (long)strlen(gDaemonNonceHex),
                    gPrefs.token, (long)strlen(gPrefs.token), want);
        if (gNeedAuth && gDaemonNonceHex[0] != '\0' && strcmp(hostProof, want) == 0) {
            gAuthed = true;
            strcpy(responseBuffer, "STATUS:0\rSTDOUT:6\rAuthOK\rSTDERR:0\r\r");
        } else {
            gAuthed = false;
            strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:11\rAuth failed\r\r"); NoteErr("auth");
        }
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* Auth gate: once a session needs auth, refuse every request until AUTH2
     * succeeds — except PING (heartbeat) and the handshake verbs above, which
     * have already returned. Keeps an unauthenticated peer from driving the Mac
     * or reading files while the link stays alive for the handshake to finish. */
    if (gNeedAuth && !gAuthed && strncmp(request, "PING", 4) != 0) {
        strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:17\rNot authenticated\r\r"); NoteErr("noauth");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* Check if it's a screenshot request */
    if (strncmp(request, PROTO_SCREENSHOT, strlen(PROTO_SCREENSHOT)) == 0) {
        ScreenshotData screenshot;
        Boolean ok = true;

        SetActivity("SCREENSHOT");          /* daemon activity -> top bar */

        result = CaptureScreenshot(&screenshot);
        if (result == kBridgeNoErr) {
            /* Stream the full pixmap (header + CLUT + pixels) — no size cap;
               SendData chunks it over OTSnd. The host decodes it to PNG. A
               partial send here desyncs the wire, so report it as unhealthy. */
            if (SendScreenshot(conn, &screenshot) != kBridgeNoErr) ok = false;
            CleanupScreenshot(&screenshot);
        } else {
            strcpy(responseBuffer, "STATUS:-1\nSTDOUT:0\n\nSTDERR:18\nScreenshot failed\n\n"); NoteErr("screenshot");
            if (ABSend(conn, responseBuffer, strlen(responseBuffer)) != noErr) ok = false;
        }

        /* Mark TX activity */
        gLastTX = TickCount();
        gTXCount++;

        return ok;
    }

    /* MACUITREE: dump the live window/dialog UI tree as JSON. READ-ONLY --
     * walks the Window Manager list + the front dialog's DITL through the
     * Toolbox, so every rect is ground truth (no VLM guessing at coordinates).
     * No ToolServer, no journal / MenuSelect -- safe even under a modal. */
    if (strncmp(request, PROTO_MACUITREE, strlen(PROTO_MACUITREE)) == 0) {
        Handle        jh;
        CommandResult res;
        WindowPtr     w, fw;
        WindowPeek    wp;
        Str255        title;
        Rect          gr;
        short         widx, kind;
        Boolean       isDlg;
        SetActivity("MACUITREE");

        jh = NewHandle(0);
        if (jh == NULL) {
            strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:9\rno memory\r\r");
            ABSend(conn, responseBuffer, strlen(responseBuffer));
            NoteErr("macuitree");
            gLastTX = TickCount();
            return true;
        }

        JStr(jh, "{\"windows\":[");
        widx = 0;
        for (w = FrontWindow(); w != NULL;
             w = (WindowPtr)((WindowPeek)w)->nextWindow) {
            wp = (WindowPeek)w;
            if (!wp->visible) continue;
            kind  = wp->windowKind;
            isDlg = (kind == dialogKind);
            GetWTitle(w, title);
            if (wp->contRgn != NULL && *wp->contRgn != NULL)
                gr = (**(wp->contRgn)).rgnBBox;          /* already GLOBAL */
            else
                gr = w->portRect;
            if (widx > 0) JStr(jh, ",");
            JStr(jh, "{\"index\":");    JNum(jh, (long)widx);
            JStr(jh, ",\"title\":\"");  JPStr(jh, title); JStr(jh, "\"");
            JStr(jh, ",\"kind\":");     JNum(jh, (long)kind);
            JStr(jh, ",\"dialog\":");   JStr(jh, isDlg ? "true" : "false");
            JStr(jh, ",\"rect\":[");    JNum(jh, (long)gr.top);    JStr(jh, ",");
                                        JNum(jh, (long)gr.left);   JStr(jh, ",");
                                        JNum(jh, (long)gr.bottom); JStr(jh, ",");
                                        JNum(jh, (long)gr.right);  JStr(jh, "]}");
            widx++;
        }
        JStr(jh, "]");

        fw = FrontWindow();
        if (fw != NULL && ((WindowPeek)fw)->windowKind == dialogKind) {
            DialogPtr dlg = (DialogPtr)fw;
            GrafPtr   savePort;
            short     i, nItems, itype, btype;
            Handle    ih;
            Rect      ir;
            Point     tl, br, ctr;
            Str255    itx;
            Boolean   disabled;

            GetPort(&savePort);
            SetPort((GrafPtr)dlg);
            {   /* item count from the DITL handle's first word (= count-1);
                 * same technique the dlgpatch walk uses so CountDITL — and its
                 * Interface.o glue — is avoided. DLGTEST validates it here. */
                Handle itemList = ((DialogPeek)dlg)->items;
                nItems = (itemList != NULL && *itemList != NULL)
                             ? (short)(*(short *)(*itemList) + 1) : 0;
            }
            if (nItems > 200) nItems = 200;
            GetWTitle(fw, title);
            JStr(jh, ",\"front\":{\"title\":\""); JPStr(jh, title);
            JStr(jh, "\",\"items\":[");
            for (i = 1; i <= nItems; i++) {
                GetDialogItem(dlg, i, &itype, &ih, &ir);
                btype    = itype & 0x7F;
                disabled = (itype & 0x80) != 0;          /* itemDisable bit */
                tl.h = ir.left;  tl.v = ir.top;    LocalToGlobal(&tl);
                br.h = ir.right; br.v = ir.bottom; LocalToGlobal(&br);
                ctr.h = (short)((ir.left + ir.right) / 2);
                ctr.v = (short)((ir.top + ir.bottom) / 2);
                LocalToGlobal(&ctr);
                itx[0] = 0;
                if (btype >= (ctrlItem + btnCtrl) && btype <= (ctrlItem + resCtrl)) {
                    if (ih != NULL) GetControlTitle((ControlHandle)ih, itx);
                } else if (btype == statText || btype == editText) {
                    if (ih != NULL) GetDialogItemText(ih, itx);
                }
                if (i > 1) JStr(jh, ",");
                JStr(jh, "{\"index\":");   JNum(jh, (long)i);
                JStr(jh, ",\"type\":");    JNum(jh, (long)btype);
                JStr(jh, ",\"enabled\":"); JStr(jh, disabled ? "false" : "true");
                if (i == ((DialogPeek)dlg)->aDefItem) JStr(jh, ",\"default\":true");
                JStr(jh, ",\"rect\":[");   JNum(jh, (long)tl.v); JStr(jh, ",");
                                           JNum(jh, (long)tl.h); JStr(jh, ",");
                                           JNum(jh, (long)br.v); JStr(jh, ",");
                                           JNum(jh, (long)br.h); JStr(jh, "]");
                JStr(jh, ",\"center\":["); JNum(jh, (long)ctr.h); JStr(jh, ",");
                                           JNum(jh, (long)ctr.v); JStr(jh, "]");
                JStr(jh, ",\"text\":\"");  JPStr(jh, itx); JStr(jh, "\"}");
            }
            JStr(jh, "]}");
            SetPort(savePort);
        }
        JStr(jh, "}");

        res.exitCode   = 0;
        res.outData    = jh;
        res.outLen     = GetHandleSize(jh);
        res.errData[0] = '\0';
        SendCommandResult(conn, &res);
        DisposeHandle(jh);
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* DLGTEST: put up an in-process DITL modal (DLOG 5000: OK/Cancel/text) so the
     * mac_ui_tree/DLGTREE DITL walk can be validated in the daemon's OWN context
     * before the cross-app trap patch. DLGOFF disposes it. Read-only otherwise. */
    if (strncmp(request, "DLGTEST", 7) == 0) {
        const char *stx;
        short L;
        char *f = responseBuffer;
        SetActivity("DLGTEST");
        if (gTestDlg == NULL) {
            gTestDlg = GetNewDialog(5000, NULL, (WindowPtr)-1L);
            if (gTestDlg != NULL) {
                SetPort((GrafPtr)gTestDlg);
                DrawDialog(gTestDlg);
            }
        }
        stx = (gTestDlg != NULL) ? "dialog up" : "GetNewDialog(5000) failed";
        L = (short)strlen(stx);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, stx); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }
    if (strncmp(request, "DLGOFF", 6) == 0) {
        SetActivity("DLGOFF");
        if (gTestDlg != NULL) { DisposeDialog(gTestDlg); gTestDlg = NULL; }
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:3\rOff\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* DLGINSTALL: adopt the boot INIT's global dlgpatch block if present, else
     * install a copy process-locally — Get1Resource('DPAT',128) from our own
     * fork, NewPtrSys+BlockMove into the system heap, chain the real
     * _ModalDialog into +oReal, and set the trap to our block. (Mirror MSINSTALL.) */
    if (strncmp(request, "DLGINSTALL", 10) == 0) {
        Ptr blk; Handle h; Size sz; unsigned long real;
        const char *msg; short L; char *f = responseBuffer;
        SetActivity("DLGINSTALL");
        blk = FindDlgPatch();
        if (blk == NULL) {
            h = Get1Resource('DPAT', 128);
            if (h != NULL) {
                HNoPurge(h);
                LoadResource(h);
                sz  = GetHandleSize(h);
                blk = NewPtrSys(sz);
                if (blk != NULL) {
                    BlockMove(*h, blk, sz);
                    if (*(unsigned short *)((char *)blk + 4) == 0x4450) {
                        real = (unsigned long)NGetTrapAddress(_ModalDialog, ToolTrap);
                        *(unsigned long *)((char *)blk + oDP_Real) = real;
                        NSetTrapAddress((UniversalProcPtr)blk, _ModalDialog, ToolTrap);
                    } else {
                        blk = NULL;
                    }
                }
            }
        }
        msg = (blk != NULL) ? "installed" : "install failed";
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* DLGTREE: read the dlgpatch block (populated by the patch in the FRONT app's
     * context) and format it as JSON — the cross-app dialog tree with exact rects. */
    if (strncmp(request, "DLGTREE", 7) == 0) {
        Ptr blk; Handle jh; CommandResult res;
        short up, gen, cnt, trunc, i;
        SetActivity("DLGTREE");
        blk = FindDlgPatch();
        jh = NewHandle(0);
        if (jh == NULL) {
            strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:9\rno memory\r\r");
            ABSend(conn, responseBuffer, strlen(responseBuffer));
            NoteErr("dlgtree"); gLastTX = TickCount();
            return true;
        }
        if (blk == NULL) {
            JStr(jh, "{\"installed\":false}");
        } else {
            short *dr = (short *)((char *)blk + oDP_Rect);
            up    = *(short *)((char *)blk + oDP_Up);
            gen   = *(short *)((char *)blk + oDP_Gen);
            cnt   = *(short *)((char *)blk + oDP_Cnt);
            trunc = *(short *)((char *)blk + oDP_Trunc);
            JStr(jh, "{\"installed\":true,\"armed\":");
            JStr(jh, (*(short *)((char *)blk + oDP_Armed)) ? "true" : "false");
            JStr(jh, ",\"close_patch\":");
            JStr(jh, FindClosePatch() ? "true" : "false");
            JStr(jh, ",\"dialog_up\":");
            JStr(jh, up ? "true" : "false");
            JStr(jh, ",\"generation\":"); JNum(jh, (long)gen);
            JStr(jh, ",\"truncated\":"); JStr(jh, trunc ? "true" : "false");
            JStr(jh, ",\"rect\":["); JNum(jh, (long)dr[0]); JStr(jh, ",");
            JNum(jh, (long)dr[1]); JStr(jh, ","); JNum(jh, (long)dr[2]); JStr(jh, ",");
            JNum(jh, (long)dr[3]); JStr(jh, "],\"items\":[");
            for (i = 0; i < cnt; i++) {
                unsigned char *rc = (unsigned char *)((char *)blk + oDP_Recs) + (long)i * DP_RECSIZE;
                short *rr = (short *)(rc + 4);
                short flags = *(short *)(rc + 12);
                short tl = *(short *)(rc + 14);
                short cx = (short)((rr[1] + rr[3]) / 2);
                short cy = (short)((rr[0] + rr[2]) / 2);
                Str255 t; short k;
                if (tl > 31) tl = 31;
                t[0] = (unsigned char)tl;
                for (k = 0; k < tl; k++) t[1 + k] = rc[16 + k];
                if (i > 0) JStr(jh, ",");
                JStr(jh, "{\"index\":");   JNum(jh, (long)*(short *)(rc + 0));
                JStr(jh, ",\"type\":");    JNum(jh, (long)*(short *)(rc + 2));
                JStr(jh, ",\"enabled\":"); JStr(jh, (flags & 1) ? "true" : "false");
                if (flags & 2) JStr(jh, ",\"default\":true");
                JStr(jh, ",\"rect\":[");   JNum(jh, (long)rr[0]); JStr(jh, ",");
                JNum(jh, (long)rr[1]); JStr(jh, ","); JNum(jh, (long)rr[2]); JStr(jh, ",");
                JNum(jh, (long)rr[3]); JStr(jh, "],\"center\":["); JNum(jh, (long)cx);
                JStr(jh, ","); JNum(jh, (long)cy); JStr(jh, "]");
                JStr(jh, ",\"text\":\""); JPStr(jh, t); JStr(jh, "\"}");
            }
            JStr(jh, "]}");
        }
        res.exitCode = 0; res.outData = jh; res.outLen = GetHandleSize(jh);
        res.errData[0] = '\0';
        SendCommandResult(conn, &res);
        DisposeHandle(jh);
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* DLGUNINSTALL: restore the real _ModalDialog IF our block is still the trap
     * head (can't safely unchain from the middle). Block left resident. */
    if (strncmp(request, "DLGUNINSTALL", 12) == 0) {
        unsigned long live; char *blk; const char *msg; short L; char *f = responseBuffer;
        SetActivity("DLGUNINSTALL");
        live = (unsigned long)NGetTrapAddress(_ModalDialog, ToolTrap);
        blk = (char *)live;
        if (*(unsigned short *)(blk + 4) == 0x4450) {
            unsigned long real = *(unsigned long *)(blk + oDP_Real);
            NSetTrapAddress((UniversalProcPtr)real, _ModalDialog, ToolTrap);
            msg = "uninstalled";
        } else {
            msg = "not-head";
        }
        {   /* P4: also unhook the close/dispose honesty patch if we are its head */
            Ptr s = FindClosePatch();
            if (s != NULL) {
                unsigned long dt = (unsigned long)NGetTrapAddress(kDisposDialogTrap, ToolTrap);
                NSetTrapAddress((UniversalProcPtr)*(unsigned long *)((char *)s + oCD_RealClose),
                                kCloseDialogTrap, ToolTrap);
                if (dt == (unsigned long)((char *)s + oCD_DispStub))
                    NSetTrapAddress((UniversalProcPtr)*(unsigned long *)((char *)s + oCD_RealDisp),
                                    kDisposDialogTrap, ToolTrap);
            }
        }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* DLGSELFMODAL: the DISCRIMINATOR (notes 18:13). Call a real ModalDialog via
     * the $A991 trap IN THE DAEMON'S OWN process (where a DLGINSTALL'd, app-local
     * patch, if it works at all, IS in force). g1>g0 => the patch head FIRES when
     * the trap dispatches — the BlockMove'd code runs — so cross-app generation:0
     * is REACH (app-installed patches are process-local, Route B; needs the boot
     * INIT), NOT a firing bug. g1==g0 => a firing bug the INIT could never fix.
     * The filter dismisses on the first pass, so this can't spin in the background. */
    if (strncmp(request, "DLGSELFMODAL", 12) == 0) {
        Ptr blk; Ptr trap; DialogPtr dp; short hit = 0, g0 = -1, g1 = -1;
        int hooked; char body[192]; char *b = body; short L; char *f = responseBuffer;
        SetActivity("DLGSELFMODAL");
        blk  = FindDlgPatch();
        trap = (Ptr)NGetTrapAddress(_ModalDialog, ToolTrap);
        hooked = (blk != NULL && trap == blk) ? 1 : 0;
        if (blk != NULL) {
            g0 = *(short *)((char *)blk + oDP_Gen);
            *(short *)((char *)blk + oDP_Armed) = 1;   /* arm — else the walk is a no-op */
        }
        dp = GetNewDialog(5000, NULL, (WindowPtr)-1L);
        if (dp != NULL) {
            SetPort((GrafPtr)dp);
            ModalDialog((ModalFilterUPP)DlgSelfFilter, &hit);  /* via $A991 -> patch if hooked here */
            DisposeDialog(dp);
        }
        blk = FindDlgPatch();
        if (blk != NULL) g1 = *(short *)((char *)blk + oDP_Gen);
        b = StatStr(b, "{\"hooked\":");  b = StatStr(b, hooked ? "true" : "false");
        b = StatStr(b, ",\"dialog\":"); b = StatStr(b, dp ? "true" : "false");
        b = StatStr(b, ",\"g0\":");     b = StatDec(b, (long)g0);
        b = StatStr(b, ",\"g1\":");     b = StatDec(b, (long)g1);
        b = StatStr(b, ",\"hit\":");    b = StatDec(b, (long)hit);
        b = StatStr(b, ",\"fired\":");  b = StatStr(b, (g1 > g0) ? "true" : "false");
        b = StatStr(b, "}");
        L = (short)(b - body);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r';
        { short k; for (k = 0; k < L; k++) *f++ = body[k]; }
        f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* DLGARM / DLGDISARM: set the dlgpatch Armed word (#182 finding 4). The patch
     * is a transparent JMP to the real trap until armed — zero cost in every app's
     * modals. The daemon arms just before it expects a dialog; OneShot makes the
     * patch walk ONE modal and self-disarm. This is mspatch.a's mechanism. */
    if (strncmp(request, "DLGARM", 6) == 0) {
        Ptr blk; const char *msg; short L; char *f = responseBuffer;
        SetActivity("DLGARM");
        { Ptr jg = FindJGProbe();          /* mutual exclusion: DLGWALK holds the DP block */
          if (jg != NULL && *(short *)((char *)jg + oJG_jArmed) != 0) {
              const char *m = "walk-armed"; short l = (short)strlen(m); char *g = responseBuffer;
              g = StatStr(g, "STATUS:0\rSTDOUT:"); g = StatDec(g, (long)l);
              *g++ = '\r'; g = StatStr(g, m); g = StatStr(g, "\rSTDERR:0\r\r");
              ABSend(conn, responseBuffer, (long)(g - responseBuffer));
              gLastTX = TickCount(); gTXCount++; return true;
          } }
        blk = FindDlgPatch();
        if (blk != NULL) {
            *(short *)((char *)blk + oDP_OneShot) = 1;   /* capture one, then disarm */
            *(short *)((char *)blk + oDP_Armed)   = 1;
            InstallDlgClosePatch(blk);                   /* P4: keep dialog_up honest */
            msg = "armed";
        } else {
            msg = "no-block";
        }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }
    if (strncmp(request, "DLGDISARM", 9) == 0) {
        Ptr blk; const char *msg; short L; char *f = responseBuffer;
        SetActivity("DLGDISARM");
        blk = FindDlgPatch();
        if (blk != NULL) { *(short *)((char *)blk + oDP_Armed) = 0; msg = "disarmed"; }
        else             { msg = "no-block"; }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* DLGWALK <targetA5> : arm the runtime jGNE one-shot walk against a STANDING
     * dialog owned by the process whose CurrentA5 == <targetA5> (the caller pins the
     * target in a quiet reference window, exactly like the jGNE-reach measurement).
     * MUTUALLY EXCLUSIVE with dlgpatch's entry walk: refused while DLGARM is armed
     * (and DLGARM is refused while this is armed) so the two writers of the DP block
     * never run at once -- the owner rule that replaced the shared 68k busy flag.
     * Installs+wires the jgne block if needed, fills jDPBlock/jTargetA5, resets jTries,
     * arms LAST (after wiring $029A). Disarm with DLGWDISARM; a reboot clears $029A.
     * The client disarms on a short timeout; jMaxTries is the in-block backstop. */
    if (strncmp(request, "DLGWALK", 7) == 0) {
        Ptr jg, dp; unsigned long targetA5 = 0L, old; const char *p; const char *msg;
        short L; char *f = responseBuffer;
        SetActivity("DLGWALK");
        for (p = request + 7; *p == ' '; p++) ;            /* skip to decimal targetA5 */
        while (*p >= '0' && *p <= '9') { targetA5 = targetA5 * 10 + (unsigned long)(*p - '0'); p++; }
        dp = FindDlgPatch();
        if (dp == NULL)                                     msg = "no-dpblock";
        else if (*(short *)((char *)dp + oDP_Armed) != 0)   msg = "entry-armed";
        else if (FindJGProbe() != NULL && *(short *)((char *)FindJGProbe() + oJG_jArmed) != 0)  msg = "walk-armed";
        else if (targetA5 == 0L)                            msg = "no-target";
        else {
            jg = InstallJGProbe();
            if (jg == NULL) msg = "no-mem";
            else {
                *(unsigned long *)((char *)jg + oJG_jTargetA5) = targetA5;
                *(unsigned long *)((char *)jg + oJG_jDPBlock)  = (unsigned long)dp;
                *(short *)((char *)jg + oJG_jOneShot)  = 1;
                *(short *)((char *)jg + oJG_jBusy)     = 0;
                *(short *)((char *)jg + oJG_jTries)    = 0;
                *(short *)((char *)jg + oJG_jMaxTries) = kJG_DefTries;
                old = *(unsigned long *)kJGNEFilter;         /* chain the old $029A */
                if (old != (unsigned long)jg)                /* not already us */
                    *(unsigned long *)((char *)jg + oJG_jReal) = old;   /* 0 -> stub RTSs */
                *(short *)((char *)jg + oJG_jArmed) = 1;      /* arm LAST, after wiring */
                *(unsigned long *)kJGNEFilter = (unsigned long)jg;      /* head = block+0 */
                msg = "walking";
            }
        }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }
    if (strncmp(request, "DLGWDISARM", 10) == 0) {
        Ptr jg; const char *msg; short L; char *f = responseBuffer;
        SetActivity("DLGWDISARM");
        jg = FindJGProbe();
        if (jg != NULL) { *(short *)((char *)jg + oJG_jArmed) = 0; msg = "disarmed"; }
        else            { msg = "no-block"; }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* MENUARM <targetA5> : arm the runtime jGNE walk against the FOREGROUND app's
     * MENU BAR (targetA5 pinned by the caller in a quiet reference window, as for
     * DLGWALK). Same shared jGNE probe as DLGWALK, but jDPBlock = the MB block, so
     * the stub's Walk() dispatcher runs MenuWalk. Mutually exclusive with dlgpatch's
     * entry walk (refused while DLGARM is armed) — the DP-block ⊥ rule, one level up.
     * The client disarms on a short timeout (MENUWDISARM); jMaxTries is the backstop. */
    if (strncmp(request, "MENUARM", 7) == 0) {
        Ptr jg, mb, dp; unsigned long targetA5 = 0L, old; const char *p; const char *msg;
        short L; char *f = responseBuffer;
        SetActivity("MENUARM");
        for (p = request + 7; *p == ' '; p++) ;
        while (*p >= '0' && *p <= '9') { targetA5 = targetA5 * 10 + (unsigned long)(*p - '0'); p++; }
        dp = FindDlgPatch();
        if (dp != NULL && *(short *)((char *)dp + oDP_Armed) != 0)  msg = "entry-armed";
        else if (FindJGProbe() != NULL && *(short *)((char *)FindJGProbe() + oJG_jArmed) != 0)  msg = "walk-armed";
        else if (targetA5 == 0L)                                    msg = "no-target";
        else {
            mb = InstallMBBlk();
            jg = InstallJGProbe();
            if (mb == NULL || jg == NULL) msg = "no-mem";
            else {
                *(unsigned long *)((char *)jg + oJG_jTargetA5) = targetA5;
                *(unsigned long *)((char *)jg + oJG_jDPBlock)  = (unsigned long)mb;  /* MB, not DP */
                *(short *)((char *)jg + oJG_jOneShot)  = 1;
                *(short *)((char *)jg + oJG_jBusy)     = 0;
                *(short *)((char *)jg + oJG_jTries)    = 0;
                *(short *)((char *)jg + oJG_jMaxTries) = kJG_DefTries;
                old = *(unsigned long *)kJGNEFilter;
                if (old != (unsigned long)jg)
                    *(unsigned long *)((char *)jg + oJG_jReal) = old;
                *(short *)((char *)jg + oJG_jArmed) = 1;       /* arm LAST, after wiring */
                *(unsigned long *)kJGNEFilter = (unsigned long)jg;
                msg = "walking";
            }
        }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* MENUTREE : read the MB block (populated by MenuWalk in the front app's context)
     * and emit JSON — menus[] each { id, title, title_x, width, enabled, points_valid,
     * title_point:[x,y], items[] { index, text, separator, enabled(true/false/null),
     * point:[x,y] when points_valid } }. enabled is null when bit4 (item index > 31,
     * beyond enableFlags) so a bit4-unaware client never reads "disabled". Same send
     * shape as DLGTREE. */
    if (strncmp(request, "MENUTREE", 8) == 0) {
        Ptr blk, jg; Handle jh; CommandResult res;
        short mc, ic, i, j, up, gen, trunc, mbarH;
        SetActivity("MENUTREE");
        blk = FindMBBlk();
        jh  = NewHandle(0);
        if (jh == NULL) {
            strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:9\rno memory\r\r");
            ABSend(conn, responseBuffer, strlen(responseBuffer));
            NoteErr("menutree"); gLastTX = TickCount();
            return true;
        }
        JStr(jh, "{");
        if (blk == NULL) {
            JStr(jh, "\"installed\":false,\"armed\":false,\"up\":false,\"generation\":0,\"menus\":[]");
        } else {
            up    = *(short *)((char *)blk + oMB_Up);
            gen   = *(short *)((char *)blk + oMB_Gen);
            mc    = *(short *)((char *)blk + oMB_MenuCount);
            ic    = *(short *)((char *)blk + oMB_ItemCount);
            trunc = *(short *)((char *)blk + oMB_Trunc);
            mbarH = *(short *)((char *)blk + oMB_MBarH);
            jg    = FindJGProbe();
            JStr(jh, "\"installed\":true,\"armed\":");
            JStr(jh, (jg != NULL && *(short *)((char *)jg + oJG_jArmed) != 0
                      && *(unsigned long *)((char *)jg + oJG_jDPBlock) == (unsigned long)blk)
                     ? "true" : "false");
            JStr(jh, ",\"up\":");          JStr(jh, up ? "true" : "false");
            JStr(jh, ",\"generation\":");  JNum(jh, (long)gen);
            JStr(jh, ",\"truncated\":");   JStr(jh, trunc ? "true" : "false");
            JStr(jh, ",\"mbar_height\":"); JNum(jh, (long)mbarH);
            JStr(jh, ",\"menus\":[");
            for (i = 0; i < mc; i++) {
                unsigned char *mrec = (unsigned char *)((char *)blk + oMB_Menus) + (long)i * kMB_MENU_REC;
                short menuID    = *(short *)(mrec + 0);
                short titleX    = *(short *)(mrec + 2);
                short titleW    = *(short *)(mrec + 4);
                short itemFirst = *(short *)(mrec + 8);
                short itemN     = *(short *)(mrec + 10);
                short mflags    = *(short *)(mrec + 12);
                short tl        = *(short *)(mrec + 14);
                short titleXc   = (short)(titleX + (titleW > 24 ? titleW / 2 : 12));
                Str255 title;   short k;
                if (tl > 24) tl = 24;
                title[0] = (unsigned char)tl;
                for (k = 0; k < tl; k++) title[1 + k] = mrec[16 + k];
                if (i > 0) JStr(jh, ",");
                JStr(jh, "{\"id\":");            JNum(jh, (long)menuID);
                JStr(jh, ",\"title\":\"");       JPStr(jh, title); JStr(jh, "\"");
                JStr(jh, ",\"title_x\":");       JNum(jh, (long)titleX);
                JStr(jh, ",\"width\":");         JNum(jh, (long)titleW);
                JStr(jh, ",\"enabled\":");       JStr(jh, (mflags & kMBM_Enabled) ? "true" : "false");
                JStr(jh, ",\"points_valid\":");  JStr(jh, (mflags & kMBM_PtsValid) ? "true" : "false");
                JStr(jh, ",\"title_point\":[");  JNum(jh, (long)titleXc); JStr(jh, ",");
                JNum(jh, (long)(mbarH / 2));     JStr(jh, "]");
                JStr(jh, ",\"items\":[");
                for (j = 0; j < itemN; j++) {
                    unsigned char *irec = (unsigned char *)((char *)blk + oMB_Items)
                                          + (long)(itemFirst + j) * kMB_ITEM_REC;
                    short iIdx   = *(short *)(irec + 2);
                    short iflags = *(short *)(irec + 4);
                    short itl    = *(short *)(irec + 6);
                    short iy     = (short)(mbarH + 16 * (iIdx - 1) + 8);
                    Str255 itxt; short kk;
                    if (itl > 24) itl = 24;
                    itxt[0] = (unsigned char)itl;
                    for (kk = 0; kk < itl; kk++) itxt[1 + kk] = irec[8 + kk];
                    if (j > 0) JStr(jh, ",");
                    JStr(jh, "{\"index\":");     JNum(jh, (long)iIdx);
                    JStr(jh, ",\"text\":\"");    JPStr(jh, itxt); JStr(jh, "\"");
                    JStr(jh, ",\"separator\":"); JStr(jh, (iflags & kMBI_Separator) ? "true" : "false");
                    if (iflags & kMBI_EnUnknown) JStr(jh, ",\"enabled\":null");
                    else { JStr(jh, ",\"enabled\":"); JStr(jh, (iflags & kMBI_Enabled) ? "true" : "false"); }
                    if (iflags & kMBI_TextTrunc) JStr(jh, ",\"text_truncated\":true");
                    if (mflags & kMBM_PtsValid) {
                        JStr(jh, ",\"point\":["); JNum(jh, (long)titleXc); JStr(jh, ",");
                        JNum(jh, (long)iy);       JStr(jh, "]");
                    }
                    JStr(jh, "}");
                }
                JStr(jh, "]}");
            }
            JStr(jh, "]");
        }
        JStr(jh, "}");
        res.exitCode = 0; res.outData = jh; res.outLen = GetHandleSize(jh);
        res.errData[0] = '\0';
        SendCommandResult(conn, &res);
        DisposeHandle(jh);
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* MENUWDISARM : disarm the shared jGNE probe (same block DLGWDISARM disarms —
     * one probe serves both walks). Provided under its own name for verb symmetry. */
    if (strncmp(request, "MENUWDISARM", 11) == 0) {
        Ptr jg; const char *msg; short L; char *f = responseBuffer;
        SetActivity("MENUWDISARM");
        jg = FindJGProbe();
        if (jg != NULL) { *(short *)((char *)jg + oJG_jArmed) = 0; msg = "disarmed"; }
        else            { msg = "no-block"; }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* CPINSTALL / CPARM / CPDISARM / CPREAD / CPUNINSTALL: the A970/A860 counter
     * probe. Ships DISARMED; arm only for a brief measurement window. CPREAD returns
     * the per-trap counts, the A5 of the last armed caller (WHO), and the daemon's
     * own A5 (self) so a foreground A5 can be told from the daemon's own pumping. */
    if (strncmp(request, "CPINSTALL", 9) == 0) {
        Ptr s; const char *msg; short L; char *f = responseBuffer;
        SetActivity("CPINSTALL");
        s = InstallCounterProbe();
        msg = (s != NULL) ? "installed" : "install failed";
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }
    if (strncmp(request, "CPUNINSTALL", 11) == 0) {
        Ptr s; const char *msg; short L; char *f = responseBuffer;
        SetActivity("CPUNINSTALL");
        s = FindCounterProbe();
        if (s != NULL) {
            unsigned long wt = (unsigned long)NGetTrapAddress(kWNETrap, ToolTrap);
            NSetTrapAddress((UniversalProcPtr)*(unsigned long *)((char *)s + oCP_RealGNE),
                            kGNETrap, ToolTrap);      /* GNE head is ours (FindCounterProbe) */
            if (wt == (unsigned long)((char *)s + oCP_WNEStub))
                NSetTrapAddress((UniversalProcPtr)*(unsigned long *)((char *)s + oCP_RealWNE),
                                kWNETrap, ToolTrap);
            msg = "uninstalled";
        } else { msg = "no-block"; }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }
    if (strncmp(request, "CPDISARM", 8) == 0) {
        Ptr s; const char *msg; short L; char *f = responseBuffer;
        SetActivity("CPDISARM");
        s = FindCounterProbe();
        if (s != NULL) { *(short *)((char *)s + oCP_Armed) = 0; msg = "disarmed"; }
        else           { msg = "no-block"; }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }
    if (strncmp(request, "CPARM", 5) == 0) {
        Ptr s; const char *msg; short L; char *f = responseBuffer;
        SetActivity("CPARM");
        s = FindCounterProbe();
        if (s != NULL) {
            *(long *)((char *)s + oCP_CntGNE) = 0L;   /* fresh window */
            *(long *)((char *)s + oCP_CntWNE) = 0L;
            *(long *)((char *)s + oCP_LastA5) = 0L;
            *(long *)((char *)s + oCP_PrevA5) = 0L;   /* both slots, or a stale PrevA5
                                                       * from the last window would be
                                                       * read as evidence from this one */
            *(long *)((char *)s + oCP_OtherCnt) = 0L;
            *(long *)((char *)s + oCP_jCnt) = 0L;
            *(short *)((char *)s + oCP_Armed) = 1;
            msg = "armed";
        } else { msg = "no-block"; }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }
    if (strncmp(request, "CPREAD", 6) == 0) {
        /* 224, not 160: with "prev" the worst case (every long printed signed, 11 chars)
         * reaches 164 and would have run off a 160-byte stack buffer. Counted, not
         * guessed — the field was added and the buffer was NOT, at first. */
        Ptr s; char body[224]; char *b = body; short L; char *f = responseBuffer;
        SetActivity("CPREAD");
        s = FindCounterProbe();
        if (s == NULL) {
            b = StatStr(b, "{\"installed\":false}");
        } else {
            b = StatStr(b, "{\"installed\":true,\"armed\":");
            b = StatStr(b, (*(short *)((char *)s + oCP_Armed)) ? "true" : "false");
            b = StatStr(b, ",\"gne\":");  b = StatDec(b, *(long *)((char *)s + oCP_CntGNE));
            b = StatStr(b, ",\"wne\":");  b = StatDec(b, *(long *)((char *)s + oCP_CntWNE));
            b = StatStr(b, ",\"last\":");  b = StatDec(b, *(long *)((char *)s + oCP_LastA5));
            /* The second slot. "Did the target enter this trap in the window?" is
             * answered by last OR prev — a fast poller can hold only one of them. */
            b = StatStr(b, ",\"prev\":");  b = StatDec(b, *(long *)((char *)s + oCP_PrevA5));
            b = StatStr(b, ",\"other\":"); b = StatDec(b, *(long *)((char *)s + oCP_OtherCnt));
            b = StatStr(b, ",\"jcnt\":");  b = StatDec(b, *(long *)((char *)s + oCP_jCnt));
            b = StatStr(b, ",\"self\":");  b = StatDec(b, *(long *)kCurrentA5);
            b = StatStr(b, "}");
        }
        L = (short)(b - body);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r';
        { short k; for (k = 0; k < L; k++) *f++ = body[k]; }
        f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* CPJINSTALL / CPJUNINSTALL: hook the jGNE filter (0x29A) at the SAME counter block,
     * to test whether 0x29A is a per-process-swapped low-mem global (then a runtime hook is
     * daemon-local, like the trap patch) or truly global (then runtime jGNE reaches the
     * foreground). Runtime only, nothing persisted -> a reboot clears 0x29A + the sysheap. */
    if (strncmp(request, "CPJINSTALL", 10) == 0) {
        Ptr s; unsigned long old; const char *msg; short L; char *f = responseBuffer;
        SetActivity("CPJINSTALL");
        s = FindCounterProbe();
        if (s != NULL) {
            old = *(unsigned long *)kJGNEFilter;                     /* existing filter, or 0 */
            *(unsigned long *)((char *)s + oCP_jReal) =
                (old != 0L) ? old : (unsigned long)((char *)s + oCP_jRTS);  /* never 0: chain is safe */
            *(long *)((char *)s + oCP_jCnt) = 0L;
            *(unsigned long *)kJGNEFilter = (unsigned long)((char *)s + oCP_jStub);
            msg = "jhooked";
        } else { msg = "no-block"; }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }
    if (strncmp(request, "CPJUNINSTALL", 12) == 0) {
        Ptr s; unsigned long jr; const char *msg; short L; char *f = responseBuffer;
        SetActivity("CPJUNINSTALL");
        s = FindCounterProbe();
        if (s != NULL && *(unsigned long *)kJGNEFilter == (unsigned long)((char *)s + oCP_jStub)) {
            jr = *(unsigned long *)((char *)s + oCP_jReal);          /* restore the TRUE original: */
            *(unsigned long *)kJGNEFilter =
                (jr == (unsigned long)((char *)s + oCP_jRTS)) ? 0L : jr;  /* 0 if there was none */
            msg = "junhooked";
        } else if (s != NULL) { msg = "not-head"; }
        else { msg = "no-block"; }
        L = (short)strlen(msg);
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)L);
        *f++ = '\r'; f = StatStr(f, msg); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, (long)(f - responseBuffer));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* PING verb: lightweight heartbeat (sent raw, not COMMAND-wrapped) */
    if (strncmp(request, "PING", 4) == 0) {
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:4\rPONG\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* QUITDAEMON verb: stop the faceless daemon over the bridge. With no window
     * or menu, this (or a system kAEQuitApplication) is how the service is
     * stopped. Ack first, then signal the main loop to exit. */
    if (strncmp(request, "QUITDAEMON", 10) == 0) {
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:7\rStopped\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        gRunning = false;   /* while(gRunning) loop exits after this request */
        return true;
    }

    /* REBOOT verb: clean System 7 restart (re-activates a freshly swapped
     * daemon). Ack first, then trigger the restart; the connection then drops
     * and the watchdog brings the new daemon up after the reboot. */
    if (strncmp(request, "REBOOT", 6) == 0) {
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:6\rReboot\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        SetActivity("REBOOT");
        RebootMac();
        return true;
    }

    /* SHUTDOWN verb: clean System 7 power-off via the Shutdown Manager. This is the
     * SAFE way to stop the guest / let Basilisk II quit — a hard process kill risks an
     * unclean HFS unmount and a corrupted disk image. Ack first, then power off; the
     * connection drops and, unlike REBOOT, the daemon does NOT come back (machine off). */
    if (strncmp(request, PROTO_SHUTDOWN, strlen(PROTO_SHUTDOWN)) == 0 &&
        (request[8] == '\0' || request[8] == '\r' || request[8] == '\n')) {
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:8\rShutdown\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        SetActivity("SHUTDOWN");
        ShutdownMac();
        return true;
    }

    /* SWAPSELF verb: self-update. The host first stages the new binary next to
     * the daemon as "<name> new" (mac_put_file); this renames the running daemon
     * aside and renames the staged binary into its place (renaming an open file
     * is allowed, unlike overwriting it). The caller then REBOOTs so the watchdog
     * launches the now-current binary. The File Manager error code is reported on
     * failure so the host can tell "no staged binary" (fnfErr -43) from a
     * rename the OS refused. */
    if (strncmp(request, PROTO_SWAPSELF, strlen(PROTO_SWAPSELF)) == 0 &&
        (request[8] == '\0' || request[8] == '\r' || request[8] == '\n')) {
        OSErr se;
        SetActivity("SWAPSELF");
        se = SwapSelf();
        if (se == noErr) {
            strcpy(responseBuffer, "STATUS:0\rSTDOUT:7\rSwapped\rSTDERR:0\r\r");
        } else {
            char msg[48];
            char *m = msg;
            char *p = responseBuffer;
            NoteErr("swap");
            m = StatStr(m, "swap err ");
            m = StatDec(m, (long)se);
            *m = '\0';
            p = StatStr(p, "STATUS:-1\rSTDOUT:0\rSTDERR:");
            p = StatDec(p, (long)(m - msg));
            *p++ = '\r';
            p = StatStr(p, msg);
            p = StatStr(p, "\r\r");
            *p = '\0';
        }
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* LAUNCH:<MacPath> verb: bring a GUI app to the foreground */
    if (strncmp(request, "LAUNCH:", 7) == 0) {
        char launchPath[MAX_COMMAND_LENGTH];
        OSErr lerr;
        short n;

        for (n = 0; request[7 + n] && request[7 + n] != '\r' &&
                    request[7 + n] != '\n' && n < MAX_COMMAND_LENGTH - 1; n++) {
            launchPath[n] = request[7 + n];
        }
        launchPath[n] = '\0';

        {
            char m[80]; short k;
            strcpy(m, "LAUNCH ");
            for (k = 0; launchPath[k] && k < 64; k++) m[7 + k] = launchPath[k];
            m[7 + k] = '\0';
            SetActivity(m);                 /* daemon activity -> top bar */
        }
        lerr = LaunchAppAtPath(launchPath);
        if (lerr == noErr) {
            strcpy(responseBuffer, "STATUS:0\rSTDOUT:8\rLaunched\rSTDERR:0\r\r");
        } else {
            strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:13\rLaunch failed\r\r"); NoteErr("launch");
            SetActivity("LAUNCH failed");
        }
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* QUIT:<4-char creator> verb: send a quit Apple Event to a running app so
     * the host can stop a launched build over the bridge (no manual quit). */
    if (strncmp(request, "QUIT:", 5) == 0) {
        OSType sig = 0;
        OSErr qerr;
        short i;
        char m[40];
        for (i = 0; i < 4 && request[5 + i] &&
                    request[5 + i] != '\r' && request[5 + i] != '\n'; i++) {
            sig = (sig << 8) | (unsigned char)request[5 + i];
        }
        while (i < 4) { sig = (sig << 8) | ' '; i++; }
        strcpy(m, "QUIT ");
        m[5] = (char)((sig >> 24) & 0xFF); m[6] = (char)((sig >> 16) & 0xFF);
        m[7] = (char)((sig >> 8) & 0xFF);  m[8] = (char)(sig & 0xFF);
        m[9] = '\0';
        SetActivity(m);                     /* daemon activity -> top bar */
        qerr = QuitAppBySignature(sig);
        if (qerr == noErr) {
            strcpy(responseBuffer, "STATUS:0\rSTDOUT:7\rQuit OK\rSTDERR:0\r\r");
        } else {
            strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:11\rQuit failed\r\r"); NoteErr("quit");
            SetActivity("QUIT no such app");
        }
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* KEY:<charCode>:<keyCode>[:<modifiers>] verb: inject one keystroke into the
     * front app. The optional 3rd field is the Event Manager modifier mask
     * (cmdKey 256, shiftKey 512, optionKey 2048, controlKey 4096); it defaults to
     * 0, so legacy KEY:<cc>:<kc> callers are unchanged. Modifiers make Command-key
     * menu shortcuts reachable (mac_menu / modified mac_key). */
    if (strncmp(request, PROTO_KEY, strlen(PROTO_KEY)) == 0) {
        short cc = 0, kc = 0, mods = 0, i = (short)strlen(PROTO_KEY);
        while (request[i] >= '0' && request[i] <= '9') cc = cc * 10 + (request[i++] - '0');
        if (request[i] == ':') i++;
        while (request[i] >= '0' && request[i] <= '9') kc = kc * 10 + (request[i++] - '0');
        if (request[i] == ':') i++;
        while (request[i] >= '0' && request[i] <= '9') mods = mods * 10 + (request[i++] - '0');
        SetActivity("KEY");
        InjectKeyMod(cc, kc, mods);
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:3\rKey\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* TYPE:<text> verb: inject a run of characters into the front app. */
    if (strncmp(request, PROTO_TYPE, strlen(PROTO_TYPE)) == 0) {
        long base = (long)strlen(PROTO_TYPE), n = 0;
        while (request[base + n] && request[base + n] != '\r' &&
               request[base + n] != '\n' && (base + n) < requestLen) n++;
        SetActivity("TYPE");
        InjectType(request + base, n);
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:5\rTyped\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* CLICK:<h>:<v>[:<count>[:<modifiers>]] verb: move the mouse and post
     * click(s) to the front app. count>1 = double/triple-click; modifiers =
     * shift/cmd-click for extend/multi-select. The two extra fields are optional,
     * so the legacy CLICK:<h>:<v> form still works (count 1, no modifiers). */
    if (strncmp(request, PROTO_CLICK, strlen(PROTO_CLICK)) == 0) {
        short h = 0, v = 0, count = 1, mods = 0, i = (short)strlen(PROTO_CLICK);
        while (request[i] >= '0' && request[i] <= '9') h = h * 10 + (request[i++] - '0');
        if (request[i] == ':') i++;
        while (request[i] >= '0' && request[i] <= '9') v = v * 10 + (request[i++] - '0');
        if (request[i] == ':') {               /* optional count */
            i++; count = 0;
            while (request[i] >= '0' && request[i] <= '9') count = count * 10 + (request[i++] - '0');
        }
        if (request[i] == ':') {               /* optional modifiers */
            i++;
            while (request[i] >= '0' && request[i] <= '9') mods = mods * 10 + (request[i++] - '0');
        }
        SetActivity("CLICK");
        InjectClickMod(h, v, count, mods);
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:5\rClick\rSTDERR:0\r\r");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* STAT verb: report daemon liveness counters (uptime, RX/TX totals, whether
     * ToolServer is running). The host merges its own connection/heartbeat view
     * and exposes the whole thing as mac_status. */
    if (strncmp(request, PROTO_STAT, strlen(PROTO_STAT)) == 0 &&
        (request[4] == '\0' || request[4] == '\r' || request[4] == '\n')) {
        char frame[448];
        char body[384];   /* holds the counters + net= + the install path (home=) */
        char *b = body;
        char *f = frame;
        long now = TickCount();
        b = StatStr(b, "uptime=");      b = StatDec(b, (now - gStartTick) / 60);
        b = StatStr(b, ";rx=");         b = StatDec(b, gRXCount);
        b = StatStr(b, ";tx=");         b = StatDec(b, gTXCount);
        b = StatStr(b, ";err=");        b = StatDec(b, gErrCount);
        b = StatStr(b, ";lat=");        b = StatDec(b, gLastLat * 1000 / 60);   /* last RX->TX, ms */
        b = StatStr(b, ";lasterr=");    b = StatStr(b, gLastErr);               /* tag of most recent error */
        b = StatStr(b, ";toolserver="); b = StatDec(b, IsAppRunning('MPSX') ? 1 : 0);
        b = StatStr(b, ";net=");        b = StatStr(b, ABTransportName());
        b = StatStr(b, ";home=");       b = StatStr(b, gPrefs.home);   /* install folder; empty on legacy setups */
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:");
        f = StatDec(f, (long)(b - body));
        *f++ = '\r';
        f = StatStr(f, body);
        f = StatStr(f, "\rSTDERR:0\r\r");
        SetActivity("STAT");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* LOG verb: return the Verbose console ring as text so the host can READ the
     * log over the bridge instead of screenshotting/scrolling the (fragile)
     * monitor window. Flattens the gLog ring (oldest -> newest, CR between lines)
     * into gTEBuf and streams it STATUS/STDOUT framed. The body is read BY
     * DECLARED LENGTH on the host (it contains CR separators, so a terminator
     * read would truncate it). "LOG:<maxbytes>" returns only the last <maxbytes>.
     * Reusing gTEBuf is safe: it is only otherwise touched by SyncLogTE, which
     * runs in the same cooperative main loop and rebuilds it from scratch. */
    if (strncmp(request, "LOG", 3) == 0 &&
        (request[3] == '\0' || request[3] == '\r' || request[3] == '\n' || request[3] == ':')) {
        long n = 0, line, idx, k;
        long cap = (long)sizeof(gTEBuf);
        long maxBytes = 0;                  /* 0 => whole buffer */
        char hdr[32];
        char *h = hdr;
        SetActivity("LOG");
        if (request[3] == ':') {
            long p = 4;
            while (request[p] >= '0' && request[p] <= '9')
                maxBytes = maxBytes * 10 + (request[p++] - '0');
        }
        for (line = 0; line < gLogN; line++) {
            idx = (gLogHead - gLogN + line + 2 * LOG_LINES) % LOG_LINES;
            for (k = 0; gLog[idx][k] && k < LOG_W - 1; k++)
                if (n < cap - 1) gTEBuf[n++] = gLog[idx][k];
            if (n < cap - 1) gTEBuf[n++] = '\r';
        }
        if (maxBytes > 0 && n > maxBytes) {  /* keep only the tail */
            long start = n - maxBytes, j;
            for (j = 0; j < maxBytes; j++) gTEBuf[j] = gTEBuf[start + j];
            n = maxBytes;
        }
        h = StatStr(h, "STATUS:0\rSTDOUT:");
        h = StatDec(h, n);
        *h++ = '\r';
        ABSend(conn, hdr, (long)(h - hdr));
        if (n > 0) ABSend(conn, gTEBuf, n);
        ABSend(conn, "\rSTDERR:0\r\r", 11);
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* JGATE verb: daemon-side journaling gate (step-3 part 2). Installs the
     * journal DRVR from the daemon's OWN faceless process, arms playback, and
     * checks that Button() returns the driver's injected value -- proving the
     * Event Manager consults a driver a BACKGROUND daemon installed. Reads
     * "ABJournalDRVR" from the daemon's home folder. The armed window is a single
     * synchronous Button() with no yield; the driver also auto-disarms on the
     * first jcEvent, so playback can never stick. Diagnostic/experimental.
     *
     * It MUST hand the driver a state block, like every other journal verb. The
     * driver does not allocate: `dCtlStorage` is a pointer to a DAEMON-owned
     * block, and a nil pointer makes it a deliberate no-op (that is the guard
     * which keeps an unprepared driver from freezing a tracking loop). This verb
     * used to skip that and then read `dCtlStorage` back as if it were a call
     * counter -- the pre-PR-#69 contract. So it armed a driver that was switched
     * off, measured a field that is now an address, and reported
     * `armed=0 calls=0 FAIL` for a perfectly good driver (2026-08-01). A
     * self-test that fails on a working system is worse than none: it sends the
     * next person after the driver instead of after the test. Counters now come
     * from the block, where the driver actually keeps them. */
    if (strncmp(request, "JGATE", 5) == 0 &&
        (request[5] == '\0' || request[5] == '\r' || request[5] == '\n')) {
        static long gblk[8];           /* daemon-owned journal state block */
        Str255      pPath;
        short       resRef, drvRef = 0, jref, i, n = 0;
        OSErr       oe;
        Boolean     bIdle, bArmed;
        DCtlHandle  dh;
        char        body[208];
        char        frame[264];
        char       *b = body;
        char       *f = frame;
        const char *fn = "ABJournalDRVR";
        SetActivity("JGATE");
        for (i = 0; gPrefs.home[i] && n < 254; i++) pPath[1 + n++] = gPrefs.home[i];
        for (i = 0; fn[i] && n < 254; i++)          pPath[1 + n++] = fn[i];
        pPath[0] = (unsigned char)n;
        resRef = OpenResFile(pPath);
        oe = OpenDriver("\p.ABJournal", &drvRef);
        jref = LMGetJournalRef();
        dh = (DCtlHandle)GetDCtlEntry(drvRef);
        /* The block the driver reads and counts into. thresh must be POSITIVE:
         * jcButton reports DOWN ($FF) while poll <= thresh and UP afterwards, so
         * a zero threshold would inject the "released" answer and look exactly
         * like a driver that never ran. */
        gblk[0] = 0;                        /* itemPt: unused, no mouse scripted */
        gblk[1] = 200;                      /* thresh */
        gblk[2] = 0; gblk[3] = 0; gblk[4] = 0; gblk[5] = 0; gblk[6] = 0;
        gblk[7] = 0;                        /* mode 0 = menu */
        if (dh) (**dh).dCtlStorage = (Handle)gblk;
        bIdle = Button();
        *(volatile short *)0x08DEL = -1;    /* arm playback (JournalFlag < 0) */
        bArmed = Button();
        *(volatile short *)0x08DEL = 0;     /* disarm immediately (no yield above) */
        b = StatStr(b, "jgate resRef="); b = StatDec(b, (long)resRef);
        b = StatStr(b, " openErr=");     b = StatDec(b, (long)oe);
        b = StatStr(b, " drvRef=");      b = StatDec(b, (long)drvRef);
        b = StatStr(b, " jref=");        b = StatDec(b, (long)jref);
        b = StatStr(b, " dh=");          b = StatDec(b, dh ? 1 : 0);
        b = StatStr(b, " idle=");        b = StatDec(b, (long)(unsigned char)bIdle);
        b = StatStr(b, " armed=");       b = StatDec(b, (long)(unsigned char)bArmed);
        b = StatStr(b, " poll=");        b = StatDec(b, gblk[2]);
        b = StatStr(b, " btn=");         b = StatDec(b, gblk[4]);
        b = StatStr(b, " result=");
        /* The driver must have been CALLED (btn) and its answer must have come
         * back (armed, with the unarmed control reading idle). Reporting only the
         * value would pass on a machine where Button() happened to read down. */
        b = StatStr(b, (bArmed && !bIdle && gblk[4] > 0) ? "PASS" : "FAIL");
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:");
        f = StatDec(f, (long)(b - body));
        *f++ = '\r';
        f = StatStr(f, body);
        f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* JMENU[:<thresh>:<v>:<h>] verb: step-3 part 2b -- drive a menu tracking loop
     * via journaling. Pops a test menu and has the journal driver hold the mouse
     * over item 3 (global v,h) then feed a mouseUp after <thresh> playback calls to
     * end tracking; reports the selected (menuID,item) + per-code counts. The state
     * block is DAEMON-owned (a static here) and dCtlStorage is pointed at it -- a
     * self-allocating driver could bail on a nil block and hard-freeze the guest. */
    if (strncmp(request, "JMENU", 5) == 0 &&
        (request[5] == '\0' || request[5] == '\r' || request[5] == '\n' || request[5] == ':')) {
        static long jblk[8];           /* daemon-owned journal state block */
        Str255      pPath;
        short       resRef, ref = 0, i, n = 0;
        OSErr       oe;
        long        res, thresh = 200, iv = 160, ih = 150, p;
        MenuHandle  m;
        DCtlHandle  dh;
        char        body[240];
        char        frame[300];
        char       *b = body, *f = frame;
        const char *fn = "ABJournalDRVR";
        SetActivity("JMENU");
        if (request[5] == ':') {       /* optional JMENU:<thresh>:<v>:<h> */
            p = 6; thresh = 0;
            while (request[p] >= '0' && request[p] <= '9') thresh = thresh * 10 + (request[p++] - '0');
            if (request[p] == ':') { p++; iv = 0; while (request[p] >= '0' && request[p] <= '9') iv = iv * 10 + (request[p++] - '0'); }
            if (request[p] == ':') { p++; ih = 0; while (request[p] >= '0' && request[p] <= '9') ih = ih * 10 + (request[p++] - '0'); }
        }
        m = NewMenu(900, "\pJ");
        if (m) {
            AppendMenu(m, "\pAlpha;Beta;Gamma;Delta");
            InsertMenu(m, -1);              /* -1 = popup/hierarchical portion */
            CalcMenuSize(m);
        }
        for (i = 0; gPrefs.home[i] && n < 254; i++) pPath[1 + n++] = gPrefs.home[i];
        for (i = 0; fn[i] && n < 254; i++)          pPath[1 + n++] = fn[i];
        pPath[0] = (unsigned char)n;
        resRef = OpenResFile(pPath);
        oe = OpenDriver("\p.ABJournal", &ref);
        dh = (DCtlHandle)GetDCtlEntry(ref);
        jblk[0] = (iv << 16) | (ih & 0xFFFF);  /* itemPt (v,h) */
        jblk[1] = thresh;                      /* release after this many calls */
        jblk[2] = 0; jblk[3] = 0; jblk[4] = 0; jblk[5] = 0; jblk[6] = 0;
        jblk[7] = 0;                           /* mode 0 = menu (null -> mouseUp) */
        if (dh) (**dh).dCtlStorage = (Handle)jblk;   /* point the driver at our block */
        *(volatile short *)0x08DEL = -1;   /* arm playback */
        res = PopUpMenuSelect(m, 120, 120, 1);
        *(volatile short *)0x08DEL = 0;    /* disarm */
        b = StatStr(b, "jmenu openErr="); b = StatDec(b, (long)oe);
        b = StatStr(b, " ref=");     b = StatDec(b, (long)ref);
        b = StatStr(b, " dh=");      b = StatDec(b, dh ? 1 : 0);
        b = StatStr(b, " menuID=");  b = StatDec(b, (long)((res >> 16) & 0xFFFF));
        b = StatStr(b, " item=");    b = StatDec(b, (long)(res & 0xFFFF));
        b = StatStr(b, " thr=");     b = StatDec(b, thresh);
        b = StatStr(b, " poll=");    b = StatDec(b, jblk[2]);
        b = StatStr(b, " mouse=");   b = StatDec(b, jblk[3]);
        b = StatStr(b, " btn=");     b = StatDec(b, jblk[4]);
        b = StatStr(b, " evt=");     b = StatDec(b, jblk[5]);
        *b = '\0';
        if (m) { DeleteMenu(900); DisposeMenu(m); }
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)(b - body));
        *f++ = '\r';
        f = StatStr(f, body); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* JABOUT[:<thresh>:<v>:<h>] verb: the last-mile demo -- drive the daemon's OWN
     * Apple menu on the real menu BAR via journaling (MenuSelect, not a popup) to
     * choose "About AppleBridge" (item 1), then open the About box. The current menu
     * list belongs to the calling process, so MenuSelect tracks the daemon's menus.
     * Reports the selection first, THEN shows the About box (which blocks on its own
     * click-to-close loop with the journal already disarmed). Freeze-safe (same
     * daemon-owned block + mouseUp feed + in-driver safety as JMENU). */
    if (strncmp(request, "JABOUT", 6) == 0 &&
        (request[6] == '\0' || request[6] == '\r' || request[6] == '\n' || request[6] == ':')) {
        static long ablk[8];           /* daemon-owned journal state block */
        Str255      pPath;
        short       resRef, ref = 0, i, n = 0;
        OSErr       oe;
        long        res, thresh = 200, iv = 28, ih = 40, p;
        Point       startPt;
        DCtlHandle  dh;
        char        body[220];
        char        frame[280];
        char       *b = body, *f = frame;
        const char *fn = "ABJournalDRVR";
        SetActivity("JABOUT");
        if (request[6] == ':') {       /* optional JABOUT:<thresh>:<v>:<h> */
            p = 7; thresh = 0;
            while (request[p] >= '0' && request[p] <= '9') thresh = thresh * 10 + (request[p++] - '0');
            if (request[p] == ':') { p++; iv = 0; while (request[p] >= '0' && request[p] <= '9') iv = iv * 10 + (request[p++] - '0'); }
            if (request[p] == ':') { p++; ih = 0; while (request[p] >= '0' && request[p] <= '9') ih = ih * 10 + (request[p++] - '0'); }
        }
        for (i = 0; gPrefs.home[i] && n < 254; i++) pPath[1 + n++] = gPrefs.home[i];
        for (i = 0; fn[i] && n < 254; i++)          pPath[1 + n++] = fn[i];
        pPath[0] = (unsigned char)n;
        resRef = OpenResFile(pPath);
        oe = OpenDriver("\p.ABJournal", &ref);
        dh = (DCtlHandle)GetDCtlEntry(ref);
        ablk[0] = (iv << 16) | (ih & 0xFFFF);  /* itemPt = the About item's location */
        ablk[1] = thresh;
        ablk[2] = 0; ablk[3] = 0; ablk[4] = 0; ablk[5] = 0; ablk[6] = 0;
        ablk[7] = 0;                           /* mode 0 = menu */
        if (dh) (**dh).dCtlStorage = (Handle)ablk;
        startPt.v = 10; startPt.h = 12;        /* Apple menu title in the menu bar */
        *(volatile short *)0x08DEL = -1;       /* arm playback */
        res = MenuSelect(startPt);
        *(volatile short *)0x08DEL = 0;        /* disarm before the About box's Button() loop */
        b = StatStr(b, "jabout openErr="); b = StatDec(b, (long)oe);
        b = StatStr(b, " ref=");     b = StatDec(b, (long)ref);
        b = StatStr(b, " menuID=");  b = StatDec(b, (long)((res >> 16) & 0xFFFF));
        b = StatStr(b, " item=");    b = StatDec(b, (long)(res & 0xFFFF));
        b = StatStr(b, " thr=");     b = StatDec(b, thresh);
        b = StatStr(b, " poll=");    b = StatDec(b, ablk[2]);
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)(b - body));
        *f++ = '\r';
        f = StatStr(f, body); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));   /* report the selection immediately */
        gLastTX = TickCount();
        gTXCount++;
        HandleMenuCommand(res);        /* item 1 -> ShowAboutBox (click-to-close) */
        return true;
    }

    /* MENU:<title>:<item> verb: journal-drive the daemon's OWN menu bar BY NAME.
     * Resolves <title> to a menu (+ its title X, read as menuLeft from the live menu
     * list) and <item> to an index (numeric, or matched case-insensitively against
     * each item's text), computes the item's screen point, and drives
     * MenuSelect(titlePt) via journaling to select it -- then dispatches it
     * (HandleMenuCommand). Generalizes JABOUT (which hardcoded the Apple menu +
     * item 1). OWN menus only: MenuSelect uses the CALLING process's menu list, so
     * this cannot reach a front app (JPROBE proved a background yield never pumps the
     * journal; see docs/JOURNALING_MENU_BY_NAME.md). Freeze-safe: same daemon-owned
     * block + mouseUp feed + in-driver safety as JMENU/JABOUT (tracking self-ends on
     * the synthesized mouseUp). Item Y uses a 16px/row model (JABOUT-verified: item 1
     * centre = 28); separators before the target may skew it (report shows itemY). */
    if (strncmp(request, "MENU:", 5) == 0) {
        static long mblk[8];
        Str255      pPath, wantTitle, wantItem, itemText;
        short       resRef, ref = 0, i, n = 0, ti = 0, ii = 0, p;
        short       menuID = 0, itemIndex = 0, titleX = 0, itemY = 0, menuW = 0;
        short       numItems = 0, off, lastMenu, found = 0;
        long        res = 0, thresh = 200;
        Boolean     itemIsNum = true;
        MenuHandle  mh, mtarget = NULL;
        Handle      mbar;
        Ptr         mp, mi;
        DCtlHandle  dh;
        Point       startPt;
        char        body[260];
        char        frame[320];
        char       *b = body, *f = frame;
        const char *fn = "ABJournalDRVR";
        SetActivity("MENU");
        /* parse "MENU:<title>:<item>" (title up to ':', item to ':' / end) */
        p = 5;
        while (request[p] && request[p] != ':' && request[p] != '\r' && request[p] != '\n' && ti < 255)
            wantTitle[1 + ti++] = request[p++];
        wantTitle[0] = (unsigned char)ti;
        if (request[p] == ':') p++;
        while (request[p] && request[p] != ':' && request[p] != '\r' && request[p] != '\n' && ii < 255)
            wantItem[1 + ii++] = request[p++];
        wantItem[0] = (unsigned char)ii;
        if (ii == 0) itemIsNum = false;
        for (i = 1; i <= ii; i++) if (wantItem[i] < '0' || wantItem[i] > '9') itemIsNum = false;
        mblk[2] = 0;                                    /* poll: stays 0 unless we drive */
        /* resolve the menu by title from the LIVE menu list (header: lastMenu@0,
         * lastRight@2, mbResID@4; then 6-byte entries: MenuHandle@0, menuLeft@4).
         * READ-ONLY -- no driver / journal / MenuSelect touched during resolution. */
        mbar = GetMenuBar();
        if (mbar != NULL) {
            mp = *mbar;
            lastMenu = *(short *)mp;
            for (off = 6; off <= lastMenu && off < 6 + 6 * 40 && !found; off += 6) {
                mh = *(MenuHandle *)(mp + off);
                if (mh != NULL) {
                    mi = *(Handle)mh;                       /* MenuInfo */
                    if (EqualString((StringPtr)(mi + 14), wantTitle, false, false)) {
                        mtarget = mh;
                        menuID  = *(short *)mi;             /* menuID  @0 */
                        menuW   = *(short *)(mi + 2);       /* menuWidth @2 */
                        titleX  = *(short *)(mp + off + 4); /* menuLeft */
                        found   = 1;
                    }
                }
            }
            DisposeHandle(mbar);
        }
        /* resolve the item index (numeric, or by item text) */
        if (found && mtarget != NULL) {
            numItems = CountMItems(mtarget);
            if (itemIsNum) {
                itemIndex = 0;
                for (i = 1; i <= ii; i++) itemIndex = itemIndex * 10 + (wantItem[i] - '0');
            } else {
                short j;
                for (j = 1; j <= numItems; j++) {
                    GetMenuItemText(mtarget, j, itemText);
                    if (EqualString(itemText, wantItem, false, false)) { itemIndex = j; break; }
                }
            }
        }
        if (itemIndex > 0) itemY = 20 + 16 * (itemIndex - 1) + 8;   /* 16px rows below the 20px bar */
        /* DRIVE only a fully-resolved, IN-RANGE target. Invalid input (unknown title
         * or item) falls through here as a pure read-only no-op -- it opens no driver,
         * arms no journal, and calls no MenuSelect, so a typo can never wedge the
         * guest. For a valid target: install the driver LAZILY (OpenDriver first; only
         * OpenResFile if it is not already in the unit table -- avoids re-opening the
         * resource file on every call), save/restore the GrafPort around the modal
         * MenuSelect, and guard it with the interrupt watchdog (a hung tracking loop
         * self-recovers at interrupt time). */
        if (found && itemIndex > 0 && itemIndex <= numItems) {
            GrafPtr savePort;
            if (OpenDriver("\p.ABJournal", &ref) != noErr) {
                for (i = 0; gPrefs.home[i] && n < 254; i++) pPath[1 + n++] = gPrefs.home[i];
                for (i = 0; fn[i] && n < 254; i++)          pPath[1 + n++] = fn[i];
                pPath[0] = (unsigned char)n;
                resRef = OpenResFile(pPath);
                OpenDriver("\p.ABJournal", &ref);
            }
            dh = (DCtlHandle)GetDCtlEntry(ref);
            mblk[0] = ((long)itemY << 16) | ((long)(titleX + (menuW > 24 ? menuW / 2 : 12)) & 0xFFFF);
            mblk[1] = thresh;
            mblk[2] = 0; mblk[3] = 0; mblk[4] = 0; mblk[5] = 0; mblk[6] = 0;
            mblk[7] = 0;                                 /* mode 0 = menu */
            if (dh) (**dh).dCtlStorage = (Handle)mblk;
            startPt.v = 10; startPt.h = titleX + 4;      /* the menu title on the bar */
            GetPort(&savePort);
            ArmJournalWatchdog(3000);                    /* interrupt-disarm if MenuSelect hangs */
            *(volatile short *)0x08DEL = -1;            /* arm playback */
            res = MenuSelect(startPt);
            *(volatile short *)0x08DEL = 0;             /* disarm */
            CancelJournalWatchdog();
            SetPort(savePort);
        }
        b = StatStr(b, "menu found=");   b = StatDec(b, (long)found);
        b = StatStr(b, " menuID=");      b = StatDec(b, (long)menuID);
        b = StatStr(b, " item=");        b = StatDec(b, (long)itemIndex);
        b = StatStr(b, " nItems=");      b = StatDec(b, (long)numItems);
        b = StatStr(b, " titleX=");      b = StatDec(b, (long)titleX);
        b = StatStr(b, " itemY=");       b = StatDec(b, (long)itemY);
        b = StatStr(b, " selID=");       b = StatDec(b, (long)((res >> 16) & 0xFFFF));
        b = StatStr(b, " selItem=");     b = StatDec(b, (long)(res & 0xFFFF));
        b = StatStr(b, " poll=");        b = StatDec(b, mblk[2]);
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)(b - body));
        *f++ = '\r';
        f = StatStr(f, body); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount();
        gTXCount++;
        if (res != 0) HandleMenuCommand(res);           /* perform the chosen command */
        return true;
    }

    /* ---- Route B: global _MenuSelect trap patch (foreign-app menu driving) ----
     * A head patch on _MenuSelect ($A93D) in the system heap (layout: mspatch.a,
     * offsets +2 Magic 'MS', +4 Armed, +6 OneShot, +8 Result menuID<<16|item,
     * +12 Real, +16 Calls, +20 Hits, +24 LastRes). Installed by the RESIDENT
     * daemon (ToolServer reverts a trap patch an MPW tool makes on exit). When
     * armed, the next MenuSelect in ANY context returns Result WITHOUT the
     * tracking loop, so a posted menu-bar mouseDown makes the FRONT app dispatch
     * that menu command -- no journal, no daemon self-foreground (Route A's crash
     * mechanisms). See docs/JOURNALING_MENU_BY_NAME.md. */
    if (strncmp(request, "MSINSTALL", 9) == 0) {
        Str255        pPath;
        short         resRef, i, n = 0;
        Handle        h;
        Size          sz;
        Ptr           blk;
        unsigned long real, live;
        const char   *fn = "ABMenuPatch";
        char body[220], frame[300];
        char *b = body, *f = frame;
        SetActivity("MSINSTALL");
        if (gMSPatch == 0L) {
            /* Prefer the boot INIT's GLOBAL block (found by heap scan) over
             * installing our own process-local copy -- only the INIT's patch
             * reaches a FOREIGN app's MenuSelect. */
            gMSPatch = FindMSPatch();
            if (gMSPatch != 0L) {
                b = StatStr(b, "adopted INIT patch blk="); b = StatHex(b, (unsigned long)gMSPatch);
                b = StatStr(b, " calls=");  b = StatDec(b, *(long *)((char *)gMSPatch + 16));
                b = StatStr(b, " hits=");   b = StatDec(b, *(long *)((char *)gMSPatch + 20));
                goto msinstall_reply;
            }
        }
        if (gMSPatch != 0L) {
            b = StatStr(b, "already installed blk="); b = StatHex(b, (unsigned long)gMSPatch);
            b = StatStr(b, " calls=");                b = StatDec(b, *(long *)((char *)gMSPatch + 16));
        } else {
            for (i = 0; gPrefs.home[i] && n < 254; i++) pPath[1 + n++] = gPrefs.home[i];
            for (i = 0; fn[i] && n < 254; i++)          pPath[1 + n++] = fn[i];
            pPath[0] = (unsigned char)n;
            resRef = OpenResFile(pPath);
            if (resRef == -1) { b = StatStr(b, "OpenResFile failed err="); b = StatDec(b, ResError()); }
            else {
                h = Get1Resource('MSPT', 128);
                if (h == 0L) { b = StatStr(b, "no MSPT 128 err="); b = StatDec(b, ResError()); }
                else {
                    HNoPurge(h); LoadResource(h);
                    sz  = GetHandleSize(h);
                    blk = NewPtrSys(sz);
                    if (blk == 0L) { b = StatStr(b, "NewPtrSys failed"); }
                    else {
                        BlockMove(*h, blk, sz);
                        if (*(short *)(blk + 2) != (short)0x4D53) {
                            b = StatStr(b, "magic mismatch");   /* leave block; harmless */
                        } else {
                            real = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
                            *(unsigned long *)(blk + 12) = real;
                            NSetTrapAddress((UniversalProcPtr)blk, _MenuSelect, ToolTrap);
                            live = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
                            gMSPatch = blk;
                            b = StatStr(b, "installed blk="); b = StatHex(b, (unsigned long)blk);
                            b = StatStr(b, " real=");         b = StatHex(b, real);
                            b = StatStr(b, (live == (unsigned long)blk) ? " head=YES" : " head=NO");
                        }
                    }
                }
                CloseResFile(resRef);
            }
        }
msinstall_reply:
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)(b - body)); *f++ = '\r';
        f = StatStr(f, body); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    if (strncmp(request, "MSREAD", 6) == 0) {
        char body[220], frame[300];
        char *b = body, *f = frame;
        SetActivity("MSREAD");
        if (gMSPatch == 0L) { b = StatStr(b, "not installed"); }
        else {
            Ptr p = gMSPatch;
            unsigned long live = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
            b = StatStr(b, "blk=");     b = StatHex(b, (unsigned long)p);
            b = StatStr(b, " calls=");  b = StatDec(b, *(long *)(p + 16));
            b = StatStr(b, " hits=");   b = StatDec(b, *(long *)(p + 20));
            b = StatStr(b, " armed=");  b = StatDec(b, *(short *)(p + 4));
            b = StatStr(b, " lastRes="); b = StatHex(b, *(unsigned long *)(p + 24));
            b = StatStr(b, (live == (unsigned long)p) ? " head=YES" : " head=NO");
        }
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)(b - body)); *f++ = '\r';
        f = StatStr(f, body); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    if (strncmp(request, "MSDRIVE:", 8) == 0) {
        char body[220], frame[300];
        char *b = body, *f = frame;
        short menuID = 0, item = 0, p2 = 8;
        SetActivity("MSDRIVE");
        while (request[p2] >= '0' && request[p2] <= '9') menuID = menuID * 10 + (request[p2++] - '0');
        if (request[p2] == ':') p2++;
        while (request[p2] >= '0' && request[p2] <= '9') item = item * 10 + (request[p2++] - '0');
        if (gMSPatch == 0L) { b = StatStr(b, "not installed (run MSINSTALL)"); }
        else if (menuID == 0 || item == 0) { b = StatStr(b, "bad args (MSDRIVE:<menuID>:<item>)"); }
        else {
            Ptr p = gMSPatch;
            *(unsigned long *)(p + 8) = ((unsigned long)menuID << 16) | (unsigned long)item;
            *(short *)(p + 6) = 1;                       /* OneShot */
            *(short *)(p + 4) = 1;                       /* Armed */
            InjectClick(40, 10);                         /* menu-bar mouseDown -> front app MenuSelect */
            b = StatStr(b, "armed menuID="); b = StatDec(b, menuID);
            b = StatStr(b, " item=");        b = StatDec(b, item);
            b = StatStr(b, " posted menu-bar click (MSREAD for hits)");
        }
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)(b - body)); *f++ = '\r';
        f = StatStr(f, body); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    if (strncmp(request, "MSUNINSTALL", 11) == 0) {
        char body[220], frame[300];
        char *b = body, *f = frame;
        SetActivity("MSUNINSTALL");
        if (gMSPatch == 0L) { b = StatStr(b, "not installed"); }
        else {
            unsigned long live = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
            unsigned long real = *(unsigned long *)((char *)gMSPatch + 12);
            if (live != (unsigned long)gMSPatch) {
                b = StatStr(b, "NOT head - refusing; live="); b = StatHex(b, live);
            } else {
                NSetTrapAddress((UniversalProcPtr)real, _MenuSelect, ToolTrap);
                gMSPatch = 0L;                            /* leave block resident (no dispose race) */
                b = StatStr(b, "uninstalled; $A93D restored to "); b = StatHex(b, real);
            }
        }
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)(b - body)); *f++ = '\r';
        f = StatStr(f, body); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount(); gTXCount++;
        return true;
    }

    /* JSAFE verb: validate the interrupt-driven journaling watchdog IN ISOLATION,
     * safely, before pointing it at a modal call. Installs the driver (so JournalRef
     * is valid), primes the watchdog to disarm in ~1500ms, arms JournalFlag, then
     * spins reading JournalFlag + RAW low-mem Ticks ($016A, interrupt-updated -- NOT
     * TickCount(), which is itself journaled) with a 5s self-timeout. So even if the
     * watchdog fails the daemon's own spin self-recovers -- JSAFE cannot hard-hang.
     * Reports elapsed ticks: ~90 (1.5s) = watchdog FIRED (PASS); ~300 (5s) = watchdog
     * failed (daemon self-timed-out). No modal call -> nothing left open. */
    if (strncmp(request, "JSAFE", 5) == 0 &&
        (request[5] == '\0' || request[5] == '\r' || request[5] == '\n')) {
        static long tblk[8];
        Str255      pPath;
        short       resRef, ref = 0, i, n = 0, jf;
        OSErr       oe;
        long        startT, elapsed;
        DCtlHandle  dh;
        char        body[160];
        char        frame[220];
        char       *b = body, *f = frame;
        const char *fn = "ABJournalDRVR";
        SetActivity("JSAFE");
        for (i = 0; gPrefs.home[i] && n < 254; i++) pPath[1 + n++] = gPrefs.home[i];
        for (i = 0; fn[i] && n < 254; i++)          pPath[1 + n++] = fn[i];
        pPath[0] = (unsigned char)n;
        resRef = OpenResFile(pPath);
        oe = OpenDriver("\p.ABJournal", &ref);
        dh = (DCtlHandle)GetDCtlEntry(ref);
        tblk[0] = 0; tblk[1] = 999999L;     /* huge thresh: in-driver safety won't fire */
        tblk[2] = 0; tblk[3] = 0; tblk[4] = 0; tblk[5] = 0; tblk[6] = 0; tblk[7] = 0;
        if (dh) (**dh).dCtlStorage = (Handle)tblk;
        ArmJournalWatchdog(1500);           /* interrupt-disarm in ~1.5s */
        startT = *(volatile long *)0x016AL;
        *(volatile short *)0x08DEL = -1;    /* arm playback */
        while (*(volatile short *)0x08DEL != 0 &&
               (*(volatile long *)0x016AL - startT) < 300) { }   /* watchdog OR 5s */
        elapsed = *(volatile long *)0x016AL - startT;
        jf = *(volatile short *)0x08DEL;
        *(volatile short *)0x08DEL = 0;     /* ensure disarmed */
        CancelJournalWatchdog();
        b = StatStr(b, "jsafe openErr="); b = StatDec(b, (long)oe);
        b = StatStr(b, " elapsedTicks="); b = StatDec(b, elapsed);
        b = StatStr(b, " flagAtExit=");  b = StatDec(b, (long)jf);
        b = StatStr(b, " result=");
        b = StatStr(b, (elapsed < 200) ? "PASS-watchdog-fired" : "FAIL-self-timeout");
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)(b - body));
        *f++ = '\r';
        f = StatStr(f, body); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* JPROBE verb: FEASIBILITY SPIKE for cross-process (front-app) menu driving.
     * The open question: with journal playback armed, does the BACKGROUND daemon's own
     * WaitNextEvent grab the injected mouseDown (so the daemon steals its own journal
     * events -> cross-process is blocked, only its OWN menus are driveable), or does the
     * event go elsewhere (a front app's event loop -> cross-process is feasible)?
     * We arm the driver to feed a menu-bar mouseDown continuously (mode 1, huge thresh so
     * it never auto-releases), then the daemon calls WaitNextEvent a few bounded times and
     * records what IT receives. Freeze-safe: interrupt watchdog (~1.5s) + a raw-Ticks
     * self-timeout (200t) + NO modal call -> nothing can stay open. Reports: is the daemon
     * itself front (selfFront); driver poll/jcEvent counts (did the ROM consult the driver);
     * how many of the daemon's own WNE calls returned an event (wneHits) and the first
     * one's what/where; and whether any was a mouseDown at our injected point (gotMD). */
    if (strncmp(request, "JPROBE", 6) == 0 &&
        (request[6] == '\0' || request[6] == '\r' || request[6] == '\n')) {
        static long pblk[8];
        Str255      pPath;
        EventRecord ev;
        ProcessSerialNumber selfPSN, frontPSN;
        Boolean     selfFront = false, gotMD = false;
        short       resRef, ref = 0, i, n = 0, wneHits = 0;
        long        firstWhat = -1, firstWhere = 0, startT;
        OSErr       oe;
        DCtlHandle  dh;
        char        body[200];
        char        frame[260];
        char       *b = body, *f = frame;
        const char *fn = "ABJournalDRVR";
        SetActivity("JPROBE");
        for (i = 0; gPrefs.home[i] && n < 254; i++) pPath[1 + n++] = gPrefs.home[i];
        for (i = 0; fn[i] && n < 254; i++)          pPath[1 + n++] = fn[i];
        pPath[0] = (unsigned char)n;
        resRef = OpenResFile(pPath);
        oe = OpenDriver("\p.ABJournal", &ref);
        dh = (DCtlHandle)GetDCtlEntry(ref);
        if (GetCurrentProcess(&selfPSN) == noErr && GetFrontProcess(&frontPSN) == noErr)
            SameProcess(&frontPSN, &selfPSN, &selfFront);
        pblk[0] = (10L << 16) | (40L & 0xFFFF);   /* itemPt = a menu-bar point (v=10,h=40) */
        pblk[1] = 999999L;                        /* huge thresh: driver keeps feeding mouseDown, never auto-mouseUp */
        pblk[2] = 0; pblk[3] = 0; pblk[4] = 0; pblk[5] = 0; pblk[6] = 0;
        pblk[7] = 1;                              /* mode 1: jcEvent -> mouseDown (pre-release) */
        if (dh) (**dh).dCtlStorage = (Handle)pblk;
        ArmJournalWatchdog(1500);
        startT = *(volatile long *)0x016AL;
        *(volatile short *)0x08DEL = -1;          /* arm playback */
        for (i = 0; i < 8 && (*(volatile short *)0x08DEL != 0) &&
                    (*(volatile long *)0x016AL - startT) < 200; i++) {
            if (WaitNextEvent(everyEvent, &ev, 2L, NULL)) {
                wneHits++;
                if (firstWhat < 0) { firstWhat = ev.what; firstWhere = ((long)ev.where.v << 16) | (ev.where.h & 0xFFFF); }
                if (ev.what == mouseDown) gotMD = true;
            }
        }
        *(volatile short *)0x08DEL = 0;           /* disarm */
        CancelJournalWatchdog();
        b = StatStr(b, "jprobe openErr="); b = StatDec(b, (long)oe);
        b = StatStr(b, " selfFront=");  b = StatDec(b, selfFront ? 1 : 0);
        b = StatStr(b, " poll=");       b = StatDec(b, pblk[2]);
        b = StatStr(b, " jcEvt=");      b = StatDec(b, pblk[5]);
        b = StatStr(b, " wneHits=");    b = StatDec(b, (long)wneHits);
        b = StatStr(b, " firstWhat=");  b = StatDec(b, firstWhat);
        b = StatStr(b, " firstV=");     b = StatDec(b, (long)((firstWhere >> 16) & 0xFFFF));
        b = StatStr(b, " firstH=");     b = StatDec(b, (long)(firstWhere & 0xFFFF));
        b = StatStr(b, " gotMD=");      b = StatDec(b, gotMD ? 1 : 0);
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)(b - body));
        *f++ = '\r';
        f = StatStr(f, body); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* JSF[:<thresh>:<item>] verb: journal-drive a modal Standard File (Open) dialog
     * to click a button DETERMINISTICALLY -- the class of thing posted clicks can
     * NEVER reach. Uses SFGetFile (lets us place the dialog + install a dlgHook);
     * the hook (SFCoordHook) reads the target item's real global rect and redirects
     * the journal there, so NO coordinate guessing. item = 1 (Open) or 3 (Cancel,
     * default). Mode 1 (dialog click: mouseDown -> hold -> mouseUp). Reports
     * reply.good + the target coord the hook resolved + the chosen file. Freeze-safe;
     * keep thresh small (~200) -- a big thresh holds the journal armed for minutes. */
    if (strncmp(request, "JSF", 3) == 0 &&
        (request[3] == '\0' || request[3] == '\r' || request[3] == '\n' || request[3] == ':')) {
        static long  sblk[8];
        SFReply      reply;
        Point        where;
        Str255       pPath;
        short        resRef, ref = 0, i, n = 0, k, item = 3;
        OSErr        oe;
        long         thresh = 200, p;
        DCtlHandle   dh;
        char         body[220];
        char         frame[280];
        char        *b = body, *f = frame;
        const char  *fn = "ABJournalDRVR";
        SetActivity("JSF");
        if (request[3] == ':') {
            p = 4; thresh = 0;
            while (request[p] >= '0' && request[p] <= '9') thresh = thresh * 10 + (request[p++] - '0');
            if (request[p] == ':') { p++; item = 0; while (request[p] >= '0' && request[p] <= '9') item = item * 10 + (request[p++] - '0'); }
        }
        for (i = 0; gPrefs.home[i] && n < 254; i++) pPath[1 + n++] = gPrefs.home[i];
        for (i = 0; fn[i] && n < 254; i++)          pPath[1 + n++] = fn[i];
        pPath[0] = (unsigned char)n;
        resRef = OpenResFile(pPath);
        oe = OpenDriver("\p.ABJournal", &ref);
        dh = (DCtlHandle)GetDCtlEntry(ref);
        sblk[0] = 0;                 /* placeholder; the dlgHook redirects it to the item */
        sblk[1] = thresh;
        sblk[2] = 0; sblk[3] = 0; sblk[4] = 0; sblk[5] = 0; sblk[6] = 0;
        sblk[7] = 1;                 /* mode 1 = dialog click */
        if (dh) (**dh).dCtlStorage = (Handle)sblk;
        gSFBlk = sblk; gSFItem = item;      /* the hook steers this block to <item> */
        where.v = 90; where.h = 100;        /* dialog top-left (global) */
        reply.good = false;
        /* FOREGROUND the daemon around SFGetFile. As a background app its modal
         * ModalDialog gets NO events (they route to the front Finder), so it spins
         * at 100% forever -- a freeze the journal watchdog can't fix (it's not the
         * journal). SetFrontProcess is ASYNCHRONOUS: the layer switch only lands
         * when the current front app next yields through the Event Manager. Since
         * ModalDialog busy-loops on GetNextEvent (never WaitNextEvent) and journal
         * playback intercepts event fetch, entering the modal immediately means the
         * switch never completes -- the daemon never truly becomes front, the dialog
         * gets no events, and it spins undismissable.  So: request the switch, then
         * PUMP WaitNextEvent (yielding) until GetFrontProcess confirms we ARE front,
         * bounded to ~2 s.  Only if confirmed front do we arm the journal + open the
         * modal.  If we never become front, we BAIL before SFGetFile -- a failed
         * switch can never peg the CPU. Restore the prior front app afterwards. */
        {
            ProcessSerialNumber selfPSN, prevPSN, frontPSN;
            Boolean     haveSelf = (GetCurrentProcess(&selfPSN) == noErr);
            Boolean     havePrev = (GetFrontProcess(&prevPSN) == noErr);
            Boolean     amFront = false;
            short       guard;
            EventRecord ev;
            if (haveSelf) {
                SetFrontProcess(&selfPSN);
                for (guard = 0; guard < 60 && !amFront; guard++) {
                    Boolean same = false;
                    WaitNextEvent(everyEvent, &ev, 2L, NULL);   /* yield ~2 ticks so MultiFinder switches */
                    if (GetFrontProcess(&frontPSN) == noErr)
                        SameProcess(&frontPSN, &selfPSN, &same);
                    amFront = same;
                }
            }
            sblk[6] = amFront ? 1 : 0;           /* reported as front= in the reply */
            if (amFront) {
                ArmJournalWatchdog(3000);        /* interrupt-disarm after 3s if we still hang */
                *(volatile short *)0x08DEL = -1;
                SFGetFile(where, "\p", NULL, -1, NULL, (DlgHookUPP)SFCoordHook, &reply);
                *(volatile short *)0x08DEL = 0;
                CancelJournalWatchdog();
            }
            if (havePrev) SetFrontProcess(&prevPSN);  /* hand the front back to Finder */
        }
        gSFBlk = NULL;
        b = StatStr(b, "jsf openErr="); b = StatDec(b, (long)oe);
        b = StatStr(b, " item=");  b = StatDec(b, (long)item);
        b = StatStr(b, " front="); b = StatDec(b, sblk[6]);
        b = StatStr(b, " good=");  b = StatDec(b, reply.good ? 1 : 0);
        b = StatStr(b, " poll=");  b = StatDec(b, sblk[2]);
        b = StatStr(b, " tgtV="); b = StatDec(b, (long)((sblk[0] >> 16) & 0xFFFF));
        b = StatStr(b, " tgtH="); b = StatDec(b, (long)(sblk[0] & 0xFFFF));
        if (reply.good) {
            b = StatStr(b, " file=");
            for (k = 1; k <= reply.fName[0] && k < 40; k++) *b++ = reply.fName[k];
        }
        *b = '\0';
        f = StatStr(f, "STATUS:0\rSTDOUT:"); f = StatDec(f, (long)(b - body));
        *f++ = '\r';
        f = StatStr(f, body); f = StatStr(f, "\rSTDERR:0\r\r");
        ABSend(conn, frame, (long)(f - frame));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* AESEND verb: send an arbitrary Apple Event and harvest its reply.
     * AESEND:<targetHex8>:<classHex8>:<idHex8>:<doLen>[:<waitTicks>]\n<directObjectBytes>
     * (length-framed; the reply rides the normal command response path).
     *
     * waitTicks is OPTIONAL and new — an older host omits it and gets
     * AE_SEND_DEFAULT_TIMEOUT, which is the interactive bound, not the five
     * minutes this verb used to inherit from 'dosc'. 0 means "do not wait at
     * all" (kAENoReply): the honest choice for an event whose 'aete' declares
     * its reply 'null', and the one that cannot starve the guest. */
    if (strncmp(request, PROTO_AESEND, strlen(PROTO_AESEND)) == 0) {
        OSType tgt, cls, eid;
        long p = (long)strlen(PROTO_AESEND);
        long doLen = 0, headerEnd, total;
        long waitTicks = -1;                    /* -1 = caller stated no bound */
        SetActivity("AESEND");
        tgt = ParseHexType(request + p); p += 8;
        if (request[p] == ':') p++;
        cls = ParseHexType(request + p); p += 8;
        if (request[p] == ':') p++;
        eid = ParseHexType(request + p); p += 8;
        if (request[p] == ':') p++;
        while (request[p] >= '0' && request[p] <= '9') doLen = doLen * 10 + (request[p++] - '0');
        if (request[p] == ':') {
            p++;
            waitTicks = 0;
            while (request[p] >= '0' && request[p] <= '9')
                waitTicks = waitTicks * 10 + (request[p++] - '0');
        }
        if (request[p] == '\n' || request[p] == '\r') p++;
        headerEnd = p;
        total = headerEnd + doLen;
        /* Top up if the direct object arrived fragmented (bounded by the buffer). */
        if (total > requestLen && total <= MAX_COMMAND_LENGTH + 256) {
            unsigned long lastProgress = TickCount();
            while (requestLen < total) {
                long chunk = 0;
                OSStatus rerr = ABRecv(conn, request + requestLen,
                                            (MAX_COMMAND_LENGTH + 256) - requestLen, &chunk);
                if (rerr == noErr && chunk > 0) { requestLen += chunk; lastProgress = TickCount(); }
                else if (rerr == kABNoData) { if (TickCount() - lastProgress > 600) break; SystemTask(); }
                else break;
            }
        }
        if (doLen > requestLen - headerEnd) doLen = requestLen - headerEnd;
        if (doLen < 0) doLen = 0;
        ExecuteAppleEvent(tgt, cls, eid, request + headerEnd, doLen, waitTicks, &cmdResult);
        err = SendCommandResult(conn, &cmdResult);
        if (cmdResult.exitCode != 0) NoteErrCode("AESEND", cmdResult.exitCode);
        gLastTX = TickCount();
        gTXCount++;
        CleanupCommandResult(&cmdResult);
        if (err != noErr) {
            SetActivity("send failed - reconnecting");
            return false;
        }
        return true;
    }

    /* CLIPGET verb: return the guest's 'TEXT' scrap (clipboard) as the reply.
     * Basilisk II mirrors this scrap with the host pasteboard, so it doubles as a
     * host<->guest text side-channel. */
    if (strncmp(request, PROTO_CLIPGET, strlen(PROTO_CLIPGET)) == 0 &&
        (request[7] == '\0' || request[7] == '\r' || request[7] == '\n')) {
        Handle h;
        long off = 0, n;
        SetActivity("CLIPGET");
        cmdResult.exitCode = 0;
        cmdResult.outData = NULL;
        cmdResult.outLen = 0;
        cmdResult.errData[0] = '\0';
        h = NewHandle(0);
        if (h != NULL) {
            n = GetScrap(h, 'TEXT', &off);     /* >=0 length, or negative error */
            if (n >= 0) { cmdResult.outData = h; cmdResult.outLen = n; }
            else { DisposeHandle(h); }         /* no TEXT on the scrap: empty reply */
        }
        err = SendCommandResult(conn, &cmdResult);
        if (cmdResult.exitCode != 0) NoteErrCode("CLIPGET", cmdResult.exitCode);
        gLastTX = TickCount();
        gTXCount++;
        CleanupCommandResult(&cmdResult);
        if (err != noErr) { SetActivity("send failed - reconnecting"); return false; }
        return true;
    }

    /* CLIPSET verb: replace the guest 'TEXT' scrap. CLIPSET:<len>\n<textBytes>. */
    if (strncmp(request, PROTO_CLIPSET, strlen(PROTO_CLIPSET)) == 0) {
        long p = (long)strlen(PROTO_CLIPSET);
        long clipLen = 0, headerEnd, total;
        OSErr serr;
        SetActivity("CLIPSET");
        while (request[p] >= '0' && request[p] <= '9') clipLen = clipLen * 10 + (request[p++] - '0');
        if (request[p] == '\n' || request[p] == '\r') p++;
        headerEnd = p;
        total = headerEnd + clipLen;
        if (total > requestLen && total <= MAX_COMMAND_LENGTH + 256) {
            unsigned long lastProgress = TickCount();
            while (requestLen < total) {
                long chunk = 0;
                OSStatus rerr = ABRecv(conn, request + requestLen,
                                            (MAX_COMMAND_LENGTH + 256) - requestLen, &chunk);
                if (rerr == noErr && chunk > 0) { requestLen += chunk; lastProgress = TickCount(); }
                else if (rerr == kABNoData) { if (TickCount() - lastProgress > 600) break; SystemTask(); }
                else break;
            }
        }
        if (clipLen > requestLen - headerEnd) clipLen = requestLen - headerEnd;
        if (clipLen < 0) clipLen = 0;
        ZeroScrap();
        serr = (OSErr)PutScrap(clipLen, 'TEXT', request + headerEnd);
        if (serr == noErr)
            strcpy(responseBuffer, "STATUS:0\rSTDOUT:7\rClipSet\rSTDERR:0\r\r");
        else
            strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:13\rPutScrap error\r\r"); NoteErr("clipboard");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* WRITEFILE: verb: receive a file (both forks + type/creator) and stream it
     * to disk. Binary-clean, length-framed, not COMMAND-wrapped. */
    if (strncmp(request, PROTO_WRITEFILE, strlen(PROTO_WRITEFILE)) == 0) {
        Boolean ok;
        SetActivity("WRITEFILE");
        ok = WriteFileVerb(conn, request, requestLen);
        gLastTX = TickCount();
        gTXCount++;
        return ok;
    }

    /* READFILE: verb: stream a file's forks back to the host. */
    if (strncmp(request, PROTO_READFILE, strlen(PROTO_READFILE)) == 0) {
        Boolean ok;
        SetActivity("READFILE");
        ok = ReadFileVerb(conn, request, requestLen);
        gLastTX = TickCount();
        gTXCount++;
        return ok;
    }

    /* LISTDIR: verb: native directory listing via PBGetCatInfo (no ToolServer). */
    if (strncmp(request, PROTO_LISTDIR, strlen(PROTO_LISTDIR)) == 0) {
        Boolean ok;
        SetActivity("LISTDIR");
        ok = ListDirVerb(conn, request, requestLen);
        gLastTX = TickCount();
        gTXCount++;
        return ok;
    }

    /* PROCLIST: verb: the running processes via GetNextProcess (no ToolServer,
     * no GUI). Checked BEFORE DISKINFO only for tidiness — the prefixes differ. */
    if (strncmp(request, PROTO_PROCLIST, strlen(PROTO_PROCLIST)) == 0) {
        Boolean ok;
        SetActivity("PROCLIST");
        ok = ProcListVerb(conn, request, requestLen);
        gLastTX = TickCount();
        gTXCount++;
        return ok;
    }

    /* DISKINFO: verb: volume totals via PBHGetVInfo (no ToolServer). */
    if (strncmp(request, PROTO_DISKINFO, strlen(PROTO_DISKINFO)) == 0) {
        Boolean ok;
        SetActivity("DISKINFO");
        ok = DiskInfoVerb(conn, request, requestLen);
        gLastTX = TickCount();
        gTXCount++;
        return ok;
    }

    /* MONITOR: verb: hide/show the Verbose window (it covers the desktop). */
    if (strncmp(request, PROTO_MONITOR, strlen(PROTO_MONITOR)) == 0) {
        Boolean ok;
        SetActivity("MONITOR");
        ok = MonitorVerb(conn, request, requestLen);
        gLastTX = TickCount();
        gTXCount++;
        return ok;
    }

    /* NBPLOOK: verb: AppleTalk name lookup (no Chooser, no ToolServer). */
    if (strncmp(request, PROTO_NBPLOOK, strlen(PROTO_NBPLOOK)) == 0) {
        Boolean ok;
        SetActivity("NBPLOOK");
        ok = NbpLookupVerb(conn, request, requestLen);
        gLastTX = TickCount();
        gTXCount++;
        return ok;
    }

    /* AFPMOUNT/AFPUNMOUNT verbs: mount an AppleShare volume with no Chooser. */
    if (strncmp(request, PROTO_AFPMOUNT, strlen(PROTO_AFPMOUNT)) == 0) {
        Boolean ok;
        SetActivity("AFPMOUNT");     /* never the arguments: they hold a password */
        ok = AfpMountVerb(conn, request, requestLen);
        gLastTX = TickCount();
        gTXCount++;
        return ok;
    }
    if (strncmp(request, PROTO_AFPUNMOUNT, strlen(PROTO_AFPUNMOUNT)) == 0) {
        Boolean ok;
        SetActivity("AFPUNMOUNT");
        ok = AfpUnmountVerb(conn, request, requestLen);
        gLastTX = TickCount();
        gTXCount++;
        return ok;
    }

    /* Parse command */
    result = ParseCommand(request, command, &commandLength);
    if (result != kBridgeNoErr) {
        strcpy(responseBuffer, "STATUS:-1\nSTDOUT:0\n\nSTDERR:21\nInvalid command format\n\n"); NoteErr("badreq");
        ABSend(conn, responseBuffer, strlen(responseBuffer));
        StatusMessage("Invalid command format");

        /* Mark TX activity */
        gLastTX = TickCount();
        gTXCount++;

        return true;
    }

    /* Top bar = the command being run; console body = "> command" (input). */
    SetActivity(command);
    {
        char m[LOG_W]; short k;
        m[0] = '>'; m[1] = ' ';
        for (k = 0; command[k] && k < LOG_W - 3; k++) m[2 + k] = command[k];
        m[2 + k] = '\0';
        AddLogLine(m, 2);   /* kind 2 = command -> shown bold in the console */
    }

    /* Execute command */
    result = ExecuteCommand(command, &cmdResult);

    /* Console body = the command's output — EVERY line, so multi-line listings
     * (Files, Catenate, ...) show in full in the Verbose window, not just the
     * first line. Capped so a huge output can't flood the rolling log; the log
     * buffer itself keeps only the last LOG_LINES anyway. */
    {
        char o[LOG_W];
        short k;
        if (cmdResult.outData && cmdResult.outLen > 0) {
            char *p = *cmdResult.outData;
            long n = cmdResult.outLen;
            long j = 0;
            short emitted = 0;
            while (j < n && emitted < CONSOLE_MAX_LINES) {
                k = 0;
                while (j < n && p[j] != '\r' && p[j] != '\n' && k < LOG_W - 1)
                    o[k++] = p[j++];
                o[k] = '\0';
                StatusMessage(o);
                emitted++;
                /* skip the line terminator (and a paired CR/LF) */
                if (j < n && (p[j] == '\r' || p[j] == '\n')) {
                    char t = p[j++];
                    if (j < n && ((t == '\r' && p[j] == '\n') ||
                                  (t == '\n' && p[j] == '\r'))) j++;
                }
            }
            if (j < n) StatusMessage("... (more output not logged)");
        } else if (cmdResult.errData[0]) {
            for (k = 0; cmdResult.errData[k] && k < LOG_W - 1; k++)
                o[k] = cmdResult.errData[k];
            o[k] = '\0';
            StatusMessage(o);
        } else {
            StatusMessage("OK");
        }
    }

    /* Stream response straight from the (possibly multi-MB) result handle. */
    err = SendCommandResult(conn, &cmdResult);
    if (cmdResult.exitCode != 0) NoteErrCode("COMMAND", cmdResult.exitCode);

    /* Mark TX activity */
    gLastTX = TickCount();
    gTXCount++;

    CleanupCommandResult(&cmdResult);

    /* A partial streamed send leaves the host counting bytes that will never
     * line up — every later command would misframe. Signal the caller to drop
     * and re-establish the link instead of limping on a desynced wire. */
    if (err != noErr) {
        StatusMessage("send failed - reconnecting");
        SetActivity("send failed - reconnecting");
        return false;
    }
    return true;
}

/* Reconnection delay in ticks (30 seconds = 1800 ticks) */
#define RECONNECT_DELAY_TICKS  1800

/* Heartbeat watchdog: declare a silent, established link dead after this long
 * with no traffic in either direction. The host PINGs every ~10 s, so 30 s is
 * three missed beats — comfortably above the interval, below a human's patience.
 * (60 ticks/sec.) */
#define HEARTBEAT_WATCHDOG_TICKS  1800

/* NET= hot-swap poll interval: how often the main loop re-reads the transport
 * pref to notice a Control-Panel radio flip / prefs edit. 300 ticks = 5 s — a
 * live switch within a few seconds, at the cost of one small prefs-file read
 * every 5 s while the daemon is otherwise idle. */
#define NET_POLL_TICKS  300

/*
 * Wait for reconnection delay, checking for user abort
 * Returns true if user aborted
 */
static Boolean WaitForReconnect(void)
{
    long startTicks = TickCount();
    long elapsed;

    SetActivity("reconnecting in 30s");

    while ((elapsed = TickCount() - startTicks) < RECONNECT_DELAY_TICKS) {
        SystemTask();
        ShowAlive();

        if (CheckUserAbort()) {
            return true;
        }

        /* Update countdown every second */
        if ((elapsed % 60) == 0) {
            long remaining = (RECONNECT_DELAY_TICKS - elapsed) / 60;
            if (remaining > 0 && (elapsed % 60) == 0) {
                /* Show countdown */
            }
        }
    }

    return false;
}

/* How often the full "host missing" help block is repeated in the Verbose log
 * while the daemon keeps retrying. One cycle is ~10 s of connect timeout + 30 s
 * of backoff, so every 8th attempt is a reminder about every 5 minutes: enough
 * that someone opening the window late still learns WHY nothing is happening,
 * without burying the log in boilerplate. */
#define HOSTHINT_REPEAT_EVERY  8

/*
 * Spell out in the Verbose console that the HOST side is missing.
 *
 * The status bar has room for one short line; the log is where actual
 * instructions fit — and the console is the only feedback channel a faceless
 * daemon has when the bridge is down (the host can't be told anything: there is
 * no link to tell it over).
 *
 * We cannot distinguish "host_server.py not running" from "wrong NIC": the
 * host's stealth firewall DROPS SYNs to a closed port instead of returning a
 * RST, so both surface here as a plain timeout. So the text names the likely
 * causes in the order they are worth checking rather than over-claiming one.
 */
/* No host address configured at all — a different situation from "the host does
 * not answer", and it must read differently. There is nothing to retry and
 * nothing to diagnose on the host side: the daemon simply has not been told
 * where to dial. Saying so beats the alternative this replaced, where a seeded
 * address made the daemon connect to whichever machine happened to hold it.
 * Repeats on the same schedule as the unreachable-host hint so a console opened
 * later still shows it, without filling the log. */
static void LogNoHostIPHint(void)
{
    static long sShown = 0;

    sShown++;
    if (sShown == 1 || (sShown % HOSTHINT_REPEAT_EVERY) == 0) {
        StatusMessage("*** NO HOST ADDRESS CONFIGURED - not dialling ***");
        StatusMessage("  the daemon has not been told where the host is, and");
        StatusMessage("  will not guess: a wrong address that answers connects");
        StatusMessage("  to the wrong machine and looks perfectly healthy.");
        StatusMessage("  set it in ONE of these, then it is picked up here:");
        StatusMessage("   1. AppleBridgeConfig");
        StatusMessage("   2. IP=<host address> in 'AppleBridge Prefs'");
        StatusMessage("      (System Folder:Preferences:, NOT the install folder)");
        StatusMessage("  the host server prints the addresses it can be dialled at.");
    }
}

static void LogHostMissingHint(long attempt, OSStatus err)
{
    char line[160];
    char *p;

    if (attempt == 1 || (attempt % HOSTHINT_REPEAT_EVERY) == 0) {
        StatusMessage("*** HOST SERVER NOT REACHABLE - bridge is DOWN ***");
        if (err == kABConnectTimeout) {
            StatusMessage("  no reply: our connect was never answered");
        } else if (err == kABConnectRefused) {
            StatusMessage("  refused: something answered but rejected us");
        } else {
            StatusMessage("  the connection attempt failed");
        }
        StatusMessage("  check on the HOST, in this order:");
        StatusMessage("   1. is host_server.py running?  host/start_stack.sh");

        p = line;
        p = StatStr(p, "   2. is ");
        p = StatStr(p, gPrefs.ip);
        p = StatStr(p, " on the default-route NIC?");
        *p = '\0';
        StatusMessage(line);

        StatusMessage("   3. emulator NIC alive? quit BasiliskII FULLY + relaunch");
        StatusMessage("  no commands can run until this link comes up.");
    }

    p = line;
    p = StatStr(p, "retry ");
    p = StatDec(p, attempt);
    p = StatStr(p, ": still no host - next attempt in 30s");
    *p = '\0';
    StatusMessage(line);
}

/*
 * NET= hot-swap. Re-read the transport pref (throttled to NET_POLL_TICKS) and,
 * if it differs from what's running, tear down the active networking stack and
 * bring up the new one — so flipping the Control-Panel radio (or editing NET= in
 * the prefs file over the bridge) switches OT / MacTCP / Serial *live*, with no
 * daemon relaunch. Drops any current link and clears *connectedP so the main
 * loop re-dials on the new backend; STAT's net= then reports it automatically
 * (it reads ABActiveTransport()). Returns true if a swap happened.
 *
 * Only the transport-selecting fields are adopted from the re-read (transport +
 * serial port/baud); IP, token, apps and home are left as loaded at startup, so
 * this is a surgical transport switch and never disturbs the rest of the config.
 * Called at the top of the main loop, i.e. only between commands — never mid-send.
 */
static long gLastNetPoll = 0;   /* TickCount of the last prefs re-read (0 => poll on first pass) */

static Boolean MaybeHotSwapTransport(ABConn **connP, Boolean *connectedP)
{
    static AppPrefs np;         /* static: avoid a ~2 KB stack frame in the hot loop */
    long now = TickCount();

    if (now - gLastNetPoll < NET_POLL_TICKS) return false;
    gLastNetPoll = now;

    PrefsDefaults(&np);
    if (!LoadPrefs(&np))              return false;   /* no file / unreadable: keep running */
    if (np.transport == gPrefs.transport) return false;   /* NET= unchanged */

    /* NET= changed -> hot-swap the stack. */
    SetActivity("NET= changed - hot-swapping transport");
    StatusMessage("NET= changed - hot-swapping transport");

    if (*connP) { ABClose(*connP); *connP = NULL; }   /* drop the current link */
    *connectedP = false;
    ABNetShutdown();                                   /* tear down the old stack */

    gPrefs.transport   = np.transport;                 /* adopt the new selection */
    gPrefs.serialPortB = np.serialPortB;
    gPrefs.serialBaud  = np.serialBaud;

    ABSerialConfig(gPrefs.serialPortB, gPrefs.serialBaud);   /* no-op unless Serial */
    if (ABNetInit(gPrefs.transport) != noErr) {
        /* The new stack couldn't come up. ABNetInit already falls back to OT for
         * a bad MacTCP; if even that failed, leave *connectedP false and let the
         * reconnect loop keep retrying rather than exit the daemon. */
        SetActivity("hot-swap: new stack init FAILED - retrying");
    }
    return true;
}

/*
 * Length-framed receive reassembly for COMMAND requests.
 *
 * TCP is a byte stream: a "COMMAND:<len>\n<payload>" can arrive split across
 * segments (a large mac_write_file is the realistic case). The old code did a
 * single OTRcv per request, so a fragmented command was parsed short — ParseCommand
 * would strncpy past the bytes actually received. This tops the buffer up to the
 * full declared length before the command is parsed.
 *
 * Safety: it only ever APPENDS bytes; it never drops or reorders. If the header
 * is incomplete, the length is invalid, or this is a (non-length-framed) verb,
 * it returns immediately and the request is handled exactly as before. When the
 * whole request already arrived in one segment (the common case) the while-loop
 * body never runs, so there is zero behaviour change on the fast path.
 */
static void TopUpCommand(ABConn *conn, char *buf, long bufSize, long *got)
{
    const char *p;
    long declared = 0;
    long headerEnd, need;
    long hdrLen = (long)strlen(PROTO_COMMAND);
    unsigned long lastProgress;

    if (*got < hdrLen || strncmp(buf, PROTO_COMMAND, hdrLen) != 0) {
        return;   /* not a COMMAND (verbs are single-recv) */
    }

    /* Parse the declared length from "COMMAND:<digits>\n", but only if the
     * whole header has arrived; otherwise let ParseCommand reject it. */
    p = buf + hdrLen;
    while (p < buf + *got && *p >= '0' && *p <= '9') {
        declared = declared * 10 + (*p - '0');
        p++;
    }
    if (p >= buf + *got) return;                 /* header digits not all here yet */
    if (*p != '\n' && *p != '\r') return;        /* malformed header */
    headerEnd = (p - buf) + 1;                   /* first payload byte */
    if (declared <= 0 || declared >= MAX_COMMAND_LENGTH) return;

    need = headerEnd + declared;                 /* bytes for a complete request */
    if (need > bufSize) need = bufSize;

    lastProgress = TickCount();
    while (*got < need) {
        long chunk = 0;
        OSStatus rerr = ABRecv(conn, buf + *got, bufSize - *got, &chunk);
        if (rerr == noErr && chunk > 0) {
            *got += chunk;
            lastProgress = TickCount();          /* progress -> reset the stall timer */
        } else if (rerr == kABNoData) {
            if (TickCount() - lastProgress > 600) break;   /* ~10 s with no more data */
            SystemTask();                        /* yield while the rest arrives */
        } else {
            break;                               /* error: process what we have */
        }
    }
}

/*
 * Main client loop
 */
int main(void)
{
    ABConn *conn = NULL;
    OSStatus err;
    char requestBuffer[MAX_COMMAND_LENGTH + 256];
    long bytesReceived;
    unsigned long hostIP;
    Boolean connected = false;
    long connectFails = 0;      /* consecutive failed dials -> drives the console hint */

    /* Initialize Mac Toolbox */
    InitApp();
    gStartTick = TickCount();   /* baseline for Alive uptime */

    /* Show the Verbose monitor window at launch. It can be closed (its close box)
     * and reopened later from the menu-bar LED ("Mitlesen") pick. */
    OpenMonitor();

    /* Cache the menu-bar LED's activity cell: the AppleBridgeMenuLED INIT (if
     * installed) registers Gestalt 'ABrg' returning the address of a system-heap
     * long. We stamp it on each RX so the menu-bar LED flashes. No extension ->
     * Gestalt fails -> gMenuLED stays NULL -> the stamp is a no-op. */
    {
        long abResp;
        if (Gestalt('ABrg', &abResp) == noErr) {
            gMenuLED = (long *) abResp;
            /* Shared block layout: [0]=gLastTick (we stamp RX), [1]=gMonReq (the
             * INIT bumps on a Mitlesen pick). Seed 'seen' from the current value
             * so a pre-existing count doesn't pop the window at launch. */
            gMonReqCell = gMenuLED + 1;
            gMonReqSeen = *gMonReqCell;
        }
    }

    /* Load config from "AppleBridge Prefs" (host IP + chain-launch apps). The
     * compiled-in fallback IP is seeded first, so a missing/corrupt file never
     * breaks connectivity; a default file is written on first run. */
    PrefsDefaults(&gPrefs);
    if (!LoadPrefs(&gPrefs)) {
        SavePrefs(&gPrefs);
    }

    /* NOT here. Opening the welcome window during startup hung the guest on the
     * boot splash: Startup Items run before the desktop is up, and a daemon that
     * stops there stops the boot with it -- measured 2026-07-29, and the resource
     * maps of the hung build and the working one were identical, so it was this
     * code path and not the build. It now runs from the event pump instead, once
     * the bridge is actually serving. That is also the honest place for it: a
     * window claiming "AppleBridge is installed and running" should appear only
     * when that sentence is demonstrably true. */

    /* Chain-launch helper apps from prefs (ToolServer first, by list order) so
     * the faceless service brings up its own dependencies — the daemon needs
     * ToolServer running to return command output. Errors are non-fatal. */
    {
        short ai;
        for (ai = 0; ai < gPrefs.appCount; ai++) {
            LaunchAppAtPath(gPrefs.apps[ai]);
        }
        /* Let a freshly-launched ToolServer register before commands arrive.
         * (The connect + first-command lag usually covers this too.) */
        if (gPrefs.appCount > 0) {
            long until = TickCount() + 180L;   /* ~3s settle */
            while (TickCount() < until) { SystemTask(); }
        }
    }

    SetActivity("init network");        /* daemon activities -> top bar */

    SystemTask();

    /* Initialize network. ABSerialConfig is a no-op unless NET=Serial. */
    ABSerialConfig(gPrefs.serialPortB, gPrefs.serialBaud);
    err = ABNetInit(gPrefs.transport);
    if (err != noErr) {
        SetActivity("network init FAILED");
        /* Faceless: no mouse to wait for. Exit; Startup Items / the watchdog
         * relaunch us, and OpenTransport is usually ready on the next try. */
        return 1;
    }

    SetActivity("network OK");

    /* Parse host IP (from prefs, or the seeded fallback) */
    hostIP = ParseIPAddress(gPrefs.ip);

    /* Main connection loop with auto-reconnect */
    while (gRunning) {
        /* Pick up a live NET= change (Control-Panel radio flip or a prefs edit
         * over the bridge) and switch OT/MacTCP/Serial without a relaunch. Runs
         * only between commands; throttled internally to one prefs read / 5 s. */
        MaybeHotSwapTransport(&conn, &connected);

        /* Connect to host if not connected */
        if (!connected) {
            /* No configured host address -> do NOT dial. There is nothing to
             * derive here: the matching value lives on the host, and a guess
             * that happens to answer connects to the wrong machine while every
             * indicator reads healthy (R2). Re-read the prefs each round so
             * setting IP= later takes effect without a relaunch. */
            if (gPrefs.ip[0] == '\0') {
                SetActivity("NO HOST IP - set IP= in AppleBridge Prefs");
                LogNoHostIPHint();
                if (WaitForReconnect()) {
                    break;              /* User aborted */
                }
                LoadPrefs(&gPrefs);
                hostIP = ParseIPAddress(gPrefs.ip);
                continue;
            }

            SetActivity("CONNECTING");
            SystemTask();

            err = ABConnect(&conn, hostIP, BRIDGE_PORT);
            if (err != noErr) {
                /* Two separate systems can't synchronise state when no link can
                 * be formed, so each can only ASSUME the other's state. Behind the
                 * host's stealth firewall a closed port returns no RST, so "server
                 * not running" and "wrong NIC" both surface as a timeout and can't
                 * be told apart here. Rather than over-claim a single cause, give
                 * the user an honest, actionable hint covering the likely fixes —
                 * a message with instructions still beats silence. */
                if (err == kABConnectTimeout) {
                    SetActivity("no host reply - server up? right NIC?");
                } else if (err == kABConnectRefused) {
                    SetActivity("host unreachable - run start_stack.sh on host?");
                } else {
                    SetActivity("connection FAILED");
                }

                /* Say it in the console too, with the actual fix steps — the
                 * one-line status bar can't carry them, and this window is the
                 * only place the user can be told while the bridge is down. */
                connectFails++;
                LogHostMissingHint(connectFails, err);

                /* Wait and retry */
                if (WaitForReconnect()) {
                    break;  /* User aborted */
                }
                continue;  /* Try again */
            }

            if (connectFails > 0) {
                StatusMessage("host server found - bridge is UP again");
                connectFails = 0;
            }

            /* Name the host we actually reached. The console said only
             * "Connecting to host..." and printed the address solely when the
             * attempt FAILED — so a healthy-looking console gave no way to tell
             * a correct host from a stranger's, which is exactly the state a
             * seeded default address produces (R4). */
            {
                char line[96];
                char *q = line;
                q = StatStr(q, "connected to ");
                q = StatStr(q, gPrefs.ip);
                q = StatStr(q, ":");
                q = StatDec(q, (long)BRIDGE_PORT);
                *q = '\0';
                StatusMessage(line);
            }

            connected = true;
            /* Fresh link -> fresh auth state: a new host must re-negotiate
             * (HELLO) and, if a token is set, re-prove (AUTH2) before commands. */
            gNeedAuth = false; gAuthed = true; gDaemonNonceHex[0] = '\0';
            /* Seed the heartbeat clock so the watchdog measures silence from NOW,
             * not from the daemon's launch (gLastRX/gLastTX start at 0). */
            gLastRX = gLastTX = TickCount();
            SetActivity("CONNECTED - waiting for commands");
        }

        SystemTask();
        PollMonitorRequest();   /* LED "Mitlesen" pick -> open the monitor window */
        ShowAlive();

        if (CheckUserAbort()) {
            SetActivity("user quit");
            break;
        }

        /* Try to receive data */
        err = ABRecv(conn, requestBuffer, sizeof(requestBuffer) - 1, &bytesReceived);

        if (err == kABNoData) {
            /* Idle. Heartbeat watchdog: the host PINGs every HEARTBEAT_INTERVAL
             * while idle, so if we've heard NOTHING (and sent nothing) for longer
             * than HEARTBEAT_WATCHDOG_TICKS the link is dead — even though no
             * FIN/RST arrived, because the MACNAT path may never deliver one.
             * Measure silence in BOTH directions (max of last RX / last TX) so a
             * long command — during which the host waits instead of pinging — is
             * not mistaken for a dead link. */
            long lastIO = (gLastRX > gLastTX) ? gLastRX : gLastTX;
            if (TickCount() - lastIO > HEARTBEAT_WATCHDOG_TICKS) {
                StatusMessage("host silent - heartbeat lost");
                SetActivity("host heartbeat lost - reconnecting");
                ABClose(conn); conn = NULL;
                connected = false;
                /* Back off like the other reconnect paths: a genuinely down
                 * host would otherwise be hammered with immediate 10 s connect
                 * spins instead of the 30 s retry cadence. */
                if (WaitForReconnect()) {
                    break;   /* user aborted */
                }
            }
            continue;
        }

        if (err != noErr) {
            SetActivity("connection lost");
        }

        if (err != noErr || bytesReceived == 0) {
            SetActivity("connection lost");

            /* Close current connection */
            ABClose(conn); conn = NULL;
            connected = false;

            /* Wait before reconnecting */
            if (WaitForReconnect()) {
                break;  /* User aborted */
            }
            continue;  /* Try to reconnect */
        }

        /* Gather a fragmented COMMAND in full before parsing (no-op for verbs
         * and single-segment commands). */
        TopUpCommand(conn, requestBuffer, sizeof(requestBuffer) - 1, &bytesReceived);

        /* Each verb handler updates the top-bar activity (SCREENSHOT / LAUNCH /
         * QUIT / the command) and the console body itself. */
        requestBuffer[bytesReceived] = '\0';
        if (!ProcessRequest(conn, requestBuffer, bytesReceived)) {
            /* A streamed response failed mid-send: the wire is desynced. Drop
             * the link and reconnect rather than misframe every later command. */
            ABClose(conn); conn = NULL;
            connected = false;
            if (WaitForReconnect()) {
                break;   /* user aborted */
            }
        }
    }

    /* Cleanup */
    if (connected) {
        ABClose(conn); conn = NULL;
    }
    ABNetShutdown();

    StatusMessage("Disconnected");

    /* Faceless: nothing to click. The loop ended because gRunning went false
     * (QUITDAEMON verb or a kAEQuitApplication), so just exit. */
    if (gLogTE) {
        TEDispose(gLogTE);
        gLogTE = NULL;
    }
    if (gStatusWindow) {
        DisposeWindow(gStatusWindow);
    }

    return 0;
}
