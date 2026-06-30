/*
 * AppleBridge - Transport seam (private shared header)
 *
 * Shared ONLY by the transport implementation files (transport.c, transport_ot.c,
 * transport_mactcp.c). Defines the concrete ABConn struct and the per-backend
 * entry points. The handle fields are stored as neutral types (void* / unsigned
 * long) so this header pulls in NEITHER OpenTransport.h NOR MacTCP.h — each
 * backend casts to its own handle type and includes its own networking headers.
 * Layers above the seam include only <transport.h> and never see this.
 */
#ifndef TRANSPORT_PRIV_H
#define TRANSPORT_PRIV_H

#include <Types.h>
#include <transport.h>

struct ABConn {
    short          transport;   /* kTransportOT / kTransportMacTCP */
    void          *ep;          /* OT: EndpointRef                 */
    unsigned long  stream;      /* MacTCP: StreamPtr               */
    Ptr            rcvBuf;       /* MacTCP: driver-owned rcv buffer */
};

/* --- Open Transport backend (transport_ot.c) --- */
OSStatus ot_Init(void);
void     ot_Shutdown(void);
OSStatus ot_Connect(ABConn *c, unsigned long hostIP, unsigned short port);
OSStatus ot_Recv(ABConn *c, char *buf, long bufSize, long *got);
OSStatus ot_Send(ABConn *c, const char *data, long size);
void     ot_Close(ABConn *c);

/* --- MacTCP backend (transport_mactcp.c) --- */
OSStatus mt_Init(void);
void     mt_Shutdown(void);
OSStatus mt_Connect(ABConn *c, unsigned long hostIP, unsigned short port);
OSStatus mt_Recv(ABConn *c, char *buf, long bufSize, long *got);
OSStatus mt_Send(ABConn *c, const char *data, long size);
void     mt_Close(ABConn *c);

/* UI/liveness helpers from main.c — let the connect poll keep the Mac alive
 * (yield + repaint) instead of freezing the cooperative scheduler. */
extern void    StatusMessage(const char *msg);   /* append a console log line     */
extern void    ShowAlive(void);                  /* repaint activity bar + uptime */
extern Boolean CheckUserAbort(void);             /* SystemTask + pump events + quit? */
extern void    SetActivity(const char *msg);     /* top-bar text, repaints now    */

#endif /* TRANSPORT_PRIV_H */
