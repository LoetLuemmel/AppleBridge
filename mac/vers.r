/*
 * AppleBridge suite version resource.
 *
 * Rez this onto each built app (append):  Rez vers.r -a -o :bin:AppleBridge
 * so the Finder shows a real version in Get Info and the "Version" list column —
 * the classic way to tell builds apart (replacing ad-hoc names like
 * "AppleBridge5"). One 'vers' file stamps the whole suite uniformly.
 *
 * This build: 0.8 development — d6 adds monitor telemetry (the Verbose console's
 * footer strip + STAT report an error counter with a last-error tag and the last
 * real command's RX->TX latency — shown as a number and a colour-coded analog
 * health bar — alongside the RX/TX totals; mac_status surfaces err_count /
 * last_latency_ms / last_error), on top of d5 (clean power-off: SHUTDOWN + mac_shutdown), d4 (bridge-drivable
 * self-update: SWAPSELF + mac_update_daemon), d3 (key-modifier injection + mac_menu),
 * d2 (lossless key injection), protocol v0.2 (HELLO + opt-in auth), the serial
 * transport backend, and v0.7.0 (selectable MacTCP, installer, LISTDIR). Bump the
 * two version bytes + strings here for the next build.
 */

#include "Types.r"

resource 'vers' (1) {
    0x00, 0x80,          /* 0.8.0 in BCD: major=0, minor=8, bugfix=0 */
    development, 0x06,   /* development stage, non-release revision 6 */
    verUS,
    "0.8d6",             /* short version -> Finder "Version" column + Get Info */
    "AppleBridge 0.8d6 - monitor telemetry (err + latency)"        /* long -> Get Info */
};

resource 'vers' (2) {
    0x00, 0x80,
    development, 0x06,
    verUS,
    "0.8d6",
    "AppleBridge"        /* the shared/suite version line */
};
