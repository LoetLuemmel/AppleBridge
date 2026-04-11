/*
 * AppleBridge Resource File
 * Defines SIZE resource for Apple Events support
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
    ignoreAppDiedEvents,
    is32BitCompatible,
    isHighLevelEventAware,        /* REQUIRED for Apple Events */
    localAndRemoteHLEvents,        /* Accept events from other apps */
    isStationeryAware,
    useTextEditServices,
    reserved,
    reserved,
    reserved,
    512 * 1024,  /* preferred size (512K) */
    512 * 1024   /* minimum size (512K) */
};
