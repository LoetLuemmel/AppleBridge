/*
 * AppleBridge Control Panel resources (cdev PoC, step 4c).
 * DITL item order MUST match the enum in abcp.c:
 *   1=label, 2=status, 3=autostart, 4=host IP, 5=helper list, 6=Add Helper App.
 * nrct/mach formats taken from a working sample cdev.
 */

#include "Types.r"

resource 'DITL' (-4064, purgeable) {
    {
        /* 1: label / instructions */
        {6, 92, 36, 318},
        StaticText {
            disabled,
            "AppleBridge cdev - step 4c: daemon + autostart status, host IP, "
            "helper apps, and Add Helper App."
        };
        /* 2: the daemon status, updated via SetDialogItemText on nulDev.
         *    Left edge indented to 114 so the LED (x 94..106) drawn by the cdev
         *    sits in a region owned by NO DITL item (Dialog Mgr won't erase it). */
        {42, 114, 58, 318},
        StaticText { disabled, "(checking daemon...)" };
        /* 3: the autostart status, updated via SetDialogItemText on nulDev.
         *    Left edge indented to 114 for the same LED reason. */
        {60, 114, 76, 318},
        StaticText { disabled, "(checking autostart...)" };
        /* 4: the host IP, read from the prefs file's IP= line */
        {78, 92, 94, 318},
        StaticText { disabled, "(reading IP...)" };
        /* 5: the helper-app list, read from the prefs file's APP= lines */
        {96, 92, 112, 318},
        StaticText { disabled, "(reading helpers...)" };
        /* 6: Add Helper App button (dispatched via hitDev -> Standard File) */
        {120, 92, 140, 318},
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
