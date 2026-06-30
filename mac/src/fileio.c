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

/* Allocate the shared chunk buffer on first use; NULL on out-of-memory. */
static Ptr EnsureChunk(void)
{
    if (gChunk == NULL) gChunk = NewPtr(FILE_CHUNK);
    return gChunk;
}

/* ---- a pushback byte reader: drains bytes already received in `request`,
 *      then pulls more from the conn (one ABRecv per call) ---------- */
typedef struct {
    ABConn     *ep;
    char       *pre;       /* bytes already in the request buffer */
    long        preLen;
    long        prePos;
} StreamCtx;

/* One read step. Returns noErr with *got>0, kABNoData if the conn had
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
    return ABRecv(ctx->ep, dst, want, got);
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
        } else if (err == kABNoData) {
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
        } else if (err == kABNoData) {
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
static Boolean ReplyStatus(ABConn *conn, const char *frame, Boolean healthy)
{
    long n = 0;
    while (frame[n]) n++;
    ABSend(conn, frame, n);
    return healthy;
}

/* ---- WRITEFILE ----------------------------------------------------------- */

Boolean WriteFileVerb(ABConn *conn, char *request, long requestLen)
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
        return ReplyStatus(conn,
            "STATUS:-1\rSTDOUT:0\rSTDERR:13\rout of memory\r\r", true);

    /* The header line must be present in the first segment (it is tiny and is
     * always sent ahead of the body). Find its terminating newline. */
    nl = NULL;
    { long i; for (i = 10; i < requestLen; i++)
        if (request[i] == '\n' || request[i] == '\r') { nl = request + i; break; } }
    if (nl == NULL)
        return ReplyStatus(conn,
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
        return ReplyStatus(conn,
            "STATUS:-1\rSTDOUT:0\rSTDERR:17\rWRITEFILE bad size\r\r", true);

    /* Body starts right after the header newline. */
    ctx.ep = conn;
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
        return ReplyStatus(conn,
            "STATUS:-1\rSTDOUT:0\rSTDERR:15\rFSpCreate failed\r\r", true);
    }

    /* 3) data fork */
    if (FSpOpenDF(&spec, fsRdWrPerm, &dataRef) != noErr)
        return ReplyStatus(conn,
            "STATUS:-1\rSTDOUT:0\rSTDERR:17\ropen data fork err\r\r", true);
    SetEOF(dataRef, dataLen);                    /* pre-size the fork */
    serr = StreamToFork(&ctx, dataRef, dataLen);
    FSClose(dataRef);
    if (serr != noErr) return false;

    /* 4) resource fork (raw bytes via FSpOpenRF; skip if empty) */
    if (rsrcLen > 0) {
        if (FSpOpenRF(&spec, fsRdWrPerm, &rsrcRef) != noErr)
            return ReplyStatus(conn,
                "STATUS:-1\rSTDOUT:0\rSTDERR:17\ropen rsrc fork err\r\r", true);
        SetEOF(rsrcRef, 0L);                     /* discard FSpCreateResFile's empty map */
        SetEOF(rsrcRef, rsrcLen);                /* pre-size to the raw fork length */
        serr = StreamToFork(&ctx, rsrcRef, rsrcLen);
        FSClose(rsrcRef);
        if (serr != noErr) return false;
    }

    return ReplyStatus(conn, "STATUS:0\rSTDOUT:7\rWritten\rSTDERR:0\r\r", true);

badhdr:
    return ReplyStatus(conn,
        "STATUS:-1\rSTDOUT:0\rSTDERR:18\rWRITEFILE bad header\r\r", true);
}

/* ---- READFILE ------------------------------------------------------------ */

/* Stream one open fork (length `total`) out to the host in FILE_CHUNK pieces. */
static Boolean SendFork(ABConn *conn, short refNum, long total)
{
    long remaining = total;
    while (remaining > 0) {
        long want = (remaining > FILE_CHUNK) ? FILE_CHUNK : remaining;
        long n = want;
        OSErr rerr = FSRead(refNum, &n, gChunk);    /* n := bytes actually read */
        if (n > 0) {
            if (ABSend(conn, gChunk, n) != noErr) return false;
            remaining -= n;
        }
        if (rerr != noErr && rerr != eofErr) return false;
        if (n == 0) break;
    }
    return true;
}

Boolean ReadFileVerb(ABConn *conn, char *request, long requestLen)
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
        return ReplyStatus(conn,
            "STATUS:-1\rSTDOUT:0\rSTDERR:13\rout of memory\r\r", true);

    /* Everything after "READFILE:" up to \r/\n/end is the path. */
    for (n = 0; (long)(9 + n) < requestLen && request[9 + n] &&
                request[9 + n] != '\r' && request[9 + n] != '\n' && n < 511; n++)
        macPath[n] = request[9 + n];
    macPath[n] = '\0';

    if (MakeFSSpecFromPath(macPath, &spec) != noErr)
        return ReplyStatus(conn,
            "STATUS:-1\rSTDOUT:0\rSTDERR:14\rno such file\r\r", true);

    if (FSpGetFInfo(&spec, &fndr) != noErr)
        return ReplyStatus(conn,
            "STATUS:-1\rSTDOUT:0\rSTDERR:14\rno such file\r\r", true);

    /* Open both forks read-only and measure them. A missing/empty resource
     * fork is normal (data-only files): treat an open failure as length 0. */
    if (FSpOpenDF(&spec, fsRdPerm, &dataRef) != noErr)
        return ReplyStatus(conn,
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

    if (ABSend(conn, header, p - header) != noErr) {
        FSClose(dataRef); if (rsrcRef) FSClose(rsrcRef);
        return false;
    }

    ok = SendFork(conn, dataRef, dataLen);
    FSClose(dataRef);
    if (ok && rsrcRef) ok = SendFork(conn, rsrcRef, rsrcLen);
    if (rsrcRef) FSClose(rsrcRef);

    return ok;   /* false on a mid-stream send failure -> reconnect */
}

/* ---- LISTDIR: native directory listing (no ToolServer) ------------------ */

static short LD_len(const char *s) { short n = 0; while (s[n]) n++; return n; }
static void  LD_cpy(char *d, const char *s) { while (*s) *d++ = *s++; *d = '\0'; }
static void  LD_zero(void *p, long n) { char *c = (char *)p; while (n-- > 0) *c++ = 0; }

static void LD_CtoP(const char *c, Str255 p)
{
    short i = 0;
    while (c[i] && i < 255) { p[i + 1] = c[i]; i++; }
    p[0] = (unsigned char)i;
}

/* Append a signed decimal to buf at *pos (advances *pos). */
static void LD_num(char *buf, short *pos, long n)
{
    char tmp[16];
    short i = 0, j;
    if (n == 0) { buf[(*pos)++] = '0'; return; }
    if (n < 0)  { buf[(*pos)++] = '-'; n = -n; }
    while (n > 0) { tmp[i++] = (char)('0' + (n % 10)); n /= 10; }
    for (j = i - 1; j >= 0; j--) buf[(*pos)++] = tmp[j];
}

/* Append an UNSIGNED decimal (Mac dates are unsigned secs-since-1904, which
 * exceed 2^31 from 2006 on, so a signed print would go negative). */
static void LD_unum(char *buf, short *pos, unsigned long n)
{
    char tmp[16];
    short i = 0, j;
    if (n == 0) { buf[(*pos)++] = '0'; return; }
    while (n > 0) { tmp[i++] = (char)('0' + (n % 10)); n /= 10; }
    for (j = i - 1; j >= 0; j--) buf[(*pos)++] = tmp[j];
}

/*
 * LISTDIR:<path> — enumerate a folder with PBGetCatInfo (File Manager only, no
 * ToolServer) and stream a tab-separated listing back. One line per entry:
 *     name<TAB>type<TAB>creator<TAB>dataSize<TAB>modSecs<CR>
 * Directories report type "fldr", empty creator, size 0. The listing is built
 * into a Handle and sent via the normal SendCommandResult framing (STATUS:0 +
 * STDOUT), so the host's length-framed reader handles any size.
 */
Boolean ListDirVerb(ABConn *conn, char *request, long requestLen)
{
    char          path[256];
    short          n, i;
    Str255         pPath, nm;
    FSSpec         spec;
    CInfoPBRec     pb;
    OSErr          err;
    short          vRefNum;
    long           dirID;
    Handle         h;
    CommandResult  res;

    /* extract the path after the "LISTDIR:" prefix */
    n = 0;
    i = LD_len(PROTO_LISTDIR);
    while (i < requestLen && request[i] != '\r' && request[i] != '\n'
           && request[i] != '\0' && n < 255) {
        path[n++] = request[i++];
    }
    path[n] = '\0';

    res.exitCode = -1;
    res.outData  = NULL;
    res.outLen   = 0;
    res.errData[0] = '\0';

    LD_CtoP(path, pPath);
    err = FSMakeFSSpec(0, 0, pPath, &spec);
    if (err != noErr && err != fnfErr) {
        LD_cpy(res.errData, "no such path");
        SendCommandResult(conn, &res);
        return true;
    }

    /* resolve the target folder's own dirID + vRefNum. Zero the PB first so no
     * stale stack bytes leak into the call. */
    BlockMoveData(spec.name, nm, spec.name[0] + 1);
    LD_zero(&pb, sizeof(pb));
    pb.dirInfo.ioNamePtr = nm;
    pb.dirInfo.ioVRefNum = spec.vRefNum;
    pb.dirInfo.ioFDirIndex = 0;
    pb.dirInfo.ioDrDirID = spec.parID;
    if (PBGetCatInfoSync(&pb) != noErr || !(pb.dirInfo.ioFlAttrib & 0x10)) {
        LD_cpy(res.errData, "not a directory");
        SendCommandResult(conn, &res);
        return true;
    }
    vRefNum = pb.dirInfo.ioVRefNum;
    dirID   = pb.dirInfo.ioDrDirID;

    h = NewHandle(0);
    if (h == NULL) {
        LD_cpy(res.errData, "out of memory");
        SendCommandResult(conn, &res);
        return true;
    }

    for (i = 1; ; i++) {
        char          line[300];
        short         p = 0, k, nameLen;
        Boolean       isDir;
        OSType        t, c;
        unsigned long mdat;

        LD_zero(&pb, sizeof(pb));         /* fresh PB each call (no stale fields) */
        pb.dirInfo.ioNamePtr = nm;
        pb.dirInfo.ioVRefNum = vRefNum;
        pb.dirInfo.ioFDirIndex = i;
        pb.dirInfo.ioDrDirID = dirID;     /* the folder to enumerate */
        if (PBGetCatInfoSync(&pb) != noErr) break;   /* past the last entry */

        isDir   = (pb.hFileInfo.ioFlAttrib & 0x10) != 0;
        nameLen = nm[0];
        for (k = 0; k < nameLen; k++) line[p++] = nm[k + 1];   /* name */
        line[p++] = '\t';
        if (isDir) {
            line[p++] = 'f'; line[p++] = 'l'; line[p++] = 'd'; line[p++] = 'r';
            line[p++] = '\t';                  /* creator empty */
            line[p++] = '\t';
            line[p++] = '0';                   /* size 0 */
            mdat = pb.dirInfo.ioDrMdDat;
        } else {
            t = pb.hFileInfo.ioFlFndrInfo.fdType;
            c = pb.hFileInfo.ioFlFndrInfo.fdCreator;
            line[p++] = (char)((t >> 24) & 0xFF); line[p++] = (char)((t >> 16) & 0xFF);
            line[p++] = (char)((t >> 8) & 0xFF);  line[p++] = (char)(t & 0xFF);
            line[p++] = '\t';
            line[p++] = (char)((c >> 24) & 0xFF); line[p++] = (char)((c >> 16) & 0xFF);
            line[p++] = (char)((c >> 8) & 0xFF);  line[p++] = (char)(c & 0xFF);
            line[p++] = '\t';
            /* total size = data fork + resource fork (68K apps keep their CODE
             * in the resource fork, so the data fork is 0 — report both). */
            LD_num(line, &p, pb.hFileInfo.ioFlLgLen + pb.hFileInfo.ioFlRLgLen);
            mdat = pb.hFileInfo.ioFlMdDat;
        }
        line[p++] = '\t';
        LD_unum(line, &p, mdat);
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

    res.exitCode = 0;
    res.outData  = h;
    res.outLen   = GetHandleSize(h);
    res.errData[0] = '\0';
    SendCommandResult(conn, &res);
    DisposeHandle(h);
    return true;
}
