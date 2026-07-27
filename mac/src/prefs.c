/*
 * AppleBridge - Preferences read/write (see prefs.h).
 *
 * Flat KEY=value text in the data fork of "AppleBridge Prefs" in the System
 * Folder's Preferences folder. Uses File Manager + Folder Manager (both in
 * Interface.o — no extra library). Lines end with CR (Mac convention).
 */

#include "prefs.h"
#include <mystring.h>
#include <Files.h>
#include <Folders.h>
#include <Errors.h>

#define kPrefCreator    'ABrg'
#define kPrefFileName   "\pAppleBridge Prefs"
#define PREFS_BUF_SIZE  4096
/* No default host address, deliberately (R2 in docs/INSTALLER_REQUIREMENTS.md).
 * A seeded address is not a harmless guess: on a LAN where it happens to answer,
 * the daemon connects to the WRONG machine and reports full health — protocol
 * negotiated, heartbeat running, zero errors on both consoles. That happened on
 * 2026-07-27 and cost two rounds of diagnosis. An unconfigured daemon that says
 * so is strictly better than a configured-looking one that is pointed elsewhere. */
#define DEFAULT_HOST_IP ""

void PrefsDefaults(AppPrefs *p)
{
    strcpy(p->ip, DEFAULT_HOST_IP);
    p->debug = false;
    p->transport = kTransportOT;   /* Open Transport is the default networking service */
    p->serialPortB = false;        /* modem port A by default (Serial backend only) */
    p->serialBaud = 9600;          /* default serial line rate (safe first-contact) */
    p->home[0] = '\0';             /* empty ⇒ legacy hardcoded path (pre-installer setups) */
    p->token[0] = '\0';            /* empty ⇒ auth disabled (opt-in; see docs/PROTOCOL_v0.2.md) */
    p->appCount = 0;
}

/* Resolve the prefs file's FSSpec in the Preferences folder. */
static OSErr PrefsSpec(FSSpec *spec)
{
    OSErr err;
    short vRefNum;
    long  dirID;

    err = FindFolder(kOnSystemDisk, kPreferencesFolderType,
                     kDontCreateFolder, &vRefNum, &dirID);
    if (err != noErr) return err;
    return FSMakeFSSpec(vRefNum, dirID, kPrefFileName, spec);
}

/* Copy src up to a CR/LF/end into dst (<= n-1 chars, NUL-terminated). */
static void CopyValue(char *dst, const char *src, short n)
{
    short i = 0;
    while (src[i] && src[i] != '\r' && src[i] != '\n' && i < n - 1) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

/* Apply one "KEY=value" line to the prefs struct. */
static void ParseLine(AppPrefs *p, const char *line)
{
    if (strncmp(line, "IP=", 3) == 0) {
        CopyValue(p->ip, line + 3, PREFS_IP_LEN);
    } else if (strncmp(line, "DEBUG=", 6) == 0) {
        p->debug = (line[6] == '1');
    } else if (strncmp(line, "NET=", 4) == 0) {
        /* Networking service: NET=MacTCP / NET=Serial select those backends;
         * anything else (incl. NET=OT) keeps Open Transport. */
        if (strncmp(line + 4, "MacTCP", 6) == 0)      p->transport = kTransportMacTCP;
        else if (strncmp(line + 4, "Serial", 6) == 0) p->transport = kTransportSerial;
        else                                          p->transport = kTransportOT;
    } else if (strncmp(line, "PORT=", 5) == 0) {
        /* Serial port select: PORT=B = printer port; anything else = modem (A). */
        p->serialPortB = (line[5] == 'B' || line[5] == 'b');
    } else if (strncmp(line, "BAUD=", 5) == 0) {
        long b = 0; short k = 5;
        while (line[k] >= '0' && line[k] <= '9') b = b * 10 + (line[k++] - '0');
        if (b > 0) p->serialBaud = b;
    } else if (strncmp(line, "HOME=", 5) == 0) {
        CopyValue(p->home, line + 5, PREFS_PATH_LEN);
    } else if (strncmp(line, "TOKEN=", 6) == 0) {
        CopyValue(p->token, line + 6, PREFS_TOKEN_LEN);
    } else if (strncmp(line, "APP=", 4) == 0) {
        if (p->appCount < PREFS_MAX_APPS) {
            CopyValue(p->apps[p->appCount], line + 4, PREFS_PATH_LEN);
            if (p->apps[p->appCount][0]) p->appCount++;
        }
    } else if (strncmp(line, "WIN=", 4) == 0) {
        /* WIN=<top>,<left>,<bottom>,<right> — saved Verbose-window bounds. */
        short vals[4]; short vi = 0, k = 4;
        long  v = 0; short sign = 1; Boolean have = false;
        while (line[k] && vi < 4) {
            char ch = line[k++];
            if (ch == '-')                     sign = -1;
            else if (ch >= '0' && ch <= '9') { v = v * 10 + (ch - '0'); have = true; }
            else if (ch == ',')              { vals[vi++] = (short)(sign * v);
                                               v = 0; sign = 1; have = false; }
        }
        if (have && vi < 4) vals[vi++] = (short)(sign * v);
        if (vi == 4) {
            p->winT = vals[0]; p->winL = vals[1];
            p->winB = vals[2]; p->winR = vals[3];
        }
    }
}

Boolean LoadPrefs(AppPrefs *p)
{
    FSSpec spec;
    short  refNum;
    OSErr  err;
    long   count;
    char   buf[PREFS_BUF_SIZE];
    short  i, lineStart;

    if (PrefsSpec(&spec) != noErr) return false;
    if (FSpOpenDF(&spec, fsRdPerm, &refNum) != noErr) return false;

    count = PREFS_BUF_SIZE - 1;
    err = FSRead(refNum, &count, buf);   /* eofErr at end-of-file is normal */
    FSClose(refNum);
    if (err != noErr && err != eofErr) return false;
    if (count <= 0) return false;
    buf[count] = '\0';

    /* The APP list rebuilds entirely from the file. */
    p->appCount = 0;

    /* Split on CR/LF and parse each non-comment line. */
    lineStart = 0;
    for (i = 0; i <= (short)count; i++) {
        if (i == (short)count || buf[i] == '\r' || buf[i] == '\n') {
            short len = i - lineStart;
            if (len > 0 && len < PREFS_PATH_LEN + 8) {
                char  line[PREFS_PATH_LEN + 8];
                short k;
                for (k = 0; k < len; k++) line[k] = buf[lineStart + k];
                line[len] = '\0';
                if (line[0] != '#') ParseLine(p, line);
            }
            lineStart = i + 1;
        }
    }
    return true;
}

/* Append a signed decimal integer to buf (this file avoids sprintf). */
static void AppendNum(char *buf, long v)
{
    char  t[16], s[18];
    short i = 0, j = 0;
    Boolean neg = (v < 0);
    if (neg) v = -v;
    do { t[i++] = (char)('0' + (short)(v % 10)); v /= 10; } while (v > 0);
    if (neg) s[j++] = '-';
    while (i > 0) s[j++] = t[--i];
    s[j] = '\0';
    strcat(buf, s);
}

OSErr SavePrefs(const AppPrefs *p)
{
    FSSpec spec;
    short  refNum;
    OSErr  err;
    long   count;
    char   buf[PREFS_BUF_SIZE];
    short  n;

    err = PrefsSpec(&spec);
    if (err != noErr && err != fnfErr) return err;

    /* Create if absent; an existing file is fine (dupFNErr).
     * scriptTag 0 == smRoman (the filename is ASCII); avoids a <Script.h> dep. */
    err = FSpCreate(&spec, kPrefCreator, 'TEXT', 0);
    if (err != noErr && err != dupFNErr) return err;

    err = FSpOpenDF(&spec, fsRdWrPerm, &refNum);
    if (err != noErr) return err;
    SetEOF(refNum, 0L);   /* rewrite from scratch */

    buf[0] = '\0';
    strcat(buf, "# AppleBridge preferences\r");
    strcat(buf, "IP=");    strcat(buf, p->ip);                strcat(buf, "\r");
    strcat(buf, "DEBUG="); strcat(buf, p->debug ? "1" : "0"); strcat(buf, "\r");
    strcat(buf, "NET=");
    if (p->transport == kTransportMacTCP)      strcat(buf, "MacTCP");
    else if (p->transport == kTransportSerial) strcat(buf, "Serial");
    else                                       strcat(buf, "OT");
    strcat(buf, "\r");
    if (p->transport == kTransportSerial) {
        char bs[16], t[16];
        long v = (p->serialBaud > 0) ? p->serialBaud : 9600;
        short i = 0, j;
        strcat(buf, "PORT="); strcat(buf, p->serialPortB ? "B" : "A"); strcat(buf, "\r");
        while (v > 0) { t[i++] = (char)('0' + (v % 10)); v /= 10; }
        for (j = 0; j < i; j++) bs[j] = t[i - 1 - j];
        bs[i] = '\0';
        strcat(buf, "BAUD="); strcat(buf, bs); strcat(buf, "\r");
    }
    if (p->home[0]) {
        strcat(buf, "HOME="); strcat(buf, p->home); strcat(buf, "\r");
    }
    if (p->token[0]) {
        strcat(buf, "TOKEN="); strcat(buf, p->token); strcat(buf, "\r");
    }
    for (n = 0; n < p->appCount; n++) {
        strcat(buf, "APP="); strcat(buf, p->apps[n]); strcat(buf, "\r");
    }
    if (p->winB > p->winT && p->winR > p->winL) {
        strcat(buf, "WIN=");
        AppendNum(buf, p->winT); strcat(buf, ",");
        AppendNum(buf, p->winL); strcat(buf, ",");
        AppendNum(buf, p->winB); strcat(buf, ",");
        AppendNum(buf, p->winR); strcat(buf, "\r");
    }

    count = (long)strlen(buf);
    err = FSWrite(refNum, &count, buf);
    FSClose(refNum);
    return err;
}
