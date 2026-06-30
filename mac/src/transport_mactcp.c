/*
 * AppleBridge - MacTCP backend (transport seam)
 *
 * A second networking backend behind transport.h, using the classic MacTCP
 * driver API (PBControl on the ".IPP" device with a TCPiopb) instead of Open
 * Transport. Same wire protocol, same host server — only the guest-side stack
 * differs. This reaches systems where MacTCP is the stack (and, on this OT
 * testbed, exercises Open Transport's built-in MacTCP backward-compatibility).
 *
 * The async-connect freeze hazard is handled exactly as the OT backend does it,
 * but with MacTCP's idiom: TCPActiveOpen is issued ASYNCHRONOUSLY and we poll
 * pb.ioResult (> 0 == "in progress") with a tick-timeout while yielding, so an
 * unreachable host can never wedge System 7's cooperative scheduler. Receive is
 * non-blocking via TCPStatus.amtUnreadData; OT's kOTNoDataErr becomes the
 * neutral kABNoData. The opaque ABConn carries the StreamPtr (c->stream) and the
 * driver-owned receive buffer (c->rcvBuf).
 */

#include <transport.h>
#include <transport_priv.h>
#include <applebridge.h>   /* kABConnectTimeout / kABConnectRefused */
#include <MacTCP.h>
#include <Devices.h>       /* OpenDriver, PBControlSync/Async, ParmBlkPtr */
#include <Memory.h>        /* NewPtr, DisposePtr */
#include <Errors.h>        /* noErr, memFullErr, userCanceledErr */
#include <Events.h>        /* TickCount, SystemTask */

#define kRcvBufSize        8192L     /* driver-owned stream receive buffer        */
#define MT_CONNECT_TICKS   600       /* 10 s connect bound (matches the OT path)  */
#define MT_VALID_TIMEOUTS  0xC0      /* validityFlags: ulpTimeoutValue+Action set */

static short   gIPRefNum  = 0;
static Boolean gDriverOpen = false;

/* Zero a parameter block before each call (the 12-byte queue header included). */
static void ClearPB(TCPiopb *pb)
{
    char *p = (char *)pb;
    long  i;
    for (i = 0; i < (long)sizeof(TCPiopb); i++) p[i] = 0;
}

/* Open the MacTCP driver (".IPP"). Failure -> dispatcher falls back to OT. */
OSStatus mt_Init(void)
{
    OSErr err;
    if (gDriverOpen) return noErr;
    err = OpenDriver("\p.IPP", &gIPRefNum);
    if (err != noErr) return err;
    gDriverOpen = true;
    return noErr;
}

/* The ".IPP" driver is a shared system device — leave it open for other clients. */
void mt_Shutdown(void)
{
}

OSStatus mt_Connect(ABConn *c, unsigned long hostIP, unsigned short port)
{
    TCPiopb pb;
    OSErr   err;
    long    startTicks;
    Ptr     rcv;

    /* 1. Allocate the stream's receive buffer (the driver owns it until release). */
    SetActivity("connect: TCPCreate");
    StatusMessage("Opening MacTCP stream...");
    rcv = NewPtr(kRcvBufSize);
    if (rcv == NULL) {
        StatusMessage("MacTCP rcv buffer alloc failed");
        return memFullErr;
    }
    c->rcvBuf = rcv;

    ClearPB(&pb);
    pb.ioCRefNum = gIPRefNum;
    pb.csCode    = TCPCreate;
    pb.csParam.create.rcvBuff     = rcv;
    pb.csParam.create.rcvBuffLen  = kRcvBufSize;
    pb.csParam.create.notifyProc  = NULL;
    pb.csParam.create.userDataPtr = NULL;
    err = PBControlSync((ParmBlkPtr)&pb);
    if (err != noErr) {
        StatusMessage("TCPCreate failed");
        DisposePtr(rcv);
        c->rcvBuf = NULL;
        return err;
    }
    c->stream = pb.tcpStream;          /* the driver returns the stream handle here */

    /* 2. Active open, ASYNCHRONOUS so the poll loop below keeps the Mac alive and
     *    a dead host can never block the cooperative scheduler. */
    SetActivity("connect: TCPActiveOpen");
    ClearPB(&pb);
    pb.ioCRefNum = gIPRefNum;
    pb.csCode    = TCPActiveOpen;
    pb.tcpStream = c->stream;
    pb.csParam.open.ulpTimeoutValue     = 30;
    pb.csParam.open.ulpTimeoutAction    = 1;      /* abort on ULP timeout */
    pb.csParam.open.validityFlags       = MT_VALID_TIMEOUTS;
    pb.csParam.open.commandTimeoutValue = 0;
    pb.csParam.open.remoteHost          = (ip_addr)hostIP;
    pb.csParam.open.remotePort          = (tcp_port)port;
    pb.csParam.open.localHost           = 0;
    pb.csParam.open.localPort           = 0;
    pb.ioCompletion = NULL;
    pb.ioResult     = 1;                /* "in progress" until the driver updates it */
    err = PBControlAsync((ParmBlkPtr)&pb);
    if (err != noErr) {
        StatusMessage("TCPActiveOpen dispatch failed");
        mt_Close(c);
        return kABConnectRefused;
    }

    SetActivity("connect: polling (yields)");
    startTicks = TickCount();
    while (pb.ioResult > 0) {           /* > 0 == still in progress */
        if (CheckUserAbort()) {         /* yields (SystemTask) + pumps events */
            TCPiopb ab;
            ClearPB(&ab);
            ab.ioCRefNum = gIPRefNum;
            ab.csCode    = TCPAbort;
            ab.tcpStream = c->stream;
            PBControlSync((ParmBlkPtr)&ab);
            mt_Close(c);
            return userCanceledErr;
        }
        ShowAlive();

        if (TickCount() - startTicks > MT_CONNECT_TICKS) {
            TCPiopb ab;
            StatusMessage("MacTCP connect timeout - no reply from host");
            ClearPB(&ab);
            ab.ioCRefNum = gIPRefNum;
            ab.csCode    = TCPAbort;
            ab.tcpStream = c->stream;
            PBControlSync((ParmBlkPtr)&ab);
            mt_Close(c);
            return kABConnectTimeout;
        }
    }

    if (pb.ioResult != noErr) {         /* negative == refused / unreachable */
        StatusMessage("MacTCP connect refused - is AppleBridge running?");
        mt_Close(c);
        return kABConnectRefused;
    }

    StatusMessage("SYNC-OK");
    return noErr;
}

/*
 * Non-blocking receive. Ask the driver how much is buffered (TCPStatus); if none,
 * report the neutral kABNoData (the main loop's idle path). Otherwise pull what
 * fits. A TCPStatus error means the connection is gone -> the caller reconnects.
 */
OSStatus mt_Recv(ABConn *c, char *buf, long bufSize, long *got)
{
    TCPiopb        pb;
    OSErr          err;
    unsigned short avail, want;

    *got = 0;

    ClearPB(&pb);
    pb.ioCRefNum = gIPRefNum;
    pb.csCode    = TCPStatus;
    pb.tcpStream = c->stream;
    err = PBControlSync((ParmBlkPtr)&pb);
    if (err != noErr) return err;       /* connection closing/gone -> reconnect */

    avail = pb.csParam.status.amtUnreadData;
    if (avail == 0) return kABNoData;

    want = avail;
    if ((long)want > bufSize) want = (unsigned short)bufSize;

    ClearPB(&pb);
    pb.ioCRefNum = gIPRefNum;
    pb.csCode    = TCPRcv;
    pb.tcpStream = c->stream;
    pb.csParam.receive.commandTimeoutValue = 1;   /* data already present */
    pb.csParam.receive.rcvBuff             = buf;
    pb.csParam.receive.rcvBuffLen          = want;
    err = PBControlSync((ParmBlkPtr)&pb);
    if (err != noErr) return err;

    *got = pb.csParam.receive.rcvBuffLen;          /* driver updates with actual count */
    return noErr;
}

/*
 * Send all bytes via TCPSend with a one-entry write-data structure, pushed.
 * MacTCP has no kOTFlowErr analogue (TCPSend blocks until the data is queued),
 * so we chunk large payloads and yield between chunks to keep the cooperative
 * scheduler breathing.
 */
OSStatus mt_Send(ABConn *c, const char *data, long size)
{
    TCPiopb  pb;
    OSErr    err;
    long     sent = 0;
    wdsEntry wds[2];

    while (sent < size) {
        long chunk = size - sent;
        if (chunk > 32767L) chunk = 32767L;        /* wdsEntry.length is unsigned short */

        wds[0].length = (unsigned short)chunk;
        wds[0].ptr    = (Ptr)(data + sent);
        wds[1].length = 0;                          /* zero-length entry terminates the WDS */
        wds[1].ptr    = NULL;

        ClearPB(&pb);
        pb.ioCRefNum = gIPRefNum;
        pb.csCode    = TCPSend;
        pb.tcpStream = c->stream;
        pb.csParam.send.ulpTimeoutValue  = 30;
        pb.csParam.send.ulpTimeoutAction = 1;       /* abort on timeout */
        pb.csParam.send.validityFlags    = MT_VALID_TIMEOUTS;
        pb.csParam.send.pushFlag         = true;
        pb.csParam.send.urgentFlag       = false;
        pb.csParam.send.wdsPtr           = (Ptr)wds;
        err = PBControlSync((ParmBlkPtr)&pb);
        if (err != noErr) {
            StatusMessage("TCPSend failed");
            return err;
        }
        sent += chunk;
        SystemTask();                                /* let the scheduler run */
    }
    return noErr;
}

void mt_Close(ABConn *c)
{
    TCPiopb pb;

    if (c->stream != 0) {
        ClearPB(&pb);
        pb.ioCRefNum = gIPRefNum;
        pb.csCode    = TCPClose;
        pb.tcpStream = c->stream;
        pb.csParam.close.ulpTimeoutValue  = 5;
        pb.csParam.close.ulpTimeoutAction = 1;
        pb.csParam.close.validityFlags    = MT_VALID_TIMEOUTS;
        PBControlSync((ParmBlkPtr)&pb);              /* best-effort graceful close */

        ClearPB(&pb);
        pb.ioCRefNum = gIPRefNum;
        pb.csCode    = TCPRelease;                   /* frees the stream; returns rcvBuff to us */
        pb.tcpStream = c->stream;
        PBControlSync((ParmBlkPtr)&pb);
        c->stream = 0;
    }

    if (c->rcvBuf != NULL) {
        DisposePtr(c->rcvBuf);
        c->rcvBuf = NULL;
    }
}
