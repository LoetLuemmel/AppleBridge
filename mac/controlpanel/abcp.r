/*
 * AppleBridge Control Panel resources (cdev PoC, step 2).
 * DITL item order MUST match the enum in abcp.c: 1=label, 2=button, 3=count.
 * nrct/mach formats taken from a working sample cdev.
 */

#include "Types.r"

resource 'DITL' (-4064, purgeable) {
    {
        /* 1: label / instructions */
        {12, 92, 56, 318},
        StaticText {
            disabled,
            "AppleBridge cdev - step 2: button dispatch.  Click the button; "
            "the Mac beeps and a '*' is added below (one per click, counted in "
            "the cdev's storage handle)."
        };
        /* 2: the button (dispatched via hitDev) */
        {66, 92, 88, 188},
        Button { enabled, "Click me" };
        /* 3: the click tally, updated via SetDialogItemText */
        {68, 200, 86, 318},
        StaticText { disabled, "(clicks appear here)" };
    }
};

/* Our rectangle inside the Control Panel window (top,left,bottom,right). */
data 'nrct' (-4064, purgeable) {
    $"0001 FFFF 0057 00FF 0142"
};

/* Machine filter: FFFF 0000 => call our macDev to decide whether to appear. */
data 'mach' (-4064) {
    $"FFFF 0000"
};
