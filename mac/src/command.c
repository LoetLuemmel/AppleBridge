/*
 * AppleBridge - Command Execution
 * Execute MPW shell commands via Apple Events
 */

#include <applebridge.h>
#include <mystring.h>
#include <AppleEvents.h>
#include <Processes.h>
#include <Memory.h>
#include <Gestalt.h>

/* External status function from main.c */
extern void StatusMessage(const char *msg);

/* MPW Shell signature */
#define kMPWShellCreator 'MPS '

/* ToolServer signature (alternative to MPW Shell) */
#define kToolServerCreator 'MPSX'

static Boolean gAEInitialized = false;

/*
 * Initialize Apple Events
 */
static OSErr InitAppleEvents(void)
{
    OSErr err;
    long response;

    if (gAEInitialized) return noErr;

    /* Check if Apple Events available */
    err = Gestalt(gestaltAppleEventsAttr, &response);
    if (err != noErr) {
        StatusMessage("No Apple Events!");
        return err;
    }

    if (!(response & (1 << gestaltAppleEventsPresent))) {
        StatusMessage("AE not present!");
        return -1;
    }

    gAEInitialized = true;
    return noErr;
}

/*
 * Find running application by signature
 */
static OSErr FindAppBySignature(OSType signature, ProcessSerialNumber *psn)
{
    ProcessInfoRec info;
    FSSpec appSpec;
    Str255 name;
    OSErr err;

    psn->highLongOfPSN = 0;
    psn->lowLongOfPSN = kNoProcess;

    info.processInfoLength = sizeof(ProcessInfoRec);
    info.processName = name;
    info.processAppSpec = &appSpec;

    while (GetNextProcess(psn) == noErr) {
        err = GetProcessInformation(psn, &info);
        if (err == noErr && info.processSignature == signature) {
            return noErr;
        }
    }

    return procNotFound;
}

/*
 * Send DoScript event to target app
 */
static OSErr SendDoScript(ProcessSerialNumber *psn, const char *script,
                          char *resultBuf, long resultBufSize, long *resultLen)
{
    OSErr err;
    AppleEvent event, reply;
    AEAddressDesc target;
    DescType actualType;
    Size actualSize;

    *resultLen = 0;

    /* Create target using PSN */
    err = AECreateDesc(typeProcessSerialNumber, psn, sizeof(*psn), &target);
    if (err != noErr) {
        return err;
    }

    /* Create DoScript event: 'misc'/'dosc' */
    err = AECreateAppleEvent('misc', 'dosc', &target,
                             kAutoGenerateReturnID, kAnyTransactionID, &event);
    AEDisposeDesc(&target);
    if (err != noErr) {
        return err;
    }

    /* Add script as direct parameter */
    err = AEPutParamPtr(&event, keyDirectObject, typeChar, script, strlen(script));
    if (err != noErr) {
        AEDisposeDesc(&event);
        return err;
    }

    /* Send with longer timeout, no interaction */
    err = AESend(&event, &reply,
                 kAEWaitReply | kAECanSwitchLayer,
                 kAENormalPriority,
                 kAEDefaultTimeout,
                 NULL, NULL);

    AEDisposeDesc(&event);
    if (err != noErr) {
        return err;
    }

    /* Get result */
    err = AEGetParamPtr(&reply, keyDirectObject, typeChar,
                        &actualType, resultBuf, resultBufSize - 1, &actualSize);
    if (err == noErr) {
        resultBuf[actualSize] = '\0';
        *resultLen = actualSize;
    } else if (err == errAEDescNotFound) {
        /* No result text - check for error */
        long errNum = 0;
        AEGetParamPtr(&reply, keyErrorNumber, typeLongInteger,
                      &actualType, &errNum, sizeof(errNum), &actualSize);
        if (errNum != 0) {
            AEGetParamPtr(&reply, keyErrorString, typeChar,
                          &actualType, resultBuf, resultBufSize - 1, &actualSize);
            if (actualSize > 0) {
                resultBuf[actualSize] = '\0';
                *resultLen = actualSize;
            }
        }
        err = noErr;  /* Not a fatal error */
    }

    AEDisposeDesc(&reply);
    return noErr;
}

/*
 * Execute command via Apple Events
 */
BridgeResult ExecuteCommand(const char *command, CommandResult *result)
{
    OSErr err;
    ProcessSerialNumber psn;
    long resultLen;
    char errNumStr[32];

    /* Initialize result */
    result->exitCode = 0;
    result->outData[0] = '\0';
    result->errData[0] = '\0';

    /* Initialize Apple Events */
    err = InitAppleEvents();
    if (err != noErr) {
        strcpy(result->errData, "Apple Events not available");
        result->exitCode = -1;
        return kBridgeCommandErr;
    }

    StatusMessage("Looking for MPW/ToolServer...");

    /* Try ToolServer first (better for scripting) */
    err = FindAppBySignature(kToolServerCreator, &psn);
    if (err == noErr) {
        StatusMessage("Found ToolServer");
    } else {
        /* Try MPW Shell */
        err = FindAppBySignature(kMPWShellCreator, &psn);
        if (err == noErr) {
            StatusMessage("Found MPW Shell");
        } else {
            StatusMessage("No MPW or ToolServer!");
            strcpy(result->errData, "Neither MPW Shell nor ToolServer is running");
            result->exitCode = -1;
            return kBridgeCommandErr;
        }
    }

    StatusMessage("Sending command...");

    /* Send the script */
    err = SendDoScript(&psn, command, result->outData, MAX_RESPONSE_LENGTH - 1, &resultLen);

    if (err != noErr) {
        /* Format error number */
        long e = err;
        int i = 0;

        StatusMessage("Send failed!");

        if (e < 0) {
            errNumStr[i++] = '-';
            e = -e;
        }
        do {
            errNumStr[i++] = '0' + (e % 10);
            e /= 10;
        } while (e > 0);
        errNumStr[i] = '\0';

        /* Reverse the number part */
        {
            int start = (errNumStr[0] == '-') ? 1 : 0;
            int end = i - 1;
            while (start < end) {
                char tmp = errNumStr[start];
                errNumStr[start] = errNumStr[end];
                errNumStr[end] = tmp;
                start++;
                end--;
            }
        }

        StatusMessage(errNumStr);

        strcpy(result->errData, "AE error: ");
        strcat(result->errData, errNumStr);
        result->exitCode = err;
        return kBridgeCommandErr;
    }

    StatusMessage("Command executed OK");

    return kBridgeNoErr;
}

/*
 * Clean up command result resources
 */
void CleanupCommandResult(CommandResult *result)
{
    /* Nothing to clean up */
}
