/* WebPeek - a menu-driven System 7 application that opens an https
 * website and saves a screenshot of the content.
 *
 * A 68K Mac cannot speak modern TLS, so the https half runs on the host:
 * the WebPeek gateway (Python, port 9080) fetches the URL, strips the HTML
 * to text, and streams it back MacRoman/CR over plain TCP. This program is
 * the native half: Open Transport fetch, TextEdit display, and - because
 * System 7 has no screenshot tool - File > Save Content as PICT draws the
 * content window into a PICT file on disk.
 *
 * Menus are built in code (NewMenu/AppendMenu), no resource file needed.
 * The OT connect is asynchronous with a notifier, per the AppleBridge
 * daemon's freeze-avoidance pattern: a synchronous OTConnect to a dead
 * host would hold the whole cooperative scheduler.
 */

#include <Types.h>
#include <Quickdraw.h>
#include <Fonts.h>
#include <Events.h>
#include <Menus.h>
#include <Windows.h>
#include <TextEdit.h>
#include <Dialogs.h>
#include <Memory.h>
#include <Files.h>
#include <ToolUtils.h>
#include <Scrap.h>
#include <Errors.h>
#include <OpenTransport.h>
#include <OpenTptInternet.h>

#define kGatewayIP    0xC0A8039AUL   /* 192.168.3.154 - the AppleBridge host */
#define kGatewayPort  9080
#define kMaxContent   28000L         /* TextEdit's comfort zone under 32K   */
#define kAppleMenu    128
#define kFileMenu     129
#define kWinW         540
#define kWinH         360

static WindowPtr gWin;
static TEHandle  gTE;
static Boolean   gDone = false;
static Boolean   gOTUp = false;
static volatile short gConnEvent;    /* 0 pending, 1 connected, 2 refused */

/* ---------- small helpers (no StdCLib formatting needed) ---------- */

static void CToPas(const char *c, Str255 p)
{
    short n = 0;
    while (c[n] && n < 255) { p[n + 1] = c[n]; n++; }
    p[0] = (unsigned char)n;
}

static void ShowText(const char *msg, long len)
{
    if (len < 0) { len = 0; while (msg[len]) len++; }
    if (len > kMaxContent) len = kMaxContent;
    TESetText(msg, len, gTE);
    SetPort(gWin);
    InvalRect(&gWin->portRect);
}

static void DrawContent(void)
{
    EraseRect(&gWin->portRect);
    TEUpdate(&gWin->portRect, gTE);
}

/* ---------- Open Transport: one endpoint per fetch ---------- */

static pascal void ConnNotifier(void *ctx, OTEventCode code,
                                OTResult result, void *cookie)
{
#pragma unused(ctx, result, cookie)
    if (code == T_CONNECT)         gConnEvent = 1;
    else if (code == T_DISCONNECT) gConnEvent = 2;
}

static void IdleYield(void)
{
    EventRecord ev;
    if (WaitNextEvent(updateMask, &ev, 1, NULL)) {
        if (ev.what == updateEvt && (WindowPtr)ev.message == gWin) {
            BeginUpdate(gWin);
            SetPort(gWin);
            DrawContent();
            EndUpdate(gWin);
        }
    }
}

/* Fetch one URL through the gateway into buf; returns bytes or negative err. */
static long GatewayFetch(const char *url, char *buf, long bufMax)
{
    EndpointRef ep;
    OSStatus    err;
    TCall       sndCall;
    InetAddress addr, localAddr;
    TBind       bindReq;
    OTFlags     flags;
    OTResult    r;
    long        total = 0, deadline, urlLen = 0;

    while (url[urlLen]) urlLen++;

    ep = OTOpenEndpoint(OTCreateConfiguration(kTCPName), 0, NULL, &err);
    if (err != noErr) return -1;

    OTMemzero(&localAddr, sizeof(localAddr));
    OTInitInetAddress(&localAddr, 0, kOTAnyInetAddress);
    OTMemzero(&bindReq, sizeof(bindReq));
    bindReq.addr.buf = (UInt8 *)&localAddr;
    bindReq.addr.len = sizeof(localAddr);
    err = OTBind(ep, &bindReq, NULL);
    if (err != noErr) { OTCloseProvider(ep); return -2; }

    gConnEvent = 0;
    err = OTInstallNotifier(ep, ConnNotifier, NULL);
    if (err == noErr) err = OTSetAsynchronous(ep);
    if (err != noErr) { OTCloseProvider(ep); return -3; }

    OTMemzero(&addr, sizeof(addr));
    OTInitInetAddress(&addr, kGatewayPort, kGatewayIP);
    OTMemzero(&sndCall, sizeof(sndCall));
    sndCall.addr.buf = (UInt8 *)&addr;
    sndCall.addr.len = sizeof(addr);

    err = OTConnect(ep, &sndCall, NULL);
    if (err != noErr && err != kOTNoDataErr) { OTCloseProvider(ep); return -4; }

    deadline = TickCount() + 15 * 60;           /* 15 s to connect */
    while (gConnEvent == 0 && TickCount() < deadline)
        IdleYield();

    if (gConnEvent != 1) {
        if (gConnEvent == 2) OTRcvDisconnect(ep, NULL);
        OTRemoveNotifier(ep);
        OTCloseProvider(ep);
        return (gConnEvent == 2) ? -5 : -6;     /* refused : timeout */
    }
    OTSetSynchronous(ep);
    err = OTRcvConnect(ep, NULL);
    OTRemoveNotifier(ep);
    OTSetNonBlocking(ep);
    if (err != noErr) { OTCloseProvider(ep); return -7; }

    /* request line: the URL, CR LF */
    OTSnd(ep, (void *)url, urlLen, 0);
    OTSnd(ep, (void *)"\015\012", 2, 0);

    /* pull until orderly disconnect; 30 s of silence gives up */
    deadline = TickCount() + 30 * 60;
    while (total < bufMax) {
        r = OTRcv(ep, buf + total, bufMax - total, &flags);
        if (r > 0) {
            total += r;
            deadline = TickCount() + 30 * 60;
        } else if (r == kOTNoDataErr) {
            if (TickCount() > deadline) break;
            IdleYield();
        } else if (r == kOTLookErr) {
            OTResult look = OTLook(ep);
            if (look == T_ORDREL) { OTRcvOrderlyDisconnect(ep); break; }
            if (look == T_DISCONNECT) { OTRcvDisconnect(ep, NULL); break; }
        } else
            break;
    }
    OTCloseProvider(ep);
    return total;
}

static void DoFetch(const char *url)
{
    static char buf[28002];
    long got;
    char head[600];
    long n = 0, i;

    if (!gOTUp) { ShowText("Open Transport is not available.", -1); return; }

    for (i = 0; url[i] && n < 500; i++) head[n++] = url[i];
    head[n] = 0;
    ShowText("Fetching ", -1);
    TEInsert(head, n, gTE);
    TEInsert(" via the WebPeek gateway...", 27, gTE);
    SetPort(gWin);
    DrawContent();                       /* show progress immediately */

    got = GatewayFetch(url, buf, kMaxContent);
    if (got > 0)
        ShowText(buf, got);
    else if (got == -5)
        ShowText("Connection refused - is webpeek_gateway.py running on the host?", -1);
    else if (got == -6)
        ShowText("Connect timeout - host unreachable.", -1);
    else
        ShowText("Fetch failed (OT error).", -1);
}

static void FetchFromClipboard(void)
{
    Handle h = NewHandle(0);
    long   off, got, i, n = 0;
    char   url[502];

    got = GetScrap(h, 'TEXT', &off);
    if (got > 0) {
        HLock(h);
        for (i = 0; i < got && n < 500; i++) {
            char c = (*h)[i];
            if (c == '\015' || c == '\012') break;
            if (c != ' ') url[n++] = c;
        }
        HUnlock(h);
    }
    DisposeHandle(h);
    url[n] = 0;
    if (n > 0) DoFetch(url);
    else ShowText("Clipboard holds no text. Copy a URL on the host, push it over with mac_clipboard_set, then choose this item again.", -1);
}

/* ---------- the screenshot: record the content into a PICT file ---------- */

static void SavePict(void)
{
    PicHandle ph;
    Rect      r;
    short     ref;
    long      len, zeros;
    char      header[512];
    OSErr     err;
    Str255    fname;

    CToPas("WebPeek Shot.pict", fname);
    SetPort(gWin);
    r = gWin->portRect;

    ph = OpenPicture(&r);
    DrawContent();
    ClosePicture();
    if (ph == NULL || GetHandleSize((Handle)ph) < 12) {
        ShowText("OpenPicture failed - no PICT written.", -1);
        return;
    }

    FSDelete(fname, 0);
    err = Create(fname, 0, 'ttxt', 'PICT');
    if (err == noErr) err = FSOpen(fname, 0, &ref);
    if (err != noErr) {
        KillPicture(ph);
        ShowText("Could not create WebPeek Shot.pict.", -1);
        return;
    }
    for (zeros = 0; zeros < 512; zeros++) header[zeros] = 0;
    len = 512;
    FSWrite(ref, &len, header);
    len = GetHandleSize((Handle)ph);
    HLock((Handle)ph);
    FSWrite(ref, &len, (Ptr)*ph);
    HUnlock((Handle)ph);
    FSClose(ref);
    FlushVol(NULL, 0);
    KillPicture(ph);

    TEInsert("\015--- saved as WebPeek Shot.pict (in the app's folder) ---", 57, gTE);
    SetPort(gWin);
    InvalRect(&gWin->portRect);
}

/* ---------- menus and events ---------- */

static void BuildMenus(void)
{
    MenuHandle m;
    Str255     t;

    t[0] = 1; t[1] = 0x14;                     /* the apple */
    m = NewMenu(kAppleMenu, t);
    AppendMenu(m, "\pAbout WebPeek");
    InsertMenu(m, 0);

    CToPas("File", t);
    m = NewMenu(kFileMenu, t);
    AppendMenu(m, "\pFetch example.com/E");
    AppendMenu(m, "\pFetch URL from Clipboard/U");
    AppendMenu(m, "\p(-");
    AppendMenu(m, "\pSave Content as PICT/S");
    AppendMenu(m, "\p(-");
    AppendMenu(m, "\pQuit/Q");
    InsertMenu(m, 0);

    DrawMenuBar();
}

static void DoMenu(long choice)
{
    short menu = HiWord(choice);
    short item = LoWord(choice);

    if (menu == kAppleMenu) {
        ShowText("WebPeek 1.0\015\015A menu-driven System 7 application that opens an https website.\015TLS runs on the host (webpeek_gateway.py); the fetch, the display\015and the PICT screenshot are native 68K code, built with MPW SC\015over AppleBridge.\015\015File > Fetch example.com to try it.", -1);
    } else if (menu == kFileMenu) {
        switch (item) {
        case 1: DoFetch("https://example.com");    break;
        case 2: FetchFromClipboard();              break;
        case 4: SavePict();                        break;
        case 6: gDone = true;                      break;
        }
    }
    HiliteMenu(0);
}

main()
{
    EventRecord ev;
    WindowPtr   who;
    short       part;
    Rect        r;

    InitGraf(&qd.thePort);
    InitFonts();
    InitWindows();
    InitMenus();
    TEInit();
    InitDialogs(0L);
    InitCursor();
    FlushEvents(everyEvent, 0);

    if (InitOpenTransport() == noErr) gOTUp = true;

    BuildMenus();

    SetRect(&r, 0, 0, kWinW, kWinH);
    OffsetRect(&r, (short)((qd.screenBits.bounds.right - kWinW) / 2),
                   (short)((qd.screenBits.bounds.bottom - kWinH) / 2 + 20));
    gWin = NewWindow(0L, &r, "\pWebPeek", true, noGrowDocProc,
                     (WindowPtr)-1L, true, 0L);
    SetPort(gWin);
    TextFont(4);                        /* Monaco - MPW headers have no 'monaco' constant */
    TextSize(9);

    SetRect(&r, 8, 4, kWinW - 8, kWinH - 4);
    gTE = TENew(&r, &r);

    ShowText("WebPeek is ready.\015\015File > Fetch example.com opens the https site through the\015host-side gateway. File > Save Content as PICT writes this\015window's content to disk - the screenshot System 7 never had.", -1);

    while (!gDone) {
        if (WaitNextEvent(everyEvent, &ev, 10L, NULL)) {
            switch (ev.what) {
            case updateEvt:
                who = (WindowPtr)ev.message;
                BeginUpdate(who);
                if (who == gWin) { SetPort(who); DrawContent(); }
                EndUpdate(who);
                break;
            case mouseDown:
                part = FindWindow(ev.where, &who);
                if (part == inMenuBar)      DoMenu(MenuSelect(ev.where));
                else if (part == inDrag)    DragWindow(who, ev.where, &qd.screenBits.bounds);
                else if (part == inGoAway)  { if (TrackGoAway(who, ev.where)) gDone = true; }
                else if (part == inContent && who != FrontWindow()) SelectWindow(who);
                break;
            case keyDown:
                if (ev.modifiers & cmdKey)
                    DoMenu(MenuKey((char)(ev.message & charCodeMask)));
                break;
            }
        }
    }

    DisposeWindow(gWin);
    return 0;
}
