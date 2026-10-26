/*
 * AppleBridge - Preferences (host IP + chain-launch app list)
 *
 * Stored as a flat, line-based text file "AppleBridge Prefs" in the System
 * Folder's Preferences folder. Human-readable and editable over the bridge:
 *
 *     # AppleBridge preferences
 *     IP=192.168.3.154
 *     DEBUG=0
 *     NET=OT                 (networking service: OT | MacTCP)
 *     APP=MeinMac:Applications:ToolServer
 *
 * Replaces the formerly hardcoded host IP. A missing or unreadable file is not
 * fatal: callers seed defaults first, so the compiled-in fallback IP always
 * keeps the bridge reachable.
 */
#ifndef PREFS_H
#define PREFS_H

#include <Types.h>
#include <Files.h>         /* FSSpec, for LoadPrefsFrom's parameter */
#include <transport.h>     /* kTransportOT / kTransportMacTCP for the `transport` field */

#define PREFS_MAX_APPS  8
#define PREFS_PATH_LEN  256
#define PREFS_IP_LEN    64
#define PREFS_TOKEN_LEN 64
#define PREFS_NAME_LEN  32
#define PREFS_EXTRA_LEN 512   /* lines this build does not understand, kept for SavePrefs (D-028) */

typedef struct {
    char    ip[PREFS_IP_LEN];
    Boolean debug;
    short   transport;      /* kTransportOT (default) / kTransportMacTCP / kTransportSerial — NET= key */
    Boolean serialPortB;    /* PORT= : false = modem port A (default), true = printer port B. Serial only. */
    long    serialBaud;     /* BAUD= : serial line rate, default 9600. Serial only. */
    char    token[PREFS_TOKEN_LEN];  /* TOKEN= : opt-in shared secret for the v0.2
                                      * auth handshake. Empty ⇒ auth disabled (the
                                      * default), so zero-config setups are unchanged.
                                      * See docs/PROTOCOL_v0.2.md. */
    char    home[PREFS_PATH_LEN];  /* HOME= : folder holding the AppleBridge binaries.
                                    * Empty ⇒ fall back to the legacy hardcoded path, so
                                    * pre-installer setups keep working. Set by the
                                    * installer; read by the watchdog + config app to
                                    * locate the daemon/watchdog wherever they live. */
    long    tcpPort;      /* TCPPORT= : the port the daemon dials. 0 ⇒ BRIDGE_PORT (9000), so a
                           * prefs file without the key behaves as before. One port per guest
                           * lets n guests share the hub's one address (D-028). */
    char    name[PREFS_NAME_LEN];  /* NAME= : this guest's name in the hub's guest table, reported
                                    * in the HELLO reply so the hub can refuse a guest that
                                    * landed on another guest's port (D-028). Empty ⇒ none. */
    char    extra[PREFS_EXTRA_LEN]; /* KEY=value lines no case below recognised, each ending in a
                                     * newline, written back verbatim by SavePrefs. Without this a
                                     * program built before a key existed DELETES it on its next
                                     * save - four programs compile this file (D-028). */
    short   winT, winL, winB, winR;  /* WIN= : saved Verbose-window bounds (global,
                                      * content rect). All zero ⇒ unset, so the
                                      * monitor opens at a screen-fitted default. */
    short   appCount;
    char    apps[PREFS_MAX_APPS][PREFS_PATH_LEN];
} AppPrefs;

/* Seed defaults (compiled-in fallback IP, no chain-launch apps). */
void PrefsDefaults(AppPrefs *p);

/* Load prefs from the Preferences folder, overriding the keys present in the
 * file. Returns true if a file was found and read, false otherwise (callers
 * keep the seeded defaults and may write a fresh file with SavePrefs). */
Boolean LoadPrefs(AppPrefs *p);

/* Same, from an arbitrary file — for the prefs a kit ships beside its
 * installer. Only the keys present in that file are touched, so calls LAYER:
 * read the kit first as defaults, then the machine's own file on top, and a
 * configured machine keeps its configuration. `appCount` is reset only once a
 * file has actually been opened, so a missing file changes nothing at all. */
Boolean LoadPrefsFrom(AppPrefs *p, const FSSpec *spec);

/* Write prefs to the Preferences folder, creating the file if needed. */
OSErr SavePrefs(const AppPrefs *p);

#endif /* PREFS_H */
