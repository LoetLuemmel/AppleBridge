/* ttls — TLS 1.2/1.3 handshake timer for System 7 (68K, Retro68 + cryanc).
 * Socket layer: MacTCP driver API (works natively and via Open Transport's
 * MacTCP compatibility). Config: ttls.cfg = "<ip> <port> <sni-host> <12|13>". */
#include <Quickdraw.h>
#include <Windows.h>
#include <Fonts.h>
#include <Events.h>
#include <Memory.h>
#include <Menus.h>
#include <TextEdit.h>
#include <Dialogs.h>
#include <Processes.h>
#include <Devices.h>
#include <Files.h>
#include <Errors.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "MacTCP.h"
#include "cryanc.h"
#include "roots.h"
#include <time.h>

#define RCVBUF   16384L
#define NETBUF   8192
#define VALID_TO 0xC0

static short  gIP;
static StreamPtr gStream;
static Ptr    gRcv;
static short  gOut;
static char   gLog[4096]; static int gLogLen; static int gEntropyCalls;

static WindowPtr gWin; static short gLine;
static unsigned char gLines[28][80];
static void Redraw(void) {
    int i; SetPort(gWin); EraseRect(&gWin->portRect);
    for (i = 0; i < gLine; i++) { MoveTo(6, 14 + 13 * i); DrawString(gLines[i]); }
}
static void Yield(void) {
    EventRecord e;
    if (WaitNextEvent(everyEvent, &e, 1, NULL) && e.what == updateEvt && (WindowPtr)e.message == gWin) {
        BeginUpdate(gWin); Redraw(); EndUpdate(gWin);
    }
}
static void Put(const char *txt) {           /* one line into the window AND the log */
    int n = strlen(txt), m;
    if (gLogLen + n + 1 < (int)sizeof gLog) { memcpy(gLog + gLogLen, txt, n); gLogLen += n; if (n && txt[n-1] != '\n') gLog[gLogLen++] = '\n'; }
    while (n && (txt[n-1] == '\n' || txt[n-1] == '\r')) n--;
    m = n > 78 ? 78 : n;
    if (gLine >= 28) { memmove(gLines[0], gLines[1], sizeof gLines - sizeof gLines[0]); gLine = 27; }
    gLines[gLine][0] = m; memcpy(gLines[gLine] + 1, txt, m); gLine++;
    Redraw();
}
/* Multiversal has no GetApplLimit; the low-memory global ApplLimit lives at $130. */
#define ApplLimitLM (*(Ptr *)0x130)
/* cryanc's random_sleep wants usleep: sleep by yielding. */
/* Entropy for cryanc's arc4 pool (TLS_ENTROPY_HOOK). System 7 has no RNG; what it has is
 * timing the machine cannot predict: how many loop iterations fit between two tick
 * boundaries depends on interrupt load and, under emulation, on host scheduling. 24 such
 * counts (0.4 s) plus the mouse, the clock and stack/heap addresses. The arc4 key
 * schedule mixes; the caller adds gettimeofday. Weak against a local observer, but not
 * the constant that an unseeded pool is. */
int tls_entropy_hook(unsigned char *out, int len) {
    unsigned long pool[32]; Point m; unsigned long t; int i, n = 0;
    for (i = 0; i < 24; i++) {
        unsigned long t0 = TickCount(), c = 0;
        while (TickCount() == t0) c++;
        pool[n++] = c ^ (t0 << 20);
    }
    GetMouse(&m); GetDateTime(&t);
    pool[n++] = t; pool[n++] = ((unsigned long)m.v << 16) | (unsigned short)m.h;
    pool[n++] = (unsigned long)&m; pool[n++] = (unsigned long)malloc(1);
    pool[n++] = TickCount(); pool[n++] = *(unsigned long *)0x16A;   /* Ticks low-mem */
    if (len > (int)(n * sizeof(long))) len = n * sizeof(long);
    memcpy(out, pool, len);
    gEntropyCalls++;
    return len;
}
int usleep(unsigned long us) { long e = TickCount() + (long)(us / 16667) + 1; while (TickCount() < e) Yield(); return 0; }

static void L(const char *fmt, long a, long b) {
    char line[160]; int n = sprintf(line, fmt, a, b);
    (void)n; Put(line);
}
static void Ls(const char *fmt, const char *s) {
    char line[200]; int n = sprintf(line, fmt, s);
    (void)n; Put(line);
}

static void WriteLog(void) {
    short ref; long cnt = gLogLen; int i;
    for (i = 0; i < gLogLen; i++) if (gLog[i] == '\n') gLog[i] = '\r';
    FSDelete("\phttps.log", 0);
    if (Create("\phttps.log", 0, 'ttxt', 'TEXT') == noErr && FSOpen("\phttps.log", 0, &ref) == noErr) {
        FSWrite(ref, &cnt, gLog); FSClose(ref);
    }
}

static char gNonce[32], gPath[512];
static int ReadReq(char *ip, int *port, char *host, int *ver) {
    short ref; long cnt = 1023; static char buf[1024]; char *q, *e; int n = 0;
    if (FSOpen("\phttps.req", 0, &ref) != noErr) return 0;
    FSRead(ref, &cnt, buf); FSClose(ref); buf[cnt] = 0;
    for (q = buf; q && *q; q = e ? e + 1 : NULL, n++) {
        e = strchr(q, '\r'); if (!e) e = strchr(q, '\n'); if (e) *e = 0;
        switch (n) {
            case 0: strncpy(gNonce, q, 31); break;
            case 1: strncpy(ip, q, 31); break;
            case 2: *port = atoi(q); break;
            case 3: strncpy(host, q, 127); break;
            case 4: strncpy(gPath, q, 511); break;
            case 5: *ver = atoi(q); break;
        }
    }
    if (!gPath[0]) strcpy(gPath, "/");
    return n >= 5;
}

static ip_addr ParseIP(const char *s) {
    unsigned a, b, c, d; if (sscanf(s, "%u.%u.%u.%u", &a, &b, &c, &d) != 4) return 0;
    return (a << 24) | (b << 16) | (c << 8) | d;
}

static void PB(TCPiopb *pb, short cs) { memset(pb, 0, sizeof *pb); pb->ioCRefNum = gIP; pb->csCode = cs; pb->tcpStream = gStream; }

static OSErr Connect(ip_addr host, int port) {
    TCPiopb pb; OSErr err; long t0;
    gRcv = NewPtr(RCVBUF); if (!gRcv) return memFullErr;
    PB(&pb, TCPCreate); pb.csParam.create.rcvBuff = gRcv; pb.csParam.create.rcvBuffLen = RCVBUF;
    err = PBControlSync((ParmBlkPtr)&pb); if (err) return err;
    gStream = pb.tcpStream;
    PB(&pb, TCPActiveOpen);
    pb.csParam.open.ulpTimeoutValue = 30; pb.csParam.open.ulpTimeoutAction = 1;
    pb.csParam.open.validityFlags = VALID_TO;
    pb.csParam.open.remoteHost = host; pb.csParam.open.remotePort = (tcp_port)port;
    pb.ioResult = 1;
    err = PBControlAsync((ParmBlkPtr)&pb); if (err) return err;
    t0 = TickCount();
    while (pb.ioResult > 0) { Yield(); if (TickCount() - t0 > 30 * 60) { PB(&pb, TCPAbort); PBControlSync((ParmBlkPtr)&pb); return -1; } }
    return pb.ioResult;
}

/* returns bytes, 0 if none, <0 if connection gone */
static long Recv(unsigned char *buf, long max) {
    TCPiopb pb; OSErr err; unsigned short avail;
    PB(&pb, TCPStatus); err = PBControlSync((ParmBlkPtr)&pb); if (err) return -1;
    avail = pb.csParam.status.amtUnreadData;
    if (!avail) return (pb.csParam.status.connectionState == 8 || pb.csParam.status.connectionState < 8) ? 0 : -1;
    if (avail > max) avail = (unsigned short)max;
    PB(&pb, TCPRcv); pb.csParam.receive.commandTimeoutValue = 1;
    pb.csParam.receive.rcvBuff = (Ptr)buf; pb.csParam.receive.rcvBuffLen = avail;
    err = PBControlSync((ParmBlkPtr)&pb); if (err) return -1;
    return pb.csParam.receive.rcvBuffLen;
}

static OSErr Send(const unsigned char *p, long n) {
    TCPiopb pb; wdsEntry w[2]; OSErr err;
    while (n > 0) {
        long c = n > 32767 ? 32767 : n;
        w[0].length = (unsigned short)c; w[0].ptr = (Ptr)p; w[1].length = 0; w[1].ptr = 0;
        PB(&pb, TCPSend); pb.csParam.send.ulpTimeoutValue = 30; pb.csParam.send.ulpTimeoutAction = 1;
        pb.csParam.send.validityFlags = VALID_TO; pb.csParam.send.pushFlag = 1; pb.csParam.send.wdsPtr = (Ptr)w;
        err = PBControlSync((ParmBlkPtr)&pb); if (err) return err;
        p += c; n -= c; Yield();
    }
    return noErr;
}

static void TcpClose(void) {
    TCPiopb pb;
    if (gStream) { PB(&pb, TCPClose); pb.csParam.close.ulpTimeoutValue = 5; pb.csParam.close.ulpTimeoutAction = 1;
        pb.csParam.close.validityFlags = VALID_TO; PBControlSync((ParmBlkPtr)&pb);
        PB(&pb, TCPRelease); PBControlSync((ParmBlkPtr)&pb); gStream = 0; }
}

static OSErr Flush(struct TLSContext *ctx) {
    unsigned int len; const unsigned char *out = tls_get_write_buffer(ctx, &len); OSErr err = noErr;
    if (out && len) err = Send(out, len);
    tls_buffer_clear(ctx);
    return err;
}

int main(void) {
    char ip[32], host[128]; int port, ver; ip_addr addr; OSErr err;
    struct TLSContext *ctx; unsigned char *net; long t0, t1, t2, t3 = 0, n, total = 0; int shown = 0;
    static char req[800];

    Rect r;
#ifndef TTLS_NOSTACK
    SetApplLimit((Ptr)((long)ApplLimitLM - 512L * 1024));
#endif
    MaxApplZone();
    InitGraf(&qd.thePort); InitFonts(); InitWindows(); InitMenus(); TEInit(); InitDialogs(NULL); InitCursor();
    SetRect(&r, 20, 60, 620, 440);
    gWin = NewWindow(NULL, &r, "\phttpsget - AppleBridge https fetch", true, documentProc, (WindowPtr)-1, false, 0);
    SetPort(gWin); TextFont(4); TextSize(9);   /* Monaco 9 */
    FSDelete("\phttps.log", 0); FSDelete("\phttps.out", 0);
    Put("httpsget 0.1 (68K, cryanc, MacTCP)");
#ifdef TTLS_MINIMAL
    Put("minimal build: window only"); goto done;
#endif
    if (!ReadReq(ip, &port, host, &ver)) { Put("no https.req"); goto done; }
    Ls("nonce %s\n", gNonce); Ls("path %s\n", gPath);
    if (Create("\phttps.out", 0, 'ttxt', 'TEXT') != noErr || FSOpen("\phttps.out", 0, &gOut) != noErr) { Put("cannot create https.out"); goto done; }
    addr = ParseIP(ip);
    Ls("target %s\n", host); L("ip=%08lx port=%ld\n", addr, port); L("tls=1.%ld\n", ver == 13 ? 3 : 2, 0);
    if (OpenDriver("\p.IPP", &gIP) != noErr) { Put("no MacTCP driver"); goto done; }

    t0 = TickCount();
    err = Connect(addr, port);
    t1 = TickCount();
    L("tcp connect: err=%ld  %ld ticks\n", err, t1 - t0);
    if (err) goto done;

    tls_init();
    ctx = tls_create_context(0, ver == 13 ? TLS_V13 : TLS_V12);
    tls_sni_set(ctx, host);
    { long t = TickCount(); int nroots = tls_load_root_certificates(ctx, (const unsigned char *)ttls_roots_pem, TTLS_ROOTS_LEN);
      L("roots loaded: %ld in %ld ticks", nroots, TickCount() - t); L("  clock=%ld\n", (long)time(NULL), 0); }
    tls_client_connect(ctx);
    L("entropy hook calls: %ld\n", (long)gEntropyCalls, 0);
    net = (unsigned char *)malloc(NETBUF);
    if (Flush(ctx)) { Put("send failed"); goto done; }
    L("client hello built in %ld ticks\n", TickCount() - t1, 0);
    while (!tls_established(ctx)) {
        int rc;
        n = Recv(net, NETBUF);
        if (n < 0) { Put("connection dropped in handshake"); goto done; }
        if (n == 0) { Yield(); if (TickCount() - t1 > 180L * 60) { Put("handshake timeout"); goto done; } continue; }
        { char hx[80]; int i, k = n > 20 ? 20 : (int)n; strcpy(hx, "  hex:"); for (i = 0; i < k; i++) sprintf(hx + strlen(hx), " %02x", net[i]); Put(hx); }
        rc = tls_consume_stream(ctx, net, (int)n, tls_default_verify);
        L("  rx %ld bytes -> consume rc=%ld", n, rc); L(" (t=%ld ticks)\n", TickCount() - t1, 0);
        if (rc < 0) { L("tls_consume_stream error %ld\n", rc, 0); goto done; }
        if (Flush(ctx)) { Put("send failed after consume"); goto done; }
    }
    t2 = TickCount();
    L("TLS handshake: %ld ticks = %ld.x s\n", t2 - t1, (t2 - t1) / 60);
    Ls("cipher=%s\n", tls_cipher_name(ctx));

    sprintf(req, "GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: httpsget/0.1 (System 7; 68K; cryanc; AppleBridge)\r\nConnection: close\r\n\r\n", gPath, host);
    tls_write(ctx, (unsigned char *)req, strlen(req)); Flush(ctx);
    for (;;) {
        n = Recv(net, NETBUF);
        if (n < 0) break;
        if (n == 0) { Yield(); if (TickCount() - t2 > 60L * 60) break; continue; }
        if (tls_consume_stream(ctx, net, (int)n, tls_default_verify) < 0) break;
        Flush(ctx);
        while ((n = tls_read(ctx, net, NETBUF)) > 0) {
            if (!t3) t3 = TickCount();
            { long w = n; FSWrite(gOut, &w, net); }
            if (!shown) { char *e; int k = n > 120 ? 120 : (int)n; net[k] = 0; e = strchr((char *)net, '\r'); if (e) *e = 0; Ls("status line: %s\n", (char *)net); shown = 1; }
            total += n;
        }
    }
    L("first app byte: %ld ticks after request\n", t3 ? t3 - t2 : -1, 0);
    L("body bytes: %ld  total %ld ticks\n", total, TickCount() - t0);
done:
    if (gOut) { FSClose(gOut); gOut = 0; }
    TcpClose();
    WriteLog();
    Put("done.");
    { unsigned long e = TickCount() + 30; while (TickCount() < e) Yield(); }
    ExitToShell();
    return 0;
}
