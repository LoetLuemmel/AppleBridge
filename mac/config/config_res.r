/*
 * AppleBridgeConfig resources.
 * A normal FOREGROUND app (not onlyBackground) — it has a window + the Standard
 * File picker. isHighLevelEventAware so it can send the quit Apple Event to the
 * daemon and be a good Apple Event citizen.
 */

#include "Types.r"

resource 'SIZE' (-1) {
    reserved,
    acceptSuspendResumeEvents,
    reserved,
    canBackground,
    doesActivateOnFGSwitch,
    backgroundAndForeground,
    dontGetFrontClicks,
    ignoreChildDiedEvents,
    is32BitCompatible,
    isHighLevelEventAware,
    localAndRemoteHLEvents,
    notStationeryAware,
    reserved,
    reserved,
    reserved,
    reserved,
    2 * 1024 * 1024,    /* preferred: 2 MB */
    1024 * 1024         /* minimum: 1 MB */
};

/*
 * The Add-helper explanation (2026-07-29).
 *
 * The Add button used to open Standard File with no guidance at all, so the
 * operator met a file dialog and was expected to know it wanted an application
 * to chain-launch beside the daemon — ToolServer first. Reported by the one
 * person who could not have guessed it.
 *
 * It is an ALERT rather than a prompt string on purpose, and the reason is a
 * trap worth leaving written down: `StandardGetFile` takes NO prompt, and
 * `SFGetFile`'s prompt parameter is IGNORED (Inside Macintosh — only
 * SFPutFile displays one). Filling that string in changes nothing on screen
 * while looking exactly like a fix.
 */
resource 'DITL' (300, purgeable) {
    {
        {58, 240, 78, 310}, Button  { enabled, "OK" };
        {10, 70,  46, 320}, StaticText { disabled,
            "Choose a helper application like ToolServer." };
    }
};

resource 'ALRT' (300, purgeable) {
    {60, 60, 156, 400}, 300,
    { OK, visible, silent, OK, visible, silent,
      OK, visible, silent, OK, visible, silent },
    alertPositionMainScreen
};

/* Refusals from the Add picker. Short on purpose: the operator is mid-task and
   the picker comes straight back, so these say what is wrong and get out of
   the way. */
resource 'DITL' (301, purgeable) {
    {
        {58, 240, 78, 310}, Button  { enabled, "OK" };
        {10, 70,  46, 320}, StaticText { disabled,
            "That is part of AppleBridge. Choose another application." };
    }
};

resource 'ALRT' (301, purgeable) {
    {60, 60, 156, 400}, 301,
    { OK, visible, silent, OK, visible, silent,
      OK, visible, silent, OK, visible, silent },
    alertPositionMainScreen
};

resource 'DITL' (302, purgeable) {
    {
        {58, 240, 78, 310}, Button  { enabled, "OK" };
        {10, 70,  46, 320}, StaticText { disabled,
            "That is not an application. Choose one that is." };
    }
};

resource 'ALRT' (302, purgeable) {
    {60, 60, 156, 400}, 302,
    { OK, visible, silent, OK, visible, silent,
      OK, visible, silent, OK, visible, silent },
    alertPositionMainScreen
};
