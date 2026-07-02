/*
 * AppleBridge suite version resource.
 *
 * Rez this onto each built app (append):  Rez vers.r -a -o :bin:AppleBridge
 * so the Finder shows a real version in Get Info and the "Version" list column —
 * the classic way to tell builds apart (replacing ad-hoc names like
 * "AppleBridge5"). One 'vers' file stamps the whole suite uniformly.
 *
 * This build: 0.8 development — d3 adds key-modifier injection (mac_key
 * modifiers + mac_menu Command-key dispatch), on top of d2 (lossless key
 * injection), protocol v0.2 (HELLO + opt-in auth), the serial transport backend,
 * and v0.7.0 (selectable MacTCP, installer, LISTDIR).
 * Bump the two version bytes + strings here for the next build.
 */

#include "Types.r"

resource 'vers' (1) {
    0x00, 0x80,          /* 0.8.0 in BCD: major=0, minor=8, bugfix=0 */
    development, 0x03,   /* development stage, non-release revision 3 */
    verUS,
    "0.8d3",             /* short version -> Finder "Version" column + Get Info */
    "AppleBridge 0.8d3 - key modifiers + mac_menu"          /* long -> Get Info */
};

resource 'vers' (2) {
    0x00, 0x80,
    development, 0x03,
    verUS,
    "0.8d3",
    "AppleBridge"        /* the shared/suite version line */
};
