/*
 * AppleBridge suite version resource.
 *
 * Rez this onto each built app (append):  Rez vers.r -a -o :bin:AppleBridge
 * so the Finder shows a real version in Get Info and the "Version" list column —
 * the classic way to tell builds apart (replacing ad-hoc names like
 * "AppleBridge5"). One 'vers' file stamps the whole suite uniformly.
 *
 * This build: 0.8 development — d33 makes the journaling self-test tell the truth. JGATE
 * reported `armed=0 calls=0 FAIL` for a working driver: it was written against the
 * pre-PR-#69 contract, where dCtlStorage held the driver's call counter. That field is now
 * a POINTER to a daemon-owned state block, and the driver treats a nil pointer as "not
 * prepared, do nothing" — the guard that stops an unprepared driver freezing a tracking
 * loop. So the verb armed a driver it had itself switched off, then read an address as a
 * count. A self-test that fails on a working system sends the next person after the driver
 * instead of after the test. It now does what the other six journal verbs do — allocate the
 * block, point dCtlStorage at it, read oPoll/oCntBtn back — and PASS additionally requires
 * btn > 0, so the driver must have been CALLED rather than merely have left a plausible
 * Button() reading. d32 makes a failure legible. Three verbs shared the tag
 * "cmd fail", so the monitor footer's ERR count named nothing: reading "ERR 2" off the
 * screen told you two commands had failed and gave you no way to learn which, or why,
 * short of reproducing them. NoteErrCode now records the verb and the OSErr — "AESEND
 * -1712" — and the host logs a non-zero STATUS with the daemon's own error text, which it
 * used to discard entirely (the request was logged, the outcome was not, so a failure and
 * a success left the same trace). d31 bounds how long AESEND may block. The verb inherited
 * AE_SCRIPT_TIMEOUT (~5 min) from 'dosc', a figure reasoned about for ToolServer — an
 * application we own and that always answers. AESEND addresses ANY application, and on a
 * cooperative scheduler the one that is not yielding holds the whole guest: a KAHL/RUN on
 * a project that could not link took the emulator down with the disk image open
 * (2026-07-27, R16). The request now carries an optional wait in ticks — 0 sends
 * kAENoReply and cannot block at all, which is the honest choice for the many events whose
 * 'aete' declares reply 'null' — and an omitted field means the interactive default (30 s)
 * rather than five minutes, clamped at 180 s so the daemon always gives up before the host
 * stops listening. 'dosc' keeps its five minutes; long Link/SC builds still need them.
 * d27 adds two small verbs that both came out of using
 * the bridge: DISKINFO[:<vol>] reports size/free per volume via PBHGetVInfo (the sibling
 * of LISTDIR — no ToolServer, so it also answers where none is installed, and it is the
 * question that follows every AFPMOUNT), and MONITOR:0|1 hides/shows the Verbose console
 * over the bridge. The console covers the desktop, which is in the way while the guest's
 * GUI is driven; hiding uses HideWindow, NOT the close box's DisposeWindow, so the log
 * ring and scroll position survive and showing it resumes the same session.
 * d26 adds AFPMOUNT/AFPUNMOUNT, the companion to d25's
 * lookup: having FOUND a file server without the Chooser, mount it without the Chooser
 * too (PBVolumeMount with a hand-built AFPVolMountInfo). Guest or cleartext UAM, no
 * interaction bit (a faceless daemon must never raise a login dialog), and the server's
 * refusal codes are named rather than passed through as bare numbers. SECURITY: the
 * request carries a password in the clear, so the verb's request line is MASKED before
 * it reaches the Verbose console — which keeps a scrollback — and the activity field
 * shows only the verb. d25 adds NBPLOOK, an AppleTalk name lookup: the daemon
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
    development, 0x29,   /* development stage, non-release revision 38 (BCD) */
    verUS,
    "0.8d38",            /* short version -> Finder "Version" column + Get Info */
    "AppleBridge 0.8d38 - QUIT reports whether the app went away, not whether the event was sent"  /* long -> Get Info */
};

resource 'vers' (2) {
    0x00, 0x80,
    development, 0x29,
    verUS,
    "0.8d38",
    "AppleBridge"        /* the shared/suite version line */
};
