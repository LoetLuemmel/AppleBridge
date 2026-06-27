/*
 * AppleBridge Control Panel resources (cdev PoC, step 4b-detect).
 * DITL item order MUST match the enum in abcp.c: 1=label, 2=status, 3=autostart.
 * nrct/mach formats taken from a working sample cdev.
 */

#include "Types.r"

resource 'DITL' (-4064, purgeable) {
    {
        /* 1: label / instructions */
        {10, 92, 50, 318},
        StaticText {
            disabled,
            "AppleBridge cdev - step 4b: live daemon + autostart status, polled "
            "on nulDev from the Process Manager and the Folder Manager."
        };
        /* 2: the daemon status, updated via SetDialogItemText on nulDev */
        {58, 92, 76, 318},
        StaticText { disabled, "(checking daemon...)" };
        /* 3: the autostart status, updated via SetDialogItemText on nulDev */
        {80, 92, 98, 318},
        StaticText { disabled, "(checking autostart...)" };
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
