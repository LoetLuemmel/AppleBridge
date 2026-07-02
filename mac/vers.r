/*
 * AppleBridge suite version resource.
 *
 * Rez this onto each built app (append):  Rez vers.r -a -o :bin:AppleBridge
 * so the Finder shows a real version in Get Info and the "Version" list column —
 * the classic way to tell builds apart (replacing ad-hoc names like
 * "AppleBridge5"). One 'vers' file stamps the whole suite uniformly.
 *
 * This build: 0.8 development — d4 adds bridge-drivable daemon self-update (the
 * SWAPSELF verb + mac_update_daemon: the daemon renames a staged binary over its
 * own running file), on top of d3 (key-modifier injection + mac_menu), d2
 * (lossless key injection), protocol v0.2 (HELLO + opt-in auth), the serial
 * transport backend, and v0.7.0 (selectable MacTCP, installer, LISTDIR).
 * Bump the two version bytes + strings here for the next build.
 */

#include "Types.r"

resource 'vers' (1) {
    0x00, 0x80,          /* 0.8.0 in BCD: major=0, minor=8, bugfix=0 */
    development, 0x04,   /* development stage, non-release revision 4 */
    verUS,
    "0.8d4",             /* short version -> Finder "Version" column + Get Info */
    "AppleBridge 0.8d4 - self-update (SWAPSELF)"            /* long -> Get Info */
};

resource 'vers' (2) {
    0x00, 0x80,
    development, 0x04,
    verUS,
    "0.8d4",
    "AppleBridge"        /* the shared/suite version line */
};
