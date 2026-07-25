/*
 * AppleBridge - AFPMOUNT / AFPUNMOUNT: mount an AppleShare volume headlessly
 *
 * The companion to NBPLOOK (nbp.c): having FOUND the file servers without the
 * Chooser, mount one the same way. The Chooser's mount path is a modal dialog
 * with a login sheet — unreachable for a faceless daemon, and reachable from
 * the host only by driving the real mouse. `PBVolumeMount` does the same job as
 * a File Manager call.
 *
 * Wire format:
 *
 *   AFPMOUNT:<zone>:<server>:<volume>:<user>:<password>[:<uam>]
 *      -> STATUS:0 + "<volume name>\t<vRefNum>"   on success
 *      -> STATUS:-1 + the OSErr and what it usually means
 *
 *   AFPUNMOUNT:<volume>
 *      -> STATUS:0 + "unmounted"                   (or the OSErr)
 *
 *   zone      "*" for the local zone (the usual single-zone network).
 *   uam       User Authentication Method: 1 = guest (no password),
 *             2 = cleartext (default). Servers commonly REFUSE cleartext
 *             nowadays; the error is reported rather than papered over.
 *
 * SECRETS: the password reaches the daemon in the clear (the AFP mount record
 * has nowhere else to put it) — so it must not be echoed anywhere. This file
 * never copies it into the response, the Verbose console, or an error string;
 * main.c's dispatcher masks the AFPMOUNT request line before it is displayed
 * or logged. The control port is loopback-only and can be token-guarded, but a
 * password on screen would outlive the call in the console's scrollback.
 */

#include <applebridge.h>
#include <Files.h>
#include <Errors.h>
#include <Memory.h>

#define AFP_MEDIA        'afpm'          /* AppleShareMediaType */
#define AFP_UAM_GUEST    1
#define AFP_UAM_CLEAR    2
#define AFP_NBP_INTERVAL 8
#define AFP_NBP_COUNT    3

static short AF_len(const char *s) { short n = 0; while (s[n]) n++; return n; }
static void  AF_cpy(char *d, const char *s) { while (*s) *d++ = *s++; *d = '\0'; }
static void  AF_cat(char *d, const char *s) { while (*d) d++; AF_cpy(d, s); }
static void  AF_zero(void *p, long n) { char *c = (char *)p; while (n-- > 0) *c++ = 0; }

static void AF_CtoP(const char *c, Str255 p)
{
    short i = 0;
    while (c[i] && i < 255) { p[i + 1] = c[i]; i++; }
    p[0] = (unsigned char)i;
}

static void AF_num(char *buf, short *pos, long n)
{
    char tmp[16];
    short i = 0, j;
    if (n == 0) { buf[(*pos)++] = '0'; return; }
    if (n < 0)  { buf[(*pos)++] = '-'; n = -n; }
    while (n > 0) { tmp[i++] = (char)('0' + (n % 10)); n /= 10; }
    for (j = i - 1; j >= 0; j--) buf[(*pos)++] = tmp[j];
}

/* Append ": <err>" plus, for the errors that actually happen here, what it
 * means. A bare number sends the reader to a table; the common three are worth
 * naming in place. */
static void AF_err(char *dst, OSErr err)
{
    short p = AF_len(dst);
    dst[p++] = ' '; dst[p++] = '(';
    AF_num(dst, &p, (long)err);
    dst[p++] = ')';
    dst[p] = '\0';
    switch (err) {
    case afpUserNotAuth:                    /* -5023 */
        AF_cat(dst, " - server rejected the login: wrong user/password, or it "
                    "refuses cleartext (try uam=1 for guest)");
        break;
    case afpBadUAM:                         /* -5021 */
        AF_cat(dst, " - server does not offer this authentication method");
        break;
    case nsvErr:                            /* -35 */
        AF_cat(dst, " - no such volume/server in that zone");
        break;
    case volOnLinErr:                       /* -55 */
        AF_cat(dst, " - that volume is already mounted");
        break;
    case afpAlreadyMounted:                 /* -5062 — what the AppleShare
                                             * client actually returns for a
                                             * duplicate mount; volOnLinErr is
                                             * the File Manager's version and
                                             * does NOT appear here (verified
                                             * live 2026-07-25) */
        AF_cat(dst, " - that volume is already mounted");
        break;
    case fBsyErr:                           /* -47 */
        AF_cat(dst, " - volume busy: files are still open on it");
        break;
    }
}

/*
 * Split "AFPMOUNT:zone:server:volume:user:password[:uam]".
 *
 * Split by hand rather than with a generic tokenizer because the password is
 * the LAST-but-one field and may legitimately contain anything except a colon;
 * an off-by-one here would silently mount as the wrong user.
 */
static Boolean AF_ParseMount(char *request, long requestLen, char *zone,
                             char *server, char *volume, char *user,
                             char *password, short *uam)
{
    short i = AF_len(PROTO_AFPMOUNT);
    short field = 0, n = 0;
    char *dst[6];
    char uamText[8];

    zone[0] = server[0] = volume[0] = user[0] = password[0] = '\0';
    uamText[0] = '\0';
    *uam = AFP_UAM_CLEAR;
    dst[0] = zone; dst[1] = server; dst[2] = volume;
    dst[3] = user; dst[4] = password; dst[5] = uamText;

    if (i >= requestLen || request[i] != ':') return false;
    i++;
    while (i < requestLen && field < 6) {
        char ch = request[i++];
        if (ch == '\r' || ch == '\n' || ch == '\0') break;
        if (ch == ':') { field++; n = 0; continue; }
        if (n < 62) { dst[field][n++] = ch; dst[field][n] = '\0'; }
    }
    if (uamText[0] >= '0' && uamText[0] <= '9')
        *uam = (short)(uamText[0] - '0');
    if (zone[0] == '\0') AF_cpy(zone, "*");
    return (server[0] != '\0' && volume[0] != '\0');
}

/*
 * Build the AFPVolMountInfo record.
 *
 * The record is one blob: a fixed header of offsets followed by the Pascal
 * strings they point at. Each offset is measured from the START of the record,
 * so the strings must be appended in the same pass that records where they
 * landed — this is the part that silently mounts the wrong thing if it drifts.
 */
static short AF_BuildMountInfo(AFPVolMountInfo *info, const char *zone,
                               const char *server, const char *volume,
                               const char *user, const char *password,
                               short uam)
{
    char  *base = (char *)info;
    short  off = (short)(sizeof(AFPVolMountInfo) - sizeof(info->AFPData));
    Str255 p;
    short  i, len;

    AF_zero(info, sizeof(AFPVolMountInfo));
    info->media = AFP_MEDIA;
    info->flags = 0;                       /* no interaction: a faceless daemon
                                            * must never raise a login dialog */
    info->nbpInterval = AFP_NBP_INTERVAL;
    info->nbpCount = AFP_NBP_COUNT;
    info->uamType = uam;

#define AF_PUT(field, text)                                   \
    do {                                                      \
        AF_CtoP(text, p);                                     \
        len = (short)(p[0] + 1);                              \
        info->field = off;                                    \
        for (i = 0; i < len; i++) base[off + i] = (char)p[i]; \
        off = (short)(off + len);                             \
    } while (0)

    AF_PUT(zoneNameOffset, zone);
    AF_PUT(serverNameOffset, server);
    AF_PUT(volNameOffset, volume);
    AF_PUT(userNameOffset, uam == AFP_UAM_GUEST ? "" : user);
    AF_PUT(userPasswordOffset, uam == AFP_UAM_GUEST ? "" : password);
    AF_PUT(volPasswordOffset, "");
#undef AF_PUT

    info->length = off;
    return off;
}

/*
 * AFPMOUNT verb — mount an AppleShare volume by name.
 */
Boolean AfpMountVerb(ABConn *conn, char *request, long requestLen)
{
    char            zone[64], server[64], volume[64], user[64], password[64];
    short           uam;
    AFPVolMountInfo info;
    ParamBlockRec   pb;
    OSErr           err;
    CommandResult   res;

    res.exitCode = -1;
    res.outData  = NULL;
    res.outLen   = 0;
    res.errData[0] = '\0';

    if (!AF_ParseMount(request, requestLen, zone, server, volume, user,
                       password, &uam)) {
        AF_cpy(res.errData,
               "usage: AFPMOUNT:<zone>:<server>:<volume>:<user>:<password>[:<uam>]");
        SendCommandResult(conn, &res);
        return true;
    }
    if (uam != AFP_UAM_GUEST && uam != AFP_UAM_CLEAR) {
        AF_cpy(res.errData, "uam must be 1 (guest) or 2 (cleartext)");
        SendCommandResult(conn, &res);
        return true;
    }

    AF_BuildMountInfo(&info, zone, server, volume, user, password, uam);

    /* Wipe the caller's copy of the password as soon as it is inside the mount
     * record: nothing below needs it, and a stack copy would linger. */
    AF_zero(password, sizeof(password));

    AF_zero(&pb, sizeof(pb));
    pb.ioParam.ioBuffer = (Ptr)&info;
    err = PBVolumeMount(&pb);

    /* And wipe the record itself — it holds the password verbatim. */
    AF_zero(&info, sizeof(info));

    if (err != noErr) {
        AF_cpy(res.errData, "mount failed");
        AF_err(res.errData, err);
        SendCommandResult(conn, &res);
        return true;
    }

    {
        Handle h = NewHandle(0);
        char   line[128];
        short  p = 0, k, n;
        Str255 nm;
        HParamBlockRec vpb;

        /* Report the volume as the File Manager now sees it: the server may
         * have mounted it under a different name than requested (a duplicate
         * name gets a suffix), and callers need the real one to build paths. */
        nm[0] = 0;
        AF_zero(&vpb, sizeof(vpb));
        vpb.volumeParam.ioNamePtr = nm;
        vpb.volumeParam.ioVRefNum = pb.ioParam.ioVRefNum;
        vpb.volumeParam.ioVolIndex = 0;
        if (PBHGetVInfoSync(&vpb) != noErr) AF_CtoP(volume, nm);

        n = nm[0];
        for (k = 0; k < n; k++) line[p++] = (char)nm[k + 1];
        line[p++] = '\t';
        AF_num(line, &p, (long)pb.ioParam.ioVRefNum);
        line[p++] = '\r';

        if (h != NULL) {
            SetHandleSize(h, p);
            if (MemError() == noErr) {
                HLock(h);
                BlockMoveData(line, *h, p);
                HUnlock(h);
            }
        }
        res.exitCode = 0;
        res.outData  = h;
        res.outLen   = (h != NULL) ? GetHandleSize(h) : 0;
        res.errData[0] = '\0';
        SendCommandResult(conn, &res);
        if (h != NULL) DisposeHandle(h);
    }
    return true;
}

/*
 * AFPUNMOUNT:<volume> — unmount a volume by name.
 *
 * Needed to make mounting testable at all: the interesting server is usually
 * mounted already, so a mount test starts by taking it away.
 */
Boolean AfpUnmountVerb(ABConn *conn, char *request, long requestLen)
{
    char          volume[64];
    short         i = AF_len(PROTO_AFPUNMOUNT), n = 0;
    Str255        pVol;
    ParamBlockRec pb;
    OSErr         err;
    CommandResult res;

    res.exitCode = -1;
    res.outData  = NULL;
    res.outLen   = 0;
    res.errData[0] = '\0';

    if (i < requestLen && request[i] == ':') i++;
    while (i < requestLen && request[i] != '\r' && request[i] != '\n'
           && request[i] != '\0' && n < 62) {
        volume[n++] = request[i++];
    }
    volume[n] = '\0';
    if (n == 0) {
        AF_cpy(res.errData, "usage: AFPUNMOUNT:<volume>");
        SendCommandResult(conn, &res);
        return true;
    }

    /* A trailing colon is how volumes are written everywhere else in this
     * protocol; the File Manager wants the bare name. */
    if (volume[n - 1] == ':') volume[--n] = '\0';
    AF_CtoP(volume, pVol);

    AF_zero(&pb, sizeof(pb));
    pb.ioParam.ioNamePtr = pVol;
    pb.ioParam.ioVRefNum = 0;
    err = PBUnmountVol((ParmBlkPtr)&pb);

    if (err != noErr) {
        AF_cpy(res.errData, "unmount failed");
        AF_err(res.errData, err);
        SendCommandResult(conn, &res);
        return true;
    }

    {
        Handle h = NewHandle(10);
        if (h != NULL) {
            HLock(h);
            BlockMoveData("unmounted\r", *h, 10);
            HUnlock(h);
        }
        res.exitCode = 0;
        res.outData  = h;
        res.outLen   = (h != NULL) ? 10 : 0;
        SendCommandResult(conn, &res);
        if (h != NULL) DisposeHandle(h);
    }
    return true;
}
