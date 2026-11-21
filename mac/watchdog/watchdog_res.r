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
    /* 128 K / 64 K (2026-11-21). It was 1 MB / 512 K, which on a 12 MB PowerBook
     * Duo 230 was a twelfth of the machine for a loop that calls GetNextProcess
     * once a second; at 128 K it ran there for the rest of the day (117 K free
     * in its own heap). The emulators never showed the cost. */
    128 * 1024,         /* preferred: 128 KB */
    64 * 1024           /* minimum: 64 KB */
};
