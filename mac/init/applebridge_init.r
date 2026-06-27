/*
 * AppleBridge INIT resources: the boot icon ('ICN#' 128).
 * A 32x32 black box frame with a horizontal "bridge deck" bar through the middle.
 * Two bitmaps, 128 bytes each: [0..127] = icon, [128..255] = mask (a filled
 * square, so PlotIcon defines the whole 32x32 area).
 */

data 'ICN#' (128, "AppleBridge") {
    /* ---- icon (32 rows x 32 bits) ---- */
    $"FFFFFFFF FFFFFFFF FFFFFFFF"          /* rows  0-2 : top edge   */
    $"E0000007 E0000007 E0000007 E0000007" /* rows  3-6 : sides      */
    $"E0000007 E0000007 E0000007 E0000007" /* rows  7-10            */
    $"E0000007 E0000007 E0000007 E0000007" /* rows 11-14            */
    $"FFFFFFFF FFFFFFFF FFFFFFFF"          /* rows 15-17: bridge deck */
    $"E0000007 E0000007 E0000007 E0000007" /* rows 18-21: sides      */
    $"E0000007 E0000007 E0000007 E0000007" /* rows 22-25            */
    $"E0000007 E0000007 E0000007"          /* rows 26-28            */
    $"FFFFFFFF FFFFFFFF FFFFFFFF"          /* rows 29-31: bottom edge */
    /* ---- mask (filled 32x32) ---- */
    $"FFFFFFFF FFFFFFFF FFFFFFFF FFFFFFFF"
    $"FFFFFFFF FFFFFFFF FFFFFFFF FFFFFFFF"
    $"FFFFFFFF FFFFFFFF FFFFFFFF FFFFFFFF"
    $"FFFFFFFF FFFFFFFF FFFFFFFF FFFFFFFF"
    $"FFFFFFFF FFFFFFFF FFFFFFFF FFFFFFFF"
    $"FFFFFFFF FFFFFFFF FFFFFFFF FFFFFFFF"
    $"FFFFFFFF FFFFFFFF FFFFFFFF FFFFFFFF"
    $"FFFFFFFF FFFFFFFF FFFFFFFF FFFFFFFF"
};
