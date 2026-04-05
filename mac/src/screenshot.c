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

/*
 * Capture screenshot of main screen
 */
BridgeResult CaptureScreenshot(ScreenshotData *screenshot)
{
    /*
     * Direct screenBits capture - no GWorld needed
     */
    BitMap *screen;
    Rect bounds;
    short width, height;
    long rowBytes, imageSize;

    StatusMessage("Getting screenBits...");

    /* Get screen bitmap directly */
    screen = &qd.screenBits;
    bounds = screen->bounds;

    width = bounds.right - bounds.left;
    height = bounds.bottom - bounds.top;
    rowBytes = screen->rowBytes & 0x3FFF;
    imageSize = (long)height * rowBytes;

    StatusMessage("Allocating memory...");

    /* Allocate memory for copy */
    screenshot->data = NewPtr(imageSize);
    if (screenshot->data == NULL) {
        StatusMessage("FAIL: NewPtr - trying smaller");
        /* Try a smaller portion - just top 100 lines */
        height = 100;
        imageSize = (long)height * rowBytes;
        screenshot->data = NewPtr(imageSize);
        if (screenshot->data == NULL) {
            StatusMessage("FAIL: Still no memory");
            return kBridgeCommandErr;
        }
    }

    StatusMessage("Copying screen data...");

    /* Copy directly from screen memory */
    BlockMoveData(screen->baseAddr, screenshot->data, imageSize);

    screenshot->width = width;
    screenshot->height = height;
    screenshot->dataSize = imageSize;

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
}
