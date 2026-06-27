/*
 * MinQDC - QuickDraw-in-C POC for AppleBridge
 *
 * A real graphics proof-of-concept: open a window and exercise a spread of
 * QuickDraw primitives (frames, fills, ovals, a rounded rect, a fan of lines,
 * pattern fills, and styled text), then stay up briefly so the host can
 * screenshot it. The MPW C runtime sets up the A5 world; the app supplies the
 * QuickDraw-globals storage itself (`QDGlobals qd;`), exactly like Apple's
 * sample apps.
 */

#include <QuickDraw.h>
#include <Fonts.h>
#include <Events.h>
#include <Windows.h>
#include <TextEdit.h>
#include <Dialogs.h>
#include <Menus.h>

QDGlobals qd;                 /* application-supplied QuickDraw globals */

static void DrawContent(WindowPtr w)
{
    Rect r;
    short i;

    SetPort(w);
    EraseRect(&w->portRect);

    /* Title + caption */
    TextFont(0);
    TextFace(bold);
    TextSize(18);
    MoveTo(20, 30);
    DrawString("\pQuickDraw in C - AppleBridge POC");
    TextFace(0);
    TextSize(10);
    MoveTo(20, 48);
    DrawString("\pframes - fills - ovals - rounded - lines - patterns - text");

    /* Row 1: framed, painted, gray-filled rectangles */
    SetRect(&r, 20, 64, 120, 134);
    FrameRect(&r);
    SetRect(&r, 150, 64, 250, 134);
    PaintRect(&r);
    SetRect(&r, 280, 64, 380, 134);
    FillRect(&r, &qd.gray);
    FrameRect(&r);

    /* Row 2: rounded rect, painted oval, framed oval */
    SetRect(&r, 20, 154, 120, 224);
    FrameRoundRect(&r, 28, 28);
    SetRect(&r, 150, 154, 250, 224);
    PaintOval(&r);
    SetRect(&r, 280, 154, 380, 224);
    FrameOval(&r);

    /* A fan of lines */
    for (i = 0; i <= 100; i += 8) {
        MoveTo(20, 244);
        LineTo(20 + i * 3, 320);
    }

    /* Pattern-filled bar with a frame */
    SetRect(&r, 20, 330, 380, 362);
    FillRect(&r, &qd.ltGray);
    FrameRect(&r);
    TextFace(0);
    TextSize(9);
    MoveTo(28, 351);
    DrawString("\pltGray pattern fill");
}

int main(void)
{
    WindowPtr win;
    Rect bounds;
    EventRecord ev;
    long deadline;

    InitGraf(&qd.thePort);
    InitFonts();
    InitWindows();
    InitMenus();
    TEInit();
    InitDialogs(0L);
    InitCursor();

    SetRect(&bounds, 60, 60, 460, 500);
    win = NewWindow(0L, &bounds, "\pQuickDraw POC", true,
                    documentProc, (WindowPtr) -1L, false, 0L);
    if (win == 0L)
        return 0;

    DrawContent(win);

    /* Stay up ~25s, or until a click, redrawing on update events. */
    deadline = TickCount() + 25L * 60L;
    while (TickCount() < deadline) {
        if (WaitNextEvent(everyEvent, &ev, 10L, 0L)) {
            if (ev.what == mouseDown)
                break;
            if (ev.what == updateEvt) {
                BeginUpdate((WindowPtr) ev.message);
                DrawContent((WindowPtr) ev.message);
                EndUpdate((WindowPtr) ev.message);
            }
        }
    }

    DisposeWindow(win);
    return 0;
}
