/*
 * AppleBridge Control Panel resources (cdev PoC, step 3).
 * DITL item order MUST match the enum in abcp.c: 1=label, 2=button, 3=name.
 * nrct/mach formats taken from a working sample cdev.
 */

#include "Types.r"

resource 'DITL' (-4064, purgeable) {
    {
        /* 1: label / instructions */
        {12, 92, 56, 318},
        StaticText {
            disabled,
            "AppleBridge cdev - step 3: Standard File from hitDev.  Click "
            "Choose File... to pop the modal Open dialog; the picked file's "
            "name appears below."
        };
        /* 2: the button (dispatched via hitDev) */
        {66, 92, 88, 200},
        Button { enabled, "Choose File..." };
        /* 3: the chosen file name, updated via SetDialogItemText */
        {68, 208, 86, 318},
        StaticText { disabled, "(no file picked)" };
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
