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
