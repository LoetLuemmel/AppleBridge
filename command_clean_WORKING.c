/*
 * AppleBridge - Command Execution
 * Execute MPW shell commands via Apple Events
 * Production version - CLEAN (no debug logging)
 *
 * This version works correctly on the Mac side.
 * If MPW commands return no output via MCP, the issue is in
 * MacintoshBridgeHost/LocalControlServer.swift (see FIXES_NEEDED.md)
 */

#include <applebridge.h>
#include <mystring.h>
#include <AppleEvents.h>
#include <Processes.h>
#include <Memory.h>
#include <Gestalt.h>

/* MPW Shell signature */
#define kMPWShellCreator 'MPS '

/* ToolServer signature (preferred for automation) */
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

	err = Gestalt(gestaltAppleEventsAttr, &response);
	if (err != noErr) return err;

	if (!(response & (1 << gestaltAppleEventsPresent))) {
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

	/* Create target descriptor */
	err = AECreateDesc(typeProcessSerialNumber, psn, sizeof(*psn), &target);
	if (err != noErr) return err;

	/* Create Apple Event */
	err = AECreateAppleEvent('misc', 'dosc', &target,
							 kAutoGenerateReturnID, kAnyTransactionID, &event);
	AEDisposeDesc(&target);
	if (err != noErr) return err;

	/* Add script parameter */
	err = AEPutParamPtr(&event, keyDirectObject, typeChar, script, strlen(script));
	if (err != noErr) {
		AEDisposeDesc(&event);
		return err;
	}

	/* Send the event */
	err = AESend(&event, &reply,
				 kAEWaitReply | kAECanSwitchLayer,
				 kAENormalPriority,
				 kAEDefaultTimeout,
				 NULL, NULL);
	AEDisposeDesc(&event);
	if (err != noErr) return err;

	/* Try to get result as typeChar */
	err = AEGetParamPtr(&reply, keyDirectObject, typeChar,
						&actualType, resultBuf, resultBufSize - 1, &actualSize);

	if (err == noErr && actualSize > 0) {
		resultBuf[actualSize] = '\0';
		*resultLen = actualSize;
	} else {
		/* Try alternate key */
		err = AEGetParamPtr(&reply, '----', typeChar,
							&actualType, resultBuf, resultBufSize - 1, &actualSize);
		if (err == noErr && actualSize > 0) {
			resultBuf[actualSize] = '\0';
			*resultLen = actualSize;
		}
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

	result->exitCode = 0;
	result->outData[0] = '\0';
	result->errData[0] = '\0';

	err = InitAppleEvents();
	if (err != noErr) {
		strcpy(result->errData, "Apple Events not available");
		result->exitCode = -1;
		return kBridgeCommandErr;
	}

	/* Try ToolServer first, then MPW Shell */
	err = FindAppBySignature(kToolServerCreator, &psn);
	if (err != noErr) {
		err = FindAppBySignature(kMPWShellCreator, &psn);
		if (err != noErr) {
			strcpy(result->errData, "ToolServer/MPW Shell not running");
			result->exitCode = -1;
			return kBridgeCommandErr;
		}
	}

	err = SendDoScript(&psn, command, result->outData, MAX_RESPONSE_LENGTH - 1, &resultLen);

	if (err != noErr) {
		result->exitCode = err;
		return kBridgeCommandErr;
	}

	return kBridgeNoErr;
}

void CleanupCommandResult(CommandResult *result)
{
	/* Nothing to cleanup */
}
