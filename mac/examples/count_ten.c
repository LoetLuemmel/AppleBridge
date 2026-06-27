/*
 * CountTen - a tiny Mac application that counts 1..10 in a window, one second
 * per step, with a System Beep at every step. Built and run on System 7.6.1
 * over AppleBridge.
 *
 * Note on stdio: this MPW's StdCLib.o doesn't resolve the stdio symbols
 * (fclose/fflush/_iob ...), so a printf/SIOW console app won't link here. A
 * Toolbox window is cleaner anyway - you SEE the number on screen. It links
 * with the same Interface.o + MacRuntime.o the daemon uses (minus OpenTransport),
 * and the count string is built by hand (no number-formatting library needed).
 */

#include <Quickdraw.h>
#include <Fonts.h>
#include <Windows.h>
#include <Sound.h>      /* SysBeep */
#include <OSUtils.h>    /* Delay   */

QDGlobals qd;

void main(void)
{
    WindowPtr     w;
    Rect          bounds;
    short         i;
    unsigned long dummy;
    Str255        s;

    InitGraf(&qd.thePort);
    InitFonts();
    InitWindows();
    InitCursor();

    SetRect(&bounds, 120, 110, 400, 250);
    w = NewWindow(0L, &bounds, "\pCount To Ten", true,
                  documentProc, (WindowPtr) -1L, false, 0L);
    SetPort(w);
    TextFont(0); TextFace(bold); TextSize(48);

    for (i = 1; i <= 10; i++) {
        if (i < 10) { s[0] = 1; s[1] = (char)('0' + i); }   /* "1".."9" */
        else        { s[0] = 2; s[1] = '1'; s[2] = '0'; }    /* "10"     */
        EraseRect(&w->portRect);
        MoveTo(120, 95);
        DrawString(s);
        SysBeep(15);             /* a short system beep */
        Delay(60UL, &dummy);     /* 60 ticks = 1 second */
    }

    /* leave the "10" up for a couple of seconds so it's visible */
    Delay(120UL, &dummy);
}
