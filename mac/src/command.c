/*
 * AppleBridge - Command Execution  (DEBUG-INSTRUMENTED)
 * Execute MPW shell commands via Apple Events.
 *
 * Debug build: every step of the ToolServer/MPW Apple-Event path is traced,
 * both to the daemon window (live, via StatusMessage) and back over the
 * bridge in STDERR (so the host sees exactly which call fails and its
 * OSErr). Used to diagnose the -903/-907 PPC errors.
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

/* Provided by main.c - writes a line to the daemon status window. */
extern void StatusMessage(const char *msg);

static Boolean gAEInitialized = false;

/* ---- tiny debug helpers ------------------------------------------------ */

static void IntToStr(long num, char *str)
{
	long i = 0, j;
	char tmp[16];
	Boolean neg = false;

	if (num < 0) { neg = true; num = -num; }
	if (num == 0) { str[0] = '0'; str[1] = '\0'; return; }
	while (num > 0) { tmp[i++] = (char)('0' + (num % 10)); num /= 10; }
	j = 0;
	if (neg) str[j++] = '-';
	while (i > 0) str[j++] = tmp[--i];
	str[j] = '\0';
}

/* Trace one "label<num>" step to the window AND append it to diag (host). */
static void Trace(char *diag, const char *label, long num)
{
	char line[80];
	char nb[16];
	long i = 0, j = 0, d;

	while (label[j] && i < 48) line[i++] = label[j++];
	IntToStr(num, nb);
	j = 0;
	while (nb[j] && i < 72) line[i++] = nb[j++];
	line[i] = '\0';

	StatusMessage(line);                 /* live, on the daemon window */

	if (diag) {                          /* accumulate for the host (STDERR) */
		d = 0;
		while (diag[d]) d++;
		j = 0;
		while (line[j] && d < 230) diag[d++] = line[j++];
		if (d < 231) diag[d++] = ' ';
		diag[d] = '\0';
	}
}

static void StrAppend(char *dst, const char *src)
{
	long i = 0, j = 0;
	while (dst[i]) i++;
	while (src[j] && i < 250) dst[i++] = src[j++];
	dst[i] = '\0';
}

/* ----------------------------------------------------------------------- */

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
 * Send DoScript event to target app (instrumented)
 */
static OSErr SendDoScript(ProcessSerialNumber *psn, const char *script,
						  char *resultBuf, long resultBufSize, long *resultLen,
						  char *diag)
{
	OSErr err;
	AppleEvent event, reply;
	AEAddressDesc target;
	DescType actualType;
	Size actualSize;

	*resultLen = 0;

	err = AECreateDesc(typeProcessSerialNumber, psn, sizeof(*psn), &target);
	Trace(diag, "createDesc=", err);
	if (err != noErr) return err;

	err = AECreateAppleEvent('misc', 'dosc', &target,
							 kAutoGenerateReturnID, kAnyTransactionID, &event);
	Trace(diag, "createAE=", err);
	AEDisposeDesc(&target);
	if (err != noErr) return err;

	err = AEPutParamPtr(&event, keyDirectObject, typeChar, script, strlen(script));
	Trace(diag, "putParam=", err);
	if (err != noErr) {
		AEDisposeDesc(&event);
		return err;
	}

	err = AESend(&event, &reply,
				 kAEWaitReply | kAECanSwitchLayer,
				 kAENormalPriority,
				 kAEDefaultTimeout,
				 NULL, NULL);
	Trace(diag, "send=", err);
	AEDisposeDesc(&event);
	if (err != noErr) return err;

	err = AEGetParamPtr(&reply, keyDirectObject, typeChar,
						&actualType, resultBuf, resultBufSize - 1, &actualSize);
	Trace(diag, "getDirect=", err);

	if (err == noErr && actualSize > 0) {
		resultBuf[actualSize] = '\0';
		*resultLen = actualSize;
	} else {
		err = AEGetParamPtr(&reply, '----', typeChar,
							&actualType, resultBuf, resultBufSize - 1, &actualSize);
		Trace(diag, "getDashes=", err);
		if (err == noErr && actualSize > 0) {
			resultBuf[actualSize] = '\0';
			*resultLen = actualSize;
		}
	}

	Trace(diag, "len=", *resultLen);
	AEDisposeDesc(&reply);
	return noErr;
}

/*
 * Execute command via Apple Events (instrumented)
 */
BridgeResult ExecuteCommand(const char *command, CommandResult *result)
{
	OSErr err;
	ProcessSerialNumber psn;
	long resultLen;
	char diag[256];
	OSType found = 0;

	result->exitCode = 0;
	result->outData[0] = '\0';
	result->errData[0] = '\0';
	diag[0] = '\0';

	err = InitAppleEvents();
	Trace(diag, "initAE=", err);
	if (err != noErr) {
		StrAppend(diag, "AE-not-available");
		strcpy(result->errData, diag);
		result->exitCode = -1;
		return kBridgeCommandErr;
	}

	/* Which target did we find? */
	err = FindAppBySignature(kToolServerCreator, &psn);
	if (err == noErr) {
		found = kToolServerCreator;
		Trace(diag, "found=TS psn=", psn.lowLongOfPSN);
	} else {
		err = FindAppBySignature(kMPWShellCreator, &psn);
		if (err == noErr) {
			found = kMPWShellCreator;
			Trace(diag, "found=MPW psn=", psn.lowLongOfPSN);
		} else {
			Trace(diag, "found=NONE ", 0);
		}
	}

	if (found == 0) {
		StrAppend(diag, "no-ToolServer/MPW");
		strcpy(result->errData, diag);
		result->exitCode = -1;
		return kBridgeCommandErr;
	}

	err = SendDoScript(&psn, command, result->outData, MAX_RESPONSE_LENGTH - 1,
					   &resultLen, diag);

	if (err != noErr) {
		/* Surface the full step trace to the host on failure. */
		strcpy(result->errData, diag);
		result->exitCode = err;
		return kBridgeCommandErr;
	}

	return kBridgeNoErr;
}

void CleanupCommandResult(CommandResult *result)
{
	/* Nothing to cleanup */
}
