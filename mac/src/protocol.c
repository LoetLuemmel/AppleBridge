/*
 * AppleBridge - Protocol Handler
 * Parse and format messages according to AppleBridge protocol
 */

#include "applebridge.h"
#include <string.h>
#include <stdio.h>

/*
 * Parse incoming command request
 * Format: COMMAND:<length>\n<command>
 */
BridgeResult ParseCommand(const char *request, char *command, long *commandLength)
{
    const char *ptr;
    long length;
    char lengthStr[32];
    int i;

    /* Check for COMMAND: prefix */
    if (strncmp(request, PROTO_COMMAND, strlen(PROTO_COMMAND)) != 0) {
        LogMessage("Invalid command format - missing COMMAND: prefix");
        return kBridgeProtocolErr;
    }

    /* Parse length */
    ptr = request + strlen(PROTO_COMMAND);
    i = 0;
    while (*ptr != '\n' && *ptr != '\0' && i < sizeof(lengthStr) - 1) {
        lengthStr[i++] = *ptr++;
    }
    lengthStr[i] = '\0';

    if (*ptr != '\n') {
        LogMessage("Invalid command format - no newline after length");
        return kBridgeProtocolErr;
    }

    length = atol(lengthStr);
    if (length <= 0 || length > MAX_COMMAND_LENGTH) {
        LogMessage("Invalid command length");
        return kBridgeProtocolErr;
    }

    /* Extract command */
    ptr++; /* Skip newline */
    strncpy(command, ptr, length);
    command[length] = '\0';
    *commandLength = length;

    return kBridgeNoErr;
}

/*
 * Format command response
 * Format: STATUS:<exit_code>\nSTDOUT:<length>\n<output>\nSTDERR:<length>\n<errors>\n\n
 */
void FormatResponse(const CommandResult *result, char *response, long *responseLength)
{
    char *ptr = response;
    long stdoutLen = strlen(result->stdout);
    long stderrLen = strlen(result->stderr);

    /* STATUS line */
    ptr += sprintf(ptr, "%s%d\n", PROTO_STATUS, result->exitCode);

    /* STDOUT */
    ptr += sprintf(ptr, "%s%ld\n", PROTO_STDOUT, stdoutLen);
    if (stdoutLen > 0) {
        strcpy(ptr, result->stdout);
        ptr += stdoutLen;
        *ptr++ = '\n';
    }

    /* STDERR */
    ptr += sprintf(ptr, "%s%ld\n", PROTO_STDERR, stderrLen);
    if (stderrLen > 0) {
        strcpy(ptr, result->stderr);
        ptr += stderrLen;
        *ptr++ = '\n';
    }

    /* End marker */
    *ptr++ = '\n';
    *ptr = '\0';

    *responseLength = ptr - response;
}

/*
 * Format screenshot response
 * Format: IMAGE:<width>:<height>:<format>:<length>\n<binary_data>
 */
void FormatScreenshotResponse(const ScreenshotData *screenshot, char *response, long *responseLength)
{
    char *ptr = response;

    /* Header */
    ptr += sprintf(ptr, "%s%d:%d:BMP:%ld\n",
                   PROTO_IMAGE,
                   screenshot->width,
                   screenshot->height,
                   screenshot->dataSize);

    /* Binary data */
    BlockMoveData(screenshot->data, ptr, screenshot->dataSize);
    ptr += screenshot->dataSize;

    *responseLength = ptr - response;
}
