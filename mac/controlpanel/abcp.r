/*
 * AppleBridge Control Panel resources (cdev PoC, step 4c).
 * DITL item order MUST match the enum in abcp.c:
 *   1=label, 2=status, 3=autostart, 4=helper list, 5=Add Helper App button.
 * nrct/mach formats taken from a working sample cdev.
 */

#include "Types.r"

resource 'DITL' (-4064, purgeable) {
    {
        /* 1: label / instructions */
        {6, 92, 38, 318},
        StaticText {
            disabled,
            "AppleBridge cdev - step 4c: daemon + autostart status, the helper "
            "apps the daemon chain-launches, and Add Helper App."
        };
        /* 2: the daemon status, updated via SetDialogItemText on nulDev */
        {44, 92, 60, 318},
        StaticText { disabled, "(checking daemon...)" };
        /* 3: the autostart status, updated via SetDialogItemText on nulDev */
        {62, 92, 78, 318},
        StaticText { disabled, "(checking autostart...)" };
        /* 4: the helper-app list, read from the prefs file's APP= lines */
        {80, 92, 96, 318},
        StaticText { disabled, "(reading helpers...)" };
        /* 5: Add Helper App button (dispatched via hitDev -> Standard File) */
        {104, 92, 124, 318},
        Button { enabled, "Add Helper App..." };
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
