/*
 * events.c -- synthetic input injection (gap 2 of the MCP tooling ledger).
 *
 * launch_app + mac_screenshot were observe-only: the bridge could see a GUI app
 * but not act on it. These verbs post keyboard and mouse events into the OS
 * event queue so the host can DRIVE the front app -- closing the loop into a
 * real "drive a control, screenshot, verify" cycle.
 *
 * Delivery: PostEvent adds to the global OS event queue; under the Process
 * Manager keyDown/mouseDown are delivered to the FRONTMOST application -- i.e.
 * whatever launch_app last brought up -- while this faceless daemon stays in the
 * background and never swallows them itself.
 *
 * Keyboard injection is robust: the target reads charCode straight from the
 * event record. Mouse injection additionally pokes the low-memory mouse
 * location and button state, so controls tracked with TrackControl/StillDown
 * (which poll the live button) register the press, not just the queued event.
 *
 * Low-memory globals are poked directly through volatile absolute pointers
 * rather than via <LowMem.h> accessors, to avoid Universal-Headers version
 * differences on the build host.
 *
 * Modifiers (Command/Option/Shift) are NOT faked here -- PostEvent records the
 * modifier flags from the live keyboard state, which this path does not drive.
 * mac_type / unmodified mac_key cover text entry, Return/Enter, Tab, Escape and
 * the arrows; Command-key menu shortcuts are a follow-up (would need KeyMap
 * poking). See the ledger note.
 */
#include <applebridge.h>
#include <Events.h>
#include <OSUtils.h>

/* Classic-Mac low-memory globals (Inside Macintosh: low-memory global list). */
#define LM_MBState   (*(volatile signed char *)0x0172L)  /* mouse button: 0 = down, <0 = up */
#define LM_MTemp     (*(volatile Point *)0x0828L)         /* interim mouse location */
#define LM_RawMouse  (*(volatile Point *)0x082CL)         /* raw mouse location */
#define LM_Mouse     (*(volatile Point *)0x0830L)         /* processed mouse location */
#define LM_CrsrNew   (*(volatile signed char *)0x08CEL)   /* nonzero => cursor needs redraw */

static void ShortDelay(long ticks)
{
    unsigned long t;
    Delay(ticks, &t);
}

/* Post one event, RETRYING while the OS event queue is full so a keystroke is
 * never silently dropped when the front app is momentarily busy (opening a file,
 * rendering a list). The ~20-deep queue overflows if we post faster than the app
 * drains it; on failure we yield (let the app run) and retry the same event.
 * Bounded (~1 s of retries) so a wedged front app can't hang the daemon. */
static OSErr PostEventRetry(short what, long msg)
{
    OSErr e;
    short tries;
    for (tries = 0; tries < 48; tries++) {
        e = PostEvent(what, msg);
        if (e == noErr) return noErr;   /* queued */
        SystemTask();                   /* give the front app a slice to drain */
        ShortDelay(2L);                 /* ...then retry the same event */
    }
    return e;                           /* still full after the retry budget */
}

/* Post one keystroke (keyDown then keyUp) to the front app. keyCode is the
 * virtual key code (0 is fine for plain characters; supply it for keys an app
 * distinguishes by position). charCode is the ASCII/MacRoman byte. */
OSErr InjectKey(short charCode, short keyCode)
{
    long  msg = (((long)(keyCode & 0x7F)) << 8) | (long)(charCode & 0xFF);
    OSErr e1, e2;
    e1 = PostEventRetry(keyDown, msg);
    ShortDelay(2L);
    e2 = PostEventRetry(keyUp, msg);
    return (e1 != noErr) ? e1 : e2;
}

/* Type a run of characters. Yields between keys so the front app drains the OS
 * event queue (default depth ~20) instead of overflowing it. */
OSErr InjectType(const char *text, long len)
{
    long  i;
    OSErr last = noErr;
    if (len > 1024L) len = 1024L;             /* bound a single burst */
    for (i = 0; i < len; i++) {
        OSErr r = InjectKey((short)(unsigned char)text[i], 0);
        if (r != noErr) last = r;
        SystemTask();                         /* give the front app a slice */
        ShortDelay(1L);                       /* ~1/60 s; keep the queue shallow */
    }
    return last;
}

/* Move the emulated mouse to (h,v) and post a click there. Pokes the button
 * state low-memory so TrackControl-style polling sees a genuine press. */
OSErr InjectClick(short h, short v)
{
    Point p;
    p.h = h; p.v = v;
    LM_MTemp    = p;
    LM_RawMouse = p;
    LM_Mouse    = p;
    LM_CrsrNew  = 1;                           /* force the cursor to follow */
    LM_MBState  = 0x00;                        /* button DOWN */
    PostEvent(mouseDown, 0L);
    ShortDelay(4L);                            /* hold ~1/15 s for tracking loops */
    LM_MBState  = (signed char)0x80;           /* button UP */
    PostEvent(mouseUp, 0L);
    return noErr;
}
