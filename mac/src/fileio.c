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
#include <Processes.h>  /* GetCurrentProcess / GetProcessInformation (SwapSelf) */
#include <OSUtils.h>    /* GetDateTime, SecondsToDate (TREE/D's date) */
#include <Script.h>     /* smSystemScript (TREE/D's file) */

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
 *     name<TAB>type<TAB>creator<TAB>dataSize<TAB>modSecs<LF>
 * The row terminator really is LF (0x0A), even though the code below writes
 * '\r': classic-Mac C maps '\r' to LF and '\n' to CR, the reverse of every
 * host-side convention. So a response carries BOTH endings — the SendCommandResult
 * framing around it is CR-separated, these rows are LF-separated. Host parsers
 * must split on either; assuming CR here yields an empty listing rather than an
 * error, which is how the trap hides (measured on the wire 2026-08-02).
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
            SetHandleSize(h, oldSize + p); NoteHeap();
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

/* ---- TREE: the folder tree, DOS style (no ToolServer) -------------------- */

/*
 * TREE:<path>   folders only        TREE/F:<path>   folders and files
 * /D (TREE/D:, TREE/F/D:) also saves it on the disk -- see below
 *
 *   MeinMac:  (2026-11-23 07:30)
 *   +---AB-Tools
 *   +---MPW
 *   |   +---Examples
 *   |   \---Tools
 *   \---System Folder
 *   12 folders
 *
 * DOS `TREE /A` drawing, because MacRoman has no box-drawing characters.
 *
 * With /D the tree is also SAVED, because a disk's tree is worth keeping and
 * ages: <home>TREE:<name> <yyyy-mm-dd>[ F], a SimpleText file whose first line
 * carries the date and time. The same tree on the same day overwrites its
 * file. On request only: not every look at a folder needs a file (operator).
 *
 * The walk is a JOB with an explicit stack, done one catalog read per step, so
 * it can stop and resume. TreeVerb runs it for up to 45 s and answers with the
 * whole tree. Past that a plain TREE stops ("truncated (time limit)", naming
 * /D); a TREE/D answers at once -- "continues in the background, file ..." --
 * and TreeService, called from the main loop, carries on a few ticks per pass
 * while every other command is served; the console says when it is done. Before this a TREE of an AppleShare volume (every read a round
 * trip to the server) held the daemon deaf for 7 minutes (odroid-c2,
 * 2026-11-23). One job at a time; a second TREE gets "busy".
 *
 * The REPLY keeps a memory budget (a quarter of what the heap may still hold,
 * 16 K..512 K -- a 1,000 K daemon on a Duo or an SE/30 must not hand half its
 * partition to one TREE/F); past it the reply says "truncated", the file goes
 * on. NOT MaxBlock(): the heap grows on demand, so it sees only the part grown
 * so far (it capped a 12 MB odroid-c2 at 513 of 1679 folders). Rows end '\r'
 * in the code = LF on the wire (as LISTDIR); the file uses '\n' = CR, the
 * Mac's own line end, so SimpleText shows lines. Added 2026-11-23.
 */
#define TREE_DEPTH    24
#define TREE_MAX      (512L * 1024L)    /* reply ceiling on a big Mac ... */
#define TREE_MIN      (16L * 1024L)     /* ... floor on a small one */
#define TREE_CONSOLE  40                /* lines echoed to the Verbose console */
#define TREE_TICKS    (45L * 60L)       /* foreground time before going background */
#define TREE_SLICE    3                 /* background ticks per main-loop pass */

extern void StatusMessage(const char *msg);
extern const char *DaemonHome(void);

typedef struct {
    long    dirID;
    short   index, total, seen;
    Boolean counting;                   /* pass 1: how many rows here */
} TreeFrame;

typedef struct {
    TreeFrame  st[TREE_DEPTH];
    short      sp;                      /* top frame; -1 = done */
    short      vRefNum;
    Boolean    files;
    Handle     h;                       /* reply (foreground only), NULL after */
    long       used, cap, max;
    Boolean    full;                    /* the reply hit its budget */
    long       nDirs, nFiles, nLines;
    short      ref;                     /* the saved file's data fork, 0 = none */
    char       pre[TREE_DEPTH * 4 + 1];
    char       line[TREE_DEPTH * 4 + 80];
    char       fname[64];               /* "<name> yyyy-mm-dd[ F]" */
    Str255     nm;
    CInfoPBRec pb;
} TreeJob;

static TreeJob *gTree = NULL;           /* heap, not A5: see gChunk */

static OSErr TreeGet(TreeJob *j, long dirID, short index)
{
    LD_zero(&j->pb, sizeof(j->pb));
    j->pb.dirInfo.ioNamePtr   = j->nm;
    j->pb.dirInfo.ioVRefNum   = j->vRefNum;
    j->pb.dirInfo.ioFDirIndex = index;
    j->pb.dirInfo.ioDrDirID   = dirID;
    return PBGetCatInfoSync(&j->pb);
}

/* One row: to the file, to the reply while there is one, and the first rows
 * to the console. toConsole forces it there (the closing lines). */
static void TreeEmit(TreeJob *j, short n, Boolean toConsole)
{
    if (j->ref != 0) {
        long cnt = n;
        char eol = '\n';                 /* CR in MPW C: the Mac's line end */
        FSWrite(j->ref, &cnt, j->line);
        cnt = 1;
        FSWrite(j->ref, &cnt, &eol);
    }
    if (j->h != NULL && !j->full) {
        if (j->used + n + 1 + 128 > j->cap) {   /* keep 128 B for the closing lines */
            long want = j->cap * 2;
            if (want > j->max) want = j->max;
            if (j->used + n + 1 + 128 > want) j->full = true;
            else {
                SetHandleSize(j->h, want);
                if (MemError() != noErr) j->full = true; else j->cap = want;
            }
        }
        if (!j->full) {
            BlockMoveData(j->line, *j->h + j->used, n);
            j->used += n;
            (*j->h)[j->used++] = '\r';  /* LF on the wire */
        }
    }
    if (toConsole || j->nLines < TREE_CONSOLE) { j->line[n] = '\0'; StatusMessage(j->line); }
    j->nLines++;
}

/* A closing line for the reply: written into the 128 B kept free. */
static void TreeReplyTail(TreeJob *j, const char *s)
{
    short n = LD_len(s);
    if (j->h == NULL || j->used + n + 1 > j->cap) return;
    BlockMoveData(s, *j->h + j->used, n);
    j->used += n;
    (*j->h)[j->used++] = '\r';
}

/* One catalog read. Returns false when the walk is complete. */
static Boolean TreeStep(TreeJob *j)
{
    TreeFrame *f;
    Boolean    isDir, last;
    short      p, k;

    if (j->sp < 0) return false;
    f = &j->st[j->sp];
    if (TreeGet(j, f->dirID, f->index) != noErr) {     /* past the last entry */
        if (f->counting) { f->counting = false; f->index = 1; return true; }
        j->sp--;                                        /* folder done: up */
        if (j->sp >= 0) j->pre[j->sp * 4] = '\0';
        return j->sp >= 0;
    }
    f->index++;
    isDir = (j->pb.hFileInfo.ioFlAttrib & 0x10) != 0;
    if (!isDir && !j->files) return true;
    if (f->counting) { f->total++; return true; }

    last = (Boolean)(++f->seen == f->total);
    p = 0;
    for (k = 0; k < j->sp * 4; k++) j->line[p++] = j->pre[k];
    j->line[p++] = last ? '\\' : '+';
    j->line[p++] = '-'; j->line[p++] = '-'; j->line[p++] = '-';
    for (k = 1; k <= j->nm[0] && p < (short)sizeof(j->line) - 2; k++)
        j->line[p++] = (char)j->nm[k];
    TreeEmit(j, p, false);

    if (!isDir) { j->nFiles++; return true; }
    j->nDirs++;
    if (j->sp + 1 >= TREE_DEPTH) return true;           /* too deep: listed, not entered */
    LD_cpy(j->pre + j->sp * 4, last ? "    " : "|   ");
    j->sp++;
    f = &j->st[j->sp];
    f->dirID = j->pb.dirInfo.ioDrDirID;
    f->index = 1; f->total = 0; f->seen = 0; f->counting = true;
    return true;
}

/* "12 folders, 345 files" into j->line; returns its length. */
static short TreeCounts(TreeJob *j)
{
    short p = 0;
    LD_num(j->line, &p, j->nDirs);
    LD_cpy(j->line + p, j->nDirs == 1 ? " folder" : " folders"); p = LD_len(j->line);
    if (j->files) {
        j->line[p++] = ','; j->line[p++] = ' ';
        LD_num(j->line, &p, j->nFiles);
        LD_cpy(j->line + p, j->nFiles == 1 ? " file" : " files"); p = LD_len(j->line);
    }
    return p;
}

/* The summary into the file, close it, free the job. */
static void TreeFinish(TreeJob *j)
{
    if (j->ref != 0) {                  /* the counts, into the file only */
        long cnt = TreeCounts(j);
        char eol = '\n';
        FSWrite(j->ref, &cnt, j->line);
        cnt = 1;
        FSWrite(j->ref, &cnt, &eol);
        FSClose(j->ref);
        j->ref = 0;
    }
}

/* "TREE:" -> <home>TREE:<fname>, created as a SimpleText file. 0 = not saved. */
static short TreeOpenFile(TreeJob *j)
{
    Str255 pp;
    FSSpec spec;
    long   dirID;
    short  n = 0, i, ref = 0;
    const char *home = DaemonHome();

    if (home == NULL || home[0] == '\0') return 0;     /* legacy setup: no folder */
    for (i = 0; home[i] && n < 240; i++) pp[1 + n++] = (unsigned char)home[i];
    pp[1 + n++] = 'T'; pp[1 + n++] = 'R'; pp[1 + n++] = 'E'; pp[1 + n++] = 'E';
    pp[0] = (unsigned char)n;
    if (FSMakeFSSpec(0, 0L, pp, &spec) == fnfErr) (void)FSpDirCreate(&spec, smSystemScript, &dirID);
    pp[1 + n++] = ':';
    for (i = 0; j->fname[i] && n < 254; i++) pp[1 + n++] = (unsigned char)j->fname[i];
    pp[0] = (unsigned char)n;
    if (FSMakeFSSpec(0, 0L, pp, &spec) == noErr) (void)FSpDelete(&spec);  /* same day: newest wins */
    if (FSpCreate(&spec, 'ttxt', 'TEXT', smSystemScript) != noErr) return 0;
    if (FSpOpenDF(&spec, fsRdWrPerm, &ref) != noErr) return 0;
    return ref;
}

/* "2026-11-23" or "2026-11-23 07:30" into s. */
static void TreeDate(char *s, Boolean withTime)
{
    unsigned long secs;
    DateTimeRec   d;
    short         p = 0;
    GetDateTime(&secs);
    SecondsToDate(secs, &d);
    LD_num(s, &p, d.year); s[p++] = '-';
    s[p++] = (char)('0' + d.month / 10); s[p++] = (char)('0' + d.month % 10); s[p++] = '-';
    s[p++] = (char)('0' + d.day / 10);   s[p++] = (char)('0' + d.day % 10);
    if (withTime) {
        s[p++] = ' ';
        s[p++] = (char)('0' + d.hour / 10);   s[p++] = (char)('0' + d.hour % 10); s[p++] = ':';
        s[p++] = (char)('0' + d.minute / 10); s[p++] = (char)('0' + d.minute % 10);
    }
    s[p] = '\0';
}

/* Main loop: carry a background TREE on, a few ticks per pass. */
void TreeService(void)
{
    long until;
    if (gTree == NULL) return;
    until = TickCount() + TREE_SLICE;
    while (TickCount() < until)
        if (!TreeStep(gTree)) {
            char msg[120];
            short p;
            TreeFinish(gTree);
            LD_cpy(msg, "TREE done: TREE:");
            p = LD_len(msg);
            LD_cpy(msg + p, gTree->fname);
            StatusMessage(msg);
            p = TreeCounts(gTree);
            gTree->line[p] = '\0';
            StatusMessage(gTree->line);
            DisposePtr((Ptr)gTree);
            gTree = NULL;
            return;
        }
}

Boolean TreeVerb(ABConn *conn, char *request, long requestLen)
{
    TreeJob      *j;
    char          path[256];
    short         n = 0, i, k, p, last = 0;
    Boolean       hasColon = false, files = false, toDisk = false, saved;
    Str255        pPath;
    FSSpec        spec;
    CommandResult res;
    long          deadline;

    res.exitCode = -1; res.outData = NULL; res.outLen = 0; res.errData[0] = '\0';
    if (gTree != NULL) {
        LD_cpy(res.errData, "busy: a TREE is still running in the background");
        SendCommandResult(conn, &res);
        return true;
    }
    i = 4;                                     /* past "TREE"; then /F, /D, ':' */
    while (i + 1 < requestLen && request[i] == '/') {
        if (request[i + 1] == 'F' || request[i + 1] == 'f') files = true;
        if (request[i + 1] == 'D' || request[i + 1] == 'd') toDisk = true;
        i += 2;
    }
    if (i < requestLen && request[i] == ':') i++;
    while (i < requestLen && request[i] != '\r' && request[i] != '\n'
           && request[i] != '\0' && n < 254) {
        if (request[i] == ':') hasColon = true;
        path[n++] = request[i++];
    }
    if (n > 0 && !hasColon) path[n++] = ':';   /* "MeinMac" is a volume, not a file */
    path[n] = '\0';
    if (n == 0) {
        LD_cpy(res.errData, "usage: TREE[/F][/D]:<path>");
        SendCommandResult(conn, &res);
        return true;
    }

    j = (TreeJob *)NewPtrClear(sizeof(TreeJob));
    if (j == NULL) {
        LD_cpy(res.errData, "out of memory");
        SendCommandResult(conn, &res);
        return true;
    }
    j->files = files;
    LD_CtoP(path, pPath);
    if (FSMakeFSSpec(0, 0, pPath, &spec) != noErr) {
        LD_cpy(res.errData, "no such path");
        goto fail;
    }
    BlockMoveData(spec.name, j->nm, spec.name[0] + 1);
    j->pb.dirInfo.ioNamePtr = j->nm;
    j->pb.dirInfo.ioVRefNum = spec.vRefNum;
    j->pb.dirInfo.ioFDirIndex = 0;
    j->pb.dirInfo.ioDrDirID = spec.parID;
    if (PBGetCatInfoSync(&j->pb) != noErr || !(j->pb.dirInfo.ioFlAttrib & 0x10)) {
        LD_cpy(res.errData, "not a directory");
        goto fail;
    }
    j->vRefNum = j->pb.dirInfo.ioVRefNum;
    j->st[0].dirID = j->pb.dirInfo.ioDrDirID;
    j->st[0].index = 1;
    j->st[0].counting = true;
    j->sp = 0;

    j->max = HeapLimit() - (HeapZoneSize() - FreeMem());
    j->max /= 4;
    if (j->max > TREE_MAX) j->max = TREE_MAX;
    if (j->max < TREE_MIN) j->max = TREE_MIN;
    j->cap = 4096;
    j->h = NewHandle(j->cap);
    if (j->h == NULL) { LD_cpy(res.errData, "out of memory"); goto fail; }

    /* file name: the last path element (volume or folder), the date, F */
    for (k = 0; k < n - 1; k++) if (path[k] == ':') last = (short)(k + 1);
    p = 0;
    for (k = last; k < n - 1 && path[k] != ':' && p < 19; k++) j->fname[p++] = path[k];
    j->fname[p++] = ' ';
    TreeDate(j->fname + p, false);
    if (files) LD_cpy(j->fname + LD_len(j->fname), " F");
    if (toDisk) j->ref = TreeOpenFile(j);
    saved = (Boolean)(j->ref != 0);

    /* the root line: the path and when the tree was taken */
    p = 0;
    for (k = 0; path[k] && p < 140; k++) j->line[p++] = path[k];
    j->line[p++] = ' '; j->line[p++] = ' '; j->line[p++] = '(';
    TreeDate(j->line + p, true);
    p = LD_len(j->line);
    j->line[p++] = ')';
    TreeEmit(j, p, false);

    deadline = TickCount() + TREE_TICKS;
    while (TickCount() < deadline && TreeStep(j)) ;

    if (j->sp >= 0 && !saved) {                /* plain TREE: stop here */
        j->sp = -1;
        TreeReplyTail(j, "... truncated (time limit) - TREE/D saves the whole tree");
        StatusMessage("... truncated (time limit) - TREE/D saves the whole tree");
    }

    if (j->sp < 0) {                           /* done in the foreground */
        p = TreeCounts(j);
        if (j->full) TreeReplyTail(j, "... truncated (output limit) - the file has it all");
        if (j->nLines > TREE_CONSOLE) {
            char more[64];
            short q;
            LD_cpy(more, "... ");
            q = LD_len(more);
            LD_num(more, &q, j->nLines - TREE_CONSOLE);
            LD_cpy(more + q, " more lines sent to the host");
            StatusMessage(more);
        }
        TreeReplyTail(j, j->line);             /* the counts */
        j->line[p] = '\0';
        StatusMessage(j->line);
        TreeFinish(j);
        if (saved) {
            char msg[96];
            LD_cpy(msg, "saved: TREE:");
            LD_cpy(msg + LD_len(msg), j->fname);
            TreeReplyTail(j, msg);
            StatusMessage(msg);
        }
    } else {                                   /* go on in the background */
        char msg[100];
        LD_cpy(msg, "... continues in the background: TREE:");
        LD_cpy(msg + LD_len(msg), j->fname);
        TreeReplyTail(j, msg);
        StatusMessage(msg);
    }

    SetHandleSize(j->h, j->used);
    res.exitCode = 0;
    res.outData  = j->h;
    res.outLen   = j->used;
    SendCommandResult(conn, &res);
    DisposeHandle(j->h);
    j->h = NULL;
    if (j->sp < 0) DisposePtr((Ptr)j);
    else gTree = j;                            /* TreeService carries on */
    return true;

fail:
    if (j->h != NULL) DisposeHandle(j->h);
    DisposePtr((Ptr)j);
    SendCommandResult(conn, &res);
    return true;
}

/* Build `out` = a file named (base's name + suffix) in base's SAME folder.
 * suffix is a C string; the combined leaf is bounded to a Str63. */
static OSErr MakeSibling(const FSSpec *base, const char *suffix, FSSpec *out)
{
    short n = base->name[0];
    short s = 0, i;
    while (suffix[s]) s++;
    if (n + s > 63) return bdNamErr;
    out->vRefNum = base->vRefNum;
    out->parID   = base->parID;
    for (i = 1; i <= n; i++)  out->name[i] = base->name[i];
    for (i = 0; i < s; i++)   out->name[n + 1 + i] = (unsigned char)suffix[i];
    out->name[0] = (unsigned char)(n + s);
    return noErr;
}

/*
 * SwapSelf -- replace the RUNNING daemon binary with a staged sibling, entirely
 * over the bridge, so updating the daemon needs no manual Shift-boot + Finder
 * rename (the OS 9 case) or ToolServer (the 68K case).
 *
 * The installer's fork-aware COPY can't do this: opening the running daemon's
 * file for write fails with fBsyErr. But RENAMING an open file is permitted by
 * the File Manager -- it edits the catalog entry, not the open forks -- so the
 * daemon renames ITSELF aside and renames the staged binary into its place. The
 * running process keeps executing from its now-renamed file; the caller then
 * reboots and the watchdog launches the current "<name>", i.e. the new binary.
 *
 * Convention: the host first stages the new binary next to the daemon as
 * "<selfName> new" (via mac_put_file -- a fresh file, so no lock). Then:
 *   1. resolve own FSSpec (GetCurrentProcess -> processAppSpec)
 *   2. require the "<selfName> new" sibling to exist
 *   3. delete any stale "<selfName> old" backup
 *   4. EXCHANGE the contents of self and "<selfName> new" (FSpExchangeFiles),
 *      then rename "<selfName> new" - now holding the old binary - to
 *      "<selfName> old".
 *   5. only if the exchange is refused: the 0.8d50 way, rename self -> "old"
 *      and "new" -> "<selfName>", rolling back on failure.
 *
 * Why the exchange (2026-11-22): a Finder alias - the Startup Items entry -
 * follows the FILE ID, not the name. Renaming moved the running file's ID to
 * "<selfName> old", so the alias launched the OLD binary at the next boot and
 * every update looked like it "did not take" (odroid-c2: an August build came
 * back from the dead that way). FSpExchangeFiles swaps the forks in the catalog
 * and keeps each name with its file ID; it works on open files and updates
 * their FCBs, so the running process keeps reading its own code.
 * The backup "<selfName> old" is removed by RemoveOldBinaries at the next start.
 */
OSErr SwapSelf(Boolean *exchanged)
{
    ProcessSerialNumber psn;
    ProcessInfoRec      info;
    FSSpec              self, staged, backup;
    Str255              selfName;
    FInfo               fi;
    OSErr               err;

    *exchanged = false;

    /* 1. this process's own file */
    err = GetCurrentProcess(&psn);
    if (err != noErr) return err;
    info.processInfoLength = sizeof(ProcessInfoRec);
    info.processName       = selfName;
    info.processAppSpec    = &self;
    err = GetProcessInformation(&psn, &info);
    if (err != noErr) return err;

    /* sibling specs in the daemon's folder */
    err = MakeSibling(&self, " new", &staged);
    if (err != noErr) return err;
    err = MakeSibling(&self, " old", &backup);
    if (err != noErr) return err;

    /* 2. the staged binary must be present (host put it there first) */
    if (FSpGetFInfo(&staged, &fi) != noErr) return fnfErr;

    /* 3. clear a stale backup (not the running file -> deletable) */
    FSpDelete(&backup);                         /* ignore error if absent */

    /* 4. swap the contents; names and file IDs stay where they are */
    if (FSpExchangeFiles(&staged, &self) == noErr) {
        *exchanged = true;
        (void)FSpRename(&staged, backup.name);  /* the old binary, as backup */
        return noErr;
    }

    /* 5. fallback: rename (moves the file ID - an alias follows the old file) */
    err = FSpRename(&self, backup.name);
    if (err != noErr) return err;               /* OS refused to rename the open app */
    err = FSpRename(&staged, self.name);
    if (err != noErr) {                         /* roll back so a reboot still finds a daemon */
        FSpRename(&backup, self.name);
        return err;
    }
    return noErr;
}

/* ---- DISKINFO: volume totals (no ToolServer) ----------------------------- */

/*
 * DISKINFO[:<volume>] — report size and free space for one volume, or for every
 * mounted volume when no name is given.
 *
 * The sibling of LISTDIR: MPW's `Volumes -l` answers the same question but needs
 * ToolServer, which a plain OS 9 or a freshly installed machine may not have.
 * It also pairs with AFPMOUNT — having mounted a server volume, the next
 * question is invariably how much room is on it.
 *
 * One line per volume:  name<TAB>vRefNum<TAB>totalBytes<TAB>freeBytes<LF>
 * LF (0x0A), for the same reason as LISTDIR above: the '\r' in the code becomes
 * LF under classic-Mac C, while the framing around it stays CR.
 *
 * Sizes are computed as blocks * blockSize in UNSIGNED long arithmetic: a 2 GB
 * volume overflows a signed long, and a negative "free space" would be worse
 * than no answer at all.
 */
Boolean DiskInfoVerb(ABConn *conn, char *request, long requestLen)
{
    char           want[64];
    short          n = 0, i;
    Str255         nm;
    HParamBlockRec pb;
    Handle         h;
    CommandResult  res;
    Boolean        one;

    res.exitCode = -1;
    res.outData  = NULL;
    res.outLen   = 0;
    res.errData[0] = '\0';

    i = LD_len(PROTO_DISKINFO);
    if (i < requestLen && request[i] == ':') i++;
    while (i < requestLen && request[i] != '\r' && request[i] != '\n'
           && request[i] != '\0' && n < 62) {
        want[n++] = request[i++];
    }
    want[n] = '\0';
    /* A volume name must END with a colon here: without it the File Manager
     * reads the string as a FILE name on the default volume and quietly answers
     * about that volume instead — "DISKINFO:AppleShare" reported MeinMac until
     * this was fixed (2026-07-25). Accept either spelling from the caller and
     * normalise to exactly one colon. */
    if (n > 0 && want[n - 1] != ':' && n < 62) { want[n++] = ':'; want[n] = '\0'; }
    one = (n > 0);

    h = NewHandle(0);
    if (h == NULL) {
        LD_cpy(res.errData, "out of memory");
        SendCommandResult(conn, &res);
        return true;
    }

    for (i = 1; ; i++) {
        char          line[200];
        short         p = 0, k, len;
        unsigned long total, freeb, blk;

        LD_zero(&pb, sizeof(pb));
        pb.volumeParam.ioNamePtr = nm;
        if (one) {
            LD_CtoP(want, nm);
            pb.volumeParam.ioVRefNum = 0;
            pb.volumeParam.ioVolIndex = -1;   /* select by NAME */
        } else {
            nm[0] = 0;
            pb.volumeParam.ioVRefNum = 0;
            pb.volumeParam.ioVolIndex = i;    /* walk the mounted volumes */
        }
        if (PBHGetVInfoSync(&pb) != noErr) break;   /* past the last / no such volume */

        blk   = (unsigned long)pb.volumeParam.ioVAlBlkSiz;
        total = (unsigned long)((unsigned short)pb.volumeParam.ioVNmAlBlks) * blk;
        freeb = (unsigned long)((unsigned short)pb.volumeParam.ioVFrBlk) * blk;

        len = nm[0];
        for (k = 0; k < len; k++) line[p++] = (char)nm[k + 1];
        line[p++] = '\t';
        LD_num(line, &p, (long)pb.volumeParam.ioVRefNum);
        line[p++] = '\t';
        LD_unum(line, &p, total);
        line[p++] = '\t';
        LD_unum(line, &p, freeb);
        line[p++] = '\r';

        {
            long oldSize = GetHandleSize(h);
            SetHandleSize(h, oldSize + p); NoteHeap();
            if (MemError() == noErr) {
                HLock(h);
                BlockMoveData(line, *h + oldSize, p);
                HUnlock(h);
            }
        }
        if (one) break;                        /* named volume: exactly one line */
    }

    if (GetHandleSize(h) == 0) {
        DisposeHandle(h);
        LD_cpy(res.errData, one ? "no such volume" : "no volumes mounted");
        SendCommandResult(conn, &res);
        return true;
    }

    res.exitCode = 0;
    res.outData  = h;
    res.outLen   = GetHandleSize(h);
    res.errData[0] = '\0';
    SendCommandResult(conn, &res);
    DisposeHandle(h);
    return true;
}

/* ---- PROCLIST: the running processes (no ToolServer, no GUI) -------------- */

/*
 * PROCLIST — one line per running process, straight from the Process Manager.
 *
 * It lives beside LISTDIR and DISKINFO because it is the same KIND of verb, not
 * because it touches files: a question the daemon can answer entirely by itself,
 * on a machine with no ToolServer and without disturbing the front application.
 *
 * Three things asked for this on 2026-08-04, and it answers all three:
 *
 *  - The system's own "what is running" view is the Application menu, the
 *    rightmost menu in the System 7 menu bar. Over the bridge it is effectively
 *    unreadable: it is a PULL-DOWN, so a click opens and closes it in one
 *    gesture, and a menu held open across two screenshots starves the daemon
 *    (OTSnd err=-3158, then a 30 s reconnect). `mac_host_menu` needs the item
 *    position IN ADVANCE, which for a process list is circular — the content is
 *    exactly what one wants to know. The operator hit this the same day.
 *
 *  - The parallel session could not tell whether SimpleText was still running
 *    and resolved it by double-clicking an icon.
 *
 *  - The fast poller in the counter probe was identified as ToolServer by a
 *    DIFFERENTIAL (quit it, the rate collapses 41x) rather than by a binding of
 *    a NAME to an A5. `processLocation` and `processSize` close that: a process
 *    partition is a contiguous range, and the A5 world sits inside it, so an
 *    observed `LastA5` falls within exactly one process's
 *    [location, location+size). The binding needs no extra Toolbox call and no
 *    change to the 68k stub — which is why those two fields are reported even
 *    though nothing else asks for them.
 *
 * One line per process:
 *   name<TAB>type<TAB>signature<TAB>psnHi<TAB>psnLo<TAB>location<TAB>size<TAB>free<TAB>front<LF>
 * LF (0x0A), because classic-Mac C maps '\r' to LF while the framing around it
 * stays CR — the same rule as LISTDIR and DISKINFO above.
 *
 * Addresses and sizes are UNSIGNED: a partition above 2 GB would print negative
 * and a negative address is worse than no answer. `front` is 1 for the process
 * `GetFrontProcess` names, which is the one synthetic input reaches.
 *
 * Bounded at 64 processes. System 7 does not get near that, but an unbounded
 * loop building a reply is not something to leave to chance — and if the bound
 * is ever hit it is REPORTED (last line `…truncated`), never silently dropped.
 */
Boolean ProcListVerb(ABConn *conn, char *request, long requestLen)
{
    ProcessSerialNumber psn, front;
    ProcessInfoRec      info;
    Str31               nm;
    FSSpec              spec;
    Handle              h;
    CommandResult       res;
    short               count = 0;
    Boolean             more  = false;

    (void)request; (void)requestLen;          /* the verb takes no argument */

    res.exitCode   = -1;
    res.outData    = NULL;
    res.outLen     = 0;
    res.errData[0] = '\0';

    h = NewHandle(0);
    if (h == NULL) {
        LD_cpy(res.errData, "out of memory");
        SendCommandResult(conn, &res);
        return true;
    }

    if (GetFrontProcess(&front) != noErr) {
        front.highLongOfPSN = 0;
        front.lowLongOfPSN  = kNoProcess;
    }

    psn.highLongOfPSN = 0;
    psn.lowLongOfPSN  = kNoProcess;
    while (GetNextProcess(&psn) == noErr) {
        char  line[240];
        short p = 0, k, len;

        if (count >= 64) { more = true; break; }

        LD_zero(&info, sizeof(info));
        info.processInfoLength = sizeof(ProcessInfoRec);
        info.processName       = nm;
        info.processAppSpec    = &spec;
        nm[0] = 0;
        if (GetProcessInformation(&psn, &info) != noErr) continue;

        len = nm[0];
        if (len > 31) len = 31;
        for (k = 0; k < len; k++) line[p++] = (char)nm[k + 1];
        line[p++] = '\t';
        for (k = 0; k < 4; k++) line[p++] = ((char *)&info.processType)[k];
        line[p++] = '\t';
        for (k = 0; k < 4; k++) line[p++] = ((char *)&info.processSignature)[k];
        line[p++] = '\t';
        LD_unum(line, &p, (unsigned long)psn.highLongOfPSN);
        line[p++] = '\t';
        LD_unum(line, &p, (unsigned long)psn.lowLongOfPSN);
        line[p++] = '\t';
        LD_unum(line, &p, (unsigned long)info.processLocation);
        line[p++] = '\t';
        LD_unum(line, &p, (unsigned long)info.processSize);
        line[p++] = '\t';
        LD_unum(line, &p, (unsigned long)info.processFreeMem);
        line[p++] = '\t';
        line[p++] = (char)((psn.highLongOfPSN == front.highLongOfPSN &&
                            psn.lowLongOfPSN  == front.lowLongOfPSN) ? '1' : '0');
        line[p++] = '\r';

        {
            long oldSize = GetHandleSize(h);
            SetHandleSize(h, oldSize + p); NoteHeap();
            if (MemError() == noErr) {
                HLock(h);
                BlockMoveData(line, *h + oldSize, p);
                HUnlock(h);
            }
        }
        count++;
    }

    if (more) {
        const char *cut = "...truncated\r";
        long        oldSize = GetHandleSize(h);
        short       n = 0;
        while (cut[n]) n++;
        SetHandleSize(h, oldSize + n);
        if (MemError() == noErr) {
            HLock(h);
            BlockMoveData((Ptr)cut, *h + oldSize, (Size)n);
            HUnlock(h);
        }
    }

    if (GetHandleSize(h) == 0) {
        DisposeHandle(h);
        LD_cpy(res.errData, "no processes");   /* cannot happen: we are one */
        SendCommandResult(conn, &res);
        return true;
    }

    res.exitCode   = 0;
    res.outData    = h;
    res.outLen     = GetHandleSize(h);
    res.errData[0] = '\0';
    SendCommandResult(conn, &res);
    DisposeHandle(h);
    return true;
}

/* Find an application FILE by its creator signature, using the Desktop Database
 * -- the same mapping the Finder uses when you double-click a document and it
 * opens the right program. No path, no volume name, no configuration.
 *
 * Why this exists (2026-08-05): the daemon chain-launches its helpers from
 * `APP=` lines in the prefs, and `PrefsDefaults` sets `appCount = 0`. So EVERY
 * fresh install came up without ToolServer, and every command that needs it
 * failed with "no-ToolServer/MPW" until somebody worked out that a line was
 * missing from a file they had never opened. The mechanism was never broken;
 * the list was simply empty, and an empty list is indistinguishable from a
 * deliberate one.
 *
 * A default PATH would have been the obvious fix and the wrong one: this
 * project's guests keep ToolServer in different places on different volumes
 * (MPW folder here, a copied-local folder on the OS 9 machine, absent on the
 * 2013 MacBook's guest). Same lesson as the System Folder that turned out to be
 * called `Systemordner`: find it, do not name it.
 *
 * -> noErr and *spec filled, or an error. Searches every mounted volume,
 * because the answer is usually not on the boot disk on a machine that has been
 * reorganised. */
OSErr FindAppFileBySignature(OSType creator, FSSpec *spec)
{
    DTPBRec   dt;
    HParamBlockRec hp;
    Str63     name;
    short     vIndex;
    OSErr     err;

    for (vIndex = 1; ; vIndex++) {
        /* Walk mounted volumes by index; ioResult != noErr ends the walk. */
        hp.volumeParam.ioNamePtr   = name;
        hp.volumeParam.ioVRefNum   = 0;
        hp.volumeParam.ioVolIndex  = vIndex;
        if (PBHGetVInfoSync(&hp) != noErr) break;

        dt.ioNamePtr  = name;
        dt.ioVRefNum  = hp.volumeParam.ioVRefNum;
        dt.ioDTRefNum = 0;
        if (PBDTGetPath(&dt) != noErr) continue;      /* no desktop database here */

        dt.ioNamePtr     = spec->name;
        dt.ioFileCreator = creator;
        dt.ioIndex       = 0;                          /* highest-version copy */
        dt.ioCompletion  = NULL;
        err = PBDTGetAPPLSync(&dt);
        if (err == noErr) {
            spec->vRefNum = hp.volumeParam.ioVRefNum;
            spec->parID   = dt.ioAPPLParID;
            return noErr;
        }
    }
    return fnfErr;
}

/*
 * RemoveOldBinaries -- at startup, delete what an update renamed aside (0.8d50).
 *
 * SwapSelf and the installer cannot overwrite a RUNNING daemon or watchdog, so
 * they rename it "<name> old" and leave it there; nothing ever removed it. On a
 * PowerBook Duo 230 (2026-11-21) an "AppleBridge old" was started by hand after
 * an update and, being an older build, cost the prefs their TCPPORT=/NAME=. At
 * the daemon's own startup none of these can be running: the daemon is the new
 * file, and the watchdog that launched it is the new watchdog. A copy that IS
 * open (an alias still pointing at it) refuses with fBsyErr and is left alone.
 * -> how many files were removed.
 */
short RemoveOldBinaries(void)
{
    static const char *kOthers[2] = { "AppleBridgeWatchdog old", "AppleBridgeConfig old" };
    ProcessSerialNumber psn;
    ProcessInfoRec      info;
    FSSpec              self, old;
    Str255              selfName, name;
    short               removed = 0, k, i;

    if (GetCurrentProcess(&psn) != noErr) return 0;
    info.processInfoLength = sizeof(ProcessInfoRec);
    info.processName       = selfName;
    info.processAppSpec    = &self;
    if (GetProcessInformation(&psn, &info) != noErr) return 0;

    if (MakeSibling(&self, " old", &old) == noErr && FSpDelete(&old) == noErr) removed++;
    for (k = 0; k < 2; k++) {
        for (i = 0; kOthers[k][i] && i < 63; i++) name[i + 1] = (unsigned char)kOthers[k][i];
        name[0] = (unsigned char)i;
        if (FSMakeFSSpec(self.vRefNum, self.parID, name, &old) == noErr && FSpDelete(&old) == noErr)
            removed++;
    }
    return removed;
}
