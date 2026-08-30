/*
 * AppleBridge - Screenshot Capture
 * Capture screen using QuickDraw
 */

#include <applebridge.h>
#include <QuickDraw.h>
#include <QDOffscreen.h>
#include <Memory.h>

/* External status function from main.c */
extern void StatusMessage(const char *msg);

/* Refuse anything bigger than this (keeps us inside the app partition). */
#define MAX_SHOT_BYTES (4L * 1024L * 1024L)

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
    screenshot->data = NewPtr(imageSize);
    if (screenshot->data == NULL) {
        StatusMessage("FAIL: NewPtr screenshot");
        return kBridgeCommandErr;
    }

    StatusMessage("Copying screen pixels...");
    BlockMoveData((*pm)->baseAddr, screenshot->data, imageSize);
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
        DisposePtr(screenshot->data);
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
    cur = NewPtr(full ? fullSize : bandSize);
    if (cur == NULL) { StatusMessage("FAIL: NewPtr screenshot"); return kBridgeCommandErr; }
    BlockMoveData((*pm)->baseAddr + (full ? 0 : (long)ry * rowBytes), cur, full ? fullSize : bandSize);
    base = (unsigned char *)cur + (full ? (long)ry * rowBytes : 0);

    wantDelta = full && (flags & SHOT2_DELTA) != 0 && gPrevFrame != NULL
                && baseGen == gGen && gPrevW == width && gPrevH == height
                && gPrevDepth == depth && gPrevRB == rowBytes;

    if (wantDelta) pack = true;      /* enc 2 is always packed rows: one host parser */
    emit = pack ? PackRow : RawRow;

    /* Output: worst case PackBits grows a row by 1/128 + 1; plus 2 bytes per
       row and 4 per delta run. */
    outCap = (long)rh * (subRB + subRB / 64 + 8) + 64;
    out = (unsigned char *)NewPtr(outCap);
    if (out == NULL) { DisposePtr(cur); StatusMessage("FAIL: NewPtr shot buffer"); return kBridgeCommandErr; }
    if (wantDelta) {
        xrow = (unsigned char *)NewPtr(rowBytes);
        if (xrow == NULL) { DisposePtr(cur); DisposePtr((Ptr)out); StatusMessage("FAIL: NewPtr xor row"); return kBridgeCommandErr; }
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
    } else {
        for (y = 0; y < rh; y++)
            pos += emit(base + y * rowBytes + byteOff, out + pos, subRB);
        s->enc = pack ? 1 : 0;
    }

    /* Retain a full frame as the base for the next delta; a region is not one. */
    if (full) {
        if (gPrevFrame != NULL) DisposePtr(gPrevFrame);
        gPrevFrame = cur;  cur = NULL;
        gPrevSize = fullSize; gPrevW = width; gPrevH = height;
        gPrevDepth = depth; gPrevRB = rowBytes;
        gGen++;
    }
    if (cur != NULL) DisposePtr(cur);
    if (xrow != NULL) DisposePtr((Ptr)xrow);

    s->gen = gGen;
    s->data = (Ptr)out;
    s->dataSize = pos;
    return kBridgeNoErr;
}
