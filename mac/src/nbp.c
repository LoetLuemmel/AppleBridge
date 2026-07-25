/*
 * AppleBridge - NBPLOOK: AppleTalk name lookup (Name Binding Protocol)
 *
 * Answers "which AppleTalk entities can this Mac see?" — file servers,
 * printers, other workstations — WITHOUT driving the Chooser.
 *
 * Why this exists: the Chooser is the only stock way to see that list, and it
 * cannot be reached from a faceless background daemon. Its list is built by a
 * modal tracking loop that polls the hardware mouse, so synthetic clicks do not
 * reach it; opening it host-side (cliclick) needs the emulator frontmost, a
 * live window origin, and a menu gesture fast enough not to starve this daemon
 * (a held-open menu blocks our event loop until the link drops). NBP itself has
 * none of those problems: it is a driver call that returns the same names the
 * Chooser would display.
 *
 * Wire format:
 *
 *   NBPLOOK[:<type>[:<zone>[:<object>]]]
 *      -> STATUS:0 + STDOUT, one entity per line:
 *             <object>\t<type>\t<zone>\t<net>.<node>.<socket>\r
 *
 *   type    NBP entity type; default "AFPServer" (what the Chooser's AppleShare
 *           icon lists). Others: "LaserWriter", "Workstation", "=" for ALL.
 *   zone    AppleTalk zone; default "*" (this Mac's own zone). On a single-zone
 *           network — the usual small setup — "*" is the whole network.
 *   object  entity name; default "=" (every name of that type).
 *
 * Timing: the lookup is a SYNCHRONOUS driver call that always runs its full
 * retry window (NBP cannot know when the last reply has arrived), so it takes
 * interval*count*8/60 s ~= 3 s and blocks the daemon for that long. That is a
 * driver wait, not a spin — the cooperative scheduler keeps running, unlike a
 * busy loop — and it stays well inside the host's default 15 s command budget.
 *
 * AppleTalk must be ACTIVE for any of this: with it off the .MPP driver is not
 * open and the lookup reports that instead of returning an empty list, so an
 * empty result never gets mistaken for "nothing on the network".
 */

#include <applebridge.h>
#include <AppleTalk.h>
#include <Errors.h>
#include <Memory.h>

/* Bounded by what one NBP reply buffer can hold; each entry needs up to ~104
 * bytes (three 32-byte names + a 4-byte address). 64 entities is far beyond any
 * plausible classic-Mac zone and keeps the buffer at ~6.5 KB. */
#define NBP_MAX_ENTITIES  64
#define NBP_ENTRY_BYTES   104
#define NBP_BUF_BYTES     (NBP_MAX_ENTITIES * NBP_ENTRY_BYTES)

/* Retry shape: interval is in units of 8 ticks, so 8 => ~1.07 s between tries.
 * Three tries is the Chooser's own order of magnitude and bounds us at ~3.2 s. */
#define NBP_INTERVAL      8
#define NBP_COUNT         3

static short NB_len(const char *s) { short n = 0; while (s[n]) n++; return n; }
static void  NB_cpy(char *d, const char *s) { while (*s) *d++ = *s++; *d = '\0'; }
static void  NB_zero(void *p, long n) { char *c = (char *)p; while (n-- > 0) *c++ = 0; }

/* C string -> Str32 (NBP names are limited to 32 characters). */
static void NB_CtoP32(const char *c, Str32 p)
{
    short i = 0;
    while (c[i] && i < 32) { p[i + 1] = c[i]; i++; }
    p[0] = (unsigned char)i;
}

/* Append an unsigned decimal to buf at *pos (advances *pos). */
static void NB_unum(char *buf, short *pos, unsigned long n)
{
    char tmp[12];
    short i = 0, j;
    if (n == 0) { buf[(*pos)++] = '0'; return; }
    while (n > 0) { tmp[i++] = (char)('0' + (n % 10)); n /= 10; }
    for (j = i - 1; j >= 0; j--) buf[(*pos)++] = tmp[j];
}

/* Append a Pascal string's characters. */
static void NB_pstr(char *buf, short *pos, const unsigned char *p)
{
    short i, n = p[0];
    for (i = 1; i <= n; i++) buf[(*pos)++] = (char)p[i];
}

/*
 * Split "NBPLOOK[:type[:zone[:object]]]" into its three fields, applying the
 * defaults. Fields are read up to the next colon or end-of-request; an empty
 * field ("NBPLOOK::*") keeps its default, so the host can address a later field
 * without having to restate an earlier one.
 */
static void NB_ParseArgs(char *request, long requestLen,
                         char *type, char *zone, char *object)
{
    short i = NB_len(PROTO_NBPLOOK);   /* "NBPLOOK" — the colon is optional */
    short field = 0, n = 0;
    char *dst[3];

    NB_cpy(type, "AFPServer");
    NB_cpy(zone, "*");
    NB_cpy(object, "=");
    dst[0] = type; dst[1] = zone; dst[2] = object;

    if (i < requestLen && request[i] == ':') i++;   /* skip the leading colon */
    else return;                                    /* bare NBPLOOK: defaults */

    n = 0;
    while (i < requestLen && field < 3) {
        char ch = request[i++];
        if (ch == '\r' || ch == '\n' || ch == '\0') break;
        if (ch == ':') {                            /* field complete */
            if (n > 0) dst[field][n] = '\0';        /* empty => keep default */
            field++; n = 0;
            continue;
        }
        if (n == 0) dst[field][0] = '\0';           /* first char overrides the default */
        if (n < 32) { dst[field][n++] = ch; dst[field][n] = '\0'; }
    }
    if (n > 0 && field < 3) dst[field][n] = '\0';
}

/*
 * NBPLOOK verb — look up AppleTalk entities and stream the matches back.
 *
 * Failure modes are reported as distinct messages rather than an empty list,
 * because "no servers found" and "AppleTalk is switched off" call for very
 * different next steps.
 */
Boolean NbpLookupVerb(ABConn *conn, char *request, long requestLen)
{
    char           type[40], zone[40], object[40];
    Str32          pObj, pType, pZone;
    EntityName     entity;
    MPPParamBlock  pb;
    Ptr            buf;
    OSErr          err;
    short          got, i;
    Handle         h;
    CommandResult  res;

    res.exitCode = -1;
    res.outData  = NULL;
    res.outLen   = 0;
    res.errData[0] = '\0';

    NB_ParseArgs(request, requestLen, type, zone, object);

    /* AppleTalk off => the .MPP driver is closed and every NBP call would fail
     * with a driver error. Say so plainly; an empty list would read as "the
     * network is empty", which is a different (and wrong) conclusion. */
    if (!IsMPPOpen()) {
        NB_cpy(res.errData, "AppleTalk is inactive (see the Chooser / AppleTalk control panel)");
        SendCommandResult(conn, &res);
        return true;
    }

    buf = NewPtr(NBP_BUF_BYTES);
    if (buf == NULL) {
        NB_cpy(res.errData, "out of memory");
        SendCommandResult(conn, &res);
        return true;
    }

    NB_CtoP32(object, pObj);
    NB_CtoP32(type, pType);
    NB_CtoP32(zone, pZone);
    NBPSetEntity((Ptr)&entity, pObj, pType, pZone);

    NB_zero(&pb, sizeof(pb));
    pb.NBPinterval    = NBP_INTERVAL;
    pb.NBPcount       = NBP_COUNT;
    pb.NBPentityPtr   = (Ptr)&entity;
    pb.NBPretBuffPtr  = buf;
    pb.NBPretBuffSize = NBP_BUF_BYTES;
    pb.NBPmaxToGet    = NBP_MAX_ENTITIES;

    err = PLookupName(&pb, false);      /* synchronous: returns after the retries */
    got = pb.NBPnumGotten;

    /* A full buffer is not an error, but it does mean the answer may be
     * truncated — the host should know rather than silently see a short list. */
    if (err != noErr && got == 0) {
        NB_cpy(res.errData, "NBP lookup failed (err ");
        {
            short p = NB_len(res.errData);
            long  e = err;
            if (e < 0) { res.errData[p++] = '-'; e = -e; }
            NB_unum(res.errData, &p, (unsigned long)e);
            res.errData[p++] = ')';
            res.errData[p] = '\0';
        }
        DisposePtr(buf);
        SendCommandResult(conn, &res);
        return true;
    }

    h = NewHandle(0);
    if (h == NULL) {
        DisposePtr(buf);
        NB_cpy(res.errData, "out of memory");
        SendCommandResult(conn, &res);
        return true;
    }

    for (i = 1; i <= got; i++) {
        EntityName found;
        AddrBlock  addr;
        char       line[200];
        short      p = 0;

        if (NBPExtract(buf, got, i, &found, &addr) != noErr) continue;

        NB_pstr(line, &p, (unsigned char *)found.objStr);
        line[p++] = '\t';
        NB_pstr(line, &p, (unsigned char *)found.typeStr);
        line[p++] = '\t';
        NB_pstr(line, &p, (unsigned char *)found.zoneStr);
        line[p++] = '\t';
        NB_unum(line, &p, (unsigned long)addr.aNet);
        line[p++] = '.';
        NB_unum(line, &p, (unsigned long)addr.aNode);
        line[p++] = '.';
        NB_unum(line, &p, (unsigned long)addr.aSocket);
        line[p++] = '\r';

        {
            long oldSize = GetHandleSize(h);
            SetHandleSize(h, oldSize + p);
            if (MemError() == noErr) {
                HLock(h);
                BlockMoveData(line, *h + oldSize, p);
                HUnlock(h);
            }
        }
    }

    DisposePtr(buf);

    res.exitCode = 0;
    res.outData  = h;
    res.outLen   = GetHandleSize(h);
    res.errData[0] = '\0';
    if (got >= NBP_MAX_ENTITIES)
        NB_cpy(res.errData, "result truncated at 64 entities");
    SendCommandResult(conn, &res);
    DisposeHandle(h);
    return true;
}
