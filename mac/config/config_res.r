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
