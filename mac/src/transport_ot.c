/*
 * AppleBridge - Open Transport backend (transport seam)
 *
 * The original network layer, now one backend behind transport.h. Same logic as
 * the old network.c: asynchronous, timeout-bounded OTConnect (so an unreachable
 * host can never freeze the cooperative scheduler), kOTFlowErr-tolerant send,
 * non-blocking poll receive. The only seam change: it operates on an opaque
 * ABConn (its EndpointRef lives in c->ep) and maps OT's kOTNoDataErr to the
 * neutral kABNoData sentinel so nothing above the seam sees an OT type.
 */

#include <transport.h>
#include <transport_priv.h>
#include <applebridge.h>   /* kABConnectTimeout / kABConnectRefused */
#include <OpenTransport.h>
#include <OpenTptInternet.h>
#include <Events.h>        /* TickCount, SystemTask */

static Boolean gNetworkInitialized = false;

/* Bound the connect so a stalled handshake can never wedge the cooperative
 * scheduler. 600 ticks = 10 s. */
#define CONNECT_TIMEOUT_TICKS  600

OSStatus ot_Init(void)
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

void ot_Shutdown(void)
{
    if (gNetworkInitialized) {
        CloseOpenTransport();
        gNetworkInitialized = false;
    }
}

/* Connect-completion outcome, recorded by the async notifier and read by the
 * ot_Connect poll loop. `volatile` because it is written from OT's notification
 * context, not the main flow. We track only the two terminal connect events. */
static volatile OTEventCode gConnectEvent = 0;

/*
 * OT notifier for the connect phase. Runs at deferred-task/interrupt time, so it
 * does the absolute minimum — just records the connect outcome. The poll loop in
 * ot_Connect does the real work (OTRcvConnect, mode switch, teardown).
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
 * never comes back (host unreachable, or a stealth firewall silently drops the
 * SYN to a closed port) it blocks until the host returns, freezing the
 * cooperative scheduler.
 *
 * So we put the endpoint in ASYNCHRONOUS mode with a notifier: OTConnect returns
 * kOTNoDataErr immediately, the notifier records T_CONNECT/T_DISCONNECT, and we
 * POLL that flag with a tick-based timeout while yielding every pass. On success
 * we complete OTRcvConnect and return the endpoint to synchronous + NON-blocking,
 * which is what the receive loop expects.
 */
OSStatus ot_Connect(ABConn *c, unsigned long hostIP, unsigned short port)
{
    OSStatus    err;
    EndpointRef ep;
    InetAddress addr;
    TCall       sndCall;
    TBind       bindReq;
    InetAddress localAddr;
    long        startTicks;

    /* Stage markers: SetActivity repaints the top bar IMMEDIATELY, so if any of
     * the synchronous OT calls below wedges the scheduler, the frozen bar names
     * the exact culprit. */
    SetActivity("connect: open endpoint");
    StatusMessage("Opening TCP endpoint...");

    /* Create TCP endpoint (synchronous, blocking by default) */
    ep = OTOpenEndpoint(OTCreateConfiguration(kTCPName), 0, NULL, &err);
    if (err != noErr) {
        StatusMessage("OTOpenEndpoint failed!");
        return err;
    }

    /* Bind to any local port. */
    SetActivity("connect: bind");
    OTMemzero(&localAddr, sizeof(localAddr));
    OTInitInetAddress(&localAddr, 0, kOTAnyInetAddress);
    bindReq.addr.buf = (UInt8 *)&localAddr;
    bindReq.addr.len = sizeof(localAddr);
    bindReq.qlen = 0;
    err = OTBind(ep, &bindReq, NULL);
    if (err != noErr) {
        StatusMessage("OTBind failed!");
        OTCloseProvider(ep);
        return err;
    }

    /* Asynchronous mode + notifier so OTConnect can't block the scheduler. */
    SetActivity("connect: async + notifier");
    gConnectEvent = 0;
    err = OTInstallNotifier(ep, ConnectNotifier, NULL);
    if (err != noErr) {
        StatusMessage("OTInstallNotifier failed!");
        OTCloseProvider(ep);
        return err;
    }
    err = OTSetAsynchronous(ep);
    if (err != noErr) {
        StatusMessage("OTSetAsynchronous failed!");
        OTRemoveNotifier(ep);
        OTCloseProvider(ep);
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
    err = OTConnect(ep, &sndCall, NULL);
    if (err != noErr && err != kOTNoDataErr) {
        StatusMessage("OTConnect failed - host reachable?");
        OTRemoveNotifier(ep);
        OTCloseProvider(ep);
        return kABConnectRefused;
    }

    /* In progress. Wait for the notifier, bounded by a timeout and yielding each
     * pass so a dead host can never freeze the Mac. */
    SetActivity("connect: polling (yields)");
    startTicks = TickCount();
    for (;;) {
        if (CheckUserAbort()) {              /* yields (SystemTask) + pumps events */
            OTSndDisconnect(ep, NULL);
            OTRemoveNotifier(ep);
            OTCloseProvider(ep);
            return kOTCanceledErr;
        }
        ShowAlive();

        if (gConnectEvent == T_CONNECT) {
            break;                            /* connection established */
        } else if (gConnectEvent == T_DISCONNECT) {
            OTRcvDisconnect(ep, NULL);        /* refused / reset by host */
            StatusMessage("host refused - is AppleBridge running?");
            OTRemoveNotifier(ep);
            OTCloseProvider(ep);
            return kABConnectRefused;
        }

        if (TickCount() - startTicks > CONNECT_TIMEOUT_TICKS) {
            StatusMessage("connect timeout - no reply from host");
            OTSndDisconnect(ep, NULL);
            OTRemoveNotifier(ep);
            OTCloseProvider(ep);
            return kABConnectTimeout;
        }
    }

    /* Connected. Complete the handshake retrieval SYNCHRONOUSLY, then return the
     * endpoint to synchronous + NON-blocking (what the receive loop expects).
     * Drop the notifier — the session uses sync polling, not events. */
    SetActivity("connect: finishing");
    OTSetSynchronous(ep);
    err = OTRcvConnect(ep, NULL);
    if (err != noErr) {
        StatusMessage("OTRcvConnect failed!");
        OTRemoveNotifier(ep);
        OTCloseProvider(ep);
        return err;
    }
    OTRemoveNotifier(ep);
    OTSetNonBlocking(ep);

    c->ep = (void *)ep;
    StatusMessage("SYNC-OK");
    return noErr;
}

/*
 * Receive data. Non-blocking: OT's kOTNoDataErr (link idle) is mapped to the
 * neutral kABNoData so the main loop never sees an OT constant.
 */
OSStatus ot_Recv(ABConn *c, char *buf, long bufSize, long *got)
{
    OTResult result;
    OTFlags  flags;

    *got = 0;
    result = OTRcv((EndpointRef)c->ep, buf, bufSize, &flags);

    if (result == kOTNoDataErr) return kABNoData;
    if (result < 0) return result;
    *got = result;
    return noErr;
}

/* --- debug helpers for the send-error path --- */
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
 * Send all bytes. OTSnd can return kOTFlowErr ("send buffer full") on large
 * payloads — not fatal: yield (so OT can drain) and retry the SAME chunk,
 * bailing only if the link stalls for ~30s. Real errors bail at once.
 */
OSStatus ot_Send(ABConn *c, const char *data, long size)
{
    EndpointRef ep = (EndpointRef)c->ep;
    OTResult result;
    long totalSent = 0;
    unsigned long flowStart = 0;   /* TickCount when flow-blocked; 0 = progressing */
    char line[80], nb[16];

    while (totalSent < size) {
        result = OTSnd(ep, (void *)(data + totalSent), size - totalSent, 0);

        if (result >= 0) {
            totalSent += result;
            flowStart = 0;
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

void ot_Close(ABConn *c)
{
    if (c->ep != NULL) {
        OTCloseProvider((EndpointRef)c->ep);
        c->ep = NULL;
    }
}
