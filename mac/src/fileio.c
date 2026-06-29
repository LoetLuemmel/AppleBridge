/*
 * AppleBridge - fork-aware binary file transfer (WRITEFILE / READFILE verbs)
 *
 * These two raw verbs move a classic-Mac file's DATA and RESOURCE forks (plus
 * its type/creator) over the bridge, STREAMING to/from disk in fixed-size
 * chunks so an arbitrarily large file costs only one chunk of daemon RAM (no
 * 4 MB cap, unlike the screenshot/command paths).
 *
 * Wire format (host <-> daemon, length-framed, binary-clean on :9000):
 *
 *   WRITEFILE:<pathLen>:<typeHex8>:<creatorHex8>:<dataLen>:<rsrcLen>\n
 *             <pathBytes><dataBytes><rsrcBytes>
 *      -> reply: STATUS:0 ...   (or STATUS:-1 ... on a File Manager error)
 *
 *   READFILE:<macPath>
 *      -> FILE:<typeHex8>:<creatorHex8>:<dataLen>:<rsrcLen>\n
 *             <dataBytes><rsrcBytes>            (or a STATUS:-1 ... error frame)
 *
 * The host carries these as base64 across the text-only control port; MacBinary
 * packaging lives entirely host-side (host/macbinary.py). The daemon only ever
 * sees explicit fields + raw fork bytes, so it stays simple.
 *
 * Cooperative-scheduler discipline (this runs inside ProcessRequest and blocks
 * main()'s loop): every idle pass calls SystemTask() to yield, a stall deadline
 * bounds a half-sent transfer so it can't freeze the Mac, and any mid-stream
 * recv/send failure returns false so the desynced link is dropped + reconnected.
 */

#include <applebridge.h>
#include <Files.h>
#include <Resources.h>  /* FSpCreateResFile */
#include <Errors.h>
#include <Events.h>     /* TickCount, SystemTask */
#include <Memory.h>     /* NewPtr */

#define FILE_CHUNK      8192L            /* per-transfer buffer */
#define FILE_STALL_TICKS 1800L           /* ~30 s with no progress -> give up */

/* One 8 KB transfer buffer, lazily allocated on the HEAP. It must NOT be a
 * static array: that lands in the A5 near-data world, whose 16-bit-addressable
 * span the daemon already fills (the ~19 KB log buffers), so an 8 KB array there
 * overflows it and the link fails with "Error 114: 16-bit reference offset out
 * of range". A heap Ptr costs only 4 bytes of A5. Single-connection daemon, so
 * one shared buffer is fine; never freed (negligible) to keep every path simple. */
static Ptr gChunk = NULL;
static long gDbgRsrcPrePos = 0;   /* INSTRUMENTATION: rsrc offset within pushback */

/* Allocate the shared chunk buffer on first use; NULL on out-of-memory. */
static Ptr EnsureChunk(void)
{
    if (gChunk == NULL) gChunk = NewPtr(FILE_CHUNK);
    return gChunk;
}

/* ---- a pushback byte reader: drains bytes already received in `request`,
 *      then pulls more from the endpoint (one ReceiveData per call) ---------- */
typedef struct {
    EndpointRef ep;
    char       *pre;       /* bytes already in the request buffer */
    long        preLen;
    long        prePos;
} StreamCtx;

/* One read step. Returns noErr with *got>0, kOTNoDataErr if the endpoint had
 * nothing yet, or a negative OTResult on a real error. */
static OSStatus StreamRead(StreamCtx *ctx, char *dst, long want, long *got)
{
    *got = 0;
    if (want <= 0) return noErr;
    if (ctx->prePos < ctx->preLen) {
        long avail = ctx->preLen - ctx->prePos;
        long n = (want < avail) ? want : avail;
        BlockMoveData(ctx->pre + ctx->prePos, dst, n);
        ctx->prePos += n;
        *got = n;
        return noErr;
    }
    return ReceiveData(ctx->ep, dst, want, got);
}

/* Read EXACTLY n bytes into dst, yielding on idle, bounded by the stall clock.
 * Returns noErr, or a negative error (incl. a synthetic -1 on stall). */
static OSStatus StreamReadFull(StreamCtx *ctx, char *dst, long n)
{
    long done = 0;
    unsigned long lastProgress = TickCount();
    while (done < n) {
        long got = 0;
        OSStatus err = StreamRead(ctx, dst + done, n - done, &got);
        if (err == noErr && got > 0) {
            done += got;
            lastProgress = TickCount();
        } else if (err == kOTNoDataErr) {
            if (TickCount() - lastProgress > FILE_STALL_TICKS) return -1;
            SystemTask();
        } else {
            return err;
        }
    }
    return noErr;
}

/* Stream `total` bytes from the wire straight into an open fork (refNum). */
static OSStatus StreamToFork(StreamCtx *ctx, short refNum, long total)
{
    long done = 0;
    unsigned long lastProgress = TickCount();
    while (done < total) {
        long want = total - done;
        long got = 0;
        OSStatus err;
        if (want > FILE_CHUNK) want = FILE_CHUNK;
        err = StreamRead(ctx, gChunk, want, &got);
        if (err == noErr && got > 0) {
            long n = got;
            OSErr werr = FSWrite(refNum, &n, gChunk);
            if (werr != noErr) return werr;     /* disk full / write error */
            done += got;
            lastProgress = TickCount();
        } else if (err == kOTNoDataErr) {
            if (TickCount() - lastProgress > FILE_STALL_TICKS) return -1;
            SystemTask();
        } else {
            return err;                          /* recv error -> desync */
        }
    }
    return noErr;
}

/* ---- small parse/format helpers ----------------------------------------- */

/* Parse decimal digits at *p, advancing *p past them. */
static long ParseDecimal(char **p)
{
    long v = 0;
    char *s = *p;
    while (*s >= '0' && *s <= '9') { v = v * 10 + (*s - '0'); s++; }
    *p = s;
    return v;
}

static short HexDigit(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Parse exactly 8 hex chars at *p into an OSType, advancing *p. */
static OSType ParseHex8(char **p)
{
    OSType v = 0;
    short i, d;
    char *s = *p;
    for (i = 0; i < 8; i++) {
        d = HexDigit(s[i]);
        if (d < 0) break;
        v = (v << 4) | (unsigned long)d;
    }
    *p = s + i;
    return v;
}

/* Append unsigned decimal; returns pointer past the written digits. */
static char *AppendULong(char *p, unsigned long v)
{
    char tmp[12];
    short n = 0;
    if (v == 0) { *p++ = '0'; return p; }
    while (v > 0 && n < 12) { tmp[n++] = (char)('0' + (v % 10)); v /= 10; }
    while (n > 0) *p++ = tmp[--n];
    return p;
}

/* Append an 8-char hex OSType. */
static char *AppendHex8(char *p, OSType v)
{
    static const char hx[] = "0123456789abcdef";
    short i;
    for (i = 7; i >= 0; i--) p[i] = hx[v & 0xF], v >>= 4;
    return p + 8;
}

/* Build an FSSpec from a C colon-path (same idiom as LaunchAppAtPath). */
static OSErr MakeFSSpecFromPath(const char *macPath, FSSpec *spec)
{
    Str255 pPath;
    short i;
    for (i = 0; macPath[i] && i < 255; i++) pPath[i + 1] = macPath[i];
    pPath[0] = (unsigned char)i;
    return FSMakeFSSpec(0, 0, pPath, spec);
}

/* Send a fixed STATUS reply (small) and return `healthy`. */
static Boolean ReplyStatus(EndpointRef endpoint, const char *frame, Boolean healthy)
{
    long n = 0;
    while (frame[n]) n++;
    SendData(endpoint, frame, n);
    return healthy;
}

/* Append a C string; returns the pointer past it. */
static char *AppendStr(char *p, const char *s)
{
    while (*s) *p++ = *s++;
    return p;
}

/* Send STATUS:0 with an arbitrary (small) STDOUT string. */
static Boolean SendStatusOut(EndpointRef endpoint, const char *out)
{
    char frame[256];
    char *p = frame;
    long olen = 0;
    const char *q;
    for (q = out; *q; q++) olen++;
    p = AppendStr(p, "STATUS:0\rSTDOUT:");
    p = AppendULong(p, (unsigned long)olen);
    *p++ = '\r';
    p = AppendStr(p, out);
    p = AppendStr(p, "\rSTDERR:0\r\r");
    SendData(endpoint, frame, p - frame);
    return true;
}

/* ---- WRITEFILE ----------------------------------------------------------- */

Boolean WriteFileVerb(EndpointRef endpoint, char *request, long requestLen)
{
    StreamCtx ctx;
    char     *h;
    char     *nl;
    long      pathLen, dataLen, rsrcLen;
    OSType    fType, fCreator;
    char      macPath[512];
    FSSpec    spec;
    OSErr     ferr;
    OSStatus  serr;
    short     dataRef = 0, rsrcRef = 0;

    if (EnsureChunk() == NULL)
        return ReplyStatus(endpoint,
            "STATUS:-1\rSTDOUT:0\rSTDERR:13\rout of memory\r\r", true);

    /* The header line must be present in the first segment (it is tiny and is
     * always sent ahead of the body). Find its terminating newline. */
    nl = NULL;
    { long i; for (i = 10; i < requestLen; i++)
        if (request[i] == '\n' || request[i] == '\r') { nl = request + i; break; } }
    if (nl == NULL)
        return ReplyStatus(endpoint,
            "STATUS:-1\rSTDOUT:0\rSTDERR:18\rWRITEFILE bad header\r\r", true);

    /* Parse: WRITEFILE:<pathLen>:<typeHex8>:<creatorHex8>:<dataLen>:<rsrcLen>\n */
    h = request + 10;
    pathLen  = ParseDecimal(&h); if (*h != ':') goto badhdr; h++;
    fType    = ParseHex8(&h);    if (*h != ':') goto badhdr; h++;
    fCreator = ParseHex8(&h);    if (*h != ':') goto badhdr; h++;
    dataLen  = ParseDecimal(&h); if (*h != ':') goto badhdr; h++;
    rsrcLen  = ParseDecimal(&h);
    if (h != nl) goto badhdr;

    if (pathLen <= 0 || pathLen > 511 ||
        dataLen < 0 || dataLen > MAX_FILE_BYTES ||
        rsrcLen < 0 || rsrcLen > MAX_FILE_BYTES)
        return ReplyStatus(endpoint,
            "STATUS:-1\rSTDOUT:0\rSTDERR:17\rWRITEFILE bad size\r\r", true);

    /* Body starts right after the header newline. */
    ctx.ep = endpoint;
    ctx.pre = request;
    ctx.preLen = requestLen;
    ctx.prePos = (nl - request) + 1;

    /* 1) path */
    serr = StreamReadFull(&ctx, macPath, pathLen);
    if (serr != noErr) return false;            /* desync -> reconnect */
    macPath[pathLen] = '\0';

    /* Resolve the path. FSMakeFSSpec returns fnfErr for a not-yet-existing
     * file but still fills the FSSpec, which is exactly what FSpCreate needs;
     * any other (path) error is caught by FSpCreate below. */
    (void)MakeFSSpecFromPath(macPath, &spec);

    /* 2) (re)create the file, stamping type + creator so an APPL is launchable.
     * Use FSpCreateResFile for the resource fork: a raw FSpOpenRF write onto a
     * bare FSpCreate'd file corrupts at offset 48 (the File Manager stamps the
     * file name into the uninitialised resource fork). FSpCreateResFile lays
     * down a proper (empty) resource fork first; we then SetEOF it to 0 and
     * overwrite with the raw bytes. */
    FSpDelete(&spec);                            /* ignore error if absent */
    ferr = FSpCreate(&spec, fCreator, fType, 0);
    if (ferr == noErr && rsrcLen > 0) {
        FSpCreateResFile(&spec, fCreator, fType, 0);   /* add an initialised resource fork */
    }
    if (ferr != noErr) {
        /* Still drain the declared body so the wire stays in sync, then report. */
        long drop = dataLen + rsrcLen, n;
        while (drop > 0) {
            long got = 0;
            n = (drop > FILE_CHUNK) ? FILE_CHUNK : drop;
            if (StreamRead(&ctx, gChunk, n, &got) == noErr && got > 0) drop -= got;
            else SystemTask();
        }
        return ReplyStatus(endpoint,
            "STATUS:-1\rSTDOUT:0\rSTDERR:15\rFSpCreate failed\r\r", true);
    }

    /* 3) data fork */
    if (FSpOpenDF(&spec, fsRdWrPerm, &dataRef) != noErr)
        return ReplyStatus(endpoint,
            "STATUS:-1\rSTDOUT:0\rSTDERR:17\ropen data fork err\r\r", true);
    SetEOF(dataRef, dataLen);                    /* pre-size the fork */
    serr = StreamToFork(&ctx, dataRef, dataLen);
    FSClose(dataRef);
    if (serr != noErr) return false;

    /* 4) resource fork (raw bytes via FSpOpenRF; skip if empty) */
    if (rsrcLen > 0) {
        if (FSpOpenRF(&spec, fsRdWrPerm, &rsrcRef) != noErr)
            return ReplyStatus(endpoint,
                "STATUS:-1\rSTDOUT:0\rSTDERR:17\ropen rsrc fork err\r\r", true);
        gDbgRsrcPrePos = ctx.prePos;             /* where rsrc sits in the pushback */
        SetEOF(rsrcRef, 0L);                     /* discard FSpCreateResFile's empty map */
        SetEOF(rsrcRef, rsrcLen);                /* pre-size to the raw fork length */
        serr = StreamToFork(&ctx, rsrcRef, rsrcLen);
        FSClose(rsrcRef);
        if (serr != noErr) return false;
    }

    /* INSTRUMENTATION: read this file's OWN resource fork back from disk at
     * offset 40..55 and report it as hex, to tell write-corruption (on-disk
     * wrong) apart from read-corruption (READFILE wrong) in one round trip. */
    {
        static const char hx[] = "0123456789abcdef";
        char  diag[160];
        char *dp = diag;
        char  rb[16];
        char *src;
        short rref = 0, i;
        long  cnt = 16;
        long  srcOff = gDbgRsrcPrePos + 40;      /* pushback offset of rsrc[40] */
        for (i = 0; i < 16; i++) rb[i] = 0;
        if (rsrcLen >= 56 && FSpOpenRF(&spec, fsRdPerm, &rref) == noErr) {
            SetFPos(rref, fsFromStart, 40L);
            cnt = 16;
            FSRead(rref, &cnt, rb);
            FSClose(rref);
        }
        dp = AppendStr(dp, "ok rl=");
        dp = AppendULong(dp, (unsigned long)rsrcLen);
        dp = AppendStr(dp, " pl=");                /* preLen: was it all one segment? */
        dp = AppendULong(dp, (unsigned long)requestLen);
        dp = AppendStr(dp, " src=");               /* what we MEANT to write (pushback) */
        if (srcOff + 16 <= requestLen) {
            src = request + srcOff;
            for (i = 0; i < 16; i++) {
                *dp++ = hx[((unsigned char)src[i] >> 4) & 0xF];
                *dp++ = hx[(unsigned char)src[i] & 0xF];
            }
        } else {
            dp = AppendStr(dp, "notinpushback");
        }
        dp = AppendStr(dp, " rb40=");              /* what's actually on disk */
        for (i = 0; i < 16; i++) {
            *dp++ = hx[(rb[i] >> 4) & 0xF];
            *dp++ = hx[rb[i] & 0xF];
        }
        *dp = '\0';
        return SendStatusOut(endpoint, diag);
    }

badhdr:
    return ReplyStatus(endpoint,
        "STATUS:-1\rSTDOUT:0\rSTDERR:18\rWRITEFILE bad header\r\r", true);
}

/* ---- READFILE ------------------------------------------------------------ */

/* Stream one open fork (length `total`) out to the host in FILE_CHUNK pieces. */
static Boolean SendFork(EndpointRef endpoint, short refNum, long total)
{
    long remaining = total;
    while (remaining > 0) {
        long want = (remaining > FILE_CHUNK) ? FILE_CHUNK : remaining;
        long n = want;
        OSErr rerr = FSRead(refNum, &n, gChunk);    /* n := bytes actually read */
        if (n > 0) {
            if (SendData(endpoint, gChunk, n) != noErr) return false;
            remaining -= n;
        }
        if (rerr != noErr && rerr != eofErr) return false;
        if (n == 0) break;
    }
    return true;
}

Boolean ReadFileVerb(EndpointRef endpoint, char *request, long requestLen)
{
    char    macPath[512];
    FSSpec  spec;
    FInfo   fndr;
    short   dataRef = 0, rsrcRef = 0;
    long    dataLen = 0, rsrcLen = 0;
    char    header[64];
    char   *p;
    short   n;
    Boolean ok;

    if (EnsureChunk() == NULL)
        return ReplyStatus(endpoint,
            "STATUS:-1\rSTDOUT:0\rSTDERR:13\rout of memory\r\r", true);

    /* Everything after "READFILE:" up to \r/\n/end is the path. */
    for (n = 0; (long)(9 + n) < requestLen && request[9 + n] &&
                request[9 + n] != '\r' && request[9 + n] != '\n' && n < 511; n++)
        macPath[n] = request[9 + n];
    macPath[n] = '\0';

    if (MakeFSSpecFromPath(macPath, &spec) != noErr)
        return ReplyStatus(endpoint,
            "STATUS:-1\rSTDOUT:0\rSTDERR:14\rno such file\r\r", true);

    if (FSpGetFInfo(&spec, &fndr) != noErr)
        return ReplyStatus(endpoint,
            "STATUS:-1\rSTDOUT:0\rSTDERR:14\rno such file\r\r", true);

    /* Open both forks read-only and measure them. A missing/empty resource
     * fork is normal (data-only files): treat an open failure as length 0. */
    if (FSpOpenDF(&spec, fsRdPerm, &dataRef) != noErr)
        return ReplyStatus(endpoint,
            "STATUS:-1\rSTDOUT:0\rSTDERR:17\ropen data fork err\r\r", true);
    GetEOF(dataRef, &dataLen);

    if (FSpOpenRF(&spec, fsRdPerm, &rsrcRef) == noErr) {
        GetEOF(rsrcRef, &rsrcLen);
    } else {
        rsrcRef = 0;
        rsrcLen = 0;
    }

    /* Header: FILE:<typeHex8>:<creatorHex8>:<dataLen>:<rsrcLen>\n */
    p = header;
    *p++='F'; *p++='I'; *p++='L'; *p++='E'; *p++=':';
    p = AppendHex8(p, fndr.fdType);   *p++ = ':';
    p = AppendHex8(p, fndr.fdCreator); *p++ = ':';
    p = AppendULong(p, (unsigned long)dataLen); *p++ = ':';
    p = AppendULong(p, (unsigned long)rsrcLen); *p++ = '\n';

    if (SendData(endpoint, header, p - header) != noErr) {
        FSClose(dataRef); if (rsrcRef) FSClose(rsrcRef);
        return false;
    }

    ok = SendFork(endpoint, dataRef, dataLen);
    FSClose(dataRef);
    if (ok && rsrcRef) ok = SendFork(endpoint, rsrcRef, rsrcLen);
    if (rsrcRef) FSClose(rsrcRef);

    return ok;   /* false on a mid-stream send failure -> reconnect */
}
