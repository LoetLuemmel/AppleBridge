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
#define DEFAULT_HOST_IP "192.168.3.154"

void PrefsDefaults(AppPrefs *p)
{
    strcpy(p->ip, DEFAULT_HOST_IP);
    p->debug = false;
    p->transport = kTransportOT;   /* Open Transport is the default networking service */
    p->home[0] = '\0';             /* empty ⇒ legacy hardcoded path (pre-installer setups) */
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
        /* Networking service: NET=MacTCP selects the MacTCP backend; anything
         * else (incl. NET=OT) keeps Open Transport. */
        p->transport = (strncmp(line + 4, "MacTCP", 6) == 0)
                           ? kTransportMacTCP : kTransportOT;
    } else if (strncmp(line, "HOME=", 5) == 0) {
        CopyValue(p->home, line + 5, PREFS_PATH_LEN);
    } else if (strncmp(line, "APP=", 4) == 0) {
        if (p->appCount < PREFS_MAX_APPS) {
            CopyValue(p->apps[p->appCount], line + 4, PREFS_PATH_LEN);
            if (p->apps[p->appCount][0]) p->appCount++;
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
    strcat(buf, "NET=");   strcat(buf, p->transport == kTransportMacTCP ? "MacTCP" : "OT");
    strcat(buf, "\r");
    if (p->home[0]) {
        strcat(buf, "HOME="); strcat(buf, p->home); strcat(buf, "\r");
    }
    for (n = 0; n < p->appCount; n++) {
        strcat(buf, "APP="); strcat(buf, p->apps[n]); strcat(buf, "\r");
    }

    count = (long)strlen(buf);
    err = FSWrite(refNum, &count, buf);
    FSClose(refNum);
    return err;
}
