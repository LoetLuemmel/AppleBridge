/*
 * AppleBridge - Main Daemon (Client Mode)
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
static MenuHandle gAppleMenu;
static MenuHandle gFileMenu;

/*
 * HOST IP - Change this to your host's IP address!
 * Or create a file "host_ip.txt" in the same folder with the IP
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

    /* Scroll if needed */
    if (gLineY > 280) {
        gLineY = 20;
        EraseRect(&gStatusWindow->portRect);
    }
}

/* Show alive indicator */
void ShowAlive(void)
{
    Rect r;
    char buf[32];
    Str255 pstr;
    short i;
    long ticks;

    if (gStatusWindow == NULL) return;

    SetPort(gStatusWindow);

    /* Update tick counter every ~60 ticks (1 second) */
    ticks = TickCount();
    if (ticks - gTickCounter < 30) return;
    gTickCounter = ticks;

    /* Draw alive indicator at bottom */
    SetRect(&r, 10, 285, 390, 300);
    EraseRect(&r);

    /* Show tick count */
    NumToStr(ticks / 60, buf);

    pstr[0] = 0;
    for (i = 0; buf[i] && i < 250; i++) {
        pstr[i + 1] = buf[i];
    }
    pstr[0] = i;

    MoveTo(10, 295);
    DrawString("\pAlive: ");
    DrawString(pstr);
    DrawString("\p sec");
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
        DrawString("\pAppleBridge v0.2.0");

        MoveTo(20, 55);
        TextSize(10);
        TextFace(0);
        DrawString("\pBuilt by Pit with Love");

        MoveTo(20, 75);
        DrawString("\pfor 68K and Claude");

        MoveTo(20, 100);
        TextFace(italic);
        DrawString("\p\"Connecting classic Mac to the future\"");

        MoveTo(20, 130);
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
    gStatusWindow = NewWindow(NULL, &bounds, "\pAppleBridge Client",
                              true, documentProc, (WindowPtr)-1L, true, 0);
    if (gStatusWindow) {
        SetPort(gStatusWindow);
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
                EndUpdate((WindowPtr)event.message);
                break;
        }
    }

    return !gRunning;
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

    request[requestLen] = '\0';

    /* Check if it's a screenshot request */
    if (strncmp(request, PROTO_SCREENSHOT, strlen(PROTO_SCREENSHOT)) == 0) {
        ScreenshotData screenshot;

        StatusMessage("Screenshot requested");

        result = CaptureScreenshot(&screenshot);
        if (result == kBridgeNoErr) {
            FormatScreenshotResponse(&screenshot, responseBuffer, &responseLength);
            SendData(endpoint, responseBuffer, responseLength);
            CleanupScreenshot(&screenshot);
            StatusMessage("Screenshot sent");
        } else {
            strcpy(responseBuffer, "STATUS:-1\nSTDOUT:0\n\nSTDERR:18\nScreenshot failed\n\n");
            SendData(endpoint, responseBuffer, strlen(responseBuffer));
        }

        return;
    }

    /* Parse command */
    result = ParseCommand(request, command, &commandLength);
    if (result != kBridgeNoErr) {
        strcpy(responseBuffer, "STATUS:-1\nSTDOUT:0\n\nSTDERR:21\nInvalid command format\n\n");
        SendData(endpoint, responseBuffer, strlen(responseBuffer));
        StatusMessage("Invalid command format");
        return;
    }

    StatusMessage("Executing command...");

    /* Execute command */
    result = ExecuteCommand(command, &cmdResult);

    /* Format response */
    FormatResponse(&cmdResult, responseBuffer, &responseLength);

    /* Send response */
    err = SendData(endpoint, responseBuffer, responseLength);
    if (err != noErr) {
        StatusMessage("Failed to send response");
    } else {
        StatusMessage("Response sent");
    }

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
     * This is the IP of your Mac host running host_server.py
     */
    char hostIPStr[] = "192.168.3.154";  /* Host Mac IP */

    /* Initialize Mac Toolbox */
    InitApp();

    StatusMessage("=== AppleBridge Client ===");
    StatusMessage("Version 0.3.0 (Client Mode)");
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
        StatusMessage("Waiting for commands...");
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
