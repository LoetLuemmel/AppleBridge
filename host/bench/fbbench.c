/*
 * fbbench.c — encoding methods on the real 68K framebuffer, timed in the guest.
 *
 * THINK C dialect (MacTraps only, no ANSI): InitGraf(&thePort), Toolbox file
 * calls, own number formatting. Built and run through the THINK Project
 * Manager over the bridge (project CalibExpand.π, this file as main.c).
 *
 * For each method: K iterations over the whole screen pixmap, output bytes and
 * TickCount() ticks, written as one line to fbbench.txt next to the project:
 *     <method>\t<outBytes>\t<ticks>\t<iters>\t<inBytes>\r
 * Ratios are hardware-independent; ticks are Basilisk (JIT) time.
 *
 * Methods (see docs/SCREENSHOT_V2.md, "Measured encoders"):
 *   copy        BlockMoveData of the frame               (the floor)
 *   rowcmp      row compare frame vs copy                (today's delta scan)
 *   packbits    Toolbox PackBits per row                 (what ships, enc 1)
 *   up+pb       row XOR row-above, then PackBits         (PNG "Up" predictor)
 *   rowdedup    identical-row references + PackBits rest
 *   xorprev+pb  row XOR previous frame, then PackBits    (enc 2 on a still frame)
 *   lzss        Okumura LZSS, raw frame
 *   up+lzss     Okumura LZSS on the Up-predicted frame
 *   half        2x2 nearest downsample (bytes only)
 *
 * The program yields between methods so the bridge daemon keeps running, and
 * quits by itself when the file is written.
 */

#define K_ITERS 20

static Ptr    gFrame;      /* copy of the screen, height*rowBytes */
static Ptr    gPrev;       /* second copy: "previous frame" for xorprev */
static Ptr    gOut;        /* output buffer, generously sized */
static Ptr    gTmp;        /* scratch: one predicted frame */
static long   gRowBytes, gHeight, gWidth, gSize;
static short  gRef;        /* fbbench.txt refnum */

static void Yield(void)
{
    EventRecord ev;
    WaitNextEvent(everyEvent, &ev, 1L, 0L);
}

/* ---- tiny formatting ------------------------------------------------- */

static void PutStr(const char *s)
{
    long n = 0;
    while (s[n]) n++;
    FSWrite(gRef, &n, (Ptr)s);
}

static void PutNum(long v)
{
    char buf[16];
    short i = 15;
    Boolean neg = v < 0;
    if (neg) v = -v;
    buf[i] = 0;
    do { buf[--i] = '0' + (v % 10); v /= 10; } while (v);
    if (neg) buf[--i] = '-';
    PutStr(buf + i);
}

static void Report(const char *name, long outBytes, long ticks, long iters)
{
    PutStr(name); PutStr("\t");
    PutNum(outBytes); PutStr("\t");
    PutNum(ticks); PutStr("\t");
    PutNum(iters); PutStr("\t");
    PutNum(gSize); PutStr("\r");
}

/* ---- capture --------------------------------------------------------- */

static Boolean Capture(void)
{
    GDHandle gd = GetMainDevice();
    PixMapHandle pm;
    Rect b;
    if (!gd) return false;
    pm = (*gd)->gdPMap;
    if (!pm) return false;
    b = (*pm)->bounds;
    gRowBytes = (*pm)->rowBytes & 0x3FFF;
    gWidth  = b.right - b.left;
    gHeight = b.bottom - b.top;
    gSize   = gHeight * gRowBytes;
    /* System heap, not the application partition: THINK C's default partition
     * is 384 KB and these four buffers are ~3.2 MB. Under System 7 the system
     * heap grows into free memory; measured 2026-08-30 — NewPtr here failed. */
    gFrame = NewPtrSys(gSize);
    gPrev  = NewPtrSys(gSize);
    gTmp   = NewPtrSys(gSize);
    gOut   = NewPtrSys(gSize + gSize / 8 + 4096);
    if (!gFrame || !gPrev || !gTmp || !gOut) return false;
    BlockMoveData((*pm)->baseAddr, gFrame, gSize);
    BlockMoveData(gFrame, gPrev, gSize);
    return true;
}

/* ---- encoders -------------------------------------------------------- */

static long PackRows(const unsigned char *src, long rows, long rb, unsigned char *dst)
{
    long y, total = 0;
    for (y = 0; y < rows; y++) {
        Ptr s = (Ptr)(src + y * rb);
        Ptr d = (Ptr)(dst + total + 2);
        long len;
        PackBits(&s, &d, (short)rb);
        len = (long)(d - (Ptr)(dst + total + 2));
        dst[total] = (unsigned char)(len >> 8);
        dst[total + 1] = (unsigned char)len;
        total += len + 2;
    }
    return total;
}

static void UpPredict(const unsigned char *src, unsigned char *dst)
{
    long y, x;
    for (x = 0; x < gRowBytes; x++) dst[x] = src[x];
    for (y = 1; y < gHeight; y++) {
        const unsigned char *r = src + y * gRowBytes;
        const unsigned char *u = r - gRowBytes;
        unsigned char *d = dst + y * gRowBytes;
        for (x = 0; x < gRowBytes; x++) d[x] = r[x] ^ u[x];
    }
}

static void XorPrev(const unsigned char *src, const unsigned char *prev, unsigned char *dst)
{
    long i;
    const long *a = (const long *)src, *b = (const long *)prev;
    long *d = (long *)dst;
    long n = gSize >> 2;
    for (i = 0; i < n; i++) d[i] = a[i] ^ b[i];
}

static long RowCompare(const unsigned char *a, const unsigned char *b)
{
    long y, i, changed = 0;
    long words = gRowBytes >> 2;
    for (y = 0; y < gHeight; y++) {
        const long *p = (const long *)(a + y * gRowBytes);
        const long *q = (const long *)(b + y * gRowBytes);
        for (i = 0; i < words; i++)
            if (p[i] != q[i]) { changed++; break; }
    }
    return changed;
}

/* rowdedup: FNV-1a hash per row; a row equal to one of the last 16 rows is
 * emitted as a 2-byte reference, otherwise PackBits'd. */
static long RowDedup(const unsigned char *src, unsigned char *dst)
{
    long y, k, total = 0;
    unsigned long hashes[16];
    for (y = 0; y < gHeight; y++) {
        const unsigned char *r = src + y * gRowBytes;
        unsigned long h = 2166136261UL;
        long x;
        Boolean found = false;
        for (x = 0; x < gRowBytes; x++) { h ^= r[x]; h *= 16777619UL; }
        for (k = 1; k <= 16 && k <= y; k++) {
            if (hashes[(y - k) & 15] == h) {
                const long *p = (const long *)r;
                const long *q = (const long *)(r - k * gRowBytes);
                long i, w = gRowBytes >> 2;
                for (i = 0; i < w; i++) if (p[i] != q[i]) break;
                if (i == w) { found = true; break; }
            }
        }
        hashes[y & 15] = h;
        if (found) {
            dst[total++] = 0xFF; dst[total++] = (unsigned char)k;   /* ref: 2 bytes */
        } else {
            Ptr s = (Ptr)r, d = (Ptr)(dst + total + 2);
            long len;
            PackBits(&s, &d, (short)gRowBytes);
            len = (long)(d - (Ptr)(dst + total + 2));
            dst[total] = (unsigned char)(len >> 8);
            dst[total + 1] = (unsigned char)len;
            total += len + 2;
        }
    }
    return total;
}

/* Okumura LZSS (1989, public domain), encoder only, 4 KB window, 18-byte match. */
#define N        4096
#define F        18
#define THRESHOLD 2
#define NIL      N

static unsigned char text_buf[N + F - 1];
static short match_position, match_length;
static short lson[N + 1], rson[N + 257], dad[N + 1];

static void InitTree(void)
{
    short i;
    for (i = N + 1; i <= N + 256; i++) rson[i] = NIL;
    for (i = 0; i < N; i++) dad[i] = NIL;
}

static void InsertNode(short r)
{
    short i, p, cmp;
    unsigned char *key;
    cmp = 1; key = &text_buf[r]; p = N + 1 + key[0];
    rson[r] = lson[r] = NIL; match_length = 0;
    for (;;) {
        if (cmp >= 0) {
            if (rson[p] != NIL) p = rson[p];
            else { rson[p] = r; dad[r] = p; return; }
        } else {
            if (lson[p] != NIL) p = lson[p];
            else { lson[p] = r; dad[r] = p; return; }
        }
        for (i = 1; i < F; i++)
            if ((cmp = key[i] - text_buf[p + i]) != 0) break;
        if (i > match_length) {
            match_position = p;
            if ((match_length = i) >= F) break;
        }
    }
    dad[r] = dad[p]; lson[r] = lson[p]; rson[r] = rson[p];
    dad[lson[p]] = r; dad[rson[p]] = r;
    if (rson[dad[p]] == p) rson[dad[p]] = r; else lson[dad[p]] = r;
    dad[p] = NIL;
}

static void DeleteNode(short p)
{
    short q;
    if (dad[p] == NIL) return;
    if (rson[p] == NIL) q = lson[p];
    else if (lson[p] == NIL) q = rson[p];
    else {
        q = lson[p];
        if (rson[q] != NIL) {
            do { q = rson[q]; } while (rson[q] != NIL);
            rson[dad[q]] = lson[q]; dad[lson[q]] = dad[q];
            lson[q] = lson[p]; dad[lson[p]] = q;
        }
        rson[q] = rson[p]; dad[rson[p]] = q;
    }
    dad[q] = dad[p];
    if (rson[dad[p]] == p) rson[dad[p]] = q; else lson[dad[p]] = q;
    dad[p] = NIL;
}

static long Lzss(const unsigned char *src, long srcLen, unsigned char *dst)
{
    short i, c, len, r, s, last_match_length, code_buf_ptr;
    unsigned char code_buf[17], mask;
    long inPos = 0, outPos = 0;

    InitTree();
    code_buf[0] = 0; code_buf_ptr = mask = 1;
    s = 0; r = N - F;
    for (i = s; i < r; i++) text_buf[i] = ' ';
    for (len = 0; len < F && inPos < srcLen; len++) text_buf[r + len] = src[inPos++];
    if (len == 0) return 0;
    for (i = 1; i <= F; i++) InsertNode(r - i);
    InsertNode(r);
    do {
        if (match_length > len) match_length = len;
        if (match_length <= THRESHOLD) {
            match_length = 1;
            code_buf[0] |= mask;
            code_buf[code_buf_ptr++] = text_buf[r];
        } else {
            code_buf[code_buf_ptr++] = (unsigned char)match_position;
            code_buf[code_buf_ptr++] = (unsigned char)(((match_position >> 4) & 0xF0)
                                                        | (match_length - (THRESHOLD + 1)));
        }
        if ((mask <<= 1) == 0) {
            for (i = 0; i < code_buf_ptr; i++) dst[outPos++] = code_buf[i];
            code_buf[0] = 0; code_buf_ptr = mask = 1;
        }
        last_match_length = match_length;
        for (i = 0; i < last_match_length && inPos < srcLen; i++) {
            c = src[inPos++];
            DeleteNode(s);
            text_buf[s] = (unsigned char)c;
            if (s < F - 1) text_buf[s + N] = (unsigned char)c;
            s = (s + 1) & (N - 1); r = (r + 1) & (N - 1);
            InsertNode(r);
        }
        while (i++ < last_match_length) {
            DeleteNode(s);
            s = (s + 1) & (N - 1); r = (r + 1) & (N - 1);
            if (--len) InsertNode(r);
        }
    } while (len > 0);
    if (code_buf_ptr > 1)
        for (i = 0; i < code_buf_ptr; i++) dst[outPos++] = code_buf[i];
    return outPos;
}

static long Half(const unsigned char *src, unsigned char *dst)
{
    long y, x, total = 0;
    for (y = 0; y < gHeight; y += 2) {
        const unsigned char *r = src + y * gRowBytes;
        for (x = 0; x < gWidth; x += 2) dst[total++] = r[x];
    }
    return total;
}

/* ---- driver ---------------------------------------------------------- */

static void RunMethod(const char *name, long (*fn)(long), long iters)
{
    long t0, t1, k, out = 0;
    Yield();
    t0 = TickCount();
    for (k = 0; k < iters; k++) out = fn(k);
    t1 = TickCount();
    Report(name, out, t1 - t0, iters);
}

static long M_copy(long k)     { BlockMoveData(gFrame, gTmp, gSize); return gSize; }
static long M_rowcmp(long k)   { return RowCompare((unsigned char *)gFrame, (unsigned char *)gPrev); }
static long M_packbits(long k) { return PackRows((unsigned char *)gFrame, gHeight, gRowBytes, (unsigned char *)gOut); }
static long M_uppb(long k)     { UpPredict((unsigned char *)gFrame, (unsigned char *)gTmp);
                                 return PackRows((unsigned char *)gTmp, gHeight, gRowBytes, (unsigned char *)gOut); }
static long M_rowdedup(long k) { return RowDedup((unsigned char *)gFrame, (unsigned char *)gOut); }
static long M_xorprev(long k)  { XorPrev((unsigned char *)gFrame, (unsigned char *)gPrev, (unsigned char *)gTmp);
                                 return PackRows((unsigned char *)gTmp, gHeight, gRowBytes, (unsigned char *)gOut); }
static long M_lzss(long k)     { return Lzss((unsigned char *)gFrame, gSize, (unsigned char *)gOut); }
static long M_uplzss(long k)   { UpPredict((unsigned char *)gFrame, (unsigned char *)gTmp);
                                 return Lzss((unsigned char *)gTmp, gSize, (unsigned char *)gOut); }
static long M_half(long k)     { return Half((unsigned char *)gFrame, (unsigned char *)gOut); }

void main(void)
{
    Str255 name = "\pfbbench.txt";
    OSErr err;

    InitGraf(&thePort);
    InitFonts(); InitWindows(); InitMenus(); TEInit(); InitDialogs(0L);
    InitCursor();
    Yield();

    FSDelete(name, 0);
    err = Create(name, 0, 'ttxt', 'TEXT');
    if (err != noErr && err != dupFNErr) return;
    if (FSOpen(name, 0, &gRef) != noErr) return;

    if (!Capture()) {
        PutStr("capture failed: NewPtrSys "); PutNum(gSize); PutStr(" x4 -> MemError "); PutNum(MemError()); PutStr("\r");
        FSClose(gRef); return;
    }
    PutStr("frame\t"); PutNum(gWidth); PutStr("x"); PutNum(gHeight);
    PutStr("\trowBytes="); PutNum(gRowBytes); PutStr("\tbytes="); PutNum(gSize); PutStr("\r");

    RunMethod("copy",       M_copy,     K_ITERS);
    RunMethod("rowcmp",     M_rowcmp,   K_ITERS);
    RunMethod("packbits",   M_packbits, K_ITERS);
    RunMethod("up+pb",      M_uppb,     K_ITERS);
    RunMethod("rowdedup",   M_rowdedup, K_ITERS);
    RunMethod("xorprev+pb", M_xorprev,  K_ITERS);
    RunMethod("half",       M_half,     K_ITERS);
    RunMethod("lzss",       M_lzss,     1);
    RunMethod("up+lzss",    M_uplzss,   1);
    PutStr("done\r");
    FSClose(gRef);
    FlushVol(0L, 0);
    DisposePtr(gFrame); DisposePtr(gPrev); DisposePtr(gTmp); DisposePtr(gOut);
}
