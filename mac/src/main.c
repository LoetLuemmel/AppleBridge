/*
 * AppleBridge - Main Daemon (Client Mode) with RX/TX LEDs
 * Connects OUT to host server
 */

#include <applebridge.h>
#include <mystring.h>
#include <Quickdraw.h>
#include <Fonts.h>
#include <Windows.h>
#include <Events.h>
#include <Menus.h>
#include <TextEdit.h>
#include <Dialogs.h>
#include <Files.h>
#include <Processes.h>
#include <ToolUtils.h>
#include <AppleEvents.h>
#include <Gestalt.h>

QDGlobals qd;

/* Menu IDs */
#define APPLE_MENU_ID   128
#define FILE_MENU_ID    129

/* Menu items */
#define ABOUT_ITEM      1
#define QUIT_ITEM       1

static Boolean gRunning = true;
static WindowPtr gStatusWindow = NULL;
/* Scrolling log as a ring buffer of the last LOG_LINES lines, redrawn whole on
 * each message (robust: always in-window, survives redraws). Small 9pt font. */
#define LOG_LINES 18
#define LOG_W     160
static char  gLog[LOG_LINES][LOG_W];
static short gLogHead = 0;   /* next slot to write */
static short gLogN    = 0;   /* lines currently stored */
static Boolean gLogDirty = false;  /* redraw the body from ShowAlive's good
                                    * context (drawing from ProcessRequest, right
                                    * after an OT receive, doesn't render) */
static long gTickCounter = 0;
static long gStartTick = 0;   /* daemon launch tick (for Alive uptime) */
static MenuHandle gAppleMenu;
static MenuHandle gFileMenu;

/* RX/TX Activity tracking */
static long gLastRX = 0;     /* Tick count of last receive */
static long gLastTX = 0;     /* Tick count of last transmit */
static long gRXCount = 0;    /* Total commands received */
static long gTXCount = 0;    /* Total responses sent */

/* Current activity shown on the top bar next to the green "Active" LED
 * (the command/verb being processed). */
static char gActivity[256] = "ready";

/* LED flash duration in ticks (~0.66 seconds, long enough to be seen) */
#define LED_FLASH_DURATION  40

/*
 * HOST IP - Change this to your host's IP address!
 */
#define DEFAULT_HOST_IP "192.168.1.100"

/* Convert number to string */
static void NumToStr(long num, char *str)
{
    long i = 0;
    long j;
    char temp[32];

    if (num == 0) {
        str[0] = '0';
        str[1] = '\0';
        return;
    }

    while (num > 0) {
        temp[i++] = '0' + (num % 10);
        num /= 10;
    }

    for (j = 0; j < i; j++) {
        str[j] = temp[i - 1 - j];
    }
    str[i] = '\0';
}

/*
 * Top bar: one round green "Active" LED + the current activity (the command/verb
 * being processed) on a single line. RX/TX always move together, so a single
 * Active indicator says all the old two-LED pair did.
 */
void DrawLEDs(void)
{
    Rect statusArea, led;
    Str255 pstr;
    short i;
    long now;
    Boolean active;
    RGBColor ledBright = { 0x1000, 0xE000, 0x1000 };  /* bright green: data moving */
    RGBColor ledIdle   = { 0x0000, 0x5800, 0x0000 };  /* dim green: connected, idle */
    RGBColor cBlack = { 0, 0, 0 };
    RGBColor cWhite = { 0xFFFF, 0xFFFF, 0xFFFF };

    if (gStatusWindow == NULL) return;

    SetPort(gStatusWindow);
    PenNormal();

    /* Clear the top bar (white background, framed) */
    SetRect(&statusArea, 0, 0, 400, 18);
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

/* Simple status display in window */
/* Redraw the whole log body from the ring buffer (9pt, black on white). */
void RedrawLog(void)
{
    Rect body;
    Str255 pstr;
    short line, idx, k;
    RGBColor cBlack = { 0, 0, 0 };
    RGBColor cWhite = { 0xFFFF, 0xFFFF, 0xFFFF };

    if (gStatusWindow == NULL) return;
    SetPort(gStatusWindow);

    SetRect(&body, 0, 19, 400, 282);
    RGBBackColor(&cWhite);
    EraseRect(&body);
    RGBForeColor(&cBlack);
    TextSize(9);

    for (line = 0; line < gLogN; line++) {
        idx = (gLogHead - gLogN + line + 2 * LOG_LINES) % LOG_LINES;
        for (k = 0; gLog[idx][k] && k < 250; k++) pstr[k + 1] = gLog[idx][k];
        pstr[0] = (unsigned char)k;
        MoveTo(8, 30 + line * 13);
        DrawString(pstr);
    }
    TextSize(12);
}

void StatusMessage(const char *msg)
{
    short k;
    if (gStatusWindow == NULL) return;

    for (k = 0; msg[k] && k < LOG_W - 1; k++) gLog[gLogHead][k] = msg[k];
    gLog[gLogHead][k] = '\0';
    gLogHead = (short)((gLogHead + 1) % LOG_LINES);
    if (gLogN < LOG_LINES) gLogN++;

    gLogDirty = true;   /* ShowAlive redraws the body from a context that renders */
}

/* Show alive indicator with LEDs */
void ShowAlive(void)
{
    Rect r;
    char buf[32];
    Str255 pstr;
    short i;
    long ticks;

    if (gStatusWindow == NULL) return;

    SetPort(gStatusWindow);

    /* Refresh ~8x/sec so the LED flash is caught and reverts promptly */
    ticks = TickCount();
    if (ticks - gTickCounter < 8) return;
    gTickCounter = ticks;

    /* Draw LEDs at top */
    DrawLEDs();

    /* Redraw the console body if new lines arrived (from this good context) */
    if (gLogDirty) { RedrawLog(); gLogDirty = false; }

    /* Draw alive indicator at bottom */
    SetRect(&r, 10, 285, 390, 300);
    EraseRect(&r);

    /* Show DAEMON uptime broken into d / h / m / s */
    {
        long secs = (ticks - gStartTick) / 60;
        long days, hours, mins;
        char nb[16];
        short p = 0, k;

        days  = secs / 86400L; secs %= 86400L;
        hours = secs / 3600L;  secs %= 3600L;
        mins  = secs / 60L;    secs %= 60L;

        if (days > 0) {
            NumToStr(days, nb);
            for (k = 0; nb[k]; k++) buf[p++] = nb[k];
            buf[p++] = 'd'; buf[p++] = ' ';
        }
        if (days > 0 || hours > 0) {
            NumToStr(hours, nb);
            for (k = 0; nb[k]; k++) buf[p++] = nb[k];
            buf[p++] = 'h'; buf[p++] = ' ';
        }
        NumToStr(mins, nb);
        for (k = 0; nb[k]; k++) buf[p++] = nb[k];
        buf[p++] = 'm'; buf[p++] = ' ';
        NumToStr(secs, nb);
        for (k = 0; nb[k]; k++) buf[p++] = nb[k];
        buf[p++] = 's';
        buf[p] = '\0';
    }

    pstr[0] = 0;
    for (i = 0; buf[i] && i < 250; i++) {
        pstr[i + 1] = buf[i];
    }
    pstr[0] = i;

    MoveTo(10, 295);
    DrawString("\pAlive: ");
    DrawString(pstr);
}

/*
 * Show About dialog
 */
void ShowAboutBox(void)
{
    DialogPtr dialog;
    Rect bounds;

    SetRect(&bounds, 100, 80, 420, 240);
    dialog = NewDialog(NULL, &bounds, "\p", true, dBoxProc,
                       (WindowPtr)-1L, false, 0, NULL);

    if (dialog != NULL) {
        SetPort(dialog);

        MoveTo(20, 30);
        TextSize(14);
        TextFace(bold);
        DrawString("\pAppleBridge v0.6.0");

        MoveTo(20, 55);
        TextSize(10);
        TextFace(0);
        DrawString("\pBuilt by Pit with Love");

        MoveTo(20, 75);
        DrawString("\pfor 68K and Claude");

        MoveTo(20, 100);
        TextFace(italic);
        DrawString("\p\"Connecting classic Mac to the future\"");

        MoveTo(20, 120);
        TextFace(bold);
        DrawString("\pActive + Console Edition");

        MoveTo(20, 140);
        TextFace(0);
        DrawString("\pClick to close...");

        while (!Button()) {
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
                        if (window == gStatusWindow) {
                            if (TrackGoAway(window, event.where)) {
                                gRunning = false;
                            }
                        }
                        break;
                    case inContent:
                        SelectWindow(window);
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

            case updateEvt:
                BeginUpdate((WindowPtr)event.message);
                DrawLEDs();   /* top bar (activity) */
                RedrawLog();   /* console body — else updates wipe it blank */
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
 * Process a request from the host
 */
/* Returns true if the connection is still healthy, false if a response send
 * failed partway (the wire is then desynced and the caller must reconnect). */
Boolean ProcessRequest(EndpointRef endpoint, char *request, long requestLen)
{
    char responseBuffer[RESP_SCRATCH];   /* small: fixed verb/error strings only (was 64 KB on the stack) */
    char command[MAX_COMMAND_LENGTH];
    long commandLength;
    BridgeResult result;
    CommandResult cmdResult;
    OSStatus err;

    /* Mark RX activity */
    gLastRX = TickCount();
    gRXCount++;
    DrawLEDs();   /* light RX immediately */

    request[requestLen] = '\0';

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
            if (SendScreenshot(endpoint, &screenshot) != kBridgeNoErr) ok = false;
            CleanupScreenshot(&screenshot);
        } else {
            strcpy(responseBuffer, "STATUS:-1\nSTDOUT:0\n\nSTDERR:18\nScreenshot failed\n\n");
            if (SendData(endpoint, responseBuffer, strlen(responseBuffer)) != noErr) ok = false;
        }

        /* Mark TX activity */
        gLastTX = TickCount();
        gTXCount++;

        return ok;
    }

    /* PING verb: lightweight heartbeat (sent raw, not COMMAND-wrapped) */
    if (strncmp(request, "PING", 4) == 0) {
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:4\rPONG\rSTDERR:0\r\r");
        SendData(endpoint, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* QUITDAEMON verb: stop the faceless daemon over the bridge. With no window
     * or menu, this (or a system kAEQuitApplication) is how the service is
     * stopped. Ack first, then signal the main loop to exit. */
    if (strncmp(request, "QUITDAEMON", 10) == 0) {
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:7\rStopped\rSTDERR:0\r\r");
        SendData(endpoint, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        gRunning = false;   /* while(gRunning) loop exits after this request */
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
            strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:13\rLaunch failed\r\r");
            SetActivity("LAUNCH failed");
        }
        SendData(endpoint, responseBuffer, strlen(responseBuffer));
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
            strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:11\rQuit failed\r\r");
            SetActivity("QUIT no such app");
        }
        SendData(endpoint, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return true;
    }

    /* Parse command */
    result = ParseCommand(request, command, &commandLength);
    if (result != kBridgeNoErr) {
        strcpy(responseBuffer, "STATUS:-1\nSTDOUT:0\n\nSTDERR:21\nInvalid command format\n\n");
        SendData(endpoint, responseBuffer, strlen(responseBuffer));
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
        StatusMessage(m);
    }

    /* Execute command */
    result = ExecuteCommand(command, &cmdResult);

    /* Console body = the command's output (first line of STDOUT, or the error
     * diagnostics, or a short status). */
    {
        char o[LOG_W]; short k = 0;
        if (cmdResult.outData && cmdResult.outLen > 0) {
            char *p = *cmdResult.outData;
            long n = cmdResult.outLen;
            long j;
            for (j = 0; j < n && k < LOG_W - 1; j++) {
                if (p[j] == '\r' || p[j] == '\n') break;
                o[k++] = p[j];
            }
            o[k] = '\0';
            if (k == 0) { o[0]='('; o[1]='e'; o[2]='m'; o[3]='p'; o[4]='t';
                          o[5]='y'; o[6]=')'; o[7]='\0'; }
        } else if (cmdResult.errData[0]) {
            for (k = 0; cmdResult.errData[k] && k < LOG_W - 1; k++)
                o[k] = cmdResult.errData[k];
            o[k] = '\0';
        } else {
            o[0]='O'; o[1]='K'; o[2]='\0';
        }
        StatusMessage(o);
    }

    /* Stream response straight from the (possibly multi-MB) result handle. */
    err = SendCommandResult(endpoint, &cmdResult);

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

/*
 * Wait for reconnection delay, checking for user abort
 * Returns true if user aborted
 */
static Boolean WaitForReconnect(void)
{
    long startTicks = TickCount();
    long elapsed;
    char buf[64];

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
static void TopUpCommand(EndpointRef endpoint, char *buf, long bufSize, long *got)
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
        OSStatus rerr = ReceiveData(endpoint, buf + *got, bufSize - *got, &chunk);
        if (rerr == noErr && chunk > 0) {
            *got += chunk;
            lastProgress = TickCount();          /* progress -> reset the stall timer */
        } else if (rerr == kOTNoDataErr) {
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
    EndpointRef endpoint;
    OSStatus err;
    char requestBuffer[MAX_COMMAND_LENGTH + 256];
    long bytesReceived;
    unsigned long hostIP;
    Boolean connected = false;

    /*
     * SET YOUR HOST IP HERE!
     */
    char hostIPStr[] = "192.168.3.154";  /* Host Mac IP */

    /* Initialize Mac Toolbox */
    InitApp();
    gStartTick = TickCount();   /* baseline for Alive uptime */

    SetActivity("init network");        /* daemon activities -> top bar */

    SystemTask();

    /* Initialize network */
    err = InitializeNetwork();
    if (err != noErr) {
        SetActivity("network init FAILED");
        /* Faceless: no mouse to wait for. Exit; Startup Items / the watchdog
         * relaunch us, and OpenTransport is usually ready on the next try. */
        return 1;
    }

    SetActivity("network OK");

    /* Parse host IP */
    hostIP = ParseIPAddress(hostIPStr);

    /* Main connection loop with auto-reconnect */
    while (gRunning) {
        /* Connect to host if not connected */
        if (!connected) {
            SetActivity("CONNECTING");
            SystemTask();

            err = ConnectToHost(&endpoint, hostIP, BRIDGE_PORT);
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
            /* Seed the heartbeat clock so the watchdog measures silence from NOW,
             * not from the daemon's launch (gLastRX/gLastTX start at 0). */
            gLastRX = gLastTX = TickCount();
            SetActivity("CONNECTED - waiting for commands");
        }

        SystemTask();
        ShowAlive();

        if (CheckUserAbort()) {
            SetActivity("user quit");
            break;
        }

        /* Try to receive data */
        err = ReceiveData(endpoint, requestBuffer, sizeof(requestBuffer) - 1, &bytesReceived);

        if (err == kOTNoDataErr) {
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
                OTCloseProvider(endpoint);
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
            OTCloseProvider(endpoint);
            connected = false;

            /* Wait before reconnecting */
            if (WaitForReconnect()) {
                break;  /* User aborted */
            }
            continue;  /* Try to reconnect */
        }

        /* Gather a fragmented COMMAND in full before parsing (no-op for verbs
         * and single-segment commands). */
        TopUpCommand(endpoint, requestBuffer, sizeof(requestBuffer) - 1, &bytesReceived);

        /* Each verb handler updates the top-bar activity (SCREENSHOT / LAUNCH /
         * QUIT / the command) and the console body itself. */
        requestBuffer[bytesReceived] = '\0';
        if (!ProcessRequest(endpoint, requestBuffer, bytesReceived)) {
            /* A streamed response failed mid-send: the wire is desynced. Drop
             * the link and reconnect rather than misframe every later command. */
            OTCloseProvider(endpoint);
            connected = false;
            if (WaitForReconnect()) {
                break;   /* user aborted */
            }
        }
    }

    /* Cleanup */
    if (connected) {
        OTCloseProvider(endpoint);
    }
    ShutdownNetwork();

    StatusMessage("Disconnected");

    /* Faceless: nothing to click. The loop ended because gRunning went false
     * (QUITDAEMON verb or a kAEQuitApplication), so just exit. */
    if (gStatusWindow) {
        DisposeWindow(gStatusWindow);
    }

    return 0;
}
