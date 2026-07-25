/*
 * AppleBridge suite version resource.
 *
 * Rez this onto each built app (append):  Rez vers.r -a -o :bin:AppleBridge
 * so the Finder shows a real version in Get Info and the "Version" list column —
 * the classic way to tell builds apart (replacing ad-hoc names like
 * "AppleBridge5"). One 'vers' file stamps the whole suite uniformly.
 *
 * This build: 0.8 development — d25 adds NBPLOOK, an AppleTalk name lookup: the daemon
 * asks NBP for the entities the Chooser would list (AFPServer / LaserWriter / Workstation,
 * any zone) and streams them back as object/type/zone/net.node.socket. It closes the last
 * "you must drive the GUI for this" gap in discovery — the Chooser's list is built by a
 * modal tracking loop a faceless daemon cannot reach, so answering "which file servers can
 * this Mac see?" used to mean taking over the host's real mouse. AppleTalk being switched
 * off is reported as such instead of as an empty list. d24 makes the Verbose console NAME the missing host.
 * On a failed dial the daemon now logs an ordered checklist (is host_server.py running /
 * is the target IP on the default-route NIC / is the emulator's NIC alive) instead of the
 * bare "connect timeout" line, with the dialled address read from gPrefs.ip. The full
 * block repeats every 8th attempt (~5 min) so a late observer still learns the cause;
 * intervening retries get one line; recovery is announced. Pairs with the host-side fix
 * that stopped a blocking accept() on :9000 from starving the :9001 control port (PR #75).
 * d23 HARDENS MENU (d22 froze the guest on invalid input /
 * repeated driving). Invalid input (unknown title or item) is now a pure READ-ONLY no-op:
 * it opens no journal driver, arms no journal, and calls no MenuSelect, so a typo can
 * never wedge the guest. Valid drives now install the driver lazily (OpenDriver first,
 * OpenResFile only if needed -- no per-call resource-file re-open), save/restore the
 * GrafPort around MenuSelect, and are guarded by the interrupt watchdog. d22 adds
 * MENU:<title>:<item>, by-name menu driving on
 * the daemon's OWN menu bar: resolves the title to a menu (menuLeft from the live menu
 * list) + the item to an index (numeric or by item text), computes the item point, and
 * journal-drives MenuSelect to select + dispatch it. Generalizes JABOUT. Own-menu only
 * (MenuSelect uses the caller's menu list; cross-process is a dead end per JPROBE). d21
 * adds JPROBE, a freeze-safe feasibility spike for
 * cross-process (front-app) menu driving: it arms the journal to feed a menu-bar
 * mouseDown and records whether the BACKGROUND daemon's own WaitNextEvent grabs that
 * event (-> daemon steals its own journal events, cross-process blocked) or it goes
 * elsewhere. Watchdog + raw-Ticks bounded, no modal call. Investigation only — no
 * behaviour change to shipping verbs. d20 fixes JSF's foreground handoff. d19 called
 * SetFrontProcess(self) then entered SFGetFile immediately, but that switch is
 * ASYNCHRONOUS and ModalDialog never yields, so the daemon never truly became front
 * and the modal spun undismissable at 100% CPU. d20 PUMPS WaitNextEvent until
 * GetFrontProcess confirms we ARE front (bounded ~2 s) BEFORE arming the journal +
 * opening the modal, and BAILS before SFGetFile if the switch never lands (so a
 * failed foreground can't peg the CPU). Reply gains front=. d18 added the Time
 * Manager journaling watchdog (JSAFE) + JSF guarded.
 * d10 shows the active transport in the monitor footer
 * (a labelled "NET OT" / "NET MacTCP" / "NET Serial" field, updated live so a NET=
 * hot-swap relabels it within seconds) via a shared ABTransportName() helper, which
 * also fixes STAT's net= field (it used to report Serial as "OT"). d9 adds transport
 * hot-swap (the daemon re-reads the
 * NET= pref every ~5 s and, on a change, tears down the active OT/MacTCP/Serial stack
 * and brings up the new one live — no relaunch; Control-Panel radio flips take effect
 * within seconds). d8 fixes a double-click crash (0.8d7 stamped modifiers on posted mouse events via PPostEvent, faulting the guest on the 2nd click; reverted to plain PostEvent) + host-side crash black-box (last command before a daemon drop is logged as the prime suspect); d7 rounded out synthetic input (named special keys
 * via mac_key `key=` — return/tab/escape/arrows/delete/home/end/pageup/pagedown/
 * f1..f12; double- and triple-click and shift/command-click via mac_click
 * count=/modifiers= and an extended CLICK verb), on top of d6 (monitor telemetry:
 * the Verbose console's footer + STAT report an error counter with a last-error
 * tag and the last command's RX->TX latency as a number and a colour-coded health
 * bar; mac_status surfaces err_count / last_latency_ms / last_error), on top of d5 (clean power-off: SHUTDOWN + mac_shutdown), d4 (bridge-drivable
 * self-update: SWAPSELF + mac_update_daemon), d3 (key-modifier injection + mac_menu),
 * d2 (lossless key injection), protocol v0.2 (HELLO + opt-in auth), the serial
 * transport backend, and v0.7.0 (selectable MacTCP, installer, LISTDIR). Bump the
 * two version bytes + strings here for the next build.
 */

#include "Types.r"

resource 'vers' (1) {
    0x00, 0x80,          /* 0.8.0 in BCD: major=0, minor=8, bugfix=0 */
    development, 0x25,   /* development stage, non-release revision 25 (BCD) */
    verUS,
    "0.8d25",            /* short version -> Finder "Version" column + Get Info */
    "AppleBridge 0.8d25 - NBPLOOK: AppleTalk name lookup without the Chooser"  /* long -> Get Info */
};

resource 'vers' (2) {
    0x00, 0x80,
    development, 0x25,
    verUS,
    "0.8d25",
    "AppleBridge"        /* the shared/suite version line */
};
