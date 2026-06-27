/*
 * AppleBridgeWatchdog resources.
 * A FACELESS (onlyBackground) helper, like the daemon — no window, no
 * Application-menu entry. isHighLevelEventAware so the shutdown
 * kAEQuitApplication reaches its handler. It needs very little memory.
 */

#include "Types.r"

resource 'SIZE' (-1) {
    reserved,
    acceptSuspendResumeEvents,
    reserved,
    canBackground,
    doesActivateOnFGSwitch,
    onlyBackground,
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
    1024 * 1024,        /* preferred: 1 MB */
    512 * 1024          /* minimum: 512 KB */
};
