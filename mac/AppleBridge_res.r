/*
 * AppleBridge Resources
 * SIZE resource required for Apple Events support
 */

#include "Types.r"

/* SIZE resource - CRITICAL for Apple Events + faceless background operation.
 * The daemon normally runs faceless (no window, no Application-menu entry), but
 * the "Mitlesen" live-traffic monitor needs a real foreground window that can
 * come to front and receive clicks/resizes — so backgroundAndForeground +
 * getFrontClicks (was onlyBackground/dontGetFrontClicks). It still opens NO
 * window until the user picks Mitlesen, so at boot it stays invisibly in back.
 * isHighLevelEventAware + localAndRemoteHLEvents stay set (required: -903 without
 * them, and the inbound kAEQuitApplication quit path needs HL events delivered).
 */
resource 'SIZE' (-1) {
    reserved,
    acceptSuspendResumeEvents,
    reserved,
    canBackground,
    doesActivateOnFGSwitch,
    backgroundAndForeground,
    getFrontClicks,
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
    getFrontClicks,
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
