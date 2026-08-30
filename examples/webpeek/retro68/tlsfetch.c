/* tlsfetch.c — see tlsfetch.h. Socket idiom after AppleBridge's transport_mactcp.c:
 * PBControl on the ".IPP" driver, active open issued asynchronously and polled
 * with a yield, receive by TCPStatus.amtUnreadData, send by a one-entry WDS. */
#include <MacTypes.h>
#include <Devices.h>
#include <Memory.h>
#include <Events.h>
#include <Errors.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "MacTCP.h"
#include "cryanc.h"
#include "roots.h"
#include "tlsfetch.h"

#define RCVBUF   16384L
#define NETBUF   8192
#define VALID_TO 0xC0
#define DNS_PORT 53

long tf_ticks_dns, tf_ticks_connect, tf_ticks_handshake, tf_ticks_total; int tf_read_timeout, tf_last_state;
char tf_cipher[64];

static short     gIP;               /* .IPP driver refnum */
static StreamPtr gStream;
static Ptr       gRcv;
static const tf_config *gCfg;

static void L(const char *s) { if (gCfg && gCfg->log) gCfg->log(s); }
static void Lf(const char *fmt, long a, long b) { char line[160]; sprintf(line, fmt, a, b); L(line); }
static void Yield(void) { if (gCfg && gCfg->yield) gCfg->yield(); }

/* ---- UDP DNS parameter blocks (only what the A-query needs) ---- */
typedef struct { Ptr rcvBuff; unsigned long rcvBuffLen; void *notifyProc; unsigned short localPort; Ptr userDataPtr; unsigned short endingPort; } UDPCreatePB;
typedef struct { unsigned short reserved; ip_addr remoteHost; unsigned short remotePort; Ptr wdsPtr; Boolean checkSum; SInt8 filler; unsigned short sendLength; Ptr userDataPtr; unsigned short localPort; } UDPSendPB;
typedef struct { unsigned short timeOut; ip_addr remoteHost; unsigned short remotePort; Ptr rcvBuff; unsigned short rcvBuffLen; unsigned short secondTimeStamp; Ptr userDataPtr; ip_addr destHost; unsigned short destPort; } UDPReceivePB;
typedef struct {
    SInt8 fill12[12]; void *ioCompletion; short ioResult; Ptr ioNamePtr; short ioVRefNum, ioCRefNum, csCode;
    StreamPtr udpStream;
    union { UDPCreatePB create; UDPSendPB send; UDPReceivePB receive; } csParam;
} UDPiopb;
enum { UDPCreate = 20, UDPRead = 21, UDPBfrReturn = 22, UDPWrite = 23, UDPRelease = 24 };

/* ---- entropy for cryanc (TLS_ENTROPY_HOOK): timing the machine cannot predict ---- */
int tls_entropy_hook(unsigned char *out, int len) {
    unsigned long pool[32]; Point m; unsigned long t; int i, n = 0;
    for (i = 0; i < 24; i++) { unsigned long t0 = TickCount(), c = 0; while (TickCount() == t0) c++; pool[n++] = c ^ (t0 << 20); }
    GetMouse(&m); GetDateTime(&t);
    pool[n++] = t; pool[n++] = ((unsigned long)m.v << 16) | (unsigned short)m.h;
    pool[n++] = (unsigned long)&m; pool[n++] = (unsigned long)malloc(1);
    pool[n++] = TickCount(); pool[n++] = *(unsigned long *)0x16A;
    if (len > (int)(n * sizeof(long))) len = n * sizeof(long);
    memcpy(out, pool, len);
    return len;
}
int usleep(unsigned long us) { unsigned long e = TickCount() + us / 16667 + 1; while (TickCount() < e) Yield(); return 0; }

/* ---- URL ---- */
static unsigned long ParseIP(const char *s) {
    unsigned a, b, c, d; if (sscanf(s, "%u.%u.%u.%u", &a, &b, &c, &d) != 4) return 0;
    return ((unsigned long)a << 24) | (b << 16) | (c << 8) | d;
}
int tf_parse_url(const char *url, tf_url *u) {
    const char *p = url, *slash, *colon; size_t n;
    memset(u, 0, sizeof *u);
    if (!strncmp(p, "https://", 8)) { u->https = 1; p += 8; }
    else if (!strncmp(p, "http://", 7)) { u->https = 0; p += 7; }
    else { u->https = 1; }                                   /* bare host: https */
    slash = strchr(p, '/');
    n = slash ? (size_t)(slash - p) : strlen(p);
    if (n == 0 || n >= sizeof u->host) return 0;
    memcpy(u->host, p, n); u->host[n] = 0;
    colon = strchr(u->host, ':');
    if (colon) { u->port = atoi(colon + 1); *(char *)colon = 0; } else u->port = u->https ? 443 : 80;
    if (slash) { strncpy(u->path, slash, sizeof u->path - 1); } else strcpy(u->path, "/");
    return u->host[0] != 0;
}

/* ---- driver ---- */
static int OpenIP(void) {
    if (gIP) return 0;
    if (OpenDriver("\p.IPP", &gIP) != noErr) { L("no MacTCP driver (.IPP)"); gIP = 0; return -1; }
    return 0;
}

/* ---- DNS: one A query over UDP, CNAME chains followed inside the answer ---- */
int tf_resolve(const tf_config *cfg, const char *host, unsigned long *ip) {
    UDPiopb pb; wdsEntry w[2]; unsigned char q[512], *p, *ans; int qlen, i, rc; long t0;
    Ptr rcv; StreamPtr s; unsigned short id, ancount, qdcount; const char *lbl;
    unsigned long server = cfg->dns_server ? cfg->dns_server : 0x0A000203UL;   /* 10.0.2.3: slirp */
    gCfg = cfg;
    *ip = ParseIP(host);
    if (*ip) return 0;
    if (OpenIP()) return -1;
    t0 = TickCount();
    /* build the query */
    id = (unsigned short)(TickCount() ^ ((unsigned long)&pb >> 4));
    p = q; *p++ = id >> 8; *p++ = id; *p++ = 1; *p++ = 0;   /* RD */
    *p++ = 0; *p++ = 1; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0; *p++ = 0;
    lbl = host;
    while (*lbl) { const char *dot = strchr(lbl, '.'); int n = dot ? (int)(dot - lbl) : (int)strlen(lbl); if (n <= 0 || n > 63) return -2; *p++ = n; memcpy(p, lbl, n); p += n; lbl += n; if (*lbl == '.') lbl++; }
    *p++ = 0; *p++ = 0; *p++ = 1; *p++ = 0; *p++ = 1;       /* QTYPE A, QCLASS IN */
    qlen = (int)(p - q);
    rcv = NewPtr(4096); if (!rcv) return -3;
    memset(&pb, 0, sizeof pb); pb.ioCRefNum = gIP; pb.csCode = UDPCreate;
    pb.csParam.create.rcvBuff = rcv; pb.csParam.create.rcvBuffLen = 4096; pb.csParam.create.localPort = 0;
    if (PBControlSync((ParmBlkPtr)&pb) != noErr) { DisposePtr(rcv); L("UDPCreate failed"); return -4; }
    s = pb.udpStream;
    w[0].length = qlen; w[0].ptr = (Ptr)q; w[1].length = 0; w[1].ptr = 0;
    memset(&pb, 0, sizeof pb); pb.ioCRefNum = gIP; pb.csCode = UDPWrite; pb.udpStream = s;
    pb.csParam.send.remoteHost = server; pb.csParam.send.remotePort = DNS_PORT; pb.csParam.send.wdsPtr = (Ptr)w; pb.csParam.send.checkSum = 1;
    if (PBControlSync((ParmBlkPtr)&pb) != noErr) { L("UDPWrite failed"); rc = -5; goto out; }
    /* read with a 5 s timeout, async so the UI keeps breathing */
    memset(&pb, 0, sizeof pb); pb.ioCRefNum = gIP; pb.csCode = UDPRead; pb.udpStream = s;
    pb.csParam.receive.timeOut = 5; pb.ioResult = 1;
    if (PBControlAsync((ParmBlkPtr)&pb) != noErr) { L("UDPRead dispatch failed"); rc = -6; goto out; }
    while (pb.ioResult > 0) Yield();
    if (pb.ioResult != noErr) { Lf("DNS: no answer from server (err %ld)", pb.ioResult, 0); rc = -7; goto out; }
    ans = (unsigned char *)pb.csParam.receive.rcvBuff;
    rc = -8;
    if (pb.csParam.receive.rcvBuffLen >= 12 && ans[0] == (id >> 8) && ans[1] == (id & 0xFF)) {
        int len = pb.csParam.receive.rcvBuffLen;
        qdcount = (ans[4] << 8) | ans[5]; ancount = (ans[6] << 8) | ans[7];
        p = ans + 12;
        for (i = 0; i < qdcount && p < ans + len; i++) { while (p < ans + len && *p) { if (*p & 0xC0) { p++; break; } p += *p + 1; } p += 5; }
        for (i = 0; i < ancount && p + 12 <= ans + len; i++) {
            unsigned short type, rdlen;
            if (*p & 0xC0) p += 2; else { while (p < ans + len && *p) p += *p + 1; p++; }
            type = (p[0] << 8) | p[1]; rdlen = (p[8] << 8) | p[9]; p += 10;
            if (type == 1 && rdlen == 4) { *ip = ((unsigned long)p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]; rc = 0; break; }
            p += rdlen;                                     /* CNAME etc.: skip, the A follows */
        }
        if (rc) { int rcode = ans[3] & 0x0F; Lf("DNS: no A record (rcode %ld, %ld answers)", rcode, ancount); }
    }
    { UDPiopb b; memset(&b, 0, sizeof b); b.ioCRefNum = gIP; b.csCode = UDPBfrReturn; b.udpStream = s; b.csParam.receive.rcvBuff = (Ptr)ans; PBControlSync((ParmBlkPtr)&b); }
out:
    memset(&pb, 0, sizeof pb); pb.ioCRefNum = gIP; pb.csCode = UDPRelease; pb.udpStream = s; PBControlSync((ParmBlkPtr)&pb);
    DisposePtr(rcv);
    tf_ticks_dns = TickCount() - t0;
    return rc;
}

/* ---- TCP ---- */
static void PB(TCPiopb *pb, short cs) { memset(pb, 0, sizeof *pb); pb->ioCRefNum = gIP; pb->csCode = cs; pb->tcpStream = gStream; }
static OSErr Connect(ip_addr host, int port) {
    TCPiopb pb; OSErr err; long t0;
    gRcv = NewPtr(RCVBUF); if (!gRcv) return memFullErr;
    PB(&pb, TCPCreate); pb.csParam.create.rcvBuff = gRcv; pb.csParam.create.rcvBuffLen = RCVBUF;
    err = PBControlSync((ParmBlkPtr)&pb); if (err) return err;
    gStream = pb.tcpStream;
    PB(&pb, TCPActiveOpen);
    pb.csParam.open.ulpTimeoutValue = 30; pb.csParam.open.ulpTimeoutAction = 1; pb.csParam.open.validityFlags = VALID_TO;
    pb.csParam.open.remoteHost = host; pb.csParam.open.remotePort = (tcp_port)port; pb.ioResult = 1;
    err = PBControlAsync((ParmBlkPtr)&pb); if (err) return err;
    t0 = TickCount();
    while (pb.ioResult > 0) { Yield(); if (TickCount() - t0 > 30L * 60) { PB(&pb, TCPAbort); PBControlSync((ParmBlkPtr)&pb); return -1; } }
    return pb.ioResult;
}
static int gLastState;
static long Recv(unsigned char *buf, long max) {           /* >0 bytes, 0 none, <0 gone */
    TCPiopb pb; unsigned short avail;
    PB(&pb, TCPStatus); if (PBControlSync((ParmBlkPtr)&pb)) { gLastState = -1; return -1; }
    avail = pb.csParam.status.amtUnreadData; gLastState = pb.csParam.status.connectionState;
    if (!avail) return (pb.csParam.status.connectionState <= 8) ? 0 : -1;
    if (avail > max) avail = (unsigned short)max;
    PB(&pb, TCPRcv); pb.csParam.receive.commandTimeoutValue = 1; pb.csParam.receive.rcvBuff = (Ptr)buf; pb.csParam.receive.rcvBuffLen = avail;
    if (PBControlSync((ParmBlkPtr)&pb)) return -1;
    return pb.csParam.receive.rcvBuffLen;
}
static OSErr Send(const unsigned char *p, long n) {
    TCPiopb pb; wdsEntry w[2]; OSErr err;
    while (n > 0) {
        long c = n > 32767 ? 32767 : n;
        w[0].length = (unsigned short)c; w[0].ptr = (Ptr)p; w[1].length = 0; w[1].ptr = 0;
        PB(&pb, TCPSend); pb.csParam.send.ulpTimeoutValue = 30; pb.csParam.send.ulpTimeoutAction = 1; pb.csParam.send.validityFlags = VALID_TO;
        pb.csParam.send.pushFlag = 1; pb.csParam.send.wdsPtr = (Ptr)w;
        err = PBControlSync((ParmBlkPtr)&pb); if (err) return err;
        p += c; n -= c; Yield();
    }
    return noErr;
}
static void TcpClose(void) {
    TCPiopb pb;
    if (gStream) {
        PB(&pb, TCPClose); pb.csParam.close.ulpTimeoutValue = 5; pb.csParam.close.ulpTimeoutAction = 1; pb.csParam.close.validityFlags = VALID_TO; PBControlSync((ParmBlkPtr)&pb);
        PB(&pb, TCPRelease); PBControlSync((ParmBlkPtr)&pb); gStream = 0;
    }
    if (gRcv) { DisposePtr(gRcv); gRcv = NULL; }
}
static OSErr Flush(struct TLSContext *ctx) {
    unsigned int len; const unsigned char *out = tls_get_write_buffer(ctx, &len); OSErr err = noErr;
    if (out && len) err = Send(out, len);
    tls_buffer_clear(ctx);
    return err;
}

/* ---- the GET ---- */
int tf_get(const tf_config *cfg, const tf_url *u) {
    unsigned long ip; OSErr err; long t0, t1, n, total = 0; int rc = -1, stop = 0;
    struct TLSContext *ctx = NULL; unsigned char *net = NULL; static char req[900];
    gCfg = cfg; tf_cipher[0] = 0; tf_ticks_handshake = 0; tf_read_timeout = 0;
    t0 = TickCount();
    if (OpenIP()) return -1;
    if (tf_resolve(cfg, u->host, &ip)) { L("name resolution failed"); return -2; }
    { char line[160]; sprintf(line, "%s -> %lu.%lu.%lu.%lu (%ld ticks)", u->host, ip >> 24, (ip >> 16) & 255, (ip >> 8) & 255, ip & 255, tf_ticks_dns); L(line); }
    t1 = TickCount();
    err = Connect(ip, u->port);
    tf_ticks_connect = TickCount() - t1;
    if (err) { Lf("connect failed (err %ld)", err, 0); TcpClose(); return -3; }
    Lf("connected in %ld ticks", tf_ticks_connect, 0);
    net = (unsigned char *)malloc(NETBUF); if (!net) { TcpClose(); return -4; }
    sprintf(req, "GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: WebPeek/2.0 (Macintosh; 68K; System 7; cryanc)\r\nConnection: close\r\n\r\n", u->path, u->host);

    if (u->https) {
        tls_init();
        ctx = tls_create_context(0, cfg->tls12 ? TLS_V12 : TLS_V13);
        tls_sni_set(ctx, u->host);
        tls_load_root_certificates(ctx, (const unsigned char *)ttls_roots_pem, TTLS_ROOTS_LEN);
        tls_client_connect(ctx);
        t1 = TickCount();
        if (Flush(ctx)) { L("send failed"); goto out; }
        while (!tls_established(ctx)) {
            int r;
            n = Recv(net, NETBUF);
            if (n < 0) { L("connection dropped during the handshake"); goto out; }
            if (n == 0) { Yield(); if (TickCount() - t1 > 120L * 60) { L("handshake timeout"); goto out; } continue; }
            r = tls_consume_stream(ctx, net, (int)n, cfg->verify ? tls_default_verify : NULL);
            if (r < 0) {
                if (r == TLS_BROKEN_CONNECTION || r == -13) L("certificate rejected (chain, name or root)");
                else Lf("TLS error %ld", r, 0);
                goto out;
            }
            if (Flush(ctx)) { L("send failed"); goto out; }
        }
        tf_ticks_handshake = TickCount() - t1;
        strncpy(tf_cipher, tls_cipher_name(ctx), sizeof tf_cipher - 1);
        { char line[160]; sprintf(line, "TLS %s in %ld ticks%s", tf_cipher, tf_ticks_handshake, cfg->verify ? ", chain verified" : ", UNVERIFIED"); L(line); }
        tls_write(ctx, (unsigned char *)req, strlen(req)); Flush(ctx);
    } else {
        if (Send((unsigned char *)req, strlen(req))) { L("send failed"); goto out; }
    }
    t1 = TickCount();
    for (;;) {
        n = Recv(net, NETBUF);
        if (n < 0) break;
        if (n == 0) { Yield(); if (TickCount() - t1 > 60L * 60) { Lf("read timeout (tcp state %ld)", gLastState, 0); tf_read_timeout = 1; break; } continue; }
        t1 = TickCount();
        if (ctx) {
            if (tls_consume_stream(ctx, net, (int)n, NULL) < 0) break;
            Flush(ctx);
            while ((n = tls_read(ctx, net, NETBUF)) > 0) { total += n; if (cfg->sink(net, n)) { stop = 1; break; } }
        } else {
            total += n; if (cfg->sink(net, n)) stop = 1;
        }
        if (stop) break;
    }
    rc = total > 0 ? 0 : -5;
    Lf("%ld bytes received", total, 0);
out:
    if (ctx) tls_destroy_context(ctx);
    if (net) free(net);
    TcpClose();
    tf_ticks_total = TickCount() - t0; tf_last_state = gLastState;
    return rc;
}
