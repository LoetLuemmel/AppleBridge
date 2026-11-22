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
#include <Memory.h>      /* BlockMoveData */
#include <Errors.h>

#define kPrefCreator    'ABrg'
#define kPrefFileName   "\pAppleBridge Prefs"
/* The daemon's own settings - the Verbose window's position (WIN=) and the
 * console's night mode (CONSOLE=) - live in the shared prefs file again (0.8d52).
 * From 0.8d49 to 0.8d51 they were in a file of their own ("AppleBridge Window"),
 * because the daemon used to rewrite the WHOLE shared file from its in-memory
 * copy on every window move, and an edit made underneath it, or a key an older
 * build did not know, was gone (R14; on a PowerBook Duo 230 TCPPORT= and NAME=
 * were lost twice, 2026-11-21). The daemon now writes the shared file only through
 * UpdateOwnKeys: re-read right before the write, every line that is not WIN= or
 * CONSOLE= kept byte for byte, those two replaced. It never creates the file. The
 * old separate file is read once, merged, and deleted. */
#define kWinFileName    "\pAppleBridge Window"
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
    p->tcpPort = 0;                /* 0 ⇒ BRIDGE_PORT (D-028) */
    p->name[0] = '\0';
    p->console = 0;                /* classic: white text area */
    p->font[0] = '\0';             /* Monaco 9 */
    p->fontSize = 0;
    p->extra[0] = '\0';
    p->appCount = 0;
}

/* Resolve a file's FSSpec in the Preferences folder. */
static OSErr PrefsFolderSpec(ConstStr255Param name, FSSpec *spec)
{
    OSErr err;
    short vRefNum;
    long  dirID;

    err = FindFolder(kOnSystemDisk, kPreferencesFolderType,
                     kDontCreateFolder, &vRefNum, &dirID);
    if (err != noErr) return err;
    return FSMakeFSSpec(vRefNum, dirID, name, spec);
}

static OSErr PrefsSpec(FSSpec *spec)
{
    return PrefsFolderSpec(kPrefFileName, spec);
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

/* True if the line has a '=' (a KEY=value line); mystring.h has no strchr. */
static Boolean HasEquals(const char *line)
{
    while (*line) { if (*line == '=') return true; line++; }
    return false;
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
    } else if (strncmp(line, "TCPPORT=", 8) == 0) {
        long v = 0; short k = 8;
        while (line[k] >= '0' && line[k] <= '9') v = v * 10 + (line[k++] - '0');
        p->tcpPort = (v > 0 && v < 65536L) ? v : 0;   /* out of range ⇒ the default, never a wrap */
    } else if (strncmp(line, "NAME=", 5) == 0) {
        CopyValue(p->name, line + 5, PREFS_NAME_LEN);
    } else if (strncmp(line, "CONSOLE=", 8) == 0) {
        /* CONSOLE=dark selects the dark text area; anything else is classic. */
        p->console = (strncmp(line + 8, "dark", 4) == 0 || strncmp(line + 8, "Dark", 4) == 0) ? 1 : 0;
    } else if (strncmp(line, "FONT=", 5) == 0) {
        /* FONT=<name> <size>: the name is everything up to the LAST space (font
         * names have spaces: "New York"), the size the digits after it. */
        char  v[PREFS_NAME_LEN];
        short k, sp = -1, sz = 0;
        CopyValue(v, line + 5, PREFS_NAME_LEN);
        for (k = 0; v[k]; k++) if (v[k] == ' ') sp = k;
        if (sp > 0) {
            for (k = sp + 1; v[k] >= '0' && v[k] <= '9'; k++) sz = sz * 10 + (v[k] - '0');
            v[sp] = '\0';
        }
        strcpy(p->font, v);
        p->fontSize = (sz >= 6 && sz <= 24) ? sz : 0;
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
    } else if (HasEquals(line)) {
        /* A key this build does not know: keep it, so SavePrefs writes it back. A file
         * written by a newer program then survives an older one (D-028). Too long to fit
         * is dropped whole rather than cut, since half a line would be a different value. */
        long have = (long)strlen(p->extra), need = (long)strlen(line) + 1;
        if (have + need < PREFS_EXTRA_LEN) {
            strcat(p->extra, line);
            strcat(p->extra, "\r");
        }
    }
}

/* Parse a prefs file at an ARBITRARY location onto `p`.
 *
 * Split out from LoadPrefs so the installer can read the prefs file shipped
 * beside it in a kit. That file existed and was generated with care, and
 * nothing ever read it: LoadPrefs resolves its path through
 * FindFolder(kPreferencesFolderType) and looks nowhere else, so a kit's `IP=`
 * reached a freshly-installed machine only by coincidence — the installer's
 * COMPILED-IN default happened to be the same developer's address. On anyone
 * else's machine that seeds a foreign address and the daemon then dials the
 * wrong computer while reporting full health, which is R2 exactly. Measured
 * 2026-07-28: `strings` on the shipped installer showed `192.168.3.154` while
 * the kit's own prefs said something else, and the installed file matched the
 * binary rather than the kit.
 *
 * Values are LAYERED, not overwritten wholesale — see the call site in
 * installer.c for why the order matters.
 */
Boolean LoadPrefsFrom(AppPrefs *p, const FSSpec *spec)
{
    short  refNum;
    OSErr  err;
    long   count;
    char   buf[PREFS_BUF_SIZE];
    short  i, lineStart;

    if (FSpOpenDF(spec, fsRdPerm, &refNum) != noErr) return false;

    count = PREFS_BUF_SIZE - 1;
    err = FSRead(refNum, &count, buf);   /* eofErr at end-of-file is normal */
    FSClose(refNum);
    if (err != noErr && err != eofErr) return false;
    if (count <= 0) return false;
    buf[count] = '\0';

    /* The APP list and the unknown lines rebuild entirely from the file. */
    p->appCount = 0;
    p->extra[0] = '\0';

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

/* The machine's own preferences, in the Preferences folder. */
Boolean LoadPrefs(AppPrefs *p)
{
    FSSpec spec;

    if (PrefsSpec(&spec) != noErr) return false;
    return LoadPrefsFrom(p, &spec);
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
    if (p->tcpPort > 0) {
        strcat(buf, "TCPPORT="); AppendNum(buf, p->tcpPort); strcat(buf, "\r");
    }
    if (p->name[0]) {
        strcat(buf, "NAME="); strcat(buf, p->name); strcat(buf, "\r");
    }
    if (p->console == 1) {
        strcat(buf, "CONSOLE=dark\r");
    }
    if (p->font[0]) {
        strcat(buf, "FONT="); strcat(buf, p->font);
        if (p->fontSize > 0) { strcat(buf, " "); AppendNum(buf, p->fontSize); }
        strcat(buf, "\r");
    }
    for (n = 0; n < p->appCount; n++) {
        strcat(buf, "APP="); strcat(buf, p->apps[n]); strcat(buf, "\r");
    }
    if (p->winB > p->winT && p->winR > p->winL) {      /* the daemon's window, when known */
        strcat(buf, "WIN=");
        AppendNum(buf, p->winT); strcat(buf, ",");
        AppendNum(buf, p->winL); strcat(buf, ",");
        AppendNum(buf, p->winB); strcat(buf, ",");
        AppendNum(buf, p->winR); strcat(buf, "\r");
    }
    strcat(buf, p->extra);   /* keys this build does not know, verbatim (D-028) */

    count = (long)strlen(buf);
    err = FSWrite(refNum, &count, buf);
    FSClose(refNum);
    return err;
}

/* The window position, in "AppleBridge Window". Only the four win fields of `p`
 * are touched, so a missing or unreadable file leaves whatever LoadPrefs found. */
/* Legacy: the separate "AppleBridge Window" file of 0.8d49-0.8d51. Its WIN= and
 * CONSOLE= are newer than what the shared file has, so they are adopted; the
 * daemon then merges them into the shared file (SaveWinPrefs) and deletes this
 * one (DropWinFile). */
Boolean LoadWinPrefs(AppPrefs *p)
{
    FSSpec spec;
    short  refNum;
    long   count;
    char   buf[64];
    OSErr  err;

    if (PrefsFolderSpec(kWinFileName, &spec) != noErr) return false;
    if (FSpOpenDF(&spec, fsRdPerm, &refNum) != noErr) return false;
    count = sizeof(buf) - 1;
    err = FSRead(refNum, &count, buf);
    FSClose(refNum);
    if ((err != noErr && err != eofErr) || count <= 4) return false;
    buf[count] = '\0';
    {
        char *line = buf;
        Boolean any = false;
        while (*line) {
            char *end = line;
            while (*end && *end != '\r' && *end != '\n') end++;
            if (strncmp(line, "WIN=", 4) == 0 || strncmp(line, "CONSOLE=", 8) == 0) {
                char save = *end;
                *end = '\0';
                ParseLine(p, line);
                *end = save;
                any = true;
            }
            line = *end ? end + 1 : end;
        }
        return any;
    }
}

void DropWinFile(void)
{
    FSSpec spec;
    if (PrefsFolderSpec(kWinFileName, &spec) == noErr) (void)FSpDelete(&spec);
}

/* Write the daemon's own keys (WIN=, CONSOLE=) into the SHARED prefs file:
 * read it right before the write, keep every other line byte for byte, drop the
 * old WIN=/CONSOLE= lines, append the new ones. Never creates the file - a daemon
 * that wrote a default file once produced the half-empty prefs of 0.8d48. */
OSErr SaveWinPrefs(const AppPrefs *p)
{
    static char in[PREFS_BUF_SIZE], out[PREFS_BUF_SIZE];
    FSSpec spec;
    short  refNum;
    long   count, n = 0, i = 0;
    OSErr  err;

    err = PrefsSpec(&spec);
    if (err != noErr) return err;
    err = FSpOpenDF(&spec, fsRdWrPerm, &refNum);
    if (err != noErr) return err;                     /* missing: leave it missing */
    count = sizeof(in) - 1;
    err = FSRead(refNum, &count, in);
    if (err != noErr && err != eofErr) { FSClose(refNum); return err; }
    in[count] = '\0';

    while (i < count) {                               /* keep every foreign line verbatim */
        long s = i;
        while (i < count && in[i] != '\r' && in[i] != '\n') i++;
        if (i < count) i++;                           /* include its line end */
        if (strncmp(in + s, "WIN=", 4) == 0 || strncmp(in + s, "CONSOLE=", 8) == 0) continue;
        if (n + (i - s) >= (long)sizeof(out) - 64) break;
        BlockMoveData(in + s, out + n, i - s);
        n += i - s;
    }
    if (n > 0 && out[n - 1] != '\r' && out[n - 1] != '\n') out[n++] = '\r';
    out[n] = '\0';
    if (p->winB > p->winT && p->winR > p->winL) {
        strcat(out, "WIN=");
        AppendNum(out, p->winT); strcat(out, ",");
        AppendNum(out, p->winL); strcat(out, ",");
        AppendNum(out, p->winB); strcat(out, ",");
        AppendNum(out, p->winR); strcat(out, "\r");
    }
    if (p->console == 1) strcat(out, "CONSOLE=dark\r");
    else                 strcat(out, "CONSOLE=classic\r");

    count = (long)strlen(out);
    SetFPos(refNum, fsFromStart, 0L);
    err = FSWrite(refNum, &count, out);
    if (err == noErr) err = SetEOF(refNum, count);
    FSClose(refNum);
    return err;
}
