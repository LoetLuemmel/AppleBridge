/*
 * AppleBridge - Main Daemon
 * TCP server that accepts connections and processes commands
 */

#include "applebridge.h"
#include <stdio.h>
#include <string.h>
#include <console.h>
#include <Menus.h>
#include <Events.h>
#include <Dialogs.h>
#include <DiskInit.h>
#include <Windows.h>
#include <Fonts.h>
#include <QuickDraw.h>

/* Menu resource IDs */
#define APPLE_MENU_ID   128
#define FILE_MENU_ID    129

/* Menu item numbers */
#define ABOUT_ITEM      1
#define QUIT_ITEM       1

static Boolean gRunning = true;
static MenuHandle gAppleMenu;
static MenuHandle gFileMenu;

/*
 * Initialize menus
 */
void InitMenuBar(void)
{
    /* Create Apple menu */
    gAppleMenu = NewMenu(APPLE_MENU_ID, "\p\024");  /* Apple logo character */
    AppendMenu(gAppleMenu, "\pAbout AppleBridge...;(-");
    AppendResMenu(gAppleMenu, 'DRVR');  /* Add desk accessories */
    InsertMenu(gAppleMenu, 0);

    /* Create File menu */
    gFileMenu = NewMenu(FILE_MENU_ID, "\pFile");
    AppendMenu(gFileMenu, "\pQuit/Q");
    InsertMenu(gFileMenu, 0);

    DrawMenuBar();
}

/*
 * Show About dialog
 */
void ShowAboutBox(void)
{
    DialogPtr dialog;
    Rect bounds;
    short itemHit;

    /* Create a simple modal dialog */
    SetRect(&bounds, 100, 80, 420, 240);
    dialog = NewDialog(nil, &bounds, "\p", true, dBoxProc,
                       (WindowPtr)-1L, false, 0, nil);

    if (dialog != nil) {
        SetPort(dialog);

        /* Draw about text */
        MoveTo(20, 30);
        TextSize(14);
        TextFace(bold);
        DrawString("\pAppleBridge v0.2.0");

        MoveTo(20, 55);
        TextSize(10);
        TextFace(normal);
        DrawString("\pBuilt by Pit with Love");

        MoveTo(20, 75);
        DrawString("\pfor 68K and Claude");

        MoveTo(20, 100);
        TextFace(italic);
        DrawString("\p\042Connecting classic Mac to the future\042");

        MoveTo(20, 130);
        TextFace(normal);
        DrawString("\pClick to close...");

        /* Wait for click */
        while (!Button()) {
            SystemTask();
        }
        while (Button()) {}  /* Wait for release */

        DisposeDialog(dialog);
    }
}

/*
 * Handle menu selection
 */
void HandleMenuCommand(long menuResult)
{
    short menuID, menuItem;
    Str255 daName;

    menuID = HiWord(menuResult);
    menuItem = LoWord(menuResult);

    switch (menuID) {
        case APPLE_MENU_ID:
            if (menuItem == ABOUT_ITEM) {
                ShowAboutBox();
            } else {
                /* Desk accessory */
                GetMenuItemText(gAppleMenu, menuItem, daName);
                OpenDeskAcc(daName);
            }
            break;

        case FILE_MENU_ID:
            if (menuItem == QUIT_ITEM) {
                gRunning = false;
                printf("\nQuit requested from menu\n");
            }
            break;
    }

    HiliteMenu(0);  /* Unhighlight menu */
}

/*
 * Process pending events (non-blocking)
 * Returns true if Quit was requested
 */
Boolean ProcessEvents(void)
{
    EventRecord event;
    WindowPtr window;
    short part;

    /* Check for events with minimal wait */
    if (WaitNextEvent(everyEvent, &event, 1, nil)) {
        switch (event.what) {
            case mouseDown:
                part = FindWindow(event.where, &window);
                if (part == inMenuBar) {
                    HandleMenuCommand(MenuSelect(event.where));
                }
                break;

            case keyDown:
            case autoKey:
                if (event.modifiers & cmdKey) {
                    HandleMenuCommand(MenuKey(event.message & charCodeMask));
                }
                break;

            case kHighLevelEvent:
                AEProcessAppleEvent(&event);
                break;
        }
    }

    return !gRunning;
}

/*
 * Handle a single client connection
 */
void HandleClient(EndpointRef clientEndpoint)
{
    char requestBuffer[MAX_COMMAND_LENGTH + 256];
    char responseBuffer[MAX_RESPONSE_LENGTH];
    char command[MAX_COMMAND_LENGTH];
    long bytesReceived, commandLength, responseLength;
    OSStatus err;
    BridgeResult result;
    CommandResult cmdResult;

    LogMessage("Handling client connection");

    /* Receive request */
    err = ReceiveData(clientEndpoint, requestBuffer, sizeof(requestBuffer), &bytesReceived);
    if (err != noErr || bytesReceived == 0) {
        LogError("Failed to receive data", err);
        return;
    }

    requestBuffer[bytesReceived] = '\0';

    /* Check if it's a screenshot request */
    if (strncmp(requestBuffer, PROTO_SCREENSHOT, strlen(PROTO_SCREENSHOT)) == 0) {
        ScreenshotData screenshot;

        LogMessage("Screenshot requested");

        result = CaptureScreenshot(&screenshot);
        if (result == kBridgeNoErr) {
            FormatScreenshotResponse(&screenshot, responseBuffer, &responseLength);
            SendData(clientEndpoint, responseBuffer, responseLength);
            CleanupScreenshot(&screenshot);
        } else {
            strcpy(responseBuffer, "STATUS:-1\nSTDOUT:0\n\nSTDERR:18\nScreenshot failed\n\n");
            SendData(clientEndpoint, responseBuffer, strlen(responseBuffer));
        }

        return;
    }

    /* Parse command */
    result = ParseCommand(requestBuffer, command, &commandLength);
    if (result != kBridgeNoErr) {
        strcpy(responseBuffer, "STATUS:-1\nSTDOUT:0\n\nSTDERR:21\nInvalid command format\n\n");
        SendData(clientEndpoint, responseBuffer, strlen(responseBuffer));
        return;
    }

    /* Execute command */
    result = ExecuteCommand(command, &cmdResult);

    /* Format response */
    FormatResponse(&cmdResult, responseBuffer, &responseLength);

    /* Send response */
    err = SendData(clientEndpoint, responseBuffer, responseLength);
    if (err != noErr) {
        LogError("Failed to send response", err);
    }

    CleanupCommandResult(&cmdResult);

    LogMessage("Client request completed");
}

/*
 * Main server loop
 */
int main(int argc, char *argv[])
{
    EndpointRef listenEndpoint, clientEndpoint;
    OSStatus err;

    /* Initialize console for printf output */
    argc = ccommand(&argv);

    /* Initialize menu bar */
    InitMenuBar();

    printf("=== AppleBridge Mac Daemon ===\n");
    printf("Version 0.2.0\n");
    printf("Listening on port %d\n\n", BRIDGE_PORT);

    /* Initialize network */
    err = InitializeNetwork();
    if (err != noErr) {
        printf("Failed to initialize network\n");
        return 1;
    }

    /* Create listening socket */
    err = CreateListenSocket(&listenEndpoint, BRIDGE_PORT);
    if (err != noErr) {
        printf("Failed to create listen socket\n");
        ShutdownNetwork();
        return 1;
    }

    printf("Server ready. Waiting for connections...\n");
    printf("Use File > Quit or Cmd-Q to stop\n\n");

    /* Main server loop */
    while (gRunning) {
        /* Process any pending events (menus, etc.) */
        if (ProcessEvents()) {
            break;  /* Quit requested */
        }

        /* Accept connection (with short timeout for responsiveness) */
        err = AcceptConnection(listenEndpoint, &clientEndpoint);
        if (err != noErr) {
            /* Check if user interrupted or timeout */
            if (err == kOTLookErr || err == kOTNoDataErr) {
                continue;  /* No connection pending, loop again */
            }
            LogError("Failed to accept connection", err);
            continue;
        }

        /* Handle client */
        HandleClient(clientEndpoint);

        /* Close client connection */
        OTCloseProvider(clientEndpoint);
    }

    /* Cleanup */
    OTCloseProvider(listenEndpoint);
    ShutdownNetwork();

    printf("\nServer stopped\n");

    return 0;
}
