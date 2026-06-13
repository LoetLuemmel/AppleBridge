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
    12 * 1024 * 1024,   /* preferred: 12 MB (headroom for 4 MB responses; AE
                           extraction transiently double-buffers ~2x) */
    8 * 1024 * 1024     /* minimum: 8 MB */
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
    12 * 1024 * 1024,
    8 * 1024 * 1024
};
