/*
 * AppleBridge - Screenshot Capture
 * Capture screen using QuickDraw
 */

#include <applebridge.h>
#include <QuickDraw.h>
#include <QDOffscreen.h>
#include <Memory.h>
#include <OSUtils.h>     /* GetMMUMode, SwapMMUMode, StripAddress */

/* External status function from main.c */
extern void StatusMessage(const char *msg);

/* Refuse anything bigger than this (keeps us inside the app partition). */
#define MAX_SHOT_BYTES (4L * 1024L * 1024L)

/* ---- big buffers: temporary memory first (diet, 0.8d50) -----------------
 * Measured on dev2019 (2026-11-21): idle the daemon uses ~120 K of its heap; one
 * full screenshot leaves 886 K in use (the retained previous frame is a whole
 * screen) and a delta capture peaks at 2,454 K -- three screens at once. On a
 * 1152x870 PowerBook Duo that is ~3 MB, which is why its partition needed 4 MB.
 * These buffers now come from TEMPORARY memory (TempNewHandle): free memory
 * OUTSIDE the partition that the Process Manager lends out, locked while held.
 * When there is none, the daemon's own heap, as before. ShotFree() knows which
 * was which. The small per-row scratch stays a plain NewPtr. */
#define kShotSlots 6
static Ptr    gShotP[kShotSlots];
static Handle gShotH[kShotSlots];

static Ptr ShotAlloc(long size)
{
    OSErr  e;
    Handle h;
    short  i;

    for (i = 0; i < kShotSlots; i++) if (gShotP[i] == NULL) break;
    if (i < kShotSlots) {
        h = TempNewHandle((Size)size, &e);
        if (h != NULL && e == noErr) {
            HLock(h);
            gShotH[i] = h;
            gShotP[i] = *h;
            return *h;
        }
    }
    return NewPtr(size);                  /* no temporary memory: the old way */
}

static void ShotFree(Ptr p)
{
    short i;
    if (p == NULL) return;
    for (i = 0; i < kShotSlots; i++)
        if (gShotP[i] == p) {
            DisposeHandle(gShotH[i]);
            gShotP[i] = NULL; gShotH[i] = NULL;
            return;
        }
    DisposePtr(p);
}


/* ---- reading the framebuffer on real hardware (0.8d54) ------------------
 * On a real Mac running in 24-bit mode a NuBus/PDS card's framebuffer -- the
 * Duo Dock's 1152x870 screen -- lives at a full 32-bit address ($Fsxxxxxx).
 * QuickDraw marks such a PixMap with pmVersion = baseAddr32 (4); reading it
 * straight in 24-bit mode drops the high byte and copies ordinary RAM instead:
 * colour noise of exactly the right size (PowerBook Duo, 2026-11-24). Inside
 * Macintosh: Imaging, "PixMap": switch to 32-bit mode for the access. The
 * destination is a dereferenced handle whose master pointer may carry 24-bit
 * flag bits, so it is stripped first. Emulators run 32-bit clean, which is why
 * no Basilisk guest ever showed this. */
#define kBaseAddr32 4
static void CopyScreen(PixMapHandle pm, long off, Ptr dst, long n)
{
    Ptr  src  = (*pm)->baseAddr + off;
    signed char mode = true32b;   /* MPW: SignedByte */
    Boolean swap = GetMMUMode() == false32b &&
                   (((*pm)->pmVersion & kBaseAddr32) != 0 ||
                    ((unsigned long)(*pm)->baseAddr & 0xFF000000UL) != 0);
    if (swap) {
        dst = StripAddress(dst);
        SwapMMUMode(&mode);
    }
    BlockMoveData(src, dst, n);
    if (swap) SwapMMUMode(&mode);
}

/* Console line: where the pixels came from and how they were read. Once per
   distinct framebuffer, so a delta loop does not fill the console. */
static void NoteScreenSource(PixMapHandle pm)
{
    static unsigned long lastBase = 1;
    static const char hx[] = "0123456789ABCDEF";
    char msg[64], *p = msg;
    const char *t;
    unsigned long b = (unsigned long)(*pm)->baseAddr;
    short i;
    if (b == lastBase) return;
    lastBase = b;
    for (t = "screen base $"; *t; ) *p++ = *t++;
    for (i = 7; i >= 0; i--) *p++ = hx[(b >> (i * 4)) & 0xF];
    for (t = " pmVersion "; *t; ) *p++ = *t++;
    *p++ = hx[(*pm)->pmVersion & 0xF];
    for (t = GetMMUMode() == false32b ? " MMU 24-bit" : " MMU 32-bit"; *t; ) *p++ = *t++;
    *p = 0;
    StatusMessage(msg);
}


/*
 * Capture the main screen from its GDevice PixMap.
 *
 * Using the PixMap (not qd.screenBits, which is a depth-less BitMap) gives us
 * the pixel depth and, for indexed depths, the colour table — everything the
 * host needs to decode the raw pixels to PNG. The pixels are copied straight
 * from the screen base address into a dynamically allocated buffer.
 */
BridgeResult CaptureScreenshot(ScreenshotData *screenshot)
{
    GDHandle gd;
    PixMapHandle pm;
    CTabHandle ct;
    Rect bounds;
    short depth, i, n;
    long rowBytes, imageSize;

    screenshot->data = NULL;
    screenshot->dataSize = 0;
    screenshot->clutCount = 0;

    StatusMessage("Getting main device...");
    gd = GetMainDevice();
    if (gd == NULL) {
        StatusMessage("No main GDevice");
        return kBridgeCommandErr;
    }
    pm = (*gd)->gdPMap;
    if (pm == NULL) {
        StatusMessage("No PixMap");
        return kBridgeCommandErr;
    }

    bounds   = (*pm)->bounds;
    depth    = (*pm)->pixelSize;
    rowBytes = (*pm)->rowBytes & 0x3FFF;

    screenshot->width    = bounds.right - bounds.left;
    screenshot->height   = bounds.bottom - bounds.top;
    screenshot->depth    = depth;
    screenshot->rowBytes = rowBytes;

    imageSize = (long)screenshot->height * rowBytes;
    if (imageSize <= 0 || imageSize > MAX_SHOT_BYTES) {
        StatusMessage("Screen too large to capture");
        return kBridgeCommandErr;
    }

    StatusMessage("Allocating screenshot buffer...");
    screenshot->data = ShotAlloc(imageSize);
    NoteHeap();
    if (screenshot->data == NULL) {
        StatusMessage("FAIL: NewPtr screenshot");
        return kBridgeCommandErr;
    }

    StatusMessage("Copying screen pixels...");
    NoteScreenSource(pm);
    CopyScreen(pm, 0L, screenshot->data, imageSize);
    screenshot->dataSize = imageSize;

    /* Colour table for indexed depths (<= 8 bpp). */
    if (depth <= 8) {
        ct = (*pm)->pmTable;
        if (ct != NULL && *ct != NULL) {
            n = (*ct)->ctSize + 1;       /* ctSize holds (count - 1) */
            if (n < 0) n = 0;
            if (n > 256) n = 256;
            for (i = 0; i < n; i++) {
                screenshot->clut[i * 3 + 0] = (unsigned char)((*ct)->ctTable[i].rgb.red   >> 8);
                screenshot->clut[i * 3 + 1] = (unsigned char)((*ct)->ctTable[i].rgb.green >> 8);
                screenshot->clut[i * 3 + 2] = (unsigned char)((*ct)->ctTable[i].rgb.blue  >> 8);
            }
            screenshot->clutCount = n;
        }
    }

    StatusMessage("Screenshot captured!");
    return kBridgeNoErr;
}

/*
 * Clean up screenshot data
 */
void CleanupScreenshot(ScreenshotData *screenshot)
{
    if (screenshot->data != NULL) {
        ShotFree(screenshot->data);
        screenshot->data = NULL;
    }
    screenshot->width = 0;
    screenshot->height = 0;
    screenshot->dataSize = 0;
    screenshot->clutCount = 0;
}

/* ------------------------------------------------------------------------
 * SCREENSHOT2 (0.8d46): a screenshot costs what it shows.
 *
 * The legacy verb streams the raw pixmap — 768 KB at 1024x768x8 — for every
 * look, and on the shipping slirp link (~200 KB/s, measured 2026-08-30) that
 * is ~4 s per capture for a picture the host then squeezes to ~30 KB. Three
 * things are done here, all where the pixels are, so every target (Basilisk,
 * SheepShaver, an SE/30 over RS-422) benefits:
 *
 *   region  — only the requested rectangle is copied and sent. For depths
 *             below 8 the crop is rows-only (rx forced to 0), the host slices
 *             the columns; at 8/16/32 bpp the columns are cropped here.
 *   PackBits — each row is packed with the Toolbox's PackBits (ROM, fast) and
 *             prefixed by its packed length (2 bytes, big-endian, PICT style).
 *             A System 7 desktop packs 10..20:1.
 *   up      — enc 3: each row is XORed with the row above it (PNG's "Up"
 *             filter) before PackBits. The System 7 desktop is a dither
 *             pattern with no horizontal runs, so PackBits alone packs it
 *             1.37:1; the same frame after the Up predictor packs 12.5:1
 *             (62.7 KB for 768 KB, measured in THINK C on 2026-08-30 at
 *             ~33 ms of 68K time against ~11 ms for PackBits alone).
 *   delta   — a full-screen capture is retained (gPrevFrame, one screen's
 *             worth of the app partition) with a generation number. When the
 *             host asks for the whole screen and names the generation it
 *             holds, only the rows that differ are sent, as runs of
 *             <y0:2><count:2> followed by packed rows — and each such row is
 *             XORed with its predecessor first, so the bytes that did not
 *             change become zero runs. Measured 2026-08-30: a plain row delta
 *             of the console logging one line moved 138 KB (PackBits gets
 *             1.33:1 on a dithered desktop); the XOR of the same rows packs
 *             to a few KB. Nothing moved -> a few bytes.
 *
 * A region capture does NOT touch the retained frame or the generation: the
 * host only ever received part of that screen, so it could not serve as a
 * base. The host, for its part, only advances its base on a full frame.
 * ------------------------------------------------------------------------ */

#include <ToolUtils.h>

#define SHOT2_PACK   1
#define SHOT2_DELTA  2
#define SHOT2_UP     4    /* enc 3: each row XOR the row above it, then PackBits */

static Ptr   gPrevFrame = NULL;   /* retained full screen, or NULL */
static long  gPrevSize  = 0;
static short gPrevW = 0, gPrevH = 0, gPrevDepth = 0;
static long  gPrevRB = 0;
static long  gGen = 0;            /* generation of gPrevFrame (0 = none) */

/* Pack one row: <len:2><PackBits bytes>. Returns bytes written to dst. */
static long PackRow(const unsigned char *src, unsigned char *dst, long n)
{
    Ptr s = (Ptr)src;
    Ptr d = (Ptr)(dst + 2);
    long len;

    PackBits(&s, &d, (short)n);
    len = (long)(d - (Ptr)(dst + 2));
    dst[0] = (unsigned char)((len >> 8) & 0xFF);
    dst[1] = (unsigned char)(len & 0xFF);
    return len + 2;
}

/* Copy one row unpacked, prefixed by nothing. */
static long RawRow(const unsigned char *src, unsigned char *dst, long n)
{
    BlockMoveData((Ptr)src, (Ptr)dst, n);
    return n;
}

static Boolean RowsDiffer(const unsigned char *a, const unsigned char *b, long n)
{
    const long *la = (const long *)a;
    const long *lb = (const long *)b;
    long words = n >> 2;
    long i;

    for (i = 0; i < words; i++)
        if (la[i] != lb[i]) return true;
    for (i = words << 2; i < n; i++)
        if (a[i] != b[i]) return true;
    return false;
}

static void FillClut(ScreenshotData *s, PixMapHandle pm)
{
    CTabHandle ct;
    short i, n;

    s->clutCount = 0;
    if (s->depth > 8) return;
    ct = (*pm)->pmTable;
    if (ct == NULL || *ct == NULL) return;
    n = (*ct)->ctSize + 1;
    if (n < 0) n = 0;
    if (n > 256) n = 256;
    for (i = 0; i < n; i++) {
        s->clut[i * 3 + 0] = (unsigned char)((*ct)->ctTable[i].rgb.red   >> 8);
        s->clut[i * 3 + 1] = (unsigned char)((*ct)->ctTable[i].rgb.green >> 8);
        s->clut[i * 3 + 2] = (unsigned char)((*ct)->ctTable[i].rgb.blue  >> 8);
    }
    s->clutCount = n;
}

BridgeResult CaptureScreenshot2(ScreenshotData *s, short rx, short ry,
                                short rw, short rh, short flags, long baseGen)
{
    GDHandle gd;
    PixMapHandle pm;
    Rect bounds;
    short depth, width, height;
    long rowBytes, fullSize, bandSize;
    Boolean full, wantDelta, pack;
    long subRB, byteOff, outCap, pos, y;
    Ptr cur = NULL;
    unsigned char *out = NULL;
    unsigned char *base;
    unsigned char *xrow = NULL;   /* scratch: row XOR previous row (delta only) */
    long (*emit)(const unsigned char *, unsigned char *, long);

    s->data = NULL;
    s->dataSize = 0;
    s->clutCount = 0;
    s->enc = 0;
    s->gen = gGen;

    gd = GetMainDevice();
    if (gd == NULL) { StatusMessage("No main GDevice"); return kBridgeCommandErr; }
    pm = (*gd)->gdPMap;
    if (pm == NULL) { StatusMessage("No PixMap"); return kBridgeCommandErr; }

    bounds   = (*pm)->bounds;
    depth    = (*pm)->pixelSize;
    rowBytes = (*pm)->rowBytes & 0x3FFF;
    width    = bounds.right - bounds.left;
    height   = bounds.bottom - bounds.top;
    fullSize = (long)height * rowBytes;
    if (fullSize <= 0 || fullSize > MAX_SHOT_BYTES) {
        StatusMessage("Screen too large to capture");
        return kBridgeCommandErr;
    }

    s->width = width; s->height = height; s->depth = depth; s->rowBytes = rowBytes;
    FillClut(s, pm);

    /* Resolve the rectangle. rw/rh of 0 = whole screen. Clamp to the screen;
       an empty result is an error, not an empty success. */
    full = (rw <= 0 || rh <= 0);
    if (full) { rx = 0; ry = 0; rw = width; rh = height; }
    if (rx < 0) rx = 0;
    if (ry < 0) ry = 0;
    if (rx >= width || ry >= height) { StatusMessage("Region off screen"); return kBridgeCommandErr; }
    if (rx + rw > width)  rw = width - rx;
    if (ry + rh > height) rh = height - ry;
    if (depth < 8) { rx = 0; rw = width; }      /* rows only below 8 bpp */
    full = (rx == 0 && ry == 0 && rw == width && rh == height);
    s->rx = rx; s->ry = ry; s->rw = rw; s->rh = rh;

    subRB   = ((long)rw * depth + 7) / 8;
    byteOff = ((long)rx * depth) / 8;
    pack    = (flags & SHOT2_PACK) != 0;

    /* Copy the band (or the whole screen) out of the framebuffer first, so the
       encoding below reads a still image rather than one the guest is drawing. */
    bandSize = (long)rh * rowBytes;
    cur = ShotAlloc(full ? fullSize : bandSize);
    if (cur == NULL) { StatusMessage("FAIL: NewPtr screenshot"); return kBridgeCommandErr; }
    NoteScreenSource(pm);
    CopyScreen(pm, full ? 0L : (long)ry * rowBytes, cur, full ? fullSize : bandSize);
    base = (unsigned char *)cur + (full ? (long)ry * rowBytes : 0);

    wantDelta = full && (flags & SHOT2_DELTA) != 0 && gPrevFrame != NULL
                && baseGen == gGen && gPrevW == width && gPrevH == height
                && gPrevDepth == depth && gPrevRB == rowBytes;

    if (wantDelta) pack = true;      /* enc 2 is always packed rows: one host parser */
    emit = pack ? PackRow : RawRow;
    if (!wantDelta && pack && (flags & SHOT2_UP)) {
        xrow = (unsigned char *)NewPtr(subRB);
        if (xrow == NULL) { ShotFree(cur); StatusMessage("FAIL: NewPtr up row"); return kBridgeCommandErr; }
    }

    /* Output: worst case PackBits grows a row by 1/128 + 1; plus 2 bytes per
       row and 4 per delta run. */
    outCap = (long)rh * (subRB + subRB / 64 + 8) + 64;
    out = (unsigned char *)ShotAlloc(outCap);
    NoteHeap();   /* current frame + output (+ the retained previous frame) are all live */
    if (out == NULL) { ShotFree(cur); StatusMessage("FAIL: NewPtr shot buffer"); return kBridgeCommandErr; }
    if (wantDelta) {
        xrow = (unsigned char *)NewPtr(rowBytes);
        if (xrow == NULL) { ShotFree(cur); ShotFree((Ptr)out); StatusMessage("FAIL: NewPtr xor row"); return kBridgeCommandErr; }
    }

    pos = 0;
    if (wantDelta) {
        long runStart = -1;    /* byte offset of the open run's header, or -1 */
        long runCount = 0;
        const unsigned char *prev = (const unsigned char *)gPrevFrame;

        for (y = 0; y < height; y++) {
            const unsigned char *row = base + y * rowBytes;
            const unsigned char *old = prev + y * rowBytes;
            if (RowsDiffer(row, old, rowBytes)) {
                long b;
                if (runStart < 0) {
                    runStart = pos;
                    out[pos++] = (unsigned char)((y >> 8) & 0xFF);
                    out[pos++] = (unsigned char)(y & 0xFF);
                    out[pos++] = 0; out[pos++] = 0;
                    runCount = 0;
                }
                for (b = 0; b < rowBytes; b++) xrow[b] = row[b] ^ old[b];
                pos += emit(xrow, out + pos, rowBytes);
                runCount++;
            } else if (runStart >= 0) {
                out[runStart + 2] = (unsigned char)((runCount >> 8) & 0xFF);
                out[runStart + 3] = (unsigned char)(runCount & 0xFF);
                runStart = -1;
            }
        }
        if (runStart >= 0) {
            out[runStart + 2] = (unsigned char)((runCount >> 8) & 0xFF);
            out[runStart + 3] = (unsigned char)(runCount & 0xFF);
        }
        s->enc = 2;
    } else if (xrow != NULL) {
        /* enc 3: first row as is, every other row XOR its predecessor */
        pos += emit(base + byteOff, out + pos, subRB);
        for (y = 1; y < rh; y++) {
            const unsigned char *row = base + y * rowBytes + byteOff;
            const unsigned char *up  = row - rowBytes;
            long b;
            for (b = 0; b < subRB; b++) xrow[b] = row[b] ^ up[b];
            pos += emit(xrow, out + pos, subRB);
        }
        s->enc = 3;
    } else {
        for (y = 0; y < rh; y++)
            pos += emit(base + y * rowBytes + byteOff, out + pos, subRB);
        s->enc = pack ? 1 : 0;
    }

    /* Retain a full frame as the base for the next delta; a region is not one. */
    if (full) {
        if (gPrevFrame != NULL) ShotFree(gPrevFrame);
        gPrevFrame = cur;  cur = NULL;
        gPrevSize = fullSize; gPrevW = width; gPrevH = height;
        gPrevDepth = depth; gPrevRB = rowBytes;
        gGen++;
    }
    if (cur != NULL) ShotFree(cur);
    if (xrow != NULL) DisposePtr((Ptr)xrow);

    s->gen = gGen;
    s->data = (Ptr)out;
    s->dataSize = pos;
    return kBridgeNoErr;
}
