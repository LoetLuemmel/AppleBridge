/*
 * IPScan - a System 7 LAN scanner (AppleBridge sibling project)
 *
 * Sweeps the local IPv4 /24 with concurrent Open Transport TCP-connect probes and
 * shows, for every responding device, a matrix row: IP, status, round-trip,
 * resolved name, and which well-known ports are open (-> inferred service).
 *
 *   Stage B: concurrent async probe pool, full /24, live progress, scrollable matrix.
 *   Stage C: name resolution (NetBIOS NBSTAT + mDNS reverse, then reverse-DNS),
 *            export (file + clipboard), dynamic column widths, resizable window.
 *   Stage D: a full System 7 application -- menu bar (Apple/File/Edit/Scan), About
 *            box, Standard File save, a Scan Options dialog, on-screen touch buttons,
 *            a complete event model, and Apple-Event awareness. Menus are mouse/touch
 *            operable (no Command key needed) and carry keyboard equivalents.
 *
 * Why TCP-connect, not ICMP: a bounded async OTConnect gives a clean three-way verdict
 * per port -- established=OPEN, T_DISCONNECT=REFUSED (host up, port closed),
 * timeout=nothing. The async-connect machinery is lifted from the AppleBridge daemon's
 * network.c (proven to survive Basilisk's cooperative scheduler).
 */

#include <OpenTransport.h>
#include <OpenTptInternet.h>
#include <OpenTptAppleTalk.h>
#include <Quickdraw.h>
#include <Windows.h>
#include <Fonts.h>
#include <Events.h>
#include <Menus.h>
#include <Controls.h>
#include <Dialogs.h>
#include <TextEdit.h>
#include <StandardFile.h>
#include <AppleEvents.h>
#include <Devices.h>
#include <ToolUtils.h>
#include <Memory.h>
#include <Scrap.h>
#include <Files.h>

/* This MPW headers vintage hides the Control Manager part codes / proc id behind
 * OLDROUTINENAMES; the numeric values are stable Mac OS ABI, so we supply them. */
#ifndef kControlScrollBarProc
#define kControlScrollBarProc    16
#endif
#ifndef kControlUpButtonPart
#define kControlUpButtonPart     20
#endif
#ifndef kControlDownButtonPart
#define kControlDownButtonPart   21
#endif
#ifndef kControlPageUpPart
#define kControlPageUpPart       22
#endif
#ifndef kControlPageDownPart
#define kControlPageDownPart     23
#endif
#ifndef kControlIndicatorPart
#define kControlIndicatorPart    129
#endif
#ifndef pushButProc
#define pushButProc              0
#endif

/* menu ids + item numbers (must match ipscan.r) */
#define mApple   128
#define mFile    129
#define mEdit    130
#define mScan    131
#define mView    132
#define mNet     133
#define rInfoAlert 129
#define iAbout   1
#define iSaveAs  1
#define iQuit    3
#define iCopy    4
#define iRescan  1
#define iStop    2
#define iOptions 4
#define rAboutAlert 128
#define rAboutDlog  132
#define rOptDialog  128

/* The QuickDraw globals are not in any library -- an application defines them. */
QDGlobals qd;

/* ---- scan configuration ---- */
#define NSLOTS    24              /* max concurrent probes (array size)          */
#define MAXPORTS  16
#define MAX_FOUND 254

typedef struct { UInt16 port; char name[12]; } PortDef;

/* known ports -> friendly service label (also the default probe set) */
static const PortDef gKnown[] = {
    { 9000, "ABridge" }, {  80, "HTTP" }, { 443, "HTTPS" }, { 445, "SMB" },
    { 139, "NetBIOS" }, { 548, "AFP"  }, {  22, "SSH"   }, { 631, "IPP" },
    {  21, "FTP"     }, {  23, "Telnet" }
};
#define NKNOWN (sizeof(gKnown) / sizeof(gKnown[0]))

/* runtime probe set + parameters (editable via Scan > Options...) */
static UInt16 gPorts[MAXPORTS];
static short  gNPorts     = 0;
static short  gFirstOctet = 1;
static short  gLastOctet  = 254;
static short  gProbeTicks = 36;   /* ~0.6 s per attempt                          */
static short  gNSlots     = NSLOTS;

/* per-host accumulator, indexed by last octet */
typedef struct { Boolean up; UInt32 openMask; long rtt; } HostRec;

typedef struct { InetHost ip; long rtt; UInt32 openMask; char name[80]; } Found;

enum { PR_TIMEOUT = 0, PR_OPEN = 1, PR_REFUSED = 2 };

typedef struct {
    EndpointRef        ep;
    short              host;
    short              portIdx;
    long               start;
    Boolean            busy;
    volatile OTEventCode ev;
} Slot;

static HostRec  gRec[256];
static Slot     gSlots[NSLOTS];
static Found    gFound[MAX_FOUND];
static short    gNFound = 0;

static InetHost  gLocalIP = 0, gNetmask = 0, gBase = 0;
static WindowPtr gWin = NULL;
static ControlHandle gVScroll = NULL;
static ControlHandle gBtnRescan = NULL, gBtnExport = NULL, gBtnCopy = NULL, gBtnQuit = NULL;
static short     gScrollTop = 0;
static short     gMonacoFont = 4;
static Boolean   gScanning = false;
static short     gProgress = 0;
static Boolean   gDone = false;
static Boolean   gStop = false;
static Boolean   gHasOutput = false;   /* false until a Scan/View produces output */

/* ---- tiny string helpers ---- */
static void UToStr(unsigned long n, char *s)
{
    char t[16]; short i = 0, j = 0;
    if (n == 0) { s[0] = '0'; s[1] = 0; return; }
    while (n > 0) { t[i++] = (char)('0' + (n % 10)); n /= 10; }
    while (i > 0) s[j++] = t[--i];
    s[j] = 0;
}
static void StrCat(char *dst, const char *src)
{
    short i = 0, j = 0;
    while (dst[i]) i++;
    while (src[j]) dst[i++] = src[j++];
    dst[i] = 0;
}
static long ParseUInt(const char *s)
{
    long v = 0; short i = 0;
    while (s[i] == ' ') i++;
    while (s[i] >= '0' && s[i] <= '9') { v = v * 10 + (s[i] - '0'); i++; }
    return v;
}
static void IPToStr(InetHost ip, char *s)
{
    char nb[8];
    s[0] = 0;
    UToStr((ip >> 24) & 0xFF, nb); StrCat(s, nb); StrCat(s, ".");
    UToStr((ip >> 16) & 0xFF, nb); StrCat(s, nb); StrCat(s, ".");
    UToStr((ip >>  8) & 0xFF, nb); StrCat(s, nb); StrCat(s, ".");
    UToStr( ip        & 0xFF, nb); StrCat(s, nb);
}
static void DrawCStr(const char *c)
{
    Str255 p; short i = 0;
    while (c[i] && i < 255) { p[i + 1] = (unsigned char)c[i]; i++; }
    p[0] = (unsigned char)i;
    DrawString(p);
}
/* friendly label for a port number: known name, else the number */
static void PortLabel(UInt16 p, char *out)
{
    short i, k;
    for (i = 0; i < (short)NKNOWN; i++) {
        if (gKnown[i].port == p) {
            for (k = 0; gKnown[i].name[k]; k++) out[k] = gKnown[i].name[k];
            out[k] = 0; return;
        }
    }
    UToStr((unsigned long)p, out);
}
static void InitPorts(void)
{
    short i;
    gNPorts = (short)NKNOWN;
    for (i = 0; i < gNPorts; i++) gPorts[i] = gKnown[i].port;
}

/* ---- Open Transport: one notifier per slot, context = the slot ---- */
static pascal void SlotNotifier(void *ctx, OTEventCode code, OTResult r, void *ck)
{
    Slot *s = (Slot *)ctx;
    if (r || ck) {}
    if (code == T_CONNECT || code == T_DISCONNECT) s->ev = code;
}

static Boolean StartProbe(Slot *s, short host, short portIdx)
{
    OSStatus    err;
    InetAddress addr, local;
    TCall       call;
    TBind       bindReq;

    s->ep = OTOpenEndpoint(OTCreateConfiguration(kTCPName), 0, NULL, &err);
    if (err != noErr || s->ep == NULL) return false;

    OTMemzero(&local, sizeof(local));
    OTInitInetAddress(&local, 0, kOTAnyInetAddress);
    bindReq.addr.buf = (UInt8 *)&local; bindReq.addr.len = sizeof(local); bindReq.qlen = 0;
    if (OTBind(s->ep, &bindReq, NULL) != noErr) { OTCloseProvider(s->ep); return false; }

    s->ev = 0;
    if (OTInstallNotifier(s->ep, SlotNotifier, s) != noErr) {
        OTCloseProvider(s->ep); return false;
    }
    if (OTSetAsynchronous(s->ep) != noErr) {
        OTRemoveNotifier(s->ep); OTCloseProvider(s->ep); return false;
    }

    OTMemzero(&addr, sizeof(addr));
    OTInitInetAddress(&addr, gPorts[portIdx], gBase | (InetHost)host);
    OTMemzero(&call, sizeof(call));
    call.addr.buf = (UInt8 *)&addr; call.addr.len = sizeof(addr);

    err = OTConnect(s->ep, &call, NULL);
    if (err != noErr && err != kOTNoDataErr) {
        OTRemoveNotifier(s->ep); OTCloseProvider(s->ep); return false;
    }
    s->host = host; s->portIdx = portIdx; s->start = TickCount(); s->busy = true;
    return true;
}

static void FinishSlot(Slot *s, short verdict)
{
    long el = TickCount() - s->start;
    if (verdict == PR_OPEN) {
        gRec[s->host].up = true;
        gRec[s->host].openMask |= (1UL << s->portIdx);
        if (gRec[s->host].rtt < 0) gRec[s->host].rtt = el;
        OTSndDisconnect(s->ep, NULL);
    } else if (verdict == PR_REFUSED) {
        gRec[s->host].up = true;
        if (gRec[s->host].rtt < 0) gRec[s->host].rtt = el;
        OTRcvDisconnect(s->ep, NULL);
    } else {
        OTSndDisconnect(s->ep, NULL);
    }
    OTRemoveNotifier(s->ep);
    OTCloseProvider(s->ep);
    s->busy = false; s->ep = NULL;
}

/* ---- name resolution: reverse-DNS, NetBIOS, mDNS ---- */
static void ReverseName(InetHost ip, char *out)
{
    InetSvcRef     svc;
    OSStatus       err;
    InetDomainName name;
    out[0] = '?'; out[1] = 0;
    svc = OTOpenInternetServices(kDefaultInternetServicesPath, 0, &err);
    if (err != noErr || svc == NULL) return;
    err = OTInetAddressToName(svc, ip, name);
    if (err == noErr && name[0]) {
        short i = 0;
        while (name[i] && i < 78) { out[i] = name[i]; i++; }
        out[i] = 0;
    }
    OTCloseProvider(svc);
}

/* one bounded UDP request/reply (synchronous + non-blocking poll) */
static long UdpQuery(InetHost dst, InetPort port, const char *q, long qlen,
                     char *reply, long replyMax, long timeoutTicks)
{
    EndpointRef ep;
    OSStatus    err;
    InetAddress local, to, from;
    TBind       bindReq;
    TUnitData   snd, rcv;
    OTFlags     flags;
    OTResult    res;
    long        start, got = 0;

    ep = OTOpenEndpoint(OTCreateConfiguration(kUDPName), 0, NULL, &err);
    if (err != noErr || ep == NULL) return 0;

    OTMemzero(&local, sizeof(local));
    OTInitInetAddress(&local, 0, kOTAnyInetAddress);
    bindReq.addr.buf = (UInt8 *)&local; bindReq.addr.len = sizeof(local); bindReq.qlen = 0;
    if (OTBind(ep, &bindReq, NULL) != noErr) { OTCloseProvider(ep); return 0; }
    OTSetNonBlocking(ep);

    OTMemzero(&to, sizeof(to));
    OTInitInetAddress(&to, port, dst);
    OTMemzero(&snd, sizeof(snd));
    snd.addr.buf = (UInt8 *)&to;  snd.addr.len  = sizeof(to);
    snd.udata.buf = (UInt8 *)q;   snd.udata.len = qlen;
    if (OTSndUData(ep, &snd) != noErr) { OTCloseProvider(ep); return 0; }

    start = TickCount();
    for (;;) {
        OTMemzero(&rcv, sizeof(rcv));
        OTMemzero(&from, sizeof(from));
        rcv.addr.buf  = (UInt8 *)&from;  rcv.addr.maxlen  = sizeof(from);
        rcv.udata.buf = (UInt8 *)reply;  rcv.udata.maxlen = replyMax;
        flags = 0;
        res = OTRcvUData(ep, &rcv, &flags);
        if (res == noErr) { got = rcv.udata.len; break; }
        if (res == kOTLookErr) { if (OTLook(ep) == T_UDERR) OTRcvUDErr(ep, NULL); }
        else if (res != kOTNoDataErr) break;
        if (TickCount() - start > timeoutTicks) break;
        SystemTask();
    }
    OTCloseProvider(ep);
    return got;
}

static void TrimRight(char *s)
{
    short e = 0;
    while (s[e]) e++;
    while (e > 0 && (s[e - 1] == ' ' || s[e - 1] == 0)) s[--e] = 0;
}

static Boolean NbnsName(InetHost ip, char *out)
{
    unsigned char q[50], rep[700], nb16[16];
    long n, i, base, nameCount;
    short k;

    q[0]=0x13; q[1]=0x37; q[2]=0x00; q[3]=0x00; q[4]=0x00; q[5]=0x01;
    q[6]=q[7]=q[8]=q[9]=q[10]=q[11]=0x00;
    q[12]=0x20;
    nb16[0]='*'; for (i=1;i<16;i++) nb16[i]=0x00;
    for (i=0;i<16;i++) {
        q[13+i*2]   = (unsigned char)('A' + (nb16[i] >> 4));
        q[13+i*2+1] = (unsigned char)('A' + (nb16[i] & 0x0F));
    }
    q[45]=0x00; q[46]=0x00; q[47]=0x21; q[48]=0x00; q[49]=0x01;

    n = UdpQuery(ip, 137, (char *)q, 50, (char *)rep, sizeof(rep), 36);
    if (n < 1) return false;

    base = -1;
    for (i = 12; i + 11 < n; i++) {
        if (rep[i]==0x00 && rep[i+1]==0x21 && rep[i+2]==0x00 && rep[i+3]==0x01) {
            base = i + 10; break;
        }
    }
    if (base < 0 || base >= n) return false;
    nameCount = rep[base++];
    for (i = 0; i < nameCount && base + 18 <= n; i++, base += 18) {
        if (rep[base+15] == 0x00 && !(rep[base+16] & 0x80)) {
            for (k = 0; k < 15; k++) out[k] = (char)rep[base+k];
            out[15] = 0; TrimRight(out);
            if (out[0]) return true;
        }
    }
    return false;
}

static long PutLabel(unsigned char *b, long pos, const char *s)
{
    long L = 0, k;
    while (s[L]) L++;
    b[pos++] = (unsigned char)L;
    for (k = 0; k < L; k++) b[pos++] = (unsigned char)s[k];
    return pos;
}
static long PutLabelNum(unsigned char *b, long pos, long v)
{
    char tmp[12]; UToStr((unsigned long)v, tmp); return PutLabel(b, pos, tmp);
}
static long SkipName(unsigned char *b, long pos, long n)
{
    while (pos < n) {
        unsigned char len = b[pos];
        if (len == 0) return pos + 1;
        if ((len & 0xC0) == 0xC0) return pos + 2;
        pos += 1 + len;
    }
    return pos;
}
static void DecodeName(unsigned char *b, long pos, long n, char *out, long max)
{
    long o = 0; short hops = 0;
    while (pos < n && hops < 20) {
        unsigned char len = b[pos];
        if (len == 0) break;
        if ((len & 0xC0) == 0xC0) { pos = ((len & 0x3F) << 8) | b[pos+1]; hops++; continue; }
        pos++;
        if (o > 0 && o < max - 1) out[o++] = '.';
        { long k; for (k = 0; k < len && pos < n && o < max - 1; k++) out[o++] = (char)b[pos++]; }
    }
    out[o] = 0;
}
static Boolean MdnsName(InetHost ip, char *out)
{
    unsigned char q[64], rep[1100];
    long n, i, pos, qd_, an, e;

    for (i = 0; i < 12; i++) q[i] = 0;
    q[5] = 0x01;
    i = 12;
    i = PutLabelNum(q, i,  ip        & 0xFF);
    i = PutLabelNum(q, i, (ip >>  8) & 0xFF);
    i = PutLabelNum(q, i, (ip >> 16) & 0xFF);
    i = PutLabelNum(q, i, (ip >> 24) & 0xFF);
    i = PutLabel(q, i, "in-addr");
    i = PutLabel(q, i, "arpa");
    q[i++] = 0x00; q[i++] = 0x00; q[i++] = 0x0C; q[i++] = 0x80; q[i++] = 0x01;

    n = UdpQuery(0xE00000FBUL, 5353, (char *)q, i, (char *)rep, sizeof(rep), 40);
    if (n < 12) return false;
    an = (rep[6] << 8) | rep[7];
    if (an == 0) return false;
    qd_ = (rep[4] << 8) | rep[5];
    pos = 12;
    for (i = 0; i < qd_; i++) { pos = SkipName(rep, pos, n); pos += 4; }
    pos = SkipName(rep, pos, n);
    if (pos + 10 > n) return false;
    pos += 10;
    DecodeName(rep, pos, n, out, 78);
    e = 0; while (out[e]) e++;
    if (e > 6 && out[e-6]=='.' && out[e-5]=='l' && out[e-4]=='o' &&
        out[e-3]=='c' && out[e-2]=='a' && out[e-1]=='l') out[e-6] = 0;
    return out[0] ? true : false;
}
static void ResolveName(InetHost ip, char *out)
{
    if (NbnsName(ip, out)) return;
    if (MdnsName(ip, out)) return;
    ReverseName(ip, out);
}

/* ---- layout (column x-positions sized to the data each draw) ---- */
#define COL_IP    8
#define ROW_H     13
#define BTN_TOP   5
#define BTN_BOT   23
#define TITLE_Y   38
#define HEAD_Y    54
#define SEP_Y     57
#define ROW0_Y    70
#define SB_TOP    60
#define SBW       15

static short gCharW = 6;

/* generic mode-agnostic table model (heap-backed cells stay clear of the A5 world) */
#define MAXCOLS  5
#define MAXTROWS 300
#define CELLW    42
enum { MODE_IP = 0, MODE_NBP = 1, MODE_ZONES = 2, MODE_PING = 3, MODE_TRACE = 4 };
static short gMode  = MODE_IP;
static Ptr   gCells = NULL;
static short gNRows = 0, gNCols = 5;
static char  gHdr[MAXCOLS][20];
static char  gTitle[120];
static short gColX[MAXCOLS], gColMax[MAXCOLS];   /* x position + char cap per col */

static char *Cell(short r, short c) { return gCells + ((long)r * MAXCOLS + c) * CELLW; }
static void TblBegin(short ncols) { gNRows = 0; gNCols = ncols; gHasOutput = true; }
static void TblHdr(short c, const char *s)
{ short k = 0; while (s[k] && k < 19) { gHdr[c][k] = s[k]; k++; } gHdr[c][k] = 0; }
static short TblRow(void)
{
    short r, c;
    if (gNRows >= MAXTROWS) return gNRows - 1;
    r = gNRows++;
    for (c = 0; c < MAXCOLS; c++) Cell(r, c)[0] = 0;   /* clear stale cells */
    return r;
}
static void TblSet(short r, short c, const char *s)
{ char *d = Cell(r, c); short k = 0; while (s[k] && k < CELLW - 1) { d[k] = s[k]; k++; } d[k] = 0; }

static short VisibleRows(void)
{
    short h = gWin->portRect.bottom - gWin->portRect.top;
    short n = (h - ROW0_Y - 4) / ROW_H;
    return (n < 1) ? 1 : n;
}
static void BuildFound(void)
{
    short octet;
    gNFound = 0;
    for (octet = gFirstOctet; octet <= gLastOctet; octet++) {
        if (gRec[octet].up && gNFound < MAX_FOUND) {
            gFound[gNFound].ip       = gBase | (InetHost)octet;
            gFound[gNFound].rtt      = gRec[octet].rtt;
            gFound[gNFound].openMask = gRec[octet].openMask;
            gFound[gNFound].name[0]  = '?'; gFound[gNFound].name[1] = 0;
            gNFound++;
        }
    }
}
static void UpdateScrollMax(void)
{
    short vis = VisibleRows(), mx = gNRows - vis;
    if (mx < 0) mx = 0;
    if (gVScroll) {
        SetControlMaximum(gVScroll, mx);
        if (gScrollTop > mx) { gScrollTop = mx; SetControlValue(gVScroll, mx); }
        HiliteControl(gVScroll, (mx > 0) ? 0 : 255);
    }
}
static void CopyTrunc(const char *src, char *dst, short maxc)
{
    short L = 0, k;
    while (src[L]) L++;
    if (maxc < 4) maxc = 4;
    if (L <= maxc) { for (k = 0; k <= L; k++) dst[k] = src[k]; return; }
    for (k = 0; k < maxc - 3; k++) dst[k] = src[k];
    dst[maxc-3]='.'; dst[maxc-2]='.'; dst[maxc-1]='.'; dst[maxc]=0;
}
/* size each column to header+cells (capped); compute x positions */
static void ComputeLayout(void)
{
    short c, r, L, x;
    char *cell;

    TextFont(gMonacoFont); TextSize(9);
    gCharW = CharWidth('0'); if (gCharW < 1) gCharW = 6;
    for (c = 0; c < gNCols; c++) {
        short mx = 0;
        L = 0; while (gHdr[c][L]) L++; if (L > mx) mx = L;
        for (r = 0; r < gNRows; r++) {
            cell = Cell(r, c); L = 0; while (cell[L]) L++; if (L > mx) mx = L;
        }
        if (mx > 30) mx = 30;
        gColMax[c] = mx;
    }
    x = COL_IP;
    for (c = 0; c < gNCols; c++) { gColX[c] = x; x += (gColMax[c] + 2) * gCharW; }
}

/* generic table renderer: title, headers, dotted grid, scrollable rows */
static void DrawMatrix(void)
{
    short y, r, c, vis, last, g, rows, vbot;
    char  tcell[64];
    Rect  rc;

    if (gWin == NULL || gCells == NULL) return;
    SetPort(gWin);
    rc = gWin->portRect;
    EraseRect(&rc);
    if (!gHasOutput) {                /* nothing scanned yet -- buttons only */
        DrawControls(gWin);
        DrawGrowIcon(gWin);
        return;
    }
    TextFont(gMonacoFont); TextSize(9);
    ComputeLayout();

    MoveTo(COL_IP, TITLE_Y); DrawCStr(gTitle);

    for (c = 0; c < gNCols; c++) { MoveTo(gColX[c], HEAD_Y); DrawCStr(gHdr[c]); }
    MoveTo(COL_IP, SEP_Y); LineTo(rc.right - SBW - 2, SEP_Y);

    vis = VisibleRows();
    last = gScrollTop + vis; if (last > gNRows) last = gNRows;
    rows = last - gScrollTop;
    vbot = ROW0_Y + ((rows > 0) ? (rows - 1) : 0) * ROW_H + 3;

    PenPat(&qd.ltGray);
    for (g = 0; g < rows; g++) {
        short gy = ROW0_Y + g * ROW_H + 3;
        MoveTo(COL_IP - 2, gy); LineTo(rc.right - SBW - 2, gy);
    }
    for (c = 1; c < gNCols; c++) { MoveTo(gColX[c] - 4, SEP_Y + 2); LineTo(gColX[c] - 4, vbot); }
    PenNormal();

    y = ROW0_Y;
    for (r = gScrollTop; r < last; r++) {
        for (c = 0; c < gNCols; c++) {
            short cap;
            if (c == gNCols - 1) { cap = (rc.right - SBW - 4 - gColX[c]) / gCharW; if (cap < 4) cap = 4; }
            else cap = gColMax[c];
            CopyTrunc(Cell(r, c), tcell, cap);
            MoveTo(gColX[c], y); DrawCStr(tcell);
        }
        y += ROW_H;
    }
    DrawControls(gWin);
    DrawGrowIcon(gWin);
}

/* set the title line for IP mode */
static void SetIPTitle(void)
{
    char nb[16], sub[20];
    IPToStr(gBase, sub);
    gTitle[0] = 0;
    StrCat(gTitle, "IP Devices  net="); StrCat(gTitle, sub); StrCat(gTitle, "/24  found=");
    UToStr((unsigned long)gNFound, nb); StrCat(gTitle, nb);
    if (gScanning) {
        StrCat(gTitle, "  scanning ."); UToStr((unsigned long)gProgress, nb); StrCat(gTitle, nb);
        StrCat(gTitle, "/"); UToStr((unsigned long)gLastOctet, nb); StrCat(gTitle, nb);
    } else StrCat(gTitle, "  done");
}

/* copy the IP results (gFound) into the generic table */
static void FillIPTable(void)
{
    short r, i, p; char nb[16], ipb[20], line[160], pl[16];
    TblBegin(5);
    TblHdr(0, "IP address"); TblHdr(1, "Status"); TblHdr(2, "ms");
    TblHdr(3, "Host name");  TblHdr(4, "Services");
    for (i = 0; i < gNFound; i++) {
        r = TblRow();
        IPToStr(gFound[i].ip, ipb); TblSet(r, 0, ipb);
        TblSet(r, 1, "up");
        if (gFound[i].rtt >= 0) { UToStr((unsigned long)(gFound[i].rtt*1000L/60L), nb); TblSet(r, 2, nb); }
        else TblSet(r, 2, "-");
        TblSet(r, 3, gFound[i].name);
        line[0] = 0;
        for (p = 0; p < gNPorts; p++) if (gFound[i].openMask & (1UL << p)) {
            if (line[0]) StrCat(line, " ");
            PortLabel(gPorts[p], pl); StrCat(line, pl);
        }
        if (!line[0]) StrCat(line, "(alive)");
        TblSet(r, 4, line);
    }
    SetIPTitle();
}

/* ---- AppleTalk ---- */
static ATSvcRef gAT = NULL;

static Boolean StrEq(const char *a, const char *b)
{ short i = 0; while (a[i] && b[i]) { if (a[i] != b[i]) return false; i++; } return a[i] == b[i]; }

static Boolean OpenAT(void)
{
    OSStatus err;
    if (gAT != NULL) return true;
    gAT = OTOpenAppleTalkServices(kDefaultAppleTalkServicesPath, 0, &err);
    return (err == noErr && gAT != NULL);
}
static void DDPToStr(DDPAddress *a, char *out)
{
    char nb[12];
    out[0] = 0;
    UToStr(a->fNetwork, nb); StrCat(out, nb); StrCat(out, ".");
    UToStr(a->fNodeID,  nb); StrCat(out, nb); StrCat(out, ":");
    UToStr(a->fSocket,  nb); StrCat(out, nb);
}

static void ShowIdentity(void)
{
    AppleTalkInfo info;
    TNetbuf nb;
    Str255  msg;
    char    c[256], t[24];
    short   k = 0;

    if (!OpenAT()) {
        ParamText("\pAppleTalk is not available on this machine.", "\p", "\p", "\p");
        Alert(rInfoAlert, NULL); return;
    }
    nb.buf = (UInt8 *)&info; nb.maxlen = sizeof(info); nb.len = 0;
    if (OTATalkGetInfo(gAT, &nb) != noErr) {
        ParamText("\pCould not read AppleTalk info.", "\p", "\p", "\p");
        Alert(rInfoAlert, NULL); return;
    }
    /* MPW C: '\n' is CR (0x0D) -- the char the Dialog Manager breaks lines on;
     * '\r' is LF (0x0A), which renders as a box glyph. */
    c[0] = 0;
    StrCat(c, "AppleTalk address  "); DDPToStr(&info.fOurAddress, t); StrCat(c, t); StrCat(c, "\n");
    StrCat(c, "Router  ");
    if (info.fFlags & kATalkInfoHasRouter) { DDPToStr(&info.fRouterAddress, t); StrCat(c, t); }
    else StrCat(c, "(none)");
    StrCat(c, "\n");
    StrCat(c, "Cable range  "); UToStr(info.fCableRange[0], t); StrCat(c, t);
    StrCat(c, "-"); UToStr(info.fCableRange[1], t); StrCat(c, t); StrCat(c, "\n");
    StrCat(c, "Extended  "); StrCat(c, (info.fFlags & kATalkInfoIsExtended) ? "yes" : "no");
    while (c[k] && k < 255) { msg[k+1] = c[k]; k++; }
    msg[0] = (unsigned char)k;
    ParamText(msg, "\p", "\p", "\p");
    Alert(rInfoAlert, NULL);
}

static void FillZones(void)
{
    TNetbuf zb, mz;
    Ptr     buf;
    Str255  nm;
    char    myzone[40];
    long    p;
    short   r, k;

    gMode = MODE_ZONES; gScrollTop = 0;
    TblBegin(1); TblHdr(0, "Zone");
    gTitle[0] = 0; StrCat(gTitle, "AppleTalk Zones");
    if (!OpenAT()) { TblSet(TblRow(), 0, "(AppleTalk not available)"); UpdateScrollMax(); DrawMatrix(); return; }

    myzone[0] = 0;
    mz.buf = (UInt8 *)nm; mz.maxlen = sizeof(nm); mz.len = 0;
    if (OTATalkGetMyZone(gAT, &mz) == noErr)
        { for (k = 0; k < nm[0] && k < 38; k++) myzone[k] = nm[k+1]; myzone[k] = 0; }

    buf = NewPtr(8192);
    if (buf) {
        zb.buf = (UInt8 *)buf; zb.maxlen = 8192; zb.len = 0;
        if (OTATalkGetZoneList(gAT, &zb) == noErr) {
            p = 0;
            while (p < zb.len) {
                short len = ((unsigned char *)buf)[p];
                char  zn[40];
                if (len == 0) break;
                for (k = 0; k < len && k < 38; k++) zn[k] = buf[p + 1 + k];
                zn[k] = 0;
                r = TblRow();
                if (myzone[0] && StrEq(zn, myzone)) {
                    char m[56]; m[0] = 0; StrCat(m, zn); StrCat(m, "   (my zone)"); TblSet(r, 0, m);
                } else TblSet(r, 0, zn);
                p += 1 + len;
            }
        }
        DisposePtr(buf);
    }
    if (gNRows == 0) TblSet(TblRow(), 0, myzone[0] ? myzone : "(no zones / not on AppleTalk)");
    { char nbs[16]; StrCat(gTitle, "  count="); UToStr((unsigned long)gNRows, nbs); StrCat(gTitle, nbs); }
    UpdateScrollMax(); DrawMatrix();
}

/* One NBP lookup of "=:=@<zone>" through an open NBP mapper; appends a table
 * row per responding entity. Per Inside Macintosh: OT, OTLookupName is a MAPPER
 * function -- the old code called it on an ATP *endpoint*, which silently failed
 * (in straight C every provider ref is void*, so the type error never showed),
 * leaving the list empty. Zone "*" is only THIS node's zone, so the caller walks
 * the whole zone list to also find servers reached through another zone. */
static void NbpLookupZone(MapperRef mapper, const char *zone)
{
    UInt8          nameBuf[128];
    char           nbpStr[80];
    long           nameLen;
    Ptr            rbuf;
    TLookupRequest req;
    TLookupReply   reply;
    OSStatus       err;

    nbpStr[0] = 0; StrCat(nbpStr, "=:=@"); StrCat(nbpStr, zone);
    nameLen = OTSetAddressFromNBPString(nameBuf, nbpStr, -1);

    rbuf = NewPtr(8192);
    if (rbuf == NULL) { return; }
    OTMemzero(&req, sizeof(req)); OTMemzero(&reply, sizeof(reply));
    req.name.buf = nameBuf; req.name.len = nameLen;
    req.addr.buf = NULL;    req.addr.len = 0;
    req.maxcnt = 200; req.timeout = 2000; req.flags = 0;
    reply.names.buf = (UInt8 *)rbuf; reply.names.maxlen = 8192; reply.names.len = 0;
    err = OTLookupName(mapper, &req, &reply);
    if (err == noErr && reply.rspcount > 0) {
        char *base  = (char *)rbuf;
        long  avail = (long)reply.names.len;   /* bytes OT actually returned */
        long  off = 0, n;
        for (n = 0; n < (long)reply.rspcount; n++) {
            TLookupBuffer *lb;
            UInt8     *ab, *nmp;
            NBPEntity  ent;
            char       obj[64], typ[64], zon[64], adr[28];
            long       alen, nlen, rec;
            short      r;
            /* HARDENING: never read or walk past what OT returned, and never hand
               the decoder a name longer than an NBPEntity (99 bytes). A malformed
               or larger-than-expected reply otherwise walks off the 8K buffer /
               overflows ent -> intermittent crash (the bug we were chasing). */
            if (off + (long)sizeof(TLookupBuffer) > avail) { break; }
            lb   = (TLookupBuffer *)(base + off);
            alen = (long)lb->fAddressLength;
            nlen = (long)lb->fNameLength;
            rec = 4 + alen + nlen;                 /* fAddressBuffer sits at offset 4 */
            if (alen < 0 || nlen < 0 || nlen > 96 || off + rec > avail) { break; }
            ab  = lb->fAddressBuffer;
            nmp = ab + alen;
            /* The name is an NBP name STRING ("obj:type@zone") -- let OT decode it. */
            OTSetNBPEntityFromAddress(&ent, nmp, (OTByteCount)nlen);
            obj[0] = typ[0] = zon[0] = 0;
            OTExtractNBPName(&ent, obj);
            OTExtractNBPType(&ent, typ);
            OTExtractNBPZone(&ent, zon);
            DDPToStr((DDPAddress *)ab, adr);
            r = TblRow();
            TblSet(r, 0, obj); TblSet(r, 1, typ);
            TblSet(r, 2, zon[0] ? zon : zone); TblSet(r, 3, adr);
            off += (rec + 3) & ~3L;                /* bounded OTNextLookupBuffer */
        }
    }
    DisposePtr(rbuf);
}

static void FillNBP(void)
{
    MapperRef mapper;
    OSStatus  err;
    Ptr       zbuf;
    Boolean   didZones = false;

    gMode = MODE_NBP; gScrollTop = 0;
    TblBegin(4); TblHdr(0, "Object"); TblHdr(1, "Type"); TblHdr(2, "Zone"); TblHdr(3, "Address");
    gTitle[0] = 0; StrCat(gTitle, "AppleTalk Devices (NBP)");
    UpdateScrollMax(); DrawMatrix();

    mapper = OTOpenMapper(OTCreateConfiguration(kNBPName), 0, &err);
    if (err != noErr || mapper == NULL) {
        TblSet(TblRow(), 0, "(cannot open NBP mapper)"); UpdateScrollMax(); DrawMatrix(); return;
    }

    /* Sweep every visible zone (like the Chooser); a flat/non-extended network
     * returns no list, so fall back to "*" (this node's own zone). */
    zbuf = NewPtr(8192);
    if (zbuf && OpenAT()) {
        TNetbuf zb;
        zb.buf = (UInt8 *)zbuf; zb.maxlen = 8192; zb.len = 0;
        if (OTATalkGetZoneList(gAT, &zb) == noErr && zb.len > 0) {
            long  p = 0; short nz = 0;
            while (p < (long)zb.len && nz < 64) {
                short len = ((unsigned char *)zbuf)[p];
                char  zn[40]; short k;
                if (len == 0) break;
                for (k = 0; k < len && k < 38; k++) zn[k] = zbuf[p + 1 + k];
                zn[k] = 0; TrimRight(zn);
                if (zn[0]) {
                    NbpLookupZone(mapper, zn);
                    didZones = true; nz++;
                    UpdateScrollMax(); DrawMatrix();   /* show results as they arrive */
                }
                p += 1 + len;
            }
        }
    }
    if (zbuf) DisposePtr(zbuf);
    if (!didZones) { NbpLookupZone(mapper, "*"); }

    OTCloseProvider(mapper);
    if (gNRows == 0) TblSet(TblRow(), 0, "(no NBP entities found)");
    { char nbs[16]; StrCat(gTitle, "  found="); UToStr((unsigned long)gNRows, nbs); StrCat(gTitle, nbs); }
    UpdateScrollMax(); DrawMatrix();
}

static void ShowIPView(void)
{
    gMode = MODE_IP; gScrollTop = 0;
    FillIPTable();                       /* shows results of the last /24 sweep */
    if (gNFound == 0)                    /* nothing scanned yet -- guide the user */
        TblSet(TblRow(), 0, "(no scan yet -- choose Scan > Rescan)");
    UpdateScrollMax(); DrawMatrix();
}

/* ---- report / export ---- */
static void App(char *buf, long *o, long max, const char *s)
{
    long j = 0;
    while (s[j] && *o < max - 1) buf[(*o)++] = s[j++];
}
static long BuildReport(char *buf, long max)
{
    long o = 0; short i, p; char nb[16], ipb[20], pl[16];
    App(buf,&o,max,"MacNetScan  net="); IPToStr(gBase,ipb); App(buf,&o,max,ipb);
    App(buf,&o,max,"/24  found="); UToStr((unsigned long)gNFound,nb); App(buf,&o,max,nb);
    App(buf,&o,max,"\n");
    App(buf,&o,max,"IP\tStatus\tms\tHost name\tServices\n");
    for (i = 0; i < gNFound; i++) {
        IPToStr(gFound[i].ip, ipb); App(buf,&o,max,ipb); App(buf,&o,max,"\tup\t");
        if (gFound[i].rtt >= 0) { UToStr((unsigned long)(gFound[i].rtt*1000L/60L),nb); App(buf,&o,max,nb); }
        else App(buf,&o,max,"-");
        App(buf,&o,max,"\t"); App(buf,&o,max,gFound[i].name); App(buf,&o,max,"\t");
        { Boolean any = false;
          for (p = 0; p < gNPorts; p++) if (gFound[i].openMask & (1UL << p)) {
              if (any) App(buf,&o,max," ");
              PortLabel(gPorts[p], pl); App(buf,&o,max,pl); any = true;
          }
          if (!any) App(buf,&o,max,"(alive)");
        }
        App(buf,&o,max,"\n");
    }
    return o;
}
static Ptr NewReport(long *outLen)
{
    long max = (long)gNFound * 120L + 256L;
    Ptr  b = NewPtr(max);
    if (b == NULL) { *outLen = 0; return NULL; }
    *outLen = BuildReport(b, max);
    return b;
}
static void WriteReportTo(FSSpec *spec, ScriptCode script)
{
    long len, count; Ptr b; OSErr err; short refNum;
    b = NewReport(&len); if (b == NULL) return;
    err = FSpDelete(spec);
    err = FSpCreate(spec, 'ttxt', 'TEXT', script);
    if (err == noErr || err == dupFNErr) {
        if (FSpOpenDF(spec, fsWrPerm, &refNum) == noErr) {
            count = len; FSWrite(refNum, &count, b); FSClose(refNum);
        }
    }
    DisposePtr(b);
}
static void ExportFile(void)   /* quick fixed-path export (Export button) */
{
    FSSpec spec;
    FSMakeFSSpec(0, 0, "\pMeinMac:MPW:IPScan:ipscan.txt", &spec);  /* fnfErr ok */
    WriteReportTo(&spec, 0);
}
static void ExportClip(void)
{
    long len; Ptr b = NewReport(&len);
    if (b == NULL) return;
    ZeroScrap(); PutScrap(len, 'TEXT', b);
    DisposePtr(b);
}

/* ---- the concurrent sweep ---- */
static void RunScan(void)
{
    InetInterfaceInfo info;
    long workTotal, workNext = 0, lastDraw = 0;
    short i, span;

    if (OTInetGetInterfaceInfo(&info, kDefaultInetInterface) == noErr) {
        gLocalIP = info.fAddress;
        gNetmask = info.fNetmask;
    }
    gBase = gLocalIP & 0xFFFFFF00UL;

    for (i = 0; i < 256; i++) { gRec[i].up = false; gRec[i].openMask = 0; gRec[i].rtt = -1; }
    for (i = 0; i < NSLOTS; i++) { gSlots[i].busy = false; gSlots[i].ep = NULL; }

    span = gLastOctet - gFirstOctet + 1; if (span < 0) span = 0;
    workTotal = (long)span * (long)gNPorts;
    gMode = MODE_IP; gScanning = true; gStop = false; gScrollTop = 0;

    for (;;) {
        Boolean anyBusy = false;
        for (i = 0; i < gNSlots; i++) {
            if (!gSlots[i].busy && workNext < workTotal && !gStop) {
                short host = gFirstOctet + (short)(workNext / gNPorts);
                short pidx = (short)(workNext % gNPorts);
                workNext++; gProgress = host;
                StartProbe(&gSlots[i], host, pidx);
            }
        }
        for (i = 0; i < gNSlots; i++) {
            if (gSlots[i].busy) {
                anyBusy = true;
                if (gSlots[i].ev == T_CONNECT)         FinishSlot(&gSlots[i], PR_OPEN);
                else if (gSlots[i].ev == T_DISCONNECT) FinishSlot(&gSlots[i], PR_REFUSED);
                else if (TickCount() - gSlots[i].start > gProbeTicks)
                                                       FinishSlot(&gSlots[i], PR_TIMEOUT);
            }
        }
        SystemTask();
        if (TickCount() - lastDraw > 20) {
            BuildFound(); FillIPTable(); UpdateScrollMax(); DrawMatrix(); lastDraw = TickCount();
        }
        if ((workNext >= workTotal || gStop) && !anyBusy) break;
    }

    gScanning = false;
    BuildFound(); FillIPTable(); UpdateScrollMax(); DrawMatrix();
    for (i = 0; i < gNFound && !gStop; i++) {
        ResolveName(gFound[i].ip, gFound[i].name);
        FillIPTable(); DrawMatrix();
    }
}

/* ---- controls ---- */
static void MakeScrollbar(void)
{
    Rect sb;
    short w = gWin->portRect.right - gWin->portRect.left;
    short h = gWin->portRect.bottom - gWin->portRect.top;
    SetRect(&sb, w - SBW, SB_TOP, w + 1, h - 14);
    gVScroll = NewControl(gWin, &sb, "\p", true, 0, 0, 0, kControlScrollBarProc, 0L);
}
static void MakeButtons(void)
{
    Rect b;
    SetRect(&b,   8, BTN_TOP,   8 + 72, BTN_BOT);
    gBtnRescan = NewControl(gWin, &b, "\pRescan", true, 0, 0, 1, pushButProc, 0L);
    SetRect(&b,  84, BTN_TOP,  84 + 72, BTN_BOT);
    gBtnExport = NewControl(gWin, &b, "\pExport", true, 0, 0, 1, pushButProc, 0L);
    SetRect(&b, 160, BTN_TOP, 160 + 64, BTN_BOT);
    gBtnCopy   = NewControl(gWin, &b, "\pCopy",   true, 0, 0, 1, pushButProc, 0L);
    SetRect(&b, 228, BTN_TOP, 228 + 56, BTN_BOT);
    gBtnQuit   = NewControl(gWin, &b, "\pQuit",   true, 0, 0, 1, pushButProc, 0L);
}
static void FitScrollbar(void)
{
    short w = gWin->portRect.right - gWin->portRect.left;
    short h = gWin->portRect.bottom - gWin->portRect.top;
    if (gVScroll == NULL) return;
    MoveControl(gVScroll, w - SBW, SB_TOP);
    SizeControl(gVScroll, SBW, (h - 14) - SB_TOP);
    UpdateScrollMax();
}

/* ---- menus, dialogs, Apple Events ---- */
static void SetupMenus(void)
{
    Handle    mbar;
    MenuHandle am;
    mbar = GetNewMBar(128);
    if (mbar) { SetMenuBar(mbar); DisposeHandle(mbar); }
    am = GetMenuHandle(mApple);
    if (am) AppendResMenu(am, 'DRVR');
    DrawMenuBar();
}
/* About box: item 3 is a Picture item (PICT 128, the MacNetScan LAN-scan logo);
   the Dialog Manager draws it, so no user-item draw proc is needed. */
static void DoAbout(void)
{
    DialogPtr d; short hit;
    d = GetNewDialog(rAboutDlog, NULL, (WindowPtr)-1L);
    if (d == NULL) return;
    ShowWindow(d);
    for (;;) { ModalDialog(NULL, &hit); if (hit == 1) break; }
    DisposeDialog(d);
}
static void SetField(DialogPtr d, short item, long val)
{
    short t; Handle h; Rect b; Str255 s; char c[16]; short k = 0;
    GetDialogItem(d, item, &t, &h, &b);
    UToStr((unsigned long)val, c);
    while (c[k]) { s[k+1] = c[k]; k++; }
    s[0] = (unsigned char)k;
    SetDialogItemText(h, s);
}
static void GetField(DialogPtr d, short item, char *out)
{
    short t, k; Handle h; Rect b; Str255 s;
    GetDialogItem(d, item, &t, &h, &b);
    GetDialogItemText(h, s);
    for (k = 0; k < s[0]; k++) out[k] = s[k+1];
    out[s[0]] = 0;
}
static void SetFieldStr(DialogPtr d, short item, const char *str)
{
    short t; Handle h; Rect b; Str255 s; short k = 0;
    GetDialogItem(d, item, &t, &h, &b);
    while (str[k]) { s[k+1] = str[k]; k++; }
    s[0] = (unsigned char)k;
    SetDialogItemText(h, s);
}
static void ParsePorts(const char *s)
{
    short n = 0; long v = 0; Boolean have = false; short i = 0;
    for (;;) {
        char c = s[i++];
        if (c >= '0' && c <= '9') { v = v*10 + (c - '0'); have = true; }
        else {
            if (have && n < MAXPORTS) gPorts[n++] = (UInt16)v;
            v = 0; have = false;
            if (c == 0) break;
        }
    }
    if (n > 0) gNPorts = n;
}
static void DoOptions(void)
{
    DialogPtr d; short hit; char f[160]; short i, o;

    d = GetNewDialog(rOptDialog, NULL, (WindowPtr)-1L);
    if (d == NULL) return;
    SetField(d, 4,  gFirstOctet);
    SetField(d, 6,  gLastOctet);
    SetField(d, 8,  (long)(gProbeTicks * 1000L / 60L));
    SetField(d, 10, gNSlots);
    o = 0;
    for (i = 0; i < gNPorts; i++) {
        char tmp[8]; short k = 0;
        if (i) f[o++] = ',';
        UToStr(gPorts[i], tmp);
        while (tmp[k]) f[o++] = tmp[k++];
    }
    f[o] = 0;
    SetFieldStr(d, 12, f);

    ShowWindow(d);
    for (;;) { ModalDialog(NULL, &hit); if (hit == 1 || hit == 2) break; }
    if (hit == 1) {
        long v;
        GetField(d, 4, f);  v = ParseUInt(f); if (v >= 0 && v <= 255) gFirstOctet = (short)v;
        GetField(d, 6, f);  v = ParseUInt(f); if (v >= 1 && v <= 255) gLastOctet  = (short)v;
        GetField(d, 8, f);  v = ParseUInt(f); { short t = (short)(v * 60L / 1000L);
                                                if (t < 3) t = 3; if (t > 600) t = 600; gProbeTicks = t; }
        GetField(d, 10, f); v = ParseUInt(f); if (v < 1) v = 1; if (v > NSLOTS) v = NSLOTS; gNSlots = (short)v;
        GetField(d, 12, f); ParsePorts(f);
        if (gFirstOctet > gLastOctet) { short t = gFirstOctet; gFirstOctet = gLastOctet; gLastOctet = t; }
    }
    DisposeDialog(d);
}
static void DoSaveAs(void)
{
    StandardFileReply reply;
    StandardPutFile("\pSave scan as:", "\pipscan.txt", &reply);
    if (reply.sfGood) WriteReportTo(&reply.sfFile, reply.sfScript);
}
/* ---- TCP/IP tools: DNS, Ping (ICMP), Traceroute ---- */
static Boolean AskHost(const unsigned char *prompt, char *out)
{
    DialogPtr d; short hit, t; Handle h; Rect b;
    d = GetNewDialog(130, NULL, (WindowPtr)-1L);
    if (d == NULL) return false;
    GetDialogItem(d, 3, &t, &h, &b); SetDialogItemText(h, prompt);
    ShowWindow(d);
    for (;;) { ModalDialog(NULL, &hit); if (hit == 1 || hit == 2) break; }
    if (hit == 1) GetField(d, 4, out); else out[0] = 0;
    DisposeDialog(d);
    return (hit == 1 && out[0]) ? true : false;
}
static Boolean ResolveHost(const char *name, InetHost *ip)
{
    InetSvcRef svc; OSStatus err; InetHostInfo hi;
    svc = OTOpenInternetServices(kDefaultInternetServicesPath, 0, &err);
    if (err != noErr || svc == NULL) return false;
    err = OTInetStringToAddress(svc, (char *)name, &hi);
    OTCloseProvider(svc);
    if (err == noErr && hi.addrs[0]) { *ip = hi.addrs[0]; return true; }
    return false;
}
static UInt16 ICMPChecksum(UInt8 *p, short n)
{
    long sum = 0; short i;
    for (i = 0; i + 1 < n; i += 2) sum += ((long)p[i] << 8) | p[i+1];
    if (i < n) sum += (long)p[i] << 8;
    while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);
    return (UInt16)(~sum);
}
static void SetTTL(EndpointRef ep, short ttl)
{
    struct { ByteCount len; OTXTILevel level; OTXTIName name; UInt32 status; UInt32 value; } opt;
    TOptMgmt req, ret;
    opt.len = sizeof(opt); opt.level = INET_IP; opt.name = kIP_TTL; opt.status = 0; opt.value = ttl;
    req.opt.buf = (UInt8 *)&opt; req.opt.len = sizeof(opt); req.flags = T_NEGOTIATE;
    ret.opt.buf = (UInt8 *)&opt; ret.opt.maxlen = sizeof(opt);
    OTOptionManagement(ep, &req, &ret);
}
/* One ICMP echo. Returns RTT ticks (>=0) on any reply, -1 on timeout.
 * *typeOut = ICMP type of the reply (0=echo reply, 11=time exceeded), *fromOut = sender. */
static long IcmpEcho(InetHost dst, short ttl, UInt16 id, UInt16 seq,
                     InetHost *fromOut, short *typeOut, long timeoutTicks)
{
    EndpointRef ep; OSStatus err;
    InetAddress to, from, local; TBind bindReq;
    TUnitData snd, rcv; OTFlags flags; OTResult res;
    UInt8 msg[40], rep[256]; long start, rtt = -1; short off;

    *typeOut = -1; *fromOut = 0;
    ep = OTOpenEndpoint(OTCreateConfiguration(kRawIPName), 0, NULL, &err);
    if (err != noErr || ep == NULL) return -1;
    OTMemzero(&local, sizeof(local)); OTInitInetAddress(&local, 0, kOTAnyInetAddress);
    bindReq.addr.buf = (UInt8 *)&local; bindReq.addr.len = sizeof(local); bindReq.qlen = 0;
    if (OTBind(ep, &bindReq, NULL) != noErr) { OTCloseProvider(ep); return -1; }
    OTSetNonBlocking(ep);
    if (ttl > 0) SetTTL(ep, ttl);

    OTMemzero(msg, sizeof(msg));
    msg[0] = 8; msg[1] = 0;                       /* echo request */
    msg[4] = id >> 8; msg[5] = id & 0xFF;
    msg[6] = seq >> 8; msg[7] = seq & 0xFF;
    { short k; for (k = 8; k < 40; k++) msg[k] = (UInt8)k; }
    { UInt16 ck = ICMPChecksum(msg, 40); msg[2] = ck >> 8; msg[3] = ck & 0xFF; }

    OTMemzero(&to, sizeof(to)); OTInitInetAddress(&to, 0, dst);
    OTMemzero(&snd, sizeof(snd));
    snd.addr.buf = (UInt8 *)&to; snd.addr.len = sizeof(to);
    snd.udata.buf = msg; snd.udata.len = 40;

    start = TickCount();
    if (OTSndUData(ep, &snd) != noErr) { OTCloseProvider(ep); return -1; }
    for (;;) {
        OTMemzero(&rcv, sizeof(rcv)); OTMemzero(&from, sizeof(from));
        rcv.addr.buf = (UInt8 *)&from; rcv.addr.maxlen = sizeof(from);
        rcv.udata.buf = rep; rcv.udata.maxlen = sizeof(rep); flags = 0;
        res = OTRcvUData(ep, &rcv, &flags);
        if (res == noErr) {
            off = ((rep[0] & 0xF0) == 0x40) ? (short)((rep[0] & 0x0F) * 4) : 0;  /* skip IP hdr */
            *typeOut = rep[off];
            *fromOut = from.fHost;
            rtt = TickCount() - start;
            break;
        }
        if (res == kOTLookErr) { if (OTLook(ep) == T_UDERR) OTRcvUDErr(ep, NULL); }
        else if (res != kOTNoDataErr) break;
        if (TickCount() - start > timeoutTicks) break;
        SystemTask();
    }
    OTCloseProvider(ep);
    return rtt;
}
static void DoDNS(void)
{
    char host[80], c[256]; InetSvcRef svc; OSStatus err; InetHostInfo hi;
    Str255 msg; short k = 0, i;
    if (!AskHost("\pHost name or dotted IP:", host)) return;
    svc = OTOpenInternetServices(kDefaultInternetServicesPath, 0, &err);
    if (err != noErr || svc == NULL) { ParamText("\pDNS is not available.", "\p","\p","\p"); Alert(rInfoAlert, NULL); return; }
    err = OTInetStringToAddress(svc, host, &hi);
    c[0] = 0;
    if (err == noErr) {
        StrCat(c, "Name  "); StrCat(c, hi.name); StrCat(c, "\n");
        for (i = 0; i < 10 && hi.addrs[i]; i++) { char ab[20]; IPToStr(hi.addrs[i], ab); StrCat(c, ab); StrCat(c, "\n"); }
    } else StrCat(c, "Lookup failed.");
    OTCloseProvider(svc);
    while (c[k] && k < 255) { msg[k+1] = c[k]; k++; } msg[0] = (unsigned char)k;
    ParamText(msg, "\p","\p","\p"); Alert(rInfoAlert, NULL);
}
/* TCP-connect "ping" — reliable RTT on OT without raw IP. A completed handshake
 * OR an active refusal (RST) both prove the host is alive. */
static volatile OTEventCode gPingEv = 0;
static pascal void PingNotifier(void *ctx, OTEventCode code, OTResult r, void *ck)
{ if (ctx || r || ck) {} if (code == T_CONNECT || code == T_DISCONNECT) gPingEv = code; }

static long TcpPing(InetHost ip, UInt16 port, long timeoutTicks)
{
    EndpointRef ep; OSStatus err;
    InetAddress addr, local; TCall call; TBind bindReq; long start, rtt = -1;
    ep = OTOpenEndpoint(OTCreateConfiguration(kTCPName), 0, NULL, &err);
    if (err != noErr || ep == NULL) return -1;
    OTMemzero(&local, sizeof(local)); OTInitInetAddress(&local, 0, kOTAnyInetAddress);
    bindReq.addr.buf = (UInt8 *)&local; bindReq.addr.len = sizeof(local); bindReq.qlen = 0;
    if (OTBind(ep, &bindReq, NULL) != noErr) { OTCloseProvider(ep); return -1; }
    gPingEv = 0;
    if (OTInstallNotifier(ep, PingNotifier, NULL) != noErr) { OTCloseProvider(ep); return -1; }
    OTSetAsynchronous(ep);
    OTMemzero(&addr, sizeof(addr)); OTInitInetAddress(&addr, port, ip);
    OTMemzero(&call, sizeof(call)); call.addr.buf = (UInt8 *)&addr; call.addr.len = sizeof(addr);
    start = TickCount();
    err = OTConnect(ep, &call, NULL);
    if (err != noErr && err != kOTNoDataErr) { OTRemoveNotifier(ep); OTCloseProvider(ep); return -1; }
    for (;;) {
        if (gPingEv == T_CONNECT)    { rtt = TickCount() - start; OTSndDisconnect(ep, NULL); break; }
        if (gPingEv == T_DISCONNECT) { rtt = TickCount() - start; OTRcvDisconnect(ep, NULL); break; }
        if (TickCount() - start > timeoutTicks) { OTSndDisconnect(ep, NULL); break; }
        SystemTask();
    }
    OTRemoveNotifier(ep); OTCloseProvider(ep);
    return rtt;
}
static void DoPing(void)
{
    char host[80], ab[20], nb[16]; InetHost ip; short i, ok = 0; long sum = 0;
    if (!AskHost("\pPing host (name or IP) - TCP:80", host)) return;
    if (!ResolveHost(host, &ip)) { ParamText("\pCannot resolve that host.", "\p","\p","\p"); Alert(rInfoAlert, NULL); return; }
    gMode = MODE_PING; gScrollTop = 0;
    TblBegin(4); TblHdr(0, "Seq"); TblHdr(1, "Host"); TblHdr(2, "ms"); TblHdr(3, "Status");
    IPToStr(ip, ab);
    gTitle[0] = 0; StrCat(gTitle, "Ping (TCP:80) "); StrCat(gTitle, host); StrCat(gTitle, "  ["); StrCat(gTitle, ab); StrCat(gTitle, "]");
    UpdateScrollMax(); DrawMatrix();
    for (i = 1; i <= 8 && !gStop; i++) {
        long rtt; short r;
        rtt = TcpPing(ip, 80, 120);
        r = TblRow();
        UToStr(i, nb); TblSet(r, 0, nb);
        TblSet(r, 1, ab);
        if (rtt >= 0) {
            long ms = rtt * 1000L / 60L; UToStr(ms, nb); TblSet(r, 2, nb); TblSet(r, 3, "alive");
            ok++; sum += ms;
        } else { TblSet(r, 2, "-"); TblSet(r, 3, "timeout"); }
        UpdateScrollMax(); DrawMatrix();
    }
    StrCat(gTitle, "  recv "); UToStr(ok, nb); StrCat(gTitle, nb); StrCat(gTitle, "/8");
    if (ok) { StrCat(gTitle, " avg "); UToStr(sum / ok, nb); StrCat(gTitle, nb); StrCat(gTitle, "ms"); }
    DrawMatrix();
}
static void DoTrace(void)
{
    char host[80], ab[20], nb[16], nm[80]; InetHost ip; short ttl;
    if (!AskHost("\pTraceroute host (name or IP):", host)) return;
    if (!ResolveHost(host, &ip)) { ParamText("\pCannot resolve that host.", "\p","\p","\p"); Alert(rInfoAlert, NULL); return; }
    gMode = MODE_TRACE; gScrollTop = 0;
    TblBegin(4); TblHdr(0, "Hop"); TblHdr(1, "Address"); TblHdr(2, "Name"); TblHdr(3, "ms");
    IPToStr(ip, ab);
    gTitle[0] = 0; StrCat(gTitle, "Traceroute "); StrCat(gTitle, host); StrCat(gTitle, "  ["); StrCat(gTitle, ab); StrCat(gTitle, "]");
    UpdateScrollMax(); DrawMatrix();
    for (ttl = 1; ttl <= 30 && !gStop; ttl++) {
        InetHost from = 0; short typ; long rtt; short r;
        rtt = IcmpEcho(ip, ttl, 0x4954, (UInt16)ttl, &from, &typ, 180);
        r = TblRow();
        UToStr(ttl, nb); TblSet(r, 0, nb);
        if (rtt >= 0) {
            IPToStr(from, ab); TblSet(r, 1, ab);
            ReverseName(from, nm); TblSet(r, 2, nm);
            UToStr(rtt * 1000L / 60L, nb); TblSet(r, 3, nb);
        } else { TblSet(r, 1, "*"); TblSet(r, 2, ""); TblSet(r, 3, "timeout"); }
        UpdateScrollMax(); DrawMatrix();
        if (rtt >= 0 && (from == ip || typ == 0)) break;
    }
    DrawMatrix();
}
static void DoMenu(long sel)
{
    short menu = HiWord(sel), item = LoWord(sel);
    Str255 daName;
    MenuHandle am;
    switch (menu) {
        case mApple:
            if (item == iAbout) DoAbout();
            else { am = GetMenuHandle(mApple); GetMenuItemText(am, item, daName); OpenDeskAcc(daName); }
            break;
        case mFile:
            if (item == iSaveAs) DoSaveAs();
            else if (item == iQuit) gDone = true;
            break;
        case mEdit:
            if (item == iCopy) ExportClip();
            break;
        case mScan:
            if (item == iRescan) RunScan();
            else if (item == iStop) gStop = true;
            else if (item == iOptions) DoOptions();
            break;
        case mView:
            if (item == 1) ShowIPView();
            else if (item == 2) FillNBP();
            else if (item == 3) FillZones();
            break;
        case mNet:
            if (item == 1) ShowIdentity();
            else if (item == 3) DoPing();
            else if (item == 4) DoDNS();
            else if (item == 5) DoTrace();
            break;
    }
    HiliteMenu(0);
}
static pascal OSErr AEQuit(const AppleEvent *e, AppleEvent *r, long ref)
{ if (e || r || ref) {} gDone = true; return noErr; }
static pascal OSErr AEOpen(const AppleEvent *e, AppleEvent *r, long ref)
{ if (e || r || ref) {} return noErr; }
static void InstallAE(void)
{
    AEInstallEventHandler(kCoreEventClass, kAEOpenApplication, (AEEventHandlerUPP)AEOpen, 0, false);
    AEInstallEventHandler(kCoreEventClass, kAEQuitApplication, (AEEventHandlerUPP)AEQuit, 0, false);
}

/* ---- events ---- */
static void DoActivate(Boolean act)
{
    short h = act ? 0 : 255;
    if (gVScroll)   HiliteControl(gVScroll, h);
    if (gBtnRescan) HiliteControl(gBtnRescan, h);
    if (gBtnExport) HiliteControl(gBtnExport, h);
    if (gBtnCopy)   HiliteControl(gBtnCopy, h);
    if (gBtnQuit)   HiliteControl(gBtnQuit, h);
    SetPort(gWin); DrawGrowIcon(gWin);
}
static void DoContent(Point gpt)
{
    ControlHandle ctl; short part; Point pt = gpt;
    GlobalToLocal(&pt);
    part = FindControl(pt, gWin, &ctl);
    if (ctl == gVScroll && part) {
        short tp = TrackControl(ctl, pt, NULL);
        if (tp == kControlIndicatorPart) gScrollTop = GetControlValue(ctl);
        else if (tp) {
            short v = GetControlValue(ctl), mx = GetControlMaximum(ctl);
            short pg = VisibleRows() - 1; if (pg < 1) pg = 1;
            if      (tp == kControlUpButtonPart)   v -= 1;
            else if (tp == kControlDownButtonPart) v += 1;
            else if (tp == kControlPageUpPart)     v -= pg;
            else if (tp == kControlPageDownPart)   v += pg;
            if (v < 0) v = 0; if (v > mx) v = mx;
            SetControlValue(ctl, v); gScrollTop = v;
        }
        DrawMatrix();
    }
    else if (ctl == gBtnRescan && part) { if (TrackControl(ctl, pt, NULL)) RunScan(); }
    else if (ctl == gBtnExport && part) { if (TrackControl(ctl, pt, NULL)) ExportFile(); }
    else if (ctl == gBtnCopy   && part) { if (TrackControl(ctl, pt, NULL)) ExportClip(); }
    else if (ctl == gBtnQuit   && part) { if (TrackControl(ctl, pt, NULL)) gDone = true; }
}
static void DoMouse(EventRecord *ev)
{
    WindowPtr w;
    short part = FindWindow(ev->where, &w);
    switch (part) {
        case inMenuBar:   DoMenu(MenuSelect(ev->where)); break;
        case inSysWindow: SystemClick(ev, w); break;
        case inGoAway:    if (w == gWin && TrackGoAway(w, ev->where)) gDone = true; break;
        case inDrag:      DragWindow(w, ev->where, &qd.screenBits.bounds); break;
        case inGrow:
            if (w == gWin) {
                Rect lim; long ns;
                SetRect(&lim, 320, 160, qd.screenBits.bounds.right, qd.screenBits.bounds.bottom);
                ns = GrowWindow(w, ev->where, &lim);
                if (ns) { SizeWindow(w, LoWord(ns), HiWord(ns), true); FitScrollbar(); InvalRect(&w->portRect); }
            }
            break;
        case inZoomIn:
        case inZoomOut:
            if (w == gWin && TrackBox(w, ev->where, part)) {
                ZoomWindow(w, part, true); FitScrollbar(); InvalRect(&w->portRect);
            }
            break;
        case inContent:
            if (w == gWin) DoContent(ev->where);
            else SelectWindow(w);
            break;
    }
}

static void Init(void)
{
    InitGraf(&qd.thePort);
    InitFonts();
    InitWindows();
    InitMenus();
    TEInit();
    InitDialogs(NULL);
    InitCursor();
}

int main(void)
{
    Rect        bounds;
    EventRecord ev;

    Init();
    InstallAE();
    SetupMenus();
    if (InitOpenTransport() != noErr) return 1;

    GetFNum("\pMonaco", &gMonacoFont);
    if (gMonacoFont == 0) gMonacoFont = 4;
    InitPorts();

    gCells = NewPtr((long)MAXTROWS * MAXCOLS * CELLW);   /* generic table store */
    if (gCells == NULL) return 1;

    SetRect(&bounds, 20, 44, 20 + 640, 44 + 420);
    gWin = NewCWindow(NULL, &bounds, "\pMacNetScan", true, zoomDocProc, (WindowPtr)-1L, true, 0);
    SetPort(gWin);
    MakeScrollbar();
    MakeButtons();

    /* Don't auto-scan on launch -- a full /24 sweep is slow. Start with a blank
       window (just the buttons); the title + column headers ("info") appear on
       top of the rows the first time a Scan or View produces output. The user
       starts a sweep via Scan > Rescan (or the Rescan button). */
    gMode = MODE_IP; gScrollTop = 0;
    gHasOutput = false;
    DrawMatrix();

    while (!gDone) {
        if (WaitNextEvent(everyEvent, &ev, 15L, NULL)) {
            switch (ev.what) {
                case mouseDown:  DoMouse(&ev); break;
                case keyDown:
                case autoKey:
                    if (ev.modifiers & cmdKey) {
                        long m = MenuKey((char)(ev.message & charCodeMask));
                        if (HiWord(m)) DoMenu(m);
                    }
                    break;
                case updateEvt:
                    BeginUpdate((WindowPtr)ev.message);
                    DrawMatrix();
                    EndUpdate((WindowPtr)ev.message);
                    break;
                case activateEvt:
                    DoActivate((ev.modifiers & activeFlag) != 0);
                    break;
                case kHighLevelEvent:
                    AEProcessAppleEvent(&ev);
                    break;
            }
        }
    }
    CloseOpenTransport();
    return 0;
}
