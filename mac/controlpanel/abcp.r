/*
 * AppleBridge Control Panel resources (cdev - polish step).
 * DITL item order MUST match the enum in abcp.c:
 *   1=label, 2=status, 3=autostart, 4=host IP,
 *   5=helper LIST (userItem, self-drawn), 6=Add Helper App, 7=Remove Helper.
 * nrct/mach formats taken from a working sample cdev.
 *
 * Polish: item 5 was a one-line StaticText ("Helpers: a, b"); it is now an
 * ENABLED userItem whose rect the cdev draws itself as a multi-row, click-to-
 * select list (List Manager is glue in this MPW - not inline traps - so a
 * self-drawn userItem keeps the code A4-free; see README). Item 7 removes the
 * selected helper's APP= line from the shared prefs file.
 */

#include "Types.r"

resource 'DITL' (-4064, purgeable) {
    {
        /* 1: label / instructions */
        /* Label fits the 34px box in exactly two lines of the system font
         * (Chicago 12); a longer string overruns to a clipped third line. */
        {6, 92, 40, 318},
        StaticText {
            disabled,
            "AppleBridge status, host IP, and helper apps."
        };
        /* 2: the daemon status, updated via SetDialogItemText on nulDev.
         *    Left edge indented to 114 so the LED (x 94..106) drawn by the cdev
         *    sits in a region owned by NO DITL item (Dialog Mgr won't erase it). */
        {44, 114, 60, 318},
        StaticText { disabled, "(checking daemon...)" };
        /* 3: the autostart status, updated via SetDialogItemText on nulDev.
         *    Left edge indented to 114 for the same LED reason. */
        {62, 114, 78, 318},
        StaticText { disabled, "(checking autostart...)" };
        /* 4: the host IP, read from the prefs file's IP= line */
        {80, 92, 96, 318},
        StaticText { disabled, "(reading IP...)" };
        /* 5: the helper-app LIST - an ENABLED userItem the cdev draws itself
         *    (rows + selection); clicks come back through hitDev as this item. */
        {100, 92, 174, 318},
        UserItem { enabled };
        /* 6: Add Helper App button (dispatched via hitDev -> Standard File) */
        {182, 92, 202, 224},
        Button { enabled, "Add Helper App..." };
        /* 7: Remove button (dispatched via hitDev -> rewrite prefs). Short label:
         *    "Remove Helper" overran this 86px button's rounded frame on-device. */
        {182, 232, 202, 318},
        Button { enabled, "Remove" };
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

/*
 * Add-picker guidance and refusals (2026-07-29), mirroring AppleBridgeConfig.
 *
 * IDs stay inside the cdev's OWN range (-4064..-4033). The Control Panel merges
 * every cdev's resources into one chain, so an id outside that range can collide
 * with another panel's and put somebody else's dialog on screen. -4064 is
 * already this cdev's DITL.
 *
 * The alert exists because SFGetFile's prompt parameter is IGNORED by the
 * Standard File package (only SFPutFile shows one) — filling that string in
 * would look like a fix and change nothing.
 */
resource 'DITL' (-4063, purgeable) {
    {
        {58, 240, 78, 310}, Button  { enabled, "OK" };
        {10, 70,  46, 320}, StaticText { disabled,
            "Choose a helper application like ToolServer." };
    }
};

resource 'ALRT' (-4063, purgeable) {
    {60, 60, 156, 400}, -4063,
    { OK, visible, silent, OK, visible, silent,
      OK, visible, silent, OK, visible, silent },
    alertPositionMainScreen
};

resource 'DITL' (-4062, purgeable) {
    {
        {58, 240, 78, 310}, Button  { enabled, "OK" };
        {10, 70,  46, 320}, StaticText { disabled,
            "That is part of AppleBridge. Choose another application." };
    }
};

resource 'ALRT' (-4062, purgeable) {
    {60, 60, 156, 400}, -4062,
    { OK, visible, silent, OK, visible, silent,
      OK, visible, silent, OK, visible, silent },
    alertPositionMainScreen
};

resource 'DITL' (-4061, purgeable) {
    {
        {58, 240, 78, 310}, Button  { enabled, "OK" };
        {10, 70,  46, 320}, StaticText { disabled,
            "That is not an application. Choose one that is." };
    }
};

resource 'ALRT' (-4061, purgeable) {
    {60, 60, 156, 400}, -4061,
    { OK, visible, silent, OK, visible, silent,
      OK, visible, silent, OK, visible, silent },
    alertPositionMainScreen
};
