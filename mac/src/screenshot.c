/*
 * AppleBridge - Screenshot Capture
 * Capture screen using QuickDraw
 */

#include "applebridge.h"
#include <QuickDraw.h>
#include <QDOffscreen.h>
#include <Memory.h>

/*
 * Capture screenshot of main screen
 */
BridgeResult CaptureScreenshot(ScreenshotData *screenshot)
{
    GDHandle mainDevice;
    PixMapHandle pixMap;
    Rect bounds;
    GWorldPtr offscreenGWorld;
    OSErr err;
    Ptr baseAddr;
    long rowBytes;
    short width, height;
    long imageSize;

    LogMessage("Capturing screenshot...");

    /* Get main screen device */
    mainDevice = GetMainDevice();
    if (mainDevice == NULL) {
        LogMessage("Failed to get main device");
        return kBridgeCommandErr;
    }

    /* Get screen bounds */
    pixMap = (**mainDevice).gdPMap;
    bounds = (**pixMap).bounds;

    width = bounds.right - bounds.left;
    height = bounds.bottom - bounds.top;

    LogMessage("Screen size: width x height");

    /* Create offscreen GWorld for copying */
    err = NewGWorld(&offscreenGWorld, 32, &bounds, NULL, NULL, 0);
    if (err != noErr) {
        LogError("Failed to create offscreen GWorld", err);
        return kBridgeCommandErr;
    }

    /* Copy screen to offscreen buffer */
    {
        CGrafPtr savedPort;
        GDHandle savedDevice;
        GWorldFlags flags;

        GetGWorld(&savedPort, &savedDevice);
        SetGWorld(offscreenGWorld, NULL);

        /* Lock pixels */
        flags = LockPixels(GetGWorldPixMap(offscreenGWorld));
        if (!flags) {
            LogMessage("Failed to lock pixels");
            DisposeGWorld(offscreenGWorld);
            SetGWorld(savedPort, savedDevice);
            return kBridgeCommandErr;
        }

        /* Copy screen */
        CopyBits((BitMap *)*pixMap,
                 (BitMap *)*GetGWorldPixMap(offscreenGWorld),
                 &bounds, &bounds,
                 srcCopy, NULL);

        /* Get pixel data */
        pixMap = GetGWorldPixMap(offscreenGWorld);
        baseAddr = GetPixBaseAddr(pixMap);
        rowBytes = (**pixMap).rowBytes & 0x3FFF;

        /* Calculate image size (simplified - just raw RGB data) */
        imageSize = (long)height * rowBytes;

        /* Allocate memory for image data */
        screenshot->data = NewPtr(imageSize);
        if (screenshot->data == NULL) {
            LogMessage("Failed to allocate screenshot memory");
            UnlockPixels(pixMap);
            DisposeGWorld(offscreenGWorld);
            SetGWorld(savedPort, savedDevice);
            return kBridgeCommandErr;
        }

        /* Copy pixel data */
        BlockMoveData(baseAddr, screenshot->data, imageSize);

        screenshot->width = width;
        screenshot->height = height;
        screenshot->dataSize = imageSize;

        /* Clean up */
        UnlockPixels(pixMap);
        SetGWorld(savedPort, savedDevice);
    }

    DisposeGWorld(offscreenGWorld);

    LogMessage("Screenshot captured successfully");

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
