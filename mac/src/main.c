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
#include <Files.h>
#include <Processes.h>
#include <ToolUtils.h>
#include <AppleEvents.h>
#include <Gestalt.h>
#include <Shutdown.h>
#include <OSUtils.h>
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
 * Show About dialog (animated bridge logo: data packets crossing the bridge)
 */
void ShowAboutBox(void)
{
    DialogPtr dialog;
    Rect bounds, logo;
    short phase = 0;
    long  nextTick = 0;

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
void OpenMonitor(void)
{
    Rect r;

    if (gStatusWindow != NULL) {      /* already open: just bring it forward */
        SelectWindow(gStatusWindow);
        return;
    }

    SetRect(&r, 40, 60, 40 + 480, 60 + 340);
    gStatusWindow = NewCWindow(NULL, &r, "\pAppleBridge - Verbose", true,
                               zoomDocProc, (WindowPtr)-1L, true, 0);
    if (gStatusWindow == NULL) return;

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
                        if (window == gStatusWindow) {
                            Rect dragRect;
                            SetRect(&dragRect, 4, 24,
                                    qd.screenBits.bounds.right - 4,
                                    qd.screenBits.bounds.bottom - 4);
                            DragWindow(window, event.where, &dragRect);
                        }
                        break;
                    case inGoAway:
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
                            }
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
                DrawLEDs();   /* top bar (activity) */
                RedrawLog();   /* console body (TEUpdate) — else updates wipe it */
                if ((WindowPtr)event.message == gStatusWindow) {
                    DrawControls(gStatusWindow);   /* the scrollbar */
                    DrawGrowIcon(gStatusWindow);   /* size box / bottom frame */
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

/* Record an error: bump the counter AND remember a short identifying tag, so the
 * monitor/STAT can say WHAT failed, not just how often. Called at each error site. */
static void NoteErr(const char *tag)
{
    short i;
    gErrCount++;
    for (i = 0; tag[i] && i < (short)sizeof(gLastErr) - 1; i++) gLastErr[i] = tag[i];
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
        for (vk = 0; vk < LOG_W - 1 && request[vk] &&
                     request[vk] != '\r' && request[vk] != '\n'; vk++)
            vt[vk] = request[vk];
        vt[vk] = '\0';
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

    /* AESEND verb: send an arbitrary Apple Event and harvest its reply.
     * AESEND:<targetHex8>:<classHex8>:<idHex8>:<doLen>\n<directObjectBytes>
     * (length-framed; the reply rides the normal command response path). */
    if (strncmp(request, PROTO_AESEND, strlen(PROTO_AESEND)) == 0) {
        OSType tgt, cls, eid;
        long p = (long)strlen(PROTO_AESEND);
        long doLen = 0, headerEnd, total;
        SetActivity("AESEND");
        tgt = ParseHexType(request + p); p += 8;
        if (request[p] == ':') p++;
        cls = ParseHexType(request + p); p += 8;
        if (request[p] == ':') p++;
        eid = ParseHexType(request + p); p += 8;
        if (request[p] == ':') p++;
        while (request[p] >= '0' && request[p] <= '9') doLen = doLen * 10 + (request[p++] - '0');
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
        ExecuteAppleEvent(tgt, cls, eid, request + headerEnd, doLen, &cmdResult);
        err = SendCommandResult(conn, &cmdResult);
    if (cmdResult.exitCode != 0) NoteErr("cmd fail");
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
    if (cmdResult.exitCode != 0) NoteErr("cmd fail");
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
    if (cmdResult.exitCode != 0) NoteErr("cmd fail");

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
                    SetActivity("no host reply - server up? .154 on right NIC?");
                } else if (err == kABConnectRefused) {
                    SetActivity("host unreachable - run start_stack.sh on host?");
                } else {
                    SetActivity("connection FAILED");
                }

                /* Wait and retry */
                if (WaitForReconnect()) {
                    break;  /* User aborted */
                }
                continue;  /* Try again */
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
