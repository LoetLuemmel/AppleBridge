/*
 * AppleBridge - Transport dispatcher (see transport.h / transport_priv.h)
 *
 * Picks the active networking backend at startup and routes every ABConnect /
 * ABRecv / ABSend / ABClose to it. Knows nothing about OT or MacTCP internals —
 * it only owns the ABConn allocation, the startup fallback ladder, and the
 * shared dotted-quad parser.
 */

#include <transport.h>
#include <transport_priv.h>
#include <Memory.h>       /* NewPtrClear, DisposePtr */
#include <Errors.h>       /* memFullErr */
#include <Gestalt.h>      /* Gestalt, gestaltOpenTpt (OT-presence gate) */

#ifndef gestaltOpenTpt
#define gestaltOpenTpt              'otan'
#endif
#ifndef gestaltOpenTptPresentMask
#define gestaltOpenTptPresentMask   0x00000001L
#endif

/* Which stack actually came up (after any fallback). */
static short   gActive       = kTransportOT;
/* Set once a MacTCP connect has ever succeeded, so a single early failure can
 * fall back to OT but a later transient hiccup does not abandon a proven stack. */
static Boolean gMacTcpProven = false;

/*
 * True iff Open Transport is installed. Calling OT traps (ot_Init / ot_Connect)
 * on a classic-MacTCP-only machine (e.g. a real Macintosh SE/30) faults with a
 * hardware exception (Error 11), so every OT fall-back below is gated on this.
 * A failed MacTCP connect there just means "host not up yet", not a broken stack.
 */
static Boolean ABOpenTptAvailable(void)
{
    long result;
    return (Gestalt(gestaltOpenTpt, &result) == noErr &&
            (result & gestaltOpenTptPresentMask) != 0);
}

/*
 * Bring up the requested stack. If MacTCP is requested but its driver can't be
 * opened (e.g. a system with no MacTCP at all), fall back to Open Transport so a
 * bad pref can never take the bridge offline.
 */
OSStatus ABNetInit(short want)
{
    if (want == kTransportSerial) {
        /* No global stack to bring up, and no fall-back to OT — a machine chosen
         * for serial has no OpenTransport to fall back to. The port itself opens
         * at connect time (sr_Connect). */
        gActive = kTransportSerial;
        return sr_Init();
    }
    if (want == kTransportMacTCP) {
        if (mt_Init() == noErr) {
            gActive = kTransportMacTCP;
            return noErr;
        }
        if (!ABOpenTptAvailable()) {
            StatusMessage("MacTCP init failed and no Open Transport - bridge offline");
            return -1;
        }
        StatusMessage("MacTCP init failed - falling back to Open Transport");
    }
    /* Explicit OT, or a MacTCP->OT fall-back: only touch OT if it is installed,
     * so a no-OT machine never trap-faults (Error 11). */
    if (ABOpenTptAvailable() && ot_Init() == noErr) {
        gActive = kTransportOT;
        return noErr;
    }
    return -1;
}

void ABNetShutdown(void)
{
    if (gActive == kTransportMacTCP) mt_Shutdown();
    else                            ot_Shutdown();
}

short ABActiveTransport(void)
{
    return gActive;
}

const char *ABTransportName(void)
{
    if (gActive == kTransportMacTCP) return "MacTCP";
    if (gActive == kTransportSerial) return "Serial";
    return "OT";
}

/*
 * Open a connection. The dispatcher allocates the (zeroed) ABConn so each
 * backend only fills in its own handle fields.
 *
 * Connect-level fallback: the FIRST MacTCP connect that fails (before any
 * MacTCP connect has ever succeeded) drops us to Open Transport for the rest of
 * the session — so a stack whose driver opens but whose link never forms can't
 * wedge the reconnect loop.
 */
OSStatus ABConnect(ABConn **conn, unsigned long hostIP, unsigned short port)
{
    ABConn  *c;
    OSStatus err;

    *conn = NULL;
    c = (ABConn *)NewPtrClear((Size)sizeof(ABConn));
    if (c == NULL) return memFullErr;

    c->transport = gActive;
    if (gActive == kTransportMacTCP) {
        err = mt_Connect(c, hostIP, port);
        if (err == noErr) {
            gMacTcpProven = true;
        } else if (!gMacTcpProven && ABOpenTptAvailable()) {
            StatusMessage("MacTCP connect failed - switching to Open Transport");
            mt_Close(c);                 /* release any half-built MacTCP state */
            gActive = kTransportOT;
            c->transport = kTransportOT;
            if (ot_Init() == noErr) err = ot_Connect(c, hostIP, port);
        } else if (err != noErr) {
            /* No OT to fall back to (e.g. a real SE/30): keep MacTCP and let the
             * reconnect loop retry — a failed connect just means "host not up
             * yet", not a broken stack, so we must NOT touch OT traps here. */
            mt_Close(c);
        }
    } else if (gActive == kTransportSerial) {
        err = sr_Connect(c, hostIP, port);
    } else {
        err = ot_Connect(c, hostIP, port);
    }

    if (err != noErr) {
        DisposePtr((Ptr)c);
        return err;
    }
    *conn = c;
    return noErr;
}

OSStatus ABRecv(ABConn *c, char *buf, long bufSize, long *got)
{
    if (c->transport == kTransportMacTCP) return mt_Recv(c, buf, bufSize, got);
    if (c->transport == kTransportSerial) return sr_Recv(c, buf, bufSize, got);
    return ot_Recv(c, buf, bufSize, got);
}

OSStatus ABSend(ABConn *c, const char *data, long size)
{
    if (c->transport == kTransportMacTCP) return mt_Send(c, data, size);
    if (c->transport == kTransportSerial) return sr_Send(c, data, size);
    return ot_Send(c, data, size);
}

void ABClose(ABConn *c)
{
    if (c == NULL) return;
    if (c->transport == kTransportMacTCP)      mt_Close(c);
    else if (c->transport == kTransportSerial) sr_Close(c);
    else                                       ot_Close(c);
    DisposePtr((Ptr)c);
}

/*
 * Parse IP string to unsigned long (e.g. "192.168.1.100"). Transport-neutral —
 * both backends feed it the same 32-bit host-order address.
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
