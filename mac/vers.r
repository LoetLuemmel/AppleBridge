/*
 * AppleBridge suite version resource.
 *
 * Rez this onto each built app (append):  Rez vers.r -a -o :bin:AppleBridge
 * so the Finder shows a real version in Get Info and the "Version" list column —
 * the classic way to tell builds apart (replacing ad-hoc names like
 * "AppleBridge5"). One 'vers' file stamps the whole suite uniformly.
 *
 * This build: 0.8 development — protocol v0.2 (HELLO + opt-in auth) + the serial
 * transport backend, on top of v0.7.0 (selectable MacTCP, installer, LISTDIR).
 * Bump the two version bytes + strings here for the next build.
 */

#include "Types.r"

resource 'vers' (1) {
    0x00, 0x80,          /* 0.8.0 in BCD: major=0, minor=8, bugfix=0 */
    development, 0x01,   /* development stage, non-release revision 1 */
    verUS,
    "0.8d1",             /* short version -> Finder "Version" column + Get Info */
    "AppleBridge 0.8d1 - protocol v0.2, OT/MacTCP/Serial"   /* long -> Get Info */
};

resource 'vers' (2) {
    0x00, 0x80,
    development, 0x01,
    verUS,
    "0.8d1",
    "AppleBridge"        /* the shared/suite version line */
};
