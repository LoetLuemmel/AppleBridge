/*
 * AppleBridge - Serial transport backend (see transport.h / docs/SERIAL_TRANSPORT.md)
 *
 * Reaches machines with no Ethernet: the classic Serial Manager over the modem
 * (port A) or printer (port B) port. Serial is point-to-point and has no address,
 * so "connect" just opens + configures the port — whatever is on the wire is the
 * host. Receive is non-blocking (SerGetBuf reports buffered bytes, then a bounded
 * FSRead), so the cooperative scheduler never starves, exactly like the OT and
 * MacTCP backends above this seam.
 */
#include <transport.h>
#include <transport_priv.h>
#include <Serial.h>       /* SerReset, SerSetBuf, SerGetBuf, baud/data/parity/stop */
#include <Devices.h>      /* OpenDriver, CloseDriver */
#include <Files.h>        /* FSRead, FSWrite */
#include <Errors.h>       /* eofErr */
#include <Memory.h>       /* NewPtr, DisposePtr — the input buffer */
#include <Events.h>       /* TickCount — bounded wait for a sliced frame header */

/* --- Why this file grew two guards (2026-07-26, diagnosed on a real SE/30) ---
 *
 * 1. INPUT BUFFER. The Serial Manager's default input buffer is 64 BYTES. A
 *    WRITEFILE of 8 KB arrives in ~1.4 s at 57600 baud, which a 16 MHz 68030
 *    running a cooperative event loop cannot drain in 64-byte bites: the buffer
 *    overruns and bytes are dropped SILENTLY. Symptom: the file lands with the
 *    right length and the wrong contents (8150 of 8192 bytes differed). Small
 *    verbs (PING, a LISTDIR request) fit in 64 bytes, which is why only bulk
 *    transfers were ever corrupted. Fixed by installing our own large buffer
 *    with SerSetBuf.
 *
 * 2. SLICED FRAME HEADERS. sr_Recv used to return whatever happened to be
 *    buffered, and the protocol layer treats one read as one frame — so a
 *    `PING` split across two reads became `P` + `ING`, two malformed commands
 *    (the `badreq` counter, and the host's "drained N stale bytes"). The body of
 *    a long frame is already reassembled by length upstream; what was missing is
 *    completing the HEADER LINE. sr_Recv now waits briefly for the newline.
 */
#define kSerInBufSize   16384   /* SerSetBuf takes a short: keep well under 32767 */
#define kLineWaitTicks     20   /* ~1/3 s, only while a partial frame is in flight */

static Ptr gInBuf = NULL;       /* our input buffer; owned by this file */

/* Line/port config, set from prefs by main.c via ABSerialConfig() before
 * ABNetInit(). Defaults: modem port (A), 57600 baud, 8-N-1. */
static Boolean gPortB   = false;        /* false = modem (A), true = printer (B) */
static long    gBaudCfg = baud9600;     /* a Serial.h baud constant (default 9600) */

/* Map a numeric baud to its Serial.h config constant (default 57600). */
static long BaudConst(long baud)
{
    switch (baud) {
        case 300:   return baud300;
        case 1200:  return baud1200;
        case 2400:  return baud2400;
        case 4800:  return baud4800;
        case 9600:  return baud9600;
        case 19200: return baud19200;
        case 38400: return baud38400;
        case 57600: return baud57600;
        default:    return baud57600;
    }
}

void ABSerialConfig(short portIsB, long baud)
{
    gPortB   = (portIsB != 0);
    gBaudCfg = BaudConst(baud);
}

OSStatus sr_Init(void)     { return noErr; }   /* no global stack to bring up */
void     sr_Shutdown(void) { }

OSStatus sr_Connect(ABConn *c, unsigned long hostIP, unsigned short port)
{
    OSErr     err;
    short     inRef = 0, outRef = 0;
    short     config;
    StringPtr inName  = (StringPtr)(gPortB ? "\p.BIn"  : "\p.AIn");
    StringPtr outName = (StringPtr)(gPortB ? "\p.BOut" : "\p.AOut");

    (void)hostIP; (void)port;          /* serial is point-to-point: no address */

    err = OpenDriver(outName, &outRef);
    if (err != noErr) return err;
    err = OpenDriver(inName, &inRef);
    if (err != noErr) { CloseDriver(outRef); return err; }

    /* 8 data bits, no parity, 1 stop bit, at the configured baud. */
    config = (short)(gBaudCfg | data8 | noParity | stop10);
    SerReset(outRef, config);
    SerReset(inRef, config);

    /* Replace the 64-byte default input buffer (see the note at the top). A
     * NewPtr block is already non-relocatable, which is what SerSetBuf needs.
     * If the allocation fails we carry on with the default buffer rather than
     * refusing the connection: small verbs still work, bulk transfers are the
     * only casualty, and a bridge that connects beats one that does not. */
    if (gInBuf == NULL) gInBuf = NewPtr((Size)kSerInBufSize);
    if (gInBuf != NULL) SerSetBuf(inRef, gInBuf, (short)kSerInBufSize);

    c->inRef  = inRef;
    c->outRef = outRef;
    return noErr;
}

/* A frame header ends at the first newline; CR is accepted because the guest's
 * C runtime maps '\n' to CR, so both ends of the wire use both. */
static Boolean HasFrameEnd(const char *buf, long len)
{
    long i;
    for (i = 0; i < len; i++)
        if (buf[i] == '\n' || buf[i] == '\r') return true;
    return false;
}

OSStatus sr_Recv(ABConn *c, char *buf, long bufSize, long *got)
{
    OSErr         err;
    long          avail = 0, n, total;
    unsigned long deadline;

    *got = 0;
    err = SerGetBuf(c->inRef, &avail);
    if (err != noErr) return err;
    if (avail <= 0) return kABNoData;            /* idle: nothing buffered — return at once */

    n = (avail < bufSize) ? avail : bufSize;     /* only read what's buffered -> no block */
    err = FSRead(c->inRef, &n, buf);
    if (err != noErr && err != eofErr) return err;
    total = n;

    /* Bytes ARE in flight now, so a partial frame means the rest is still on the
     * wire — wait for it rather than handing a sliced header upstream to be
     * rejected as `badreq`. Bounded (~1/3 s) and only entered when a frame is
     * already mid-arrival, so an idle link never pays for this and the
     * cooperative scheduler is never starved for long. Bulk bodies have no
     * newline, so they simply fill the buffer and leave early. */
    deadline = TickCount() + kLineWaitTicks;
    while (total > 0 && total < bufSize && !HasFrameEnd(buf, total)) {
        if (TickCount() >= deadline) break;
        avail = 0;
        if (SerGetBuf(c->inRef, &avail) != noErr) break;
        if (avail <= 0) continue;                /* nothing yet — keep waiting */
        n = bufSize - total;
        if (avail < n) n = avail;
        err = FSRead(c->inRef, &n, buf + total);
        if (err != noErr && err != eofErr) break;
        if (n <= 0) continue;
        total += n;
        deadline = TickCount() + kLineWaitTicks; /* progress: allow the frame to finish */
    }

    *got = total;
    return (total > 0) ? noErr : kABNoData;
}

OSStatus sr_Send(ABConn *c, const char *data, long size)
{
    OSErr err;
    long  off = 0;

    while (off < size) {
        long chunk = size - off;
        if (chunk > 256) chunk = 256;            /* chunk so one FSWrite can't wedge the loop */
        err = FSWrite(c->outRef, &chunk, (Ptr)(data + off));
        if (err != noErr) return err;
        off += chunk;
        /* NB: no cooperative yield between chunks in v1 — a large serial send
         * blocks the loop for its duration (bounded by baud). Add a yield when
         * bulk-over-serial (screenshots/large files) proves painful. */
    }
    return noErr;
}

void sr_Close(ABConn *c)
{
    /* Hand the driver its own buffer back BEFORE ours is freed — otherwise the
     * Serial Manager keeps writing into disposed memory. Order matters:
     * restore, close, then dispose. */
    if (c->inRef) {
        if (gInBuf != NULL) SerSetBuf(c->inRef, NULL, 0);
        CloseDriver(c->inRef);
        c->inRef = 0;
    }
    if (c->outRef) { CloseDriver(c->outRef); c->outRef = 0; }
    if (gInBuf != NULL) { DisposePtr(gInBuf); gInBuf = NULL; }
}
