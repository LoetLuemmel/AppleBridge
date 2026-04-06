/*
 * AppleBridge - Main Daemon
 * TCP server that accepts connections and processes commands
 */

#include "applebridge.h"
#include <stdio.h>
#include <string.h>
#include <console.h>

static Boolean gRunning = true;

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

    printf("=== AppleBridge Mac Daemon ===\n");
    printf("Version 0.1.0\n");
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
    printf("Press Cmd-. to stop\n\n");

    /* Main server loop */
    while (gRunning) {
        /* Accept connection */
        err = AcceptConnection(listenEndpoint, &clientEndpoint);
        if (err != noErr) {
            /* Check if user interrupted */
            if (err == kOTLookErr) {
                break;
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
