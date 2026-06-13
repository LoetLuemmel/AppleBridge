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

QDGlobals qd;

/* Menu IDs */
#define APPLE_MENU_ID   128
#define FILE_MENU_ID    129

/* Menu items */
#define ABOUT_ITEM      1
#define QUIT_ITEM       1

static Boolean gRunning = true;
static WindowPtr gStatusWindow = NULL;
static short gLineY = 20;
static long gTickCounter = 0;
static long gStartTick = 0;   /* daemon launch tick (for Alive uptime) */
static MenuHandle gAppleMenu;
static MenuHandle gFileMenu;

/* RX/TX Activity tracking */
static long gLastRX = 0;     /* Tick count of last receive */
static long gLastTX = 0;     /* Tick count of last transmit */
static long gRXCount = 0;    /* Total commands received */
static long gTXCount = 0;    /* Total responses sent */

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
 * Draw RX/TX LED indicators
 */
void DrawLEDs(void)
{
    Rect rxLED, txLED, statusArea;
    long now = TickCount();
    Boolean rxActive, txActive;
    char buf[64];
    Str255 pstr;
    short i;
    RGBColor rxOn  = { 0x3000, 0xFFFF, 0x3000 };  /* hellgruen */
    RGBColor rxOff = { 0x0C00, 0x4000, 0x0C00 };  /* dunkelgruen */
    RGBColor txOn  = { 0xFFFF, 0x3000, 0x3000 };  /* hellrot */
    RGBColor txOff = { 0x4000, 0x0C00, 0x0C00 };  /* dunkelrot */
    RGBColor cBlack = { 0, 0, 0 };
    RGBColor cWhite = { 0xFFFF, 0xFFFF, 0xFFFF };

    if (gStatusWindow == NULL) return;

    SetPort(gStatusWindow);
    PenNormal();

    /* Clear status area at top (white background) */
    SetRect(&statusArea, 0, 0, 400, 18);
    RGBBackColor(&cWhite);
    RGBForeColor(&cBlack);
    EraseRect(&statusArea);
    FrameRect(&statusArea);

    SetRect(&rxLED, 10, 4, 30, 14);   /* RX LED - left  */
    SetRect(&txLED, 35, 4, 55, 14);   /* TX LED - right */

    rxActive = (now - gLastRX) < LED_FLASH_DURATION;
    txActive = (now - gLastTX) < LED_FLASH_DURATION;

    /* RX LED - green (bright when active, dim otherwise) */
    RGBForeColor(rxActive ? &rxOn : &rxOff);
    PaintRect(&rxLED);
    RGBForeColor(&cBlack);
    FrameRect(&rxLED);

    /* TX LED - red */
    RGBForeColor(txActive ? &txOn : &txOff);
    PaintRect(&txLED);
    RGBForeColor(&cBlack);
    FrameRect(&txLED);

    /* Draw labels and counters (black) */
    RGBForeColor(&cBlack);
    TextSize(9);
    MoveTo(60, 12);

    /* Build string manually: "RX:n TX:n" */
    buf[0] = 'R'; buf[1] = 'X'; buf[2] = ':';
    NumToStr(gRXCount, buf + 3);
    i = strlen(buf);
    buf[i++] = ' '; buf[i++] = 'T'; buf[i++] = 'X'; buf[i++] = ':';
    NumToStr(gTXCount, buf + i);

    /* Convert to Pascal string */
    for (i = 0; buf[i] && i < 250; i++) {
        pstr[i + 1] = buf[i];
    }
    pstr[0] = i;
    DrawString(pstr);

    TextSize(12);
}

/* Simple status display in window */
void StatusMessage(const char *msg)
{
    Str255 pstr;
    short i;

    if (gStatusWindow == NULL) return;

    SetPort(gStatusWindow);

    /* Convert C string to Pascal string */
    for (i = 0; msg[i] && i < 254; i++) {
        pstr[i+1] = msg[i];
    }
    pstr[0] = i;

    MoveTo(10, gLineY);
    DrawString(pstr);
    gLineY += 15;

    /* Scroll if needed - leave room for LED area */
    if (gLineY > 280) {
        Rect contentArea;
        gLineY = 20;
        SetRect(&contentArea, 0, 18, 400, 300);
        EraseRect(&contentArea);
    }
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
        DrawString("\pAppleBridge v0.4.0");

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
        DrawString("\pRX/TX Health Monitor Edition");

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
 * Initialize Toolbox and create status window
 */
void InitApp(void)
{
    Rect bounds;

    InitGraf(&qd.thePort);
    InitFonts();
    InitWindows();
    InitMenus();
    TEInit();
    InitDialogs(NULL);
    InitCursor();

    /* Initialize menu bar */
    InitMenuBar();

    /* Create status window */
    SetRect(&bounds, 50, 50, 450, 350);
    gStatusWindow = NewCWindow(NULL, &bounds, "\pAppleBridge v0.4.0 (Verbs)",
                               true, documentProc, (WindowPtr)-1L, true, 0);
    if (gStatusWindow) {
        SetPort(gStatusWindow);
        /* Initial LED area needs space at top */
        gLineY = 20;
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

    SystemTask();

    if (GetNextEvent(everyEvent, &event)) {
        switch (event.what) {
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
                DrawLEDs();  /* Redraw LEDs on update */
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
void ProcessRequest(EndpointRef endpoint, char *request, long requestLen)
{
    char responseBuffer[MAX_RESPONSE_LENGTH];
    char command[MAX_COMMAND_LENGTH];
    long commandLength, responseLength;
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

        StatusMessage("Screenshot requested");

        result = CaptureScreenshot(&screenshot);
        if (result == kBridgeNoErr) {
            /* Overflow guard: only format/send if the raw image fits the
               response buffer; otherwise report an error instead of smashing
               memory. (Large-image chunking needs the framed protocol = later.) */
            if (screenshot.dataSize > 0 &&
                screenshot.dataSize <= MAX_RESPONSE_LENGTH - 128) {
                FormatScreenshotResponse(&screenshot, responseBuffer, &responseLength);
                SendData(endpoint, responseBuffer, responseLength);
                StatusMessage("Screenshot sent");
            } else {
                strcpy(responseBuffer, "STATUS:-1\nSTDOUT:0\n\nSTDERR:20\nScreenshot too large\n\n");
                SendData(endpoint, responseBuffer, strlen(responseBuffer));
                StatusMessage("Screenshot too large");
            }
            CleanupScreenshot(&screenshot);
        } else {
            strcpy(responseBuffer, "STATUS:-1\nSTDOUT:0\n\nSTDERR:18\nScreenshot failed\n\n");
            SendData(endpoint, responseBuffer, strlen(responseBuffer));
        }

        /* Mark TX activity */
        gLastTX = TickCount();
        gTXCount++;

        return;
    }

    /* PING verb: lightweight heartbeat (sent raw, not COMMAND-wrapped) */
    if (strncmp(request, "PING", 4) == 0) {
        strcpy(responseBuffer, "STATUS:0\rSTDOUT:4\rPONG\rSTDERR:0\r\r");
        SendData(endpoint, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return;
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

        StatusMessage("Launch requested");
        lerr = LaunchAppAtPath(launchPath);
        if (lerr == noErr) {
            strcpy(responseBuffer, "STATUS:0\rSTDOUT:8\rLaunched\rSTDERR:0\r\r");
        } else {
            strcpy(responseBuffer, "STATUS:-1\rSTDOUT:0\rSTDERR:13\rLaunch failed\r\r");
        }
        SendData(endpoint, responseBuffer, strlen(responseBuffer));
        gLastTX = TickCount();
        gTXCount++;
        return;
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

        return;
    }

    StatusMessage("Executing command...");

    /* Execute command */
    result = ExecuteCommand(command, &cmdResult);

    StatusMessage("Command executed");

    /* Format response */
    FormatResponse(&cmdResult, responseBuffer, &responseLength);

    /* Send response */
    err = SendData(endpoint, responseBuffer, responseLength);
    if (err != noErr) {
        StatusMessage("Failed to send response");
    } else {
        StatusMessage("Response sent");
    }

    /* Mark TX activity */
    gLastTX = TickCount();
    gTXCount++;

    CleanupCommandResult(&cmdResult);
}

/* Reconnection delay in ticks (30 seconds = 1800 ticks) */
#define RECONNECT_DELAY_TICKS  1800

/*
 * Wait for reconnection delay, checking for user abort
 * Returns true if user aborted
 */
static Boolean WaitForReconnect(void)
{
    long startTicks = TickCount();
    long elapsed;
    char buf[64];

    StatusMessage("Reconnecting in 30 sec...");

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

    StatusMessage("=== AppleBridge Client ===");
    StatusMessage("Version 0.4.0 + Verbs");
    StatusMessage("");
    StatusMessage("Host IP:");
    StatusMessage(hostIPStr);
    StatusMessage("");
    StatusMessage("Initializing network...");

    SystemTask();

    /* Initialize network */
    err = InitializeNetwork();
    if (err != noErr) {
        StatusMessage("Network init failed!");
        while (!Button()) { SystemTask(); ShowAlive(); }
        return 1;
    }

    StatusMessage("Network OK");

    /* Parse host IP */
    hostIP = ParseIPAddress(hostIPStr);

    /* Main connection loop with auto-reconnect */
    while (gRunning) {
        /* Connect to host if not connected */
        if (!connected) {
            StatusMessage("Connecting to host...");
            SystemTask();

            err = ConnectToHost(&endpoint, hostIP, BRIDGE_PORT);
            if (err != noErr) {
                StatusMessage("Connection failed!");

                /* Wait and retry */
                if (WaitForReconnect()) {
                    break;  /* User aborted */
                }
                continue;  /* Try again */
            }

            connected = true;
            StatusMessage("Connected!");
            StatusMessage("Waiting for commands...");
        }

        SystemTask();
        ShowAlive();

        if (CheckUserAbort()) {
            StatusMessage("User quit");
            break;
        }

        /* Try to receive data */
        err = ReceiveData(endpoint, requestBuffer, sizeof(requestBuffer) - 1, &bytesReceived);

        if (err == kOTNoDataErr) {
            /* No data yet, keep waiting */
            continue;
        }

        if (err != noErr) {
            char errMsg[80];
            strcpy(errMsg, "ReceiveData error: ");
            StatusMessage(errMsg);
            StatusMessage("Connection lost");
        }

        if (err != noErr || bytesReceived == 0) {
            StatusMessage("Connection lost");

            /* Close current connection */
            OTCloseProvider(endpoint);
            connected = false;

            /* Wait before reconnecting */
            if (WaitForReconnect()) {
                break;  /* User aborted */
            }
            continue;  /* Try to reconnect */
        }

        StatusMessage("Request received");
        ProcessRequest(endpoint, requestBuffer, bytesReceived);
    }

    /* Cleanup */
    if (connected) {
        OTCloseProvider(endpoint);
    }
    ShutdownNetwork();

    StatusMessage("Disconnected");
    StatusMessage("Click to exit...");

    while (!Button()) { SystemTask(); ShowAlive(); }

    if (gStatusWindow) {
        DisposeWindow(gStatusWindow);
    }

    return 0;
}
