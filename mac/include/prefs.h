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
#include <transport.h>     /* kTransportOT / kTransportMacTCP for the `transport` field */

#define PREFS_MAX_APPS  8
#define PREFS_PATH_LEN  256
#define PREFS_IP_LEN    64
#define PREFS_TOKEN_LEN 64

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
    short   appCount;
    char    apps[PREFS_MAX_APPS][PREFS_PATH_LEN];
} AppPrefs;

/* Seed defaults (compiled-in fallback IP, no chain-launch apps). */
void PrefsDefaults(AppPrefs *p);

/* Load prefs from the Preferences folder, overriding the keys present in the
 * file. Returns true if a file was found and read, false otherwise (callers
 * keep the seeded defaults and may write a fresh file with SavePrefs). */
Boolean LoadPrefs(AppPrefs *p);

/* Write prefs to the Preferences folder, creating the file if needed. */
OSErr SavePrefs(const AppPrefs *p);

#endif /* PREFS_H */
