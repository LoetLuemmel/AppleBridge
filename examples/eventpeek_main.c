/* EventPeek - every EventRecord, as it arrives.
 *
 * A THINK C companion to the THINK Reference walkthrough: each Toolbox call
 * in this file was looked up in THINK Reference before it was written, and
 * the article shows the page that answered it.
 *
 * The window prints one line per event the application receives: a running
 * number, the event's name, and the raw fields of the EventRecord exactly as
 * THINK Reference's structure page lays them out - message (hex), when
 * (ticks), where (global coordinates), modifiers (hex). Watch a click or a
 * keystroke arrive and the abstract structure becomes a row of numbers.
 *
 * Driven over AppleBridge this closes a loop: the bridge can inject events
 * into the OS event queue (mac_click, mac_key), and EventPeek shows the
 * very records those injections became.
 *
 * THINK C conventions (no #includes - the IDE precompiles MacHeaders):
 *   InitGraf(&thePort), NOT qd.thePort.
 *   Every pass yields through WaitNextEvent - a spin loop would starve the
 *   AppleBridge daemon running behind this program.
 * Keys: q quits. The close box works too (TrackGoAway).
 */

#define kMaxLines 13
#define kWinW     470
#define kWinH     250
#define kLineH    14
#define kListTop  40

WindowPtr gWin;
Boolean   gDone  = false;
Str255    gLines[kMaxLines];
short     gCount = 0;          /* lines currently held           */
long      gSeen  = 0;          /* events recorded since launch   */

/* ---- tiny Str255 builders: no stdio, NumToString does the digits ---- */

static void AppendStr(unsigned char *dst, const unsigned char *src)
{
    short i;
    for (i = 1; i <= src[0] && dst[0] < 250; i++)
        dst[++dst[0]] = src[i];
}

static void AppendNum(unsigned char *dst, long n)
{
    Str255 s;
    NumToString(n, s);
    AppendStr(dst, s);
}

static void AppendHex(unsigned char *dst, unsigned long v, short digits)
{
    short i, d;
    for (i = digits - 1; i >= 0 && dst[0] < 250; i--) {
        d = (short)((v >> (4 * i)) & 0xF);
        dst[++dst[0]] = (unsigned char)(d < 10 ? '0' + d : 'A' + d - 10);
    }
}

static unsigned char *EvtName(short what)
{
    switch (what) {
    case mouseDown:   return "\pmouseDown";
    case mouseUp:     return "\pmouseUp  ";
    case keyDown:     return "\pkeyDown  ";
    case keyUp:       return "\pkeyUp    ";
    case autoKey:     return "\pautoKey  ";
    case diskEvt:     return "\pdiskEvt  ";
    case activateEvt: return "\pactivate ";
    case osEvt:       return "\posEvt    ";
    default:          return "\pevent?   ";
    }
}

/* One event becomes one line, laid out field by field in the order of the
 * EventRecord structure page: what, message, when, where, modifiers. */
static void Record(EventRecord *ev)
{
    Str255 line;
    short  i;

    line[0] = 0;
    gSeen++;
    AppendNum(line, gSeen);
    AppendStr(line, "\p  ");
    AppendStr(line, EvtName(ev->what));
    AppendStr(line, "\p msg=");
    AppendHex(line, (unsigned long)ev->message, 8);
    AppendStr(line, "\p when=");
    AppendNum(line, (long)ev->when);
    AppendStr(line, "\p at(");
    AppendNum(line, (long)ev->where.h);
    AppendStr(line, "\p,");
    AppendNum(line, (long)ev->where.v);
    AppendStr(line, "\p) mod=");
    AppendHex(line, (unsigned long)(unsigned short)ev->modifiers, 4);

    if (gCount == kMaxLines) {
        for (i = 0; i < kMaxLines - 1; i++)
            BlockMove(gLines[i + 1], gLines[i], (long)gLines[i + 1][0] + 1);
        BlockMove(line, gLines[kMaxLines - 1], (long)line[0] + 1);
    } else {
        BlockMove(line, gLines[gCount], (long)line[0] + 1);
        gCount++;
    }

    SetPort(gWin);
    InvalRect(&gWin->portRect);
}

static void Redraw(void)
{
    Str255 s;
    short  i;

    EraseRect(&gWin->portRect);
    TextFont(4);                    /* Monaco */
    TextSize(9);

    MoveTo(8, 14);
    DrawString("\pEventPeek - every EventRecord, as it arrives.  ");
    DrawString("\pseen: ");
    s[0] = 0;
    AppendNum(s, gSeen);
    DrawString(s);
    MoveTo(8, 26);
    DrawString("\pno  what      msg (hex)     when (ticks)  where (h,v)  modifiers");

    for (i = 0; i < gCount; i++) {
        MoveTo(8, kListTop + (i + 1) * kLineH);
        DrawString(gLines[i]);
    }
}

main()
{
    EventRecord ev;
    WindowPtr   who;
    Rect        r;
    short       part;
    char        ch;

    InitGraf(&thePort);
    InitFonts();
    InitWindows();
    InitMenus();
    TEInit();
    InitDialogs(0L);
    InitCursor();
    FlushEvents(everyEvent, 0);

    SetRect(&r, 0, 0, kWinW, kWinH);
    OffsetRect(&r, (screenBits.bounds.right - kWinW) / 2,
                   (screenBits.bounds.bottom - kWinH) / 2 + 40);
    gWin = NewWindow(0L, &r, "\pEventPeek - THINK C over AppleBridge",
                     true, noGrowDocProc, (WindowPtr)-1L, true, 0L);
    SetPort(gWin);

    while (!gDone) {
        if (WaitNextEvent(everyEvent, &ev, 10L, 0L)) {
            switch (ev.what) {
            case updateEvt:
                who = (WindowPtr)ev.message;
                BeginUpdate(who);
                SetPort(who);
                if (who == gWin)
                    Redraw();
                EndUpdate(who);
                break;

            case keyDown:
            case autoKey:
                Record(&ev);
                ch = (char)(ev.message & charCodeMask);
                if (ch == 'q' || ch == 'Q')
                    gDone = true;
                break;

            case mouseDown:
                Record(&ev);
                part = FindWindow(ev.where, &who);
                if (part == inDrag)
                    DragWindow(who, ev.where, &screenBits.bounds);
                else if (part == inGoAway) {
                    if (TrackGoAway(who, ev.where))
                        gDone = true;
                } else if (part == inContent && who != FrontWindow())
                    SelectWindow(who);
                break;

            default:
                Record(&ev);
                break;
            }
        }
    }

    DisposeWindow(gWin);
    return 0;
}
