/*
 * AppleBridge - Protocol Handler
 * Parse and format messages according to AppleBridge protocol
 * SIMPLIFIED VERSION - no sprintf
 */

#include <applebridge.h>
#include <mystring.h>
#include <Memory.h>

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
 * Stream a command response to the host.
 *
 * Wire format (unchanged, so the host parser needs no changes):
 *   STATUS:<code>\r STDOUT:<outLen>\r <outLen bytes>\r STDERR:<errLen>\r <err>\r \r
 *
 * Instead of assembling everything into one buffer (which capped output at
 * 64 KB), we send the small header, then stream the stdout payload straight
 * from its Handle. SendData already loops over OTSnd and rides out
 * kOTFlowErr, so an arbitrarily large payload is chunked transparently on the
 * wire. The declared STDOUT:<outLen> always matches the bytes actually sent,
 * so the host's length-framed reader (_read_exact) stays in sync.
 *
 * Note: when outLen == 0 the data block AND its trailing \r are skipped (just
 * "STDOUT:0\r"), exactly as the old FormatResponse did, so the host's
 * _read_exact(0) + _read_until_terminator line up.
 */
BridgeResult SendCommandResult(EndpointRef endpoint, const CommandResult *result)
{
    char hdr[64];
    char *p;
    OSStatus err;
    long errLen = StrLen(result->errData);

    /* STATUS:<code>\r STDOUT:<outLen>\r */
    p = hdr;
    strcpy(p, PROTO_STATUS);   p += strlen(PROTO_STATUS);
    NumToString(result->exitCode, p);   while (*p) p++;
    *p++ = '\r';
    strcpy(p, PROTO_STDOUT);   p += strlen(PROTO_STDOUT);
    NumToString(result->outLen, p);     while (*p) p++;
    *p++ = '\r';
    err = SendData(endpoint, hdr, p - hdr);
    if (err != noErr) return kBridgeCommandErr;

    /* STDOUT payload, streamed from the handle (locked so it can't move). */
    if (result->outLen > 0 && result->outData != NULL) {
        SignedByte hstate = HGetState(result->outData);
        HLock(result->outData);
        err = SendData(endpoint, *result->outData, result->outLen);
        HSetState(result->outData, hstate);
        if (err != noErr) return kBridgeCommandErr;
        err = SendData(endpoint, "\r", 1);
        if (err != noErr) return kBridgeCommandErr;
    }

    /* STDERR:<errLen>\r <err>\r */
    p = hdr;
    strcpy(p, PROTO_STDERR);   p += strlen(PROTO_STDERR);
    NumToString(errLen, p);    while (*p) p++;
    *p++ = '\r';
    err = SendData(endpoint, hdr, p - hdr);
    if (err != noErr) return kBridgeCommandErr;
    if (errLen > 0) {
        err = SendData(endpoint, result->errData, errLen);
        if (err != noErr) return kBridgeCommandErr;
        err = SendData(endpoint, "\r", 1);
        if (err != noErr) return kBridgeCommandErr;
    }

    /* End marker */
    err = SendData(endpoint, "\r", 1);
    if (err != noErr) return kBridgeCommandErr;

    return kBridgeNoErr;
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
