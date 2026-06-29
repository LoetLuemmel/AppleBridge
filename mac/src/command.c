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

/* Provided by main.c - writes a line to the daemon status window. StatusDetail
 * marks the line as collapsible AE-trace detail (hidden unless "Show details"). */
extern void StatusMessage(const char *msg);
extern void StatusDetail(const char *msg);

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

	StatusDetail(line);                  /* live, on the daemon window (collapsible) */

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
 * Is an application with the given creator signature currently running?
 * Used by the STAT verb to report whether ToolServer ('MPSX') is alive.
 */
Boolean IsAppRunning(OSType signature)
{
	ProcessSerialNumber psn;
	return FindAppBySignature(signature, &psn) == noErr;
}

/*
 * Send a Quit Apple Event (kCoreEventClass / kAEQuitApplication) to the running
 * application with the given creator signature. Lets the QUIT: verb stop a
 * launched GUI app (e.g. a game build) over the bridge — no manual quit needed.
 * Returns procNotFound if no such app is running.
 */
OSErr QuitAppBySignature(OSType signature)
{
	OSErr err;
	ProcessSerialNumber psn;
	AppleEvent event, reply;
	AEAddressDesc target;

	if (InitAppleEvents() != noErr) return -1;

	err = FindAppBySignature(signature, &psn);
	if (err != noErr) return err;          /* not running */

	err = AECreateDesc(typeProcessSerialNumber, &psn, sizeof(psn), &target);
	if (err != noErr) return err;

	err = AECreateAppleEvent(kCoreEventClass, kAEQuitApplication, &target,
							 kAutoGenerateReturnID, kAnyTransactionID, &event);
	AEDisposeDesc(&target);
	if (err != noErr) return err;

	err = AESend(&event, &reply, kAENoReply | kAECanSwitchLayer,
				 kAENormalPriority, kAEDefaultTimeout, NULL, NULL);
	AEDisposeDesc(&event);
	return err;
}

/*
 * Send DoScript event to target app (instrumented).
 *
 * The reply data is extracted into a dynamically sized Handle (outH) instead
 * of a fixed 64 KB buffer: AEGetParamDesc hands back a copy of the parameter
 * data AS a Handle, and we STEAL that handle (no extra NewHandle/BlockMove) so
 * it survives after the reply is disposed. Output larger than
 * MAX_DYNAMIC_RESPONSE is shrunk in place and *capped is set. A genuine
 * extraction failure (e.g. memFullErr on a huge reply) is surfaced as the
 * return code; "no output parameter" is not an error (outLen stays 0).
 */
static OSErr SendDoScript(ProcessSerialNumber *psn, const char *script,
						  Handle *outH, long *outLen, Boolean *capped,
						  char *diag)
{
	OSErr err;
	AppleEvent event, reply;
	AEAddressDesc target;
	AEDesc dataDesc;
	long size;

	*outH = NULL;
	*outLen = 0;
	*capped = false;

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

	/* AE_SCRIPT_TIMEOUT (~5 min) not kAEDefaultTimeout (~60 s): long Link/SC
	 * builds blew past the default and returned -1712 even though the build
	 * had completed, forcing "verify by the artifact, not the status". The
	 * generous bounded timeout lets the daemon report the real exit code for
	 * builds up to a few minutes, while still bailing if ToolServer wedges. */
	err = AESend(&event, &reply,
				 kAEWaitReply | kAECanSwitchLayer,
				 kAENormalPriority,
				 AE_SCRIPT_TIMEOUT,
				 NULL, NULL);
	Trace(diag, "send=", err);
	AEDisposeDesc(&event);
	if (err != noErr) return err;

	/* Grab the reply data as a coerced typeChar descriptor. Try keyDirectObject
	 * first; only fall back to '----' if the parameter is genuinely absent. */
	dataDesc.descriptorType = typeNull;
	dataDesc.dataHandle = NULL;
	err = AEGetParamDesc(&reply, keyDirectObject, typeChar, &dataDesc);
	if (err == errAEDescNotFound) {
		err = AEGetParamDesc(&reply, '----', typeChar, &dataDesc);
	}
	Trace(diag, "getDesc=", err);

	if (err == noErr && dataDesc.dataHandle != NULL) {
		size = GetHandleSize(dataDesc.dataHandle);
		Trace(diag, "rsize=", size);
		if (size > MAX_DYNAMIC_RESPONSE) {
			SetHandleSize(dataDesc.dataHandle, MAX_DYNAMIC_RESPONSE);
			size = MAX_DYNAMIC_RESPONSE;
			*capped = true;
		}
		*outH = dataDesc.dataHandle;   /* steal: keep it past AEDisposeDesc */
		*outLen = size;
		dataDesc.dataHandle = NULL;    /* so AEDisposeDesc won't free it */
		AEDisposeDesc(&dataDesc);      /* no-op now */
	} else if (err == errAEDescNotFound) {
		err = noErr;                   /* command ran but produced no output */
	} else {
		/* Real failure (e.g. memFullErr extracting a huge reply). Surface it. */
		Trace(diag, "getDesc-fail=", err);
		AEDisposeDesc(&dataDesc);
		AEDisposeDesc(&reply);
		return err;
	}

	Trace(diag, "len=", *outLen);
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
	Handle cmdHandle;
	long cmdLen;
	Boolean capped;
	char diag[256];
	OSType found = 0;

	result->exitCode = 0;
	result->outData = NULL;
	result->outLen = 0;
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

	err = SendDoScript(&psn, command, &cmdHandle, &cmdLen, &capped, diag);

	if (err != noErr) {
		/* Surface the full step trace to the host on failure. */
		strcpy(result->errData, diag);
		result->exitCode = err;
		return kBridgeCommandErr;
	}

	result->outData = cmdHandle;
	result->outLen  = cmdLen;
	if (capped) {
		strcpy(result->errData,
			   "output capped at 4194304 bytes (4 MB daemon limit)");
	}

	return kBridgeNoErr;
}

void CleanupCommandResult(CommandResult *result)
{
	if (result->outData != NULL) {
		DisposeHandle(result->outData);
		result->outData = NULL;
	}
	result->outLen = 0;
}

/*
 * Send an ARBITRARY Apple Event (any class/ID) with an optional text direct
 * object to a process, harvesting the reply text — the generalisation of
 * SendDoScript behind mac_send_apple_event. Same reply-stealing Handle logic.
 */
static OSErr SendGenericAE(ProcessSerialNumber *psn, OSType evtClass, OSType evtID,
						   const char *directObj, long doLen,
						   Handle *outH, long *outLen, Boolean *capped)
{
	OSErr err;
	AppleEvent event, reply;
	AEAddressDesc target;
	AEDesc dataDesc;
	long size;

	*outH = NULL;
	*outLen = 0;
	*capped = false;

	err = AECreateDesc(typeProcessSerialNumber, psn, sizeof(*psn), &target);
	if (err != noErr) return err;

	err = AECreateAppleEvent(evtClass, evtID, &target,
							 kAutoGenerateReturnID, kAnyTransactionID, &event);
	AEDisposeDesc(&target);
	if (err != noErr) return err;

	if (directObj != NULL && doLen > 0) {
		err = AEPutParamPtr(&event, keyDirectObject, typeChar, directObj, doLen);
		if (err != noErr) { AEDisposeDesc(&event); return err; }
	}

	err = AESend(&event, &reply, kAEWaitReply | kAECanSwitchLayer,
				 kAENormalPriority, AE_SCRIPT_TIMEOUT, NULL, NULL);
	AEDisposeDesc(&event);
	if (err != noErr) return err;

	dataDesc.descriptorType = typeNull;
	dataDesc.dataHandle = NULL;
	err = AEGetParamDesc(&reply, keyDirectObject, typeChar, &dataDesc);
	if (err == errAEDescNotFound)
		err = AEGetParamDesc(&reply, '----', typeChar, &dataDesc);

	if (err == noErr && dataDesc.dataHandle != NULL) {
		size = GetHandleSize(dataDesc.dataHandle);
		if (size > MAX_DYNAMIC_RESPONSE) {
			SetHandleSize(dataDesc.dataHandle, MAX_DYNAMIC_RESPONSE);
			size = MAX_DYNAMIC_RESPONSE;
			*capped = true;
		}
		*outH = dataDesc.dataHandle;   /* steal so it survives AEDisposeDesc */
		*outLen = size;
		dataDesc.dataHandle = NULL;
		AEDisposeDesc(&dataDesc);
	} else if (err == errAEDescNotFound) {
		err = noErr;                   /* event sent, no reply parameter */
	} else {
		AEDisposeDesc(&dataDesc);
		AEDisposeDesc(&reply);
		return err;
	}

	AEDisposeDesc(&reply);
	return noErr;
}

/*
 * Send an arbitrary Apple Event to the app with the given creator signature and
 * return its reply text as a CommandResult — reusing the command response path.
 */
BridgeResult ExecuteAppleEvent(OSType targetSig, OSType evtClass, OSType evtID,
							   const char *directObj, long doLen, CommandResult *result)
{
	OSErr err;
	ProcessSerialNumber psn;
	Handle h;
	long len;
	Boolean capped;

	result->exitCode = 0;
	result->outData = NULL;
	result->outLen = 0;
	result->errData[0] = '\0';

	if (InitAppleEvents() != noErr) {
		strcpy(result->errData, "AE-not-available");
		result->exitCode = -1;
		return kBridgeCommandErr;
	}

	err = FindAppBySignature(targetSig, &psn);
	if (err != noErr) {
		strcpy(result->errData, "target app not running");
		result->exitCode = err;
		return kBridgeCommandErr;
	}

	err = SendGenericAE(&psn, evtClass, evtID, directObj, doLen, &h, &len, &capped);
	if (err != noErr) {
		strcpy(result->errData, "AESend failed");
		result->exitCode = err;
		return kBridgeCommandErr;
	}

	result->outData = h;
	result->outLen = len;
	if (capped)
		strcpy(result->errData, "output capped at 4194304 bytes (4 MB daemon limit)");
	return kBridgeNoErr;
}
