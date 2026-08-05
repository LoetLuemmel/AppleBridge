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
 *
 * waitTicks bounds how long the daemon may block, because blocking here blocks
 * the guest: with waitTicks == 0 the event goes kAENoReply and this returns as
 * soon as the Apple Event Manager has queued it, which is the correct call for
 * an event whose vocabulary declares reply 'null' (KAHL/RUN, KAHL/MAKE and most
 * of the THINK suite). Anything else waits, but only for as long as it said it
 * would — see AE_SEND_DEFAULT_TIMEOUT in applebridge.h for what that cost.
 */
static OSErr SendGenericAE(ProcessSerialNumber *psn, OSType evtClass, OSType evtID,
						   const char *directObj, long doLen, long waitTicks,
						   Handle *outH, long *outLen, Boolean *capped,
						   long *handlerErr, char *handlerMsg)
{
	OSErr err;
	AppleEvent event, reply;
	AEAddressDesc target;
	AEDesc dataDesc;
	long size;
	DescType gotType;
	Size gotSize;
	long code;

	*outH = NULL;
	*outLen = 0;
	*capped = false;
	*handlerErr = 0;
	handlerMsg[0] = '\0';

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

	if (waitTicks <= 0) {
		/* Queue it and go. No reply to harvest, so the daemon never sits in the
		 * Apple Event Manager waiting on an application it does not own. */
		err = AESend(&event, &reply, kAENoReply | kAECanSwitchLayer,
					 kAENormalPriority, kAEDefaultTimeout, NULL, NULL);
		AEDisposeDesc(&event);
		return err;
	}

	if (waitTicks > AE_SEND_MAX_TIMEOUT) waitTicks = AE_SEND_MAX_TIMEOUT;

	err = AESend(&event, &reply, kAEWaitReply | kAECanSwitchLayer,
				 kAENormalPriority, waitTicks, NULL, NULL);
	AEDisposeDesc(&event);
	if (err != noErr) return err;

	/* The reply's OWN error field, BEFORE the direct object.
	 *
	 * noErr from AESend means the event was delivered and a reply came back —
	 * NOT that the handler accepted it. A target that refuses puts its reason in
	 * keyErrorNumber, and until 0.8d45 nobody read it: the harvest below looks
	 * for keyDirectObject, falls back to '----', and on finding neither sets
	 * err = noErr with the comment "event sent, no reply parameter". So every
	 * REFUSED event reported STATUS:0 — indistinguishable from a successful one.
	 *
	 * Measured 2026-08-05 by the parallel session, which sent an event no
	 * application handles (class/ID 'ZZZZ') and got STATUS:0 back in 0.34 s. The
	 * Apple Event Manager had answered errAEEventNotHandled (-1708) in that very
	 * reply; the daemon dropped it on the floor. Same shape as menuHeight at
	 * offset 6 and the last column of PROCLIST: the answer was already in hand
	 * and simply not read.
	 *
	 * typeLongInteger and not typeShortInteger: the AEM coerces, and an app that
	 * returns a short would otherwise be read as errAECoercionFail here — which
	 * would invent a failure where the target reported none. */
	code = 0;
	if (AEGetParamPtr(&reply, keyErrorNumber, typeLongInteger, &gotType,
					  (Ptr)&code, sizeof(code), &gotSize) == noErr && code != 0) {
		*handlerErr = code;
		gotSize = 0;
		if (AEGetParamPtr(&reply, keyErrorString, typeChar, &gotType,
						  (Ptr)handlerMsg, (Size)(AE_HANDLER_MSG_MAX - 1),
						  &gotSize) != noErr)
			gotSize = 0;
		if (gotSize < 0) gotSize = 0;
		handlerMsg[gotSize] = '\0';
	}

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
							   const char *directObj, long doLen, long waitTicks,
							   CommandResult *result)
{
	OSErr err;
	ProcessSerialNumber psn;
	Handle h;
	long len;
	Boolean capped;
	long handlerErr;
	char handlerMsg[AE_HANDLER_MSG_MAX];

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

	/* A caller that did not state a bound gets the interactive one, not the
	 * five minutes 'dosc' needs — the whole point of R16's second verb. */
	if (waitTicks < 0) waitTicks = AE_SEND_DEFAULT_TIMEOUT;

	err = SendGenericAE(&psn, evtClass, evtID, directObj, doLen, waitTicks,
						&h, &len, &capped, &handlerErr, handlerMsg);
	if (err != noErr) {
		/* -1712 here is the guard doing its job, not a fault: the target did not
		 * answer inside the bound. Say which, so the caller can tell a timeout
		 * from a refusal without reading the daemon source. */
		if (err == errAETimeout) {
			char nb[16];
			IntToStr(waitTicks, nb);
			strcpy(result->errData, "AESend timed out after ");
			StrAppend(result->errData, nb);
			StrAppend(result->errData, " ticks - target did not reply");
		} else {
			strcpy(result->errData, "AESend failed");
		}
		result->exitCode = err;
		return kBridgeCommandErr;
	}

	result->outData = h;
	result->outLen = len;
	if (capped)
		strcpy(result->errData, "output capped at 4194304 bytes (4 MB daemon limit)");

	/* The target answered, and said no. Report THAT, not the delivery.
	 *
	 * The reply is kept: a handler may return both an error and output, and
	 * throwing the output away would take the only description of the failure
	 * with it. The exitCode is what a caller branches on, so it carries the
	 * target's code — a -1708 now looks like a -1708 instead of like success. */
	if (handlerErr != 0) {
		char nb[16];
		IntToStr(handlerErr, nb);
		result->exitCode = handlerErr;
		strcpy(result->errData, "target refused the event: ");
		StrAppend(result->errData, nb);
		if (handlerMsg[0] != '\0') {
			StrAppend(result->errData, " - ");
			StrAppend(result->errData, handlerMsg);
		}
		return kBridgeCommandErr;
	}
	return kBridgeNoErr;
}
