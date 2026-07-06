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
#include <Serial.h>       /* SerReset, SerGetBuf, baud/data/parity/stop constants */
#include <Devices.h>      /* OpenDriver, CloseDriver */
#include <Files.h>        /* FSRead, FSWrite */
#include <Errors.h>       /* eofErr */

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

    c->inRef  = inRef;
    c->outRef = outRef;
    return noErr;
}

OSStatus sr_Recv(ABConn *c, char *buf, long bufSize, long *got)
{
    OSErr err;
    long  avail = 0, n;

    *got = 0;
    err = SerGetBuf(c->inRef, &avail);
    if (err != noErr) return err;
    if (avail <= 0) return kABNoData;            /* idle: nothing buffered */

    n = (avail < bufSize) ? avail : bufSize;     /* only read what's buffered -> no block */
    err = FSRead(c->inRef, &n, buf);
    if (err != noErr && err != eofErr) return err;
    *got = n;
    return (n > 0) ? noErr : kABNoData;
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
    if (c->inRef)  { CloseDriver(c->inRef);  c->inRef  = 0; }
    if (c->outRef) { CloseDriver(c->outRef); c->outRef = 0; }
}
