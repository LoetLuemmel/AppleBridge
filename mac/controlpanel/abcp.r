/*
 * AppleBridge Control Panel resources (cdev PoC, step 4a).
 * DITL item order MUST match the enum in abcp.c: 1=label, 2=status.
 * nrct/mach formats taken from a working sample cdev.
 */

#include "Types.r"

resource 'DITL' (-4064, purgeable) {
    {
        /* 1: label / instructions */
        {12, 92, 56, 318},
        StaticText {
            disabled,
            "AppleBridge cdev - step 4a: live daemon status.  Polled from the "
            "Process Manager on nulDev (the Control Panel's idle message)."
        };
        /* 2: the daemon status, updated via SetDialogItemText on nulDev */
        {66, 92, 84, 318},
        StaticText { disabled, "(checking...)" };
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
