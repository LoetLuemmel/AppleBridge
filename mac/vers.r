/*
 * AppleBridge suite version resource.
 *
 * Rez this onto each built app (append):  Rez vers.r -a -o :bin:AppleBridge
 * so the Finder shows a real version in Get Info and the "Version" list column —
 * the classic way to tell builds apart (replacing ad-hoc names like
 * "AppleBridge5"). One 'vers' file stamps the whole suite uniformly.
 *
 * This build: 0.8 development — d5 adds a clean power-off verb (SHUTDOWN +
 * mac_shutdown: the daemon calls the Shutdown Manager's ShutDwnPower so the guest
 * powers off and the emulator quits without a host-side process kill), on top of
 * d4 (bridge-drivable self-update: SWAPSELF + mac_update_daemon), d3 (key-modifier
 * injection + mac_menu), d2 (lossless key injection), protocol v0.2 (HELLO + opt-in
 * auth), the serial transport backend, and v0.7.0 (selectable MacTCP, installer,
 * LISTDIR). Bump the two version bytes + strings here for the next build.
 */

#include "Types.r"

resource 'vers' (1) {
    0x00, 0x80,          /* 0.8.0 in BCD: major=0, minor=8, bugfix=0 */
    development, 0x05,   /* development stage, non-release revision 5 */
    verUS,
    "0.8d5",             /* short version -> Finder "Version" column + Get Info */
    "AppleBridge 0.8d5 - clean power-off (SHUTDOWN)"        /* long -> Get Info */
};

resource 'vers' (2) {
    0x00, 0x80,
    development, 0x05,
    verUS,
    "0.8d5",
    "AppleBridge"        /* the shared/suite version line */
};
