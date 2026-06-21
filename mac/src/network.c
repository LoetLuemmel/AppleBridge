/*
 * AppleBridge - Network Layer
 * TCP/IP communication using Open Transport
 * CLIENT MODE - connects OUT to host server
 */

#include <applebridge.h>
#include <mystring.h>
#include <Events.h>     /* TickCount, SystemTask (flow-control yield/timeout) */

static Boolean gNetworkInitialized = false;

/* External UI/status helpers from main.c (let the connect poll keep the Mac alive) */
extern void    StatusMessage(const char *msg);   /* append a line to the console log */
extern void    ShowAlive(void);                  /* repaint activity bar + uptime    */
extern Boolean CheckUserAbort(void);             /* SystemTask + pump events + quit?  */

/* Bound the connect so a stalled handshake can never wedge the cooperative
 * scheduler. 600 ticks = 10 s. */
#define CONNECT_TIMEOUT_TICKS  600

/*
 * Initialize Open Transport network stack
 */
OSStatus InitializeNetwork(void)
{
    OSStatus err;

    if (gNetworkInitialized) {
        return noErr;
    }

    err = InitOpenTransport();
    if (err != noErr) {
        return err;
    }

    gNetworkInitialized = true;

    return noErr;
}

/*
 * Shutdown Open Transport
 */
void ShutdownNetwork(void)
{
    if (gNetworkInitialized) {
        CloseOpenTransport();
        gNetworkInitialized = false;
    }
}

/*
 * Connect to host server — non-blocking + timeout-bounded.
 *
 * An OT endpoint defaults to synchronous-BLOCKING mode, where OTConnect waits
 * for the entire TCP handshake to finish. If the SYN-ACK never comes back (the
 * classic symptom: the host's .154 is on a different NIC than the one the
 * guest's MACNAT exits through), that wait spins forever WITHOUT yielding —
 * starving System 7's cooperative scheduler, so the whole emulator freezes at
 * 100% CPU and looks like a crash.
 *
 * So we flip the endpoint to non-blocking JUST for the connect, kick it off,
 * then POLL OTRcvConnect with a tick-based timeout while yielding every pass
 * (SystemTask, via CheckUserAbort). The UI stays alive, the user can quit, and
 * a dead path gives up after CONNECT_TIMEOUT_TICKS with a diagnostic hint.
 * On success we restore BLOCKING mode so the idle receive loop sleeps in OTRcv
 * (low CPU) exactly as before — only the connect changes.
 */
OSStatus ConnectToHost(EndpointRef *endpoint, unsigned long hostIP, InetPort port)
{
    OSStatus err;
    InetAddress addr;
    TCall sndCall;
    TBind bindReq;
    InetAddress localAddr;
    long startTicks;

    StatusMessage("Opening TCP endpoint...");

    /* Create TCP endpoint (synchronous, blocking by default) */
    *endpoint = OTOpenEndpoint(OTCreateConfiguration(kTCPName), 0, NULL, &err);
    if (err != noErr) {
        StatusMessage("OTOpenEndpoint failed!");
        return err;
    }

    /* Bind to any local port (quick + local; fine in default blocking mode) */
    OTMemzero(&localAddr, sizeof(localAddr));
    OTInitInetAddress(&localAddr, 0, kOTAnyInetAddress);
    bindReq.addr.buf = (UInt8 *)&localAddr;
    bindReq.addr.len = sizeof(localAddr);
    bindReq.qlen = 0;
    err = OTBind(*endpoint, &bindReq, NULL);
    if (err != noErr) {
        StatusMessage("OTBind failed!");
        OTCloseProvider(*endpoint);
        return err;
    }

    /* Non-blocking ONLY for the connect, so OTConnect can't block the Mac */
    OTSetNonBlocking(*endpoint);

    /* Initiate connect — non-blocking returns kOTNoDataErr ("in progress") */
    StatusMessage("Connecting to host...");
    OTMemzero(&addr, sizeof(addr));
    OTInitInetAddress(&addr, port, hostIP);
    OTMemzero(&sndCall, sizeof(sndCall));
    sndCall.addr.buf = (UInt8 *)&addr;
    sndCall.addr.len = sizeof(addr);

    err = OTConnect(*endpoint, &sndCall, NULL);
    if (err != noErr && err != kOTNoDataErr) {
        StatusMessage("OTConnect failed!");
        OTCloseProvider(*endpoint);
        return err;
    }

    /* Poll for completion, bounded + yielding, so a stall can't freeze us */
    startTicks = TickCount();
    for (;;) {
        if (CheckUserAbort()) {              /* yields (SystemTask) + pumps events */
            OTSndDisconnect(*endpoint, NULL);
            OTCloseProvider(*endpoint);
            return kOTCanceledErr;
        }
        ShowAlive();

        err = OTRcvConnect(*endpoint, NULL);
        if (err == noErr) {
            break;                            /* connection established */
        } else if (err == kOTNoDataErr) {
            if (TickCount() - startTicks > CONNECT_TIMEOUT_TICKS) {
                StatusMessage("connect timeout - is .154 on the default-route NIC?");
                OTSndDisconnect(*endpoint, NULL);
                OTCloseProvider(*endpoint);
                return kOTTimeOutErr;
            }
            /* still pending — keep polling */
        } else if (err == kOTLookErr) {
            OTResult ev = OTLook(*endpoint);
            if (ev == T_DISCONNECT) {         /* refused / reset by host */
                OTRcvDisconnect(*endpoint, NULL);
                StatusMessage("connection refused by host");
                OTCloseProvider(*endpoint);
                return kOTLookErr;
            }
            /* other async event — clear it by continuing to poll */
        } else {
            StatusMessage("OTRcvConnect failed!");
            OTCloseProvider(*endpoint);
            return err;
        }
    }

    /* Restore blocking so the idle receive loop sleeps in OTRcv (low CPU) */
    OTSetBlocking(*endpoint);

    StatusMessage("Connected to host!");
    return noErr;
}

/*
 * Receive data from host
 */
OSStatus ReceiveData(EndpointRef endpoint, char *buffer, long bufferSize, long *bytesReceived)
{
    OTResult result;
    OTFlags flags;

    *bytesReceived = 0;

    /* Receive data */
    result = OTRcv(endpoint, buffer, bufferSize, &flags);

    if (result < 0) {
        return result;
    }

    *bytesReceived = result;
    return noErr;
}

/* --- debug helpers (Scope-3 root-cause instrumentation) --- */
static void NetIntToStr(long num, char *str)
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

static void NetCat(char *dst, const char *src)
{
    long i = 0, j = 0;
    while (dst[i]) i++;
    while (src[j] && i < 76) dst[i++] = src[j++];
    dst[i] = '\0';
}

/*
 * Send data to host.
 *
 * Robust send: OTSnd can return kOTFlowErr ("send buffer full") on large
 * payloads — this is NOT fatal. We yield (SystemTask, so OT can drain its
 * send buffer) and retry the SAME chunk, bailing only if the link stalls
 * for ~30s (a genuinely dead connection). Real errors still bail at once.
 *
 * (The measured root cause of truncated large responses was actually
 * host-side framing — the host stopped at the first \r\r in the data. This
 * hardens the daemon against the flow-control case too, so a >OT-buffer
 * single send no longer drops the connection. Logs only on the error path,
 * to keep the daemon window quiet during normal large transfers.)
 */
OSStatus SendData(EndpointRef endpoint, const char *data, long dataSize)
{
    OTResult result;
    long totalSent = 0;
    unsigned long flowStart = 0;   /* TickCount when flow-blocked; 0 = making progress */
    char line[80], nb[16];

    while (totalSent < dataSize) {
        result = OTSnd(endpoint, (void *)(data + totalSent), dataSize - totalSent, 0);

        if (result >= 0) {
            totalSent += result;
            flowStart = 0;                 /* progress -> clear the stall timer */
        } else if (result == kOTFlowErr) {
            if (flowStart == 0) {
                flowStart = TickCount();
            } else if (TickCount() - flowStart > 1800) {   /* ~30s stalled */
                line[0] = '\0';
                NetCat(line, "OTSnd flow-timeout sent=");
                NetIntToStr(totalSent, nb); NetCat(line, nb);
                StatusMessage(line);
                return result;
            }
            SystemTask();                  /* give OT time to drain the buffer */
        } else {
            line[0] = '\0';
            NetCat(line, "OTSnd err="); NetIntToStr(result, nb); NetCat(line, nb);
            NetCat(line, " sent="); NetIntToStr(totalSent, nb); NetCat(line, nb);
            StatusMessage(line);
            return result;
        }
    }

    return noErr;
}

/*
 * Parse IP string to unsigned long (e.g., "192.168.1.100")
 */
unsigned long ParseIPAddress(const char *ipStr)
{
    unsigned long ip = 0;
    unsigned long octet = 0;
    int i;

    for (i = 0; ipStr[i]; i++) {
        if (ipStr[i] >= '0' && ipStr[i] <= '9') {
            octet = octet * 10 + (ipStr[i] - '0');
        } else if (ipStr[i] == '.') {
            ip = (ip << 8) | (octet & 0xFF);
            octet = 0;
        }
    }
    ip = (ip << 8) | (octet & 0xFF);

    return ip;
}
