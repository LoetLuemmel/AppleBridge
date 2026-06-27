/*
 * AppleBridge Control Panel resources (minimal cdev).
 * The required cdev resources at ID -4064: DITL (the items, appended to the
 * Control Panel's), nrct (the rectangle our area occupies), mach (machine
 * filter; FFFF/0000 = "ask macDev"). Formats taken from a working sample cdev.
 */

#include "Types.r"

/* One statText. The host's Dialog Manager draws it automatically. */
resource 'DITL' (-4064, purgeable) {
    {
        {16, 92, 120, 318},
        StaticText {
            disabled,
            "AppleBridge - minimal Control Panel (cdev) test.  "
            "If you can read this in the Control Panel, the cdev built, the "
            "host called macDev + initDev, and the DITL drew.  Step 1 proven."
        };
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
