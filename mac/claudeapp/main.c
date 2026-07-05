/* ClaudeApp - a windowed Macintosh application.
 * Written and built by Claude over the AppleBridge, driving a 1994
 * THINK C IDE on System 7.6.1.  Apple menu -> About for the story.
 *
 * The About box plays a real animated logo: 16 frames of the "HEX OVER THE
 * BRIDGE" party GIF, downscaled to 320x120 and stored as PackBits'd 'Gfrm'
 * resources with a shared 'clut'.  Each tick we UnpackBits one frame into an
 * offscreen PixMap and CopyBits it into the window - just text + the
 * animation, no hand-drawn graphics.  Cheers!  - Claude
 */

#define kApple  128
#define kFile   129
#define kAbout  1
#define kQuit   1
#define kBase   128        /* resource id of 'clut'/'GFin'; 'Gfrm' = baseID.. */

#define AUTO_ABOUT 1       /* TEST: open the About box on launch (set 0 for release) */

WindowPtr  gWin;
Boolean    gDone = false;

/* ---- animated About-box logo (the party GIF) -------------------------- */

typedef struct {
    short count;    /* number of frames            */
    short w;        /* frame width  (== rowBytes)  */
    short h;        /* frame height                */
    short baseID;   /* 'Gfrm' id of frame 0        */
    short delay;    /* ticks between frames        */
    short packed;   /* 1 => rows are PackBits'd    */
} GifInfo;

static GifInfo    gGif;
static Boolean    gGifReady = false;
static CTabHandle gClut     = 0L;
static Ptr        gFrameBuf = 0L;      /* one unpacked frame, w*h bytes */
static PixMap     gSrcPM;              /* source pixmap over gFrameBuf   */
static Handle     gFrames[64];         /* the 'Gfrm' resources           */

static void GifLoad(void)
{
    Handle info;
    short  i;

    if (gGifReady) return;

    info = GetResource('GFin', kBase);
    if (info == 0L) return;
    BlockMove(*info, &gGif, (long)sizeof(GifInfo));
    if (gGif.count <= 0 || gGif.count > 64) return;

    gClut = (CTabHandle) GetResource('clut', kBase);
    if (gClut == 0L) return;
    HNoPurge((Handle)gClut);

    for (i = 0; i < gGif.count; i++) {
        gFrames[i] = GetResource('Gfrm', gGif.baseID + i);
        if (gFrames[i] == 0L) return;
        HNoPurge(gFrames[i]);
    }

    gFrameBuf = NewPtr((long)gGif.w * (long)gGif.h);
    if (gFrameBuf == 0L) return;

    gSrcPM.baseAddr   = gFrameBuf;
    gSrcPM.rowBytes   = (short)(gGif.w | 0x8000);   /* high bit => PixMap */
    SetRect(&gSrcPM.bounds, 0, 0, gGif.w, gGif.h);
    gSrcPM.pmVersion  = 0;
    gSrcPM.packType   = 0;
    gSrcPM.packSize   = 0;
    gSrcPM.hRes       = 0x00480000L;                /* 72 dpi */
    gSrcPM.vRes       = 0x00480000L;
    gSrcPM.pixelType  = 0;                          /* chunky */
    gSrcPM.pixelSize  = 8;
    gSrcPM.cmpCount   = 1;
    gSrcPM.cmpSize    = 8;
    gSrcPM.planeBytes = 0;
    gSrcPM.pmTable    = gClut;
    gSrcPM.pmReserved = 0;

    gGifReady = true;
}

static void GifDrawFrame(WindowPtr w, short idx, Rect *dst)
{
    Handle   h;
    Ptr      src, dp;
    short    row;
    RGBColor savedFore, savedBack;

    h = gFrames[idx];
    if (h == 0L) return;

    HLock(h);
    src = *h;
    dp  = gFrameBuf;
    if (gGif.packed) {
        for (row = 0; row < gGif.h; row++)
            UnpackBits(&src, &dp, gGif.w);
    } else {
        BlockMove(src, gFrameBuf, (long)gGif.w * (long)gGif.h);
    }
    HUnlock(h);

    GetForeColor(&savedFore);
    GetBackColor(&savedBack);
    ForeColor(blackColor);
    BackColor(whiteColor);
    CopyBits((BitMap *)&gSrcPM,
             (BitMap *)*(((CGrafPtr)w)->portPixMap),
             &gSrcPM.bounds, dst, srcCopy, 0L);
    RGBForeColor(&savedFore);
    RGBBackColor(&savedBack);
}

void ShowAbout(void)
{
    WindowPtr   w;
    Rect        r, gif;
    EventRecord ev;
    short       fr;
    Boolean     done;

    SetRect(&r, 80, 60, 520, 380);               /* 440 x 320 */
    w = NewCWindow(0L, &r, "\pAbout ClaudeApp", true, dBoxProc,
                   (WindowPtr)-1L, false, 0L);
    SetPort(w);
    TextFont(3);        /* Geneva */
    TextSize(10);

    MoveTo(26, 34);  TextSize(14); DrawString("\pClaudeApp");  TextSize(10);
    MoveTo(26, 62);  DrawString("\pA windowed Macintosh application.");
    MoveTo(26, 90);  DrawString("\pWritten and built by Claude, an AI from 2026,");
    MoveTo(26, 108); DrawString("\pdriving a 1994 THINK C IDE on System 7.6.1");
    MoveTo(26, 126); DrawString("\pthrough the AppleBridge - no hands on the keyboard.");
    MoveTo(26, 296); DrawString("\p(click anywhere to close)");

    SetRect(&gif, 60, 150, 380, 270);
    GifLoad();
    ValidRect(&w->portRect);   /* clear any pending update so WNE can sleep */

    /* Cooperative animation loop: WaitNextEvent yields the CPU to background
     * processes (the AppleBridge daemon!) between frames, so the bridge stays
     * responsive - and a synthetic mouseDown dismisses it like a real one. */
    fr   = 0;
    done = false;
    while (!done) {
        if (gGifReady) {
            GifDrawFrame(w, fr, &gif);
            if (++fr >= gGif.count) fr = 0;
        }
        if (WaitNextEvent(mDownMask | keyDownMask, &ev,
                          gGifReady ? (long)gGif.delay : 30L, 0L)) {
            if (ev.what == mouseDown || ev.what == keyDown)
                done = true;
        }
    }
    DisposeWindow(w);
    SetPort(gWin);
}

void DoMenu(long pick)
{
    short  menu = HiWord(pick);
    short  item = LoWord(pick);
    Str255 nm;

    if (menu == kApple) {
        if (item == kAbout) ShowAbout();
        else { GetItem(GetMHandle(kApple), item, nm); OpenDeskAcc(nm); }
    } else if (menu == kFile) {
        if (item == kQuit) gDone = true;
    }
    HiliteMenu(0);
}

void main(void)
{
    EventRecord ev;
    WindowPtr   who;
    MenuHandle  m;
    Rect        r;
    short       part;

    InitGraf(&thePort);
    InitFonts();
    InitWindows();
    InitMenus();
    TEInit();
    InitDialogs(0L);
    InitCursor();

    m = NewMenu(kApple, "\p\024");
    AppendMenu(m, "\pAbout ClaudeApp...;(-");
    AddResMenu(m, 'DRVR');
    InsertMenu(m, 0);
    m = NewMenu(kFile, "\pFile");
    AppendMenu(m, "\pQuit/Q");
    InsertMenu(m, 0);
    DrawMenuBar();

    SetRect(&r, 50, 60, 500, 330);
    gWin = NewWindow(0L, &r, "\pClaudeApp", true, documentProc,
                     (WindowPtr)-1L, true, 0L);
    SetPort(gWin);

#if AUTO_ABOUT
    ShowAbout();       /* TEST: pop the About box straight away */
#endif

    while (!gDone) {
        if (WaitNextEvent(everyEvent, &ev, 20L, 0L)) {
            switch (ev.what) {
            case mouseDown:
                part = FindWindow(ev.where, &who);
                if (part == inMenuBar)
                    DoMenu(MenuSelect(ev.where));
                else if (part == inDrag)
                    DragWindow(who, ev.where, &screenBits.bounds);
                else if (part == inGoAway) {
                    if (TrackGoAway(who, ev.where)) gDone = true;
                }
                break;
            case keyDown:
            case autoKey:
                if (ev.modifiers & cmdKey)
                    DoMenu(MenuKey((char)(ev.message & charCodeMask)));
                break;
            case updateEvt:
                who = (WindowPtr)ev.message;
                BeginUpdate(who);
                SetPort(who);
                EraseRect(&who->portRect);
                if (who == gWin) {
                    TextFont(3); TextSize(12);
                    MoveTo(30, 52);  DrawString("\pHello from THINK C - a windowed app!");
                    MoveTo(30, 88);  DrawString("\pWritten and built by Claude over the");
                    MoveTo(30, 108); DrawString("\pAppleBridge, driving the THINK C IDE.");
                    MoveTo(30, 152); DrawString("\pApple menu  ->  About ClaudeApp   for the party.");
                    MoveTo(30, 186); DrawString("\pFile menu  ->  Quit  (Cmd-Q)  to exit.");
                }
                EndUpdate(who);
                break;
            }
        }
    }
}
