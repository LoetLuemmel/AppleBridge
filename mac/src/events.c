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
    OSErr     e;
    short     tries;
    EvQElPtr  qEl;
    for (tries = 0; tries < 48; tries++) {
        qEl = 0L;
        e = PPostEvent((EventKind)what, (unsigned long)msg, &qEl);
        if (e == noErr) {
            if (qEl) qEl->evtQModifiers = (EventModifiers)modifiers;
            return noErr;                /* queued (modifiers stamped) */
        }
        SystemTask();                    /* give the front app a slice to drain */
        ShortDelay(2L);                  /* ...then retry the same event */
    }
    return e;                            /* still full after the retry budget */
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
