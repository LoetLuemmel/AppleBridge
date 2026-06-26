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
extern void    SetActivity(const char *msg);     /* top-bar text; repaints IMMEDIATELY
                                                  * (so a stage marker survives a freeze
                                                  * and pinpoints which call blocked)   */

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

/* Connect-completion outcome, recorded by the async notifier below and read by
 * the ConnectToHost poll loop. `volatile` because it is written from OT's
 * notification context, not the main flow. We track only the two terminal
 * connect events; everything else is ignored. */
static volatile OTEventCode gConnectEvent = 0;

/*
 * OT notifier for the connect phase. Runs at deferred-task/interrupt time, so it
 * does the absolute minimum — just records the connect outcome. The poll loop in
 * ConnectToHost does the real work (OTRcvConnect, mode switch, teardown). On 68K
 * OTNotifyProcPtr "is never a UniversalProcPtr", so this plain pascal function is
 * passed straight to OTInstallNotifier.
 */
static pascal void ConnectNotifier(void *contextPtr, OTEventCode code,
                                   OTResult result, void *cookie)
{
    if (contextPtr || result || cookie) { /* silence unused-param warnings */ }
    if (code == T_CONNECT || code == T_DISCONNECT) {
        gConnectEvent = code;
    }
}

/*
 * Connect to host server — asynchronous + timeout-bounded.
 *
 * Subtlety that bit us twice: OT has two ORTHOGONAL mode axes. Blocking vs
 * non-blocking governs FLOW CONTROL (OTSnd/OTRcv); synchronous vs asynchronous
 * governs whether a call WAITS FOR COMPLETION. A *synchronous* OTConnect waits
 * for the whole handshake regardless of the blocking flag — so if the SYN-ACK
 * never comes back (host unreachable, or the host's stealth firewall silently
 * drops the SYN to a closed port) it blocks until the host returns, freezing the
 * cooperative scheduler. The earlier "non-blocking + poll" design only seemed to
 * work because the local .154 connect completes instantly; an unreachable host
 * exposed the block (v0.5.6 heartbeat test: 'Alive' froze on 'connect: OTConnect').
 *
 * So we put the endpoint in ASYNCHRONOUS mode with a notifier: OTConnect returns
 * kOTNoDataErr immediately, the notifier records T_CONNECT/T_DISCONNECT, and we
 * POLL that flag with a tick-based timeout while yielding every pass (via
 * CheckUserAbort/ShowAlive). The UI stays alive, the user can quit, a dead path
 * gives up after CONNECT_TIMEOUT_TICKS. On success we complete OTRcvConnect and
 * return the endpoint to synchronous + NON-blocking, which is what the receive
 * loop expects (it polls OTRcv, kOTNoDataErr when idle, and yields) — do NOT
 * leave it blocking, that starves the cooperative scheduler.
 */
OSStatus ConnectToHost(EndpointRef *endpoint, unsigned long hostIP, InetPort port)
{
    OSStatus err;
    InetAddress addr;
    TCall sndCall;
    TBind bindReq;
    InetAddress localAddr;
    long startTicks;

    /* Stage markers: SetActivity repaints the top bar IMMEDIATELY, so if any of
     * the synchronous OT calls below wedges the cooperative scheduler, the frozen
     * bar names the exact culprit (the console log only repaints from ShowAlive,
     * which a blocked call never reaches). */
    SetActivity("connect: open endpoint");
    StatusMessage("Opening TCP endpoint...");

    /* Create TCP endpoint (synchronous, blocking by default) */
    *endpoint = OTOpenEndpoint(OTCreateConfiguration(kTCPName), 0, NULL, &err);
    if (err != noErr) {
        StatusMessage("OTOpenEndpoint failed!");
        return err;
    }

    /* Bind to any local port (quick + local; fine in default blocking mode — a
     * non-blocking bind could return kOTNoDataErr and need its own poll). */
    SetActivity("connect: bind");
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

    /* THE FIX (v0.5.6 heartbeat test froze here on 'connect: OTConnect'):
     * the blocking/non-blocking flag governs FLOW CONTROL, not connection setup —
     * a SYNCHRONOUS OTConnect waits for the whole handshake, so an unreachable
     * host blocks it until the host returns, freezing the cooperative scheduler.
     * In ASYNCHRONOUS mode OTConnect returns kOTNoDataErr at once and the outcome
     * arrives via the notifier, so the yielding poll loop below keeps the Mac
     * alive and can time out. (Verified: async OTConnect returns immediately —
     * Inside Macintosh: Networking With Open Transport.) */
    SetActivity("connect: async + notifier");
    gConnectEvent = 0;
    err = OTInstallNotifier(*endpoint, ConnectNotifier, NULL);
    if (err != noErr) {
        StatusMessage("OTInstallNotifier failed!");
        OTCloseProvider(*endpoint);
        return err;
    }
    err = OTSetAsynchronous(*endpoint);
    if (err != noErr) {
        StatusMessage("OTSetAsynchronous failed!");
        OTRemoveNotifier(*endpoint);
        OTCloseProvider(*endpoint);
        return err;
    }

    /* Initiate connect — async returns kOTNoDataErr ("in progress") immediately */
    StatusMessage("Connecting to host...");
    OTMemzero(&addr, sizeof(addr));
    OTInitInetAddress(&addr, port, hostIP);
    OTMemzero(&sndCall, sizeof(sndCall));
    sndCall.addr.buf = (UInt8 *)&addr;
    sndCall.addr.len = sizeof(addr);

    SetActivity("connect: OTConnect");
    err = OTConnect(*endpoint, &sndCall, NULL);
    if (err != noErr && err != kOTNoDataErr) {
        /* Immediate failure — e.g. no route to the host address (.154 not set up). */
        StatusMessage("OTConnect failed - host reachable?");
        OTRemoveNotifier(*endpoint);
        OTCloseProvider(*endpoint);
        return kABConnectRefused;
    }

    /* In progress. Wait for the notifier to record the outcome, bounded by a
     * timeout and yielding each pass so a dead host can never freeze the Mac. */
    SetActivity("connect: polling (yields)");
    startTicks = TickCount();
    for (;;) {
        if (CheckUserAbort()) {              /* yields (SystemTask) + pumps events */
            OTSndDisconnect(*endpoint, NULL);
            OTRemoveNotifier(*endpoint);
            OTCloseProvider(*endpoint);
            return kOTCanceledErr;
        }
        ShowAlive();

        if (gConnectEvent == T_CONNECT) {
            break;                            /* connection established */
        } else if (gConnectEvent == T_DISCONNECT) {
            OTRcvDisconnect(*endpoint, NULL); /* refused / reset by host */
            StatusMessage("host refused - is AppleBridge running?");
            OTRemoveNotifier(*endpoint);
            OTCloseProvider(*endpoint);
            return kABConnectRefused;
        }

        /* nothing decisive yet — give up after the timeout */
        if (TickCount() - startTicks > CONNECT_TIMEOUT_TICKS) {
            StatusMessage("connect timeout - no reply from host");
            OTSndDisconnect(*endpoint, NULL);
            OTRemoveNotifier(*endpoint);
            OTCloseProvider(*endpoint);
            return kABConnectTimeout;
        }
    }

    /* Connected. T_CONNECT is pending, so complete the handshake retrieval
     * SYNCHRONOUSLY (returns at once), then return the endpoint to the
     * synchronous + NON-blocking mode the receive loop expects (it polls OTRcv
     * and yields). Drop the notifier — the session uses sync polling, not events. */
    SetActivity("connect: finishing");
    OTSetSynchronous(*endpoint);
    err = OTRcvConnect(*endpoint, NULL);
    if (err != noErr) {
        StatusMessage("OTRcvConnect failed!");
        OTRemoveNotifier(*endpoint);
        OTCloseProvider(*endpoint);
        return err;
    }
    OTRemoveNotifier(*endpoint);
    OTSetNonBlocking(*endpoint);

    /* Connected and the async->sync mode handover completed cleanly. */
    StatusMessage("SYNC-OK");
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
