/*
 * AppleBridge - Transport seam (public interface)
 *
 * One backend-neutral interface over the daemon's TCP link to the host, so the
 * networking stack is a swappable backend rather than hard-wired Open Transport.
 * Two backends live behind this seam:
 *
 *   transport_ot.c      - Open Transport (the original, default)
 *   transport_mactcp.c  - classic MacTCP (reaches OT-less / pre-OT systems)
 *
 * Everything above the seam (main.c, protocol.c, fileio.c) sees only the opaque
 * ABConn handle and the neutral kABNoData sentinel — never EndpointRef,
 * StreamPtr, kOTNoDataErr, or any other backend type. The dispatcher in
 * transport.c picks the backend at startup from the NET= pref and auto-falls
 * back to Open Transport if the chosen stack can't come up.
 */
#ifndef TRANSPORT_H
#define TRANSPORT_H

#include <Types.h>

/* Networking service selector (stored in prefs as NET=OT|MacTCP|Serial). */
#define kTransportOT      0
#define kTransportMacTCP  1
#define kTransportSerial  2   /* Serial Manager (reaches Ethernet-less machines) */

/* Neutral "link is idle, no data yet" sentinel returned by ABRecv. Replaces the
 * OT-specific kOTNoDataErr that used to leak into the main loop; each backend
 * maps its own would-block code to this. Positive (1) so it never collides with
 * a real toolbox error (negative) or success (0). */
#define kABNoData         1

/* Opaque per-connection handle (defined privately in transport.c/transport_priv.h). */
typedef struct ABConn ABConn;

/* Bring up the chosen stack. `want` is kTransportOT / kTransportMacTCP; if that
 * stack fails to initialise, falls back to Open Transport. Returns noErr if any
 * stack came up, else a non-zero error. */
OSStatus ABNetInit(short want);

/* Tear down whichever stack came up. */
void ABNetShutdown(void);

/* Which stack is actually active (after any fallback). */
short ABActiveTransport(void);

/* Human-readable name of the active stack ("OT" / "MacTCP" / "Serial"), for the
 * monitor footer and STAT's net= field. Never NULL. */
const char *ABTransportName(void);

/* Open a connection to host:port. Allocates an ABConn (freed by ABClose).
 * On the very first MacTCP connect failure the dispatcher falls back to OT for
 * the rest of the session. *conn is NULL on failure. */
OSStatus ABConnect(ABConn **conn, unsigned long hostIP, unsigned short port);

/* Non-blocking receive. noErr + *got>0 on data; kABNoData when idle; a negative
 * error on a dead link. */
OSStatus ABRecv(ABConn *conn, char *buf, long bufSize, long *got);

/* Send all `size` bytes, riding out backend flow control. noErr or a negative error. */
OSStatus ABSend(ABConn *conn, const char *data, long size);

/* Close + free the connection (NULL-safe). */
void ABClose(ABConn *conn);

/* Dotted-quad string -> 32-bit host-order address (transport-neutral). */
unsigned long ParseIPAddress(const char *ipStr);

/* Configure the serial backend (port + baud) from prefs, before ABNetInit().
 * portIsB: 0 = modem port A (default), non-zero = printer port B. baud: a decimal
 * rate (e.g. 57600). No-op for the OT/MacTCP backends. See docs/SERIAL_TRANSPORT.md. */
void ABSerialConfig(short portIsB, long baud);

#endif /* TRANSPORT_H */
