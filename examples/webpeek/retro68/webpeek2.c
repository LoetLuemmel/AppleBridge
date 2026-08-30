/* WebPeek 2 — a System 7 application that opens an https:// site BY ITSELF.
 *
 * WebPeek 1 (examples/webpeek/webpeek_main.c, MPW SC) sent the URL to a host
 * gateway that did the TLS. This one does the cryptography on the 68K: name
 * resolution over UDP, TLS 1.2/1.3 through Crypto Ancienne with the certificate
 * chain verified, the HTML reduced to text on the way in. Built with Retro68 —
 * MPW SC has no 64-bit integer, and the crypto needs one.
 *
 * Window: a location line at the top (type a URL, press Return), the page text
 * below with a scroll bar. Links menu: the first 24 anchors of the page. File:
 * fetch from the clipboard, save the content as PICT (the screenshot System 7
 * never had), quit. A prefs file "WebPeek Prefs" in the app's folder may carry
 * DNS=a.b.c.d (default 10.0.2.3, the slirp resolver) and HOME=url.
 *
 * Every wait yields through WaitNextEvent, so the AppleBridge daemon keeps its
 * link while a page is being fetched.
 */
#include <MacTypes.h>
#include <Quickdraw.h>
#include <Fonts.h>
#include <Events.h>
#include <Menus.h>
#include <Windows.h>
#include <TextEdit.h>
#include <Dialogs.h>
#include <Multiverse.h>
#include <Memory.h>
#include <Files.h>
#include <Multiverse.h>
#include <Errors.h>
#include <Processes.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "tlsfetch.h"
#include "html2text.h"

#define kAppleMenu 128
#define kFileMenu  129
#define kLinksMenu 130
#define kWinW      600
#define kWinH      420
#define kLocH      20
#define kMaxText   28000L
#define kMaxHTML   200000L        /* stop reading a page after this much HTML */

static WindowPtr     gWin;
static TEHandle      gLoc, gTE;
static ControlHandle gScroll;
static MenuHandle    gLinks;
static Boolean       gDone, gBusy;
static char          gURL[600], gStatus[160];
static h2t_state     gH2T;
static char         *gText;
static long          gHTMLBytes;
static unsigned long gDNS;
static char          gHome[300] = "https://td5.390er.de/68k-tls/";

/* ---------- drawing ---------- */
static void ContentRect(Rect *r) { *r = gWin->portRect; r->top += kLocH + 2; r->right -= 16; InsetRect(r, 4, 2); }
static void DrawFrame(void) {
    Rect r = gWin->portRect, st; r.bottom = r.top + kLocH;
    st = r; st.left = kWinW - 196;                       /* the status area, right of the location field */
    EraseRect(&st); MoveTo(0, kLocH); LineTo(r.right, kLocH);
    MoveTo(4, 14); TextFont(0); TextSize(12); DrawString("\pURL:"); TextFont(4); TextSize(9);
    { unsigned char p[160]; int n = strlen(gStatus); if (n > 150) n = 150; p[0] = n; memcpy(p + 1, gStatus, n);
      TextFont(3); TextSize(9); MoveTo(r.right - 20 - StringWidth(p), 14); DrawString(p); TextFont(4); TextSize(9); }
    TEUpdate(&(*gLoc)->viewRect, gLoc);
}
static void DrawAll(void) {
    Rect c;
    SetPort(gWin);
    DrawFrame();
    TEUpdate(&(*gLoc)->viewRect, gLoc);
    ContentRect(&c); EraseRect(&c);
    TEUpdate(&c, gTE);
    DrawControls(gWin);
}
static void SetStatus(const char *s) { strncpy(gStatus, s, sizeof gStatus - 1); SetPort(gWin); DrawFrame(); }
static void SetLocation(const char *url) { TESetText(url, strlen(url), gLoc); TESetSelect(0, 32767, gLoc); SetPort(gWin); EraseRect(&(*gLoc)->viewRect); TEUpdate(&(*gLoc)->viewRect, gLoc); }
static void ShowText(const char *txt, long len) {
    Rect c;
    if (len < 0) len = strlen(txt);
    if (len > kMaxText) len = kMaxText;
    TESetText(txt, len, gTE);
    SetPort(gWin); ContentRect(&c); InvalRect(&c);
    { short lines = (*gTE)->nLines, vis = (c.bottom - c.top) / (*gTE)->lineHeight; short max = lines - vis; if (max < 0) max = 0;
      SetControlMaximum(gScroll, max); SetControlValue(gScroll, 0); }
}
static void ScrollTo(short v) {
    short cur = GetControlValue(gScroll); short lh = (*gTE)->lineHeight; if (lh <= 0) lh = 12;
    if (v < 0) v = 0; if (v > GetControlMaximum(gScroll)) v = GetControlMaximum(gScroll);
    if (v == cur) return;
    SetControlValue(gScroll, v);
    TEScroll(0, (cur - v) * lh, gTE);
}
static pascal void ScrollAction(ControlHandle c, short part) {
    short v = GetControlValue(c), page;
    Rect r; ContentRect(&r); page = (r.bottom - r.top) / (*gTE)->lineHeight - 1; if (page < 1) page = 1;
    switch (part) {
        case inUpButton:   ScrollTo(v - 1); break;
        case inDownButton: ScrollTo(v + 1); break;
        case inPageUp:     ScrollTo(v - page); break;
        case inPageDown:   ScrollTo(v + page); break;
    }
}

/* ---------- event pump used as the fetch's yield ---------- */
static void HandleEvent(EventRecord *ev);
static void Yield(void) { EventRecord ev; if (WaitNextEvent(everyEvent, &ev, 1, NULL)) HandleEvent(&ev); }

/* ---------- fetch ---------- */
static void LogLine(const char *line) { SetStatus(line); }
static int  sk_in_body; static char sk_hdr[2048]; static int sk_hl;
static int Sink(const unsigned char *data, long len) {
#define in_body sk_in_body
#define hdr sk_hdr
#define hl sk_hl
    long i = 0;
    gHTMLBytes += len;
    if (!in_body) {                                        /* skip HTTP headers, remember the status */
        for (; i < len; i++) {
            if (hl < (int)sizeof hdr - 1) hdr[hl++] = data[i];
            if (hl >= 4 && hdr[hl-1] == '\n' && hdr[hl-2] == '\r' && hdr[hl-3] == '\n' && hdr[hl-4] == '\r') { in_body = 1; i++; break; }
        }
        hdr[hl] = 0;
        if (!in_body) return 0;
        { char *e = strstr(hdr, "\r\n"); if (e) { *e = 0; strncpy(gStatus, hdr, sizeof gStatus - 1); }
          /* a redirect: remember the Location in gURL's twin */
          { char *loc = strstr(e ? e + 2 : hdr, "\nLocation: "); if (!loc) loc = strstr(e ? e + 2 : hdr, "\nlocation: ");
            if (loc && (atoi(hdr + 9) / 100) == 3) { char *q = loc + 11, *z = strpbrk(q, "\r\n"); if (z) *z = 0; strncpy(gH2T.links[0], q, H2T_LINK_LEN - 1); gH2T.nlinks = -1; return 1; } } }
    }
    h2t_feed(&gH2T, data + i, len - i);
    if (gH2T.full || gHTMLBytes > kMaxHTML) return 1;
    return 0;
#undef in_body
#undef hdr
#undef hl
}
static void BuildLinksMenu(void) {
    int i, n = CountMItems(gLinks);
    while (n-- > 0) DeleteMenuItem(gLinks, 1);
    for (i = 0; i < gH2T.nlinks; i++) {
        unsigned char p[80]; const char *t = gH2T.link_text[i][0] ? gH2T.link_text[i] : gH2T.links[i]; int k = strlen(t); if (k > 60) k = 60;
        p[0] = k; memcpy(p + 1, t, k);
        AppendMenu(gLinks, "\px"); SetMenuItemText(gLinks, i + 1, p);
    }
    if (!gH2T.nlinks) { AppendMenu(gLinks, "\p(no links on this page"); }
}
static void Fetch(const char *url, int depth) {
    tf_config cfg; tf_url u; int rc; char line[300];
    if (gBusy) return;
    if (!tf_parse_url(url, &u)) { ShowText("That is not a URL I understand.", -1); return; }
    gBusy = true;
    strncpy(gURL, url, sizeof gURL - 1); SetLocation(gURL);
    gHTMLBytes = 0;
    h2t_init(&gH2T, gText, kMaxText);
    sk_in_body = 0; sk_hl = 0; sk_hdr[0] = 0;
    sprintf(line, "Fetching %s ...", u.host); ShowText(line, -1);
    memset(&cfg, 0, sizeof cfg);
    cfg.yield = Yield; cfg.log = LogLine; cfg.sink = Sink; cfg.dns_server = gDNS; cfg.verify = 1;
    rc = tf_get(&cfg, &u);
    gBusy = false;
    if (gH2T.nlinks == -1) {                               /* redirect */
        char target[600];
        if (depth < 5 && h2t_resolve(gURL, gH2T.links[0], target, sizeof target)) { Fetch(target, depth + 1); return; }
        sprintf(line, "Redirect loop or bad Location: %.200s", gH2T.links[0]); ShowText(line, -1); return;
    }
    if (rc) { sprintf(line, "Fetch failed: %s", gStatus); ShowText(line, -1); return; }
    h2t_finish(&gH2T);
    {   /* header: title, URL, timings */
        static char page[kMaxText + 600]; int n;
        n = sprintf(page, "%s\r%s\r[%s]\r", gH2T.title[0] ? gH2T.title : "(untitled)", gH2T.title[0] ? "==========" : "", gURL);
        n += sprintf(page + n, "%s%sDNS %ld, connect %ld, TLS %ld, total %ld ticks; %ld bytes of HTML%s%s\r\r", tf_cipher[0] ? tf_cipher : "plain http", tf_cipher[0] ? ", chain verified. " : ". ", tf_ticks_dns, tf_ticks_connect, tf_ticks_handshake, tf_ticks_total, gHTMLBytes, gH2T.full ? " (truncated at 28 KB of text)" : "", tf_read_timeout ? " [read timeout]" : "");
        memcpy(page + n, gText, gH2T.len); n += gH2T.len; page[n] = 0;
        ShowText(page, n);
    }
    BuildLinksMenu();
    sprintf(line, "%ld ticks", tf_ticks_total); SetStatus(line);
}
static void FetchFromLocation(void) {
    char url[600]; long n = (*gLoc)->teLength; if (n > 598) n = 598;
    memcpy(url, *(*gLoc)->hText, n); url[n] = 0;
    { char *q = url + n; while (q > url && (q[-1] == ' ' || q[-1] == '\r')) *--q = 0; }
    if (url[0]) Fetch(url, 0);
}
/* Read the 'TEXT' item of the desk scrap WITHOUT the GetScrap trap.
 * Basilisk II patches GetScrap to re-import the host pasteboard into the Mac scrap
 * first, and that re-import empties it here (measured 2026-08-30: size 0, count
 * bumped, GetScrap -102 although PutScrap had just succeeded and the host held the
 * text). The scrap itself is documented (Inside Macintosh, Scrap Manager): a
 * sequence of {OSType type; long length; data padded to even}, at the low-memory
 * ScrapHandle once loaded. Reading it directly works on Basilisk and real Macs alike. */
static long ReadScrapText(char *out, long cap) {
    Handle h; long size, pos = 0, n = 0;
    if (LMGetScrapState() == 0) LoadScrap();                 /* on disk -> memory (not patched) */
    h = LMGetScrapHandle(); size = LMGetScrapSize();
    if (!h || size < 8) return 0;
    HLock(h);
    while (pos + 8 <= size) {
        unsigned char *p = (unsigned char *)*h + pos;
        unsigned long type = ((unsigned long)p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3];
        long len = ((long)p[4] << 24) | ((long)p[5] << 16) | ((long)p[6] << 8) | p[7];
        if (len < 0 || pos + 8 + len > size) break;
        if (type == 'TEXT') { n = len < cap ? len : cap; memcpy(out, p + 8, n); break; }
        pos += 8 + ((len + 1) & ~1L);
    }
    HUnlock(h);
    return n;
}
static void FetchFromClipboard(void) {
    static char raw[2048]; long got, i, n = 0, gs, off = 0; char url[600];
    { Handle h = NewHandle(0); gs = GetScrap(h, 'TEXT', &off); DisposeHandle(h); }   /* lets an emulator import the host pasteboard */
    got = ReadScrapText(raw, sizeof raw);
    for (i = 0; i < got && n < 598; i++) { char c = raw[i]; if (c == '\r' || c == '\n') break; if (c != ' ') url[n++] = c; }
    url[n] = 0;
    if (n) Fetch(url, 0);
    else { char msg[200]; sprintf(msg, "Clipboard holds no text (GetScrap %ld; scrap size %ld, state %d).", gs, (long)LMGetScrapSize(), (int)LMGetScrapState()); ShowText(msg, -1); }
}

/* ---------- PICT ---------- */
static void SavePict(void) {
    PicHandle ph; Rect r; short ref; long len; static char header[512]; OSErr err;
    SetPort(gWin); r = gWin->portRect;
    ph = OpenPicture(&r); DrawAll(); ClosePicture();
    if (!ph || GetHandleSize((Handle)ph) < 12) { ShowText("OpenPicture failed.", -1); return; }
    FSDelete("\pWebPeek Shot.pict", 0);
    err = Create("\pWebPeek Shot.pict", 0, 'ttxt', 'PICT');
    if (err == noErr) err = FSOpen("\pWebPeek Shot.pict", 0, &ref);
    if (err != noErr) { KillPicture(ph); ShowText("Could not create WebPeek Shot.pict.", -1); return; }
    memset(header, 0, 512); len = 512; FSWrite(ref, &len, header);
    len = GetHandleSize((Handle)ph); HLock((Handle)ph); FSWrite(ref, &len, (Ptr)*ph); HUnlock((Handle)ph);
    FSClose(ref); FlushVol(NULL, 0); KillPicture(ph);
    SetStatus("saved WebPeek Shot.pict");
}

/* ---------- prefs ---------- */
static void ReadPrefs(void) {
    short ref; long cnt = 1000; static char buf[1024]; char *q, *e;
    gDNS = 0;
    if (FSOpen("\pWebPeek Prefs", 0, &ref) != noErr) return;
    FSRead(ref, &cnt, buf); FSClose(ref); buf[cnt] = 0;
    for (q = buf; q && *q; q = e ? e + 1 : NULL) {
        e = strpbrk(q, "\r\n"); if (e) *e = 0;
        if (!strncmp(q, "DNS=", 4)) { unsigned a, b, c, d; if (sscanf(q + 4, "%u.%u.%u.%u", &a, &b, &c, &d) == 4) gDNS = ((unsigned long)a << 24) | (b << 16) | (c << 8) | d; }
        else if (!strncmp(q, "HOME=", 5)) strncpy(gHome, q + 5, sizeof gHome - 1);
    }
}

/* ---------- menus & events ---------- */
static void BuildMenus(void) {
    MenuHandle m; Str255 t;
    t[0] = 1; t[1] = 0x14; m = NewMenu(kAppleMenu, t); AppendMenu(m, "\pAbout WebPeek 2"); InsertMenu(m, 0);
    m = NewMenu(kFileMenu, "\pFile");
    AppendMenu(m, "\pOpen Home Page/H"); AppendMenu(m, "\pOpen URL from Clipboard/U"); AppendMenu(m, "\pReload/R");
    AppendMenu(m, "\p(-"); AppendMenu(m, "\pSave Content as PICT/S"); AppendMenu(m, "\p(-"); AppendMenu(m, "\pQuit/Q"); InsertMenu(m, 0);
    gLinks = NewMenu(kLinksMenu, "\pLinks"); AppendMenu(gLinks, "\p(no page yet"); InsertMenu(gLinks, 0);
    DrawMenuBar();
}
static void DoMenu(long choice) {
    short menu = HiWord(choice), item = LoWord(choice);
    if (menu == kAppleMenu) ShowText("WebPeek 2.0\r\rA System 7 application that opens an https:// site by itself: DNS over UDP, TLS 1.2/1.3 through Crypto Ancienne with the certificate chain verified against 13 built-in roots, HTML reduced to text on the way in. 68K code built with Retro68 and delivered over AppleBridge.\r\rType a URL in the location line and press Return. The Links menu lists the page's first 24 links.", -1);
    else if (menu == kFileMenu) switch (item) {
        case 1: Fetch(gHome, 0); break;
        case 2: FetchFromClipboard(); break;
        case 3: if (gURL[0]) Fetch(gURL, 0); break;
        case 5: SavePict(); break;
        case 7: gDone = true; break;
    }
    else if (menu == kLinksMenu && item >= 1 && item <= gH2T.nlinks) {
        char target[600];
        if (h2t_resolve(gURL, gH2T.links[item - 1], target, sizeof target)) Fetch(target, 0);
    }
    HiliteMenu(0);
}
static void HandleEvent(EventRecord *ev) {
    WindowPtr who; short part; ControlHandle c; Point p;
    switch (ev->what) {
        case updateEvt:
            who = (WindowPtr)ev->message; BeginUpdate(who); if (who == gWin) DrawAll(); EndUpdate(who); break;
        case activateEvt:
            if ((WindowPtr)ev->message == gWin) { if (ev->modifiers & activeFlag) TEActivate(gLoc); else TEDeactivate(gLoc); }
            break;
        case mouseDown:
            part = FindWindow(ev->where, &who);
            if (part == inMenuBar) { if (!gBusy) DoMenu(MenuSelect(ev->where)); }
            else if (part == inDrag) DragWindow(who, ev->where, &qd.screenBits.bounds);
            else if (part == inGoAway) { if (TrackGoAway(who, ev->where)) gDone = true; }
            else if (part == inContent) {
                if (who != FrontWindow()) { SelectWindow(who); break; }
                SetPort(gWin); p = ev->where; GlobalToLocal(&p);
                if (PtInRect(p, &(*gLoc)->viewRect)) TEClick(p, (ev->modifiers & shiftKey) != 0, gLoc);
                else if ((part = FindControl(p, gWin, &c)) != 0 && c == gScroll) {
                    if (part == inThumb) { TrackControl(c, p, NULL); ScrollTo(GetControlValue(c)); }
                    else TrackControl(c, p, (ControlActionUPP)ScrollAction);
                }
            }
            break;
        case keyDown: case autoKey: {
            char ch = ev->message & charCodeMask;
            if (ev->modifiers & cmdKey) { if (!gBusy) DoMenu(MenuKey(ch)); }
            else if (ch == '\r' || ch == 3) { if (!gBusy) FetchFromLocation(); }
            else if (ch == 0x0B) ScrollTo(GetControlValue(gScroll) - 10);      /* page up */
            else if (ch == 0x0C) ScrollTo(GetControlValue(gScroll) + 10);      /* page down */
            else { TEKey(ch, gLoc); }
            break; }
    }
}

int main(void) {
    EventRecord ev; Rect r;
    MaxApplZone();
    InitGraf(&qd.thePort); InitFonts(); InitWindows(); InitMenus(); TEInit(); InitDialogs(NULL); InitCursor();
    FlushEvents(everyEvent, 0);
    gText = (char *)malloc(kMaxText + 16);
    ReadPrefs();
    BuildMenus();
    SetRect(&r, 0, 0, kWinW, kWinH);
    OffsetRect(&r, (short)((qd.screenBits.bounds.right - kWinW) / 2), (short)((qd.screenBits.bounds.bottom - kWinH) / 2 + 10));
    gWin = NewWindow(NULL, &r, "\pWebPeek 2", true, noGrowDocProc, (WindowPtr)-1, true, 0);
    SetPort(gWin); TextFont(4); TextSize(9);
    SetRect(&r, 36, 3, kWinW - 200, kLocH - 2); gLoc = TENew(&r, &r); TEAutoView(true, gLoc);
    ContentRect(&r); gTE = TENew(&r, &r);
    SetRect(&r, kWinW - 16, kLocH + 1, kWinW, kWinH); gScroll = NewControl(gWin, &r, "\p", true, 0, 0, 0, scrollBarProc, 0);
    SetLocation(gHome);
    ShowText("WebPeek 2 is ready.\r\rType a URL in the location line and press Return, or File > Open Home Page.\rThe Macintosh resolves the name, negotiates TLS and verifies the certificate itself; expect a few seconds per page on a 68030.", -1);
    TEActivate(gLoc);
    while (!gDone) {
        if (WaitNextEvent(everyEvent, &ev, 10L, NULL)) HandleEvent(&ev); else TEIdle(gLoc);
    }
    DisposeWindow(gWin);
    ExitToShell();
    return 0;
}
