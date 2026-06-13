/*
 * AppleBridge Resources
 * SIZE resource required for Apple Events support
 */

#include "Types.r"

/* SIZE resource - CRITICAL for Apple Events!
 * Flags:
 *   0x5880 = acceptSuspendResumeEvents | canBackground | isHighLevelEventAware | localAndRemoteHLEvents
 */
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
    isHighLevelEventAware,      /* REQUIRED for Apple Events! */
    localAndRemoteHLEvents,     /* Accept events from other apps */
    notStationeryAware,
    reserved,
    reserved,
    reserved,
    reserved,
    512 * 1024,    /* preferred size: 512K */
    256 * 1024     /* minimum size: 256K */
};

/* Also include SIZE 0 for compatibility */
resource 'SIZE' (0) {
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
    512 * 1024,
    256 * 1024
};
