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
 * Modifiers (Command/Option/Shift) ARE faked here (added for menu shortcuts).
 * PostEvent alone can't carry them -- it supplies only (what, message) and the
 * modifier flags are otherwise taken from the live keyboard state. So we drive
 * BOTH mechanisms belt-and-suspenders: PPostEvent hands back the queued event
 * element so we stamp its evtQModifiers directly, AND we hold the modifier keys'
 * bits down in the low-memory KeyMap across the front app's read, so whether the
 * Event Manager delivers the stored modifiers or recomputes them at
 * GetNextEvent time, the app sees e.g. cmdKey and dispatches MenuKey. This is
 * what makes Command-key menu shortcuts reachable (mac_menu / modified mac_key).
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

/* Low-memory KeyMap: 16-byte bitmap of the current keyboard state, one bit per
 * virtual key code (byte keyCode>>3, bit keyCode&7 -- the documented GetKeys
 * layout). GetNextEvent reads this to build EventRecord.modifiers. */
#define LM_KeyMap    ((volatile unsigned char *)0x0174L)

/* Modifier virtual key codes (Inside Macintosh: Toolbox/keyboard). */
/* System event mask -- which event types PostEvent is allowed to queue at all.
 * PostEvent answers evtNotEnb (1) for a type whose bit is clear here, and it
 * does so EVERY time, so a retry loop against it can only burn its budget. Read
 * rather than assumed: this is the first candidate the KEYSTAT verb reports. */
#define LM_SysEvtMask (*(volatile short *)0x0144L)

/* --- keystroke instrument -------------------------------------------------
 * Measured 2026-08-05: a keystroke costs 1.69 s while a CLICK costs 0.102 s,
 * on an idle freshly booted guest, and the difference is almost exactly ONE
 * exhausted retry budget. The arithmetic said as much and the arithmetic had
 * already pointed at a wrong cause once that morning ("the queue is full",
 * refuted by a reboot). So this counts instead of inferring: how many attempts
 * each half of a keystroke really used, and what PostEvent actually answered. */
static short gKD_Tries = -1;     /* attempts used by the last keyDown  */
static short gKU_Tries = -1;     /* attempts used by the last keyUp    */
static OSErr gKD_Err   = 0;      /* what PostEvent last answered for keyDown */
static OSErr gKU_Err   = 0;      /* ...and for keyUp */
static long  gKeyTicks = 0;      /* wall-clock ticks of the last keystroke */
static long  gKeyCount = 0;      /* keystrokes injected since launch */
static short gKeyMaskFix = 0;    /* 1 if the last post had to enable its own event type */

short KeyProbeMaskFix(void) { return gKeyMaskFix; }

void KeyProbeRead(short *kdTries, short *kuTries, short *kdErr, short *kuErr,
                  long *ticks, long *count, short *sysEvtMask)
{
    *kdTries = gKD_Tries;  *kuTries = gKU_Tries;
    *kdErr   = (short)gKD_Err;  *kuErr = (short)gKU_Err;
    *ticks   = gKeyTicks;  *count = gKeyCount;
    *sysEvtMask = LM_SysEvtMask;
}

#define VK_CMD       0x37
#define VK_SHIFT     0x38
#define VK_CAPS      0x39
#define VK_OPTION    0x3A
#define VK_CONTROL   0x3B

static void ShortDelay(long ticks)
{
    unsigned long t;
    Delay(ticks, &t);
}

/* Set (down=true) or clear one virtual key's bit in the low-memory KeyMap. */
static void KeyMapBit(short vkey, Boolean down)
{
    volatile unsigned char *p = LM_KeyMap + (vkey >> 3);
    unsigned char mask = (unsigned char)(1 << (vkey & 7));
    if (down) *p = (unsigned char)(*p | mask);
    else      *p = (unsigned char)(*p & ~mask);
}

/* Press (down=true) or release every modifier key present in the Event Manager
 * modifier mask, by poking its KeyMap bit. Mirrors the mask bits back to the
 * physical modifier keys the KeyMap tracks. */
static void ApplyModifierKeys(short mods, Boolean down)
{
    if (mods & cmdKey)     KeyMapBit(VK_CMD,     down);
    if (mods & shiftKey)   KeyMapBit(VK_SHIFT,   down);
    if (mods & alphaLock)  KeyMapBit(VK_CAPS,    down);
    if (mods & optionKey)  KeyMapBit(VK_OPTION,  down);
    if (mods & controlKey) KeyMapBit(VK_CONTROL, down);
}

/* Post one event, RETRYING while the OS event queue is full so a keystroke is
 * never silently dropped when the front app is momentarily busy (opening a file,
 * rendering a list). The ~20-deep queue overflows if we post faster than the app
 * drains it; on failure we yield (let the app run) and retry the same event.
 * Bounded (~1 s of retries) so a wedged front app can't hang the daemon.
 *
 * Uses PPostEvent (the queue-element-returning variant of PostEvent) so we can
 * stamp evtQModifiers directly on the queued event -- carrying Command/Shift/etc.
 * that PostEvent's (what,message)-only interface can't express. */
static OSErr PPostEventRetry(short what, long msg, short modifiers)
{
    OSErr     e = noErr;
    short     tries;
    EvQElPtr  qEl;
    /* MEASURED 2026-08-05, by the instrument two functions up, after three
     * wrong explanations from arithmetic alone:
     *
     *     keydownTries=0  keydownErr=0     <- keyDown lands first try
     *     keyupTries=48   keyupErr=1       <- keyUp refused 48 times, evtNotEnb
     *     sysEvtMask=-17  == 0xFFEF        <- every bit set EXCEPT bit 4 = keyUpMask
     *
     * System 7 ships with keyUp disabled in the system event mask, because
     * almost no classic application wants keyUp events. So PostEvent refused
     * every keyUp this daemon ever sent, the retry loop burned its whole budget
     * against a wall, and the verb answered "Typed". 1.69 s per keystroke, since
     * the feature was written.
     *
     * Two fixes, and the second is the one that generalises. Enable the type
     * for the duration of the post and put the mask back; and STOP RETRYING an
     * error that retrying cannot fix — evtNotEnb is a statement about
     * configuration, not about congestion, and 48 attempts at it only convert a
     * refusal into a delay. */
    short     bit       = (short)(1 << what);
    short     savedMask = LM_SysEvtMask;
    Boolean   patched   = (Boolean)((savedMask & bit) == 0);
    if (patched) SetEventMask((short)(savedMask | bit));
    for (tries = 0; tries < 48; tries++) {
        qEl = 0L;
        e = PPostEvent((EventKind)what, (unsigned long)msg, &qEl);
        if (e == noErr) {
            if (qEl) qEl->evtQModifiers = (EventModifiers)modifiers;
            break;                       /* queued (modifiers stamped) */
        }
        if (e == evtNotEnb) break;       /* a disabled type stays disabled */
        SystemTask();                    /* give the front app a slice to drain */
        ShortDelay(2L);                  /* ...then retry the same event */
    }
    if (patched) SetEventMask(savedMask);
    gKeyMaskFix = patched ? 1 : 0;
    /* Record BOTH halves separately. "One of the two posts burns its budget"
     * was an inference from a stopwatch; which one, and with what error, is a
     * fact the daemon can simply state. */
    if (what == keyDown) { gKD_Tries = tries; gKD_Err = e; }
    else if (what == keyUp) { gKU_Tries = tries; gKU_Err = e; }
    return e;                            /* noErr, or the last refusal */
}

/* Post one keystroke (keyDown then keyUp) with modifiers to the front app.
 * keyCode is the virtual key code (0 is fine for plain characters); charCode is
 * the ASCII/MacRoman byte; modifiers is the Event Manager mask (cmdKey 256,
 * shiftKey 512, optionKey 2048, controlKey 4096, alphaLock 1024).
 *
 * Both mechanisms run so the modifier lands regardless of how the front app's
 * event read resolves it: we stamp evtQModifiers on the queued event AND hold
 * the modifier keys down in the KeyMap across the app's read, then release. */
OSErr InjectKeyMod(short charCode, short keyCode, short modifiers)
{
    long  t0  = (long)TickCount();
    long  msg = (((long)(keyCode & 0x7F)) << 8) | (long)(charCode & 0xFF);
    short m   = (short)(modifiers | btnState);   /* btnState = mouse up, as real key events carry */
    OSErr e1, e2;

    ApplyModifierKeys(modifiers, true);          /* hold the modifiers down */
    e1 = PPostEventRetry(keyDown, msg, m);
    SystemTask();                                /* let the app read keyDown while held */
    ShortDelay(2L);
    e2 = PPostEventRetry(keyUp, msg, m);
    ShortDelay(2L);
    ApplyModifierKeys(modifiers, false);         /* release: restore keyboard state */

    gKeyTicks = (long)TickCount() - t0;
    gKeyCount++;
    return (e1 != noErr) ? e1 : e2;
}

/* Post one unmodified keystroke -- the common text-entry path (kept lossless via
 * the retry loop). Thin wrapper over InjectKeyMod with no modifiers. */
OSErr InjectKey(short charCode, short keyCode)
{
    return InjectKeyMod(charCode, keyCode, 0);
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

/* Move the emulated mouse to (h,v) and post `count` clicks there with modifiers.
 * Pokes the button-state low-memory so TrackControl-style polling sees a genuine
 * press. count>1 posts successive mouseDown/Up pairs at the SAME point within a
 * few ticks (< GetDblTime) so the front app's own double-click detection (which
 * compares consecutive mouseDown time+location) fires -- there is no double-click
 * event flag on classic Mac. Modifiers (shift/cmd/option for extend/multi-select)
 * are stamped on the queued mouseDown via PPostEvent AND held in the KeyMap across
 * the app's read, mirroring the keystroke path. */
OSErr InjectClickMod(short h, short v, short count, short modifiers)
{
    Point p;
    short c;

    p.h = h; p.v = v;
    LM_MTemp    = p;
    LM_RawMouse = p;
    LM_Mouse    = p;
    LM_CrsrNew  = 1;                           /* force the cursor to follow */

    if (count < 1) count = 1;
    if (count > 3) count = 3;                  /* single / double / triple */

    /* Modifiers ride ONLY the low-memory KeyMap here (shift-/command-click),
     * never PPostEvent's queue-element stamping: a first attempt at stamping
     * evtQModifiers on POSTED MOUSE events crashed the guest on the second click
     * (0.8d7). Real double-clicks are just repeated plain PostEvent presses at
     * the same point within GetDblTime, so that is exactly what we do. */
    if (modifiers) ApplyModifierKeys(modifiers, true);
    for (c = 0; c < count; c++) {
        LM_MBState = 0x00;                     /* button DOWN */
        PostEvent(mouseDown, 0L);              /* plain PostEvent (proven safe) */
        ShortDelay(4L);                        /* hold ~1/15 s for tracking loops */
        LM_MBState = (signed char)0x80;        /* button UP */
        PostEvent(mouseUp, 0L);
        if (c + 1 < count) {
            /* Size the inter-click gap to the machine's OWN double-click window so
             * the down->down interval stays inside GetDblTime() even when the Mouse
             * control panel is set fast -- a fixed gap can exceed it, and the front
             * app then registers two single clicks instead of a double-click. */
            long g = GetDblTime() / 2 - 4;     /* less the 4-tick press hold above */
            ShortDelay(g > 1 ? g : 1);
        }
    }
    if (modifiers) ApplyModifierKeys(modifiers, false);
    return noErr;
}

/* Single unmodified click -- the common path (CLICK:<h>:<v>). */
OSErr InjectClick(short h, short v)
{
    return InjectClickMod(h, v, 1, 0);
}
