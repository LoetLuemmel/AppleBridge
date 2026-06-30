/*
 * AppleBridge Installer resources.
 * A normal FOREGROUND app: a window + the Standard File picker. isHighLevelEventAware
 * so it is a good Apple Event citizen (and can be quit cleanly), matching the rest of
 * the suite. It only PROBES the TCP stack via Gestalt — it never opens Open Transport.
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
