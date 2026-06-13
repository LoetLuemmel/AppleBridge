/*
 * AppleBridge - Protocol Handler
 * Parse and format messages according to AppleBridge protocol
 * SIMPLIFIED VERSION - no sprintf
 */

#include <applebridge.h>
#include <mystring.h>

/* Simple number to string conversion */
static void NumToString(long num, char *str)
{
    long i = 0;
    long j;
    char temp[32];
    Boolean neg = false;

    if (num < 0) {
        neg = true;
        num = -num;
    }

    if (num == 0) {
        str[0] = '0';
        str[1] = '\0';
        return;
    }

    while (num > 0) {
        temp[i++] = '0' + (num % 10);
        num /= 10;
    }

    j = 0;
    if (neg) str[j++] = '-';

    while (i > 0) {
        str[j++] = temp[--i];
    }
    str[j] = '\0';
}

/* Simple string length as long */
static long StrLen(const char *s)
{
    long len = 0;
    while (*s++) len++;
    return len;
}

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
        return kBridgeProtocolErr;
    }

    /* Parse length - accept both \n and \r as line ending */
    ptr = request + strlen(PROTO_COMMAND);
    i = 0;
    while (*ptr != '\n' && *ptr != '\r' && *ptr != '\0' && i < sizeof(lengthStr) - 1) {
        lengthStr[i++] = *ptr++;
    }
    lengthStr[i] = '\0';

    if (*ptr != '\n' && *ptr != '\r') {
        return kBridgeProtocolErr;
    }

    /* Simple atol replacement */
    length = 0;
    for (i = 0; lengthStr[i]; i++) {
        if (lengthStr[i] >= '0' && lengthStr[i] <= '9') {
            length = length * 10 + (lengthStr[i] - '0');
        }
    }

    if (length <= 0 || length > MAX_COMMAND_LENGTH) {
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
/* Headroom reserved for STATUS/STDOUT/STDERR headers + terminators. */
#define RESP_RESERVE 256

void FormatResponse(const CommandResult *result, char *response, long *responseLength)
{
    char *ptr = response;
    char numBuf[32];
    long k;
    long outLen = StrLen(result->outData);
    long errLen = StrLen(result->errData);
    long budget = MAX_RESPONSE_LENGTH - RESP_RESERVE;

    /*
     * Overflow guard: the response buffer is MAX_RESPONSE_LENGTH bytes. If
     * stdout+stderr would not fit (e.g. a >64K DumpFile), CAP them so the
     * total fits. The DECLARED length always matches what we actually send,
     * so the host reads a valid (shortened) response instead of the daemon
     * smashing its stack. Real fix for >64K is framing/chunking (later).
     */
    if (errLen > budget) errLen = budget;
    if (outLen > budget - errLen) outLen = budget - errLen;

    /* STATUS line */
    strcpy(ptr, PROTO_STATUS);
    ptr += strlen(PROTO_STATUS);
    NumToString(result->exitCode, numBuf);
    strcpy(ptr, numBuf);
    ptr += strlen(numBuf);
    *ptr++ = '\r';

    /* STDOUT (copy exactly outLen bytes - may be capped) */
    strcpy(ptr, PROTO_STDOUT);
    ptr += strlen(PROTO_STDOUT);
    NumToString(outLen, numBuf);
    strcpy(ptr, numBuf);
    ptr += strlen(numBuf);
    *ptr++ = '\r';
    if (outLen > 0) {
        for (k = 0; k < outLen; k++) *ptr++ = result->outData[k];
        *ptr++ = '\r';
    }

    /* STDERR (copy exactly errLen bytes - may be capped) */
    strcpy(ptr, PROTO_STDERR);
    ptr += strlen(PROTO_STDERR);
    NumToString(errLen, numBuf);
    strcpy(ptr, numBuf);
    ptr += strlen(numBuf);
    *ptr++ = '\r';
    if (errLen > 0) {
        for (k = 0; k < errLen; k++) *ptr++ = result->errData[k];
        *ptr++ = '\r';
    }

    /* End marker */
    *ptr++ = '\r';
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
    char numBuf[32];
    long i;

    /* Header: IMAGE: */
    strcpy(ptr, PROTO_IMAGE);
    ptr += strlen(PROTO_IMAGE);

    /* Width */
    NumToString(screenshot->width, numBuf);
    strcpy(ptr, numBuf);
    ptr += strlen(numBuf);
    *ptr++ = ':';

    /* Height */
    NumToString(screenshot->height, numBuf);
    strcpy(ptr, numBuf);
    ptr += strlen(numBuf);
    *ptr++ = ':';

    /* Format */
    strcpy(ptr, "BMP:");
    ptr += 4;

    /* Data size */
    NumToString(screenshot->dataSize, numBuf);
    strcpy(ptr, numBuf);
    ptr += strlen(numBuf);
    *ptr++ = '\n';

    /* Binary data - copy byte by byte */
    for (i = 0; i < screenshot->dataSize; i++) {
        *ptr++ = screenshot->data[i];
    }

    *responseLength = ptr - response;
}
