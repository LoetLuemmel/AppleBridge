/*
 * AppleBridge Control Panel - cdev proof of concept.
 *
 * Step 1: macDev/initDev + a statText (the host calls us; the DITL draws).
 * Step 2: a pushButton dispatched through hitDev. Clicking it:
 *   - beeps (audible proof the click reached our hitDev handler),
 *   - increments a counter kept in the cdevValue HANDLE (proves handle-based
 *     state — no globals), and
 *   - shows the count in a statText via SetDialogItemText (proves we can update
 *     a DITL item from a cdev, with the required windowKind juggle).
 *
 * Strictly self-contained and A4-free, by necessity for a code resource:
 *   - no globals/statics (state is in the handle),
 *   - no string literals in code (label text is in the DITL; the number is
 *     formatted inline),
 *   - no calls to glue routines or helper functions — only INLINE Toolbox traps.
 *     (NumToString is glue; in far model its 32-bit call can't be relocated in a
 *     code resource — "Linker does not edit 32-bit instructions" — so we format
 *     the count by hand with 16-bit arithmetic, which the 68k divides in hardware.)
 *
 * Item numbering: our DITL items are appended after the Control Panel's own, so a
 * click on our item N arrives as hitDev with (item == numItems + N).
 */

#include <Types.h>
#include <Quickdraw.h>
#include <Windows.h>
#include <Dialogs.h>
#include <Events.h>
#include <Memory.h>
#include <Sound.h>          /* SysBeep (moved here from OSUtils.h) */

#define macDev    8
#define initDev   0
#define hitDev    1
#define closeDev  2

/* our DITL items, 1-based within our own DITL */
enum { kLabel = 1, kButton, kCount };

pascal long CDevMain(short message, short item, short numItems, short rsrcID,
                     EventRecord *event, long cdevValue, DialogPtr cpDialog)
{
#pragma unused(rsrcID, event)
    switch (message) {
        case macDev:
            return 1;                              /* appear on this machine */

        case initDev: {
            Handle h = NewHandle(sizeof(short));   /* our private storage */
            if (h) *(short *)(*h) = 0;             /* click count = 0 */
            return (long) h;                       /* becomes cdevValue */
        }

        case hitDev:
            if (item - numItems == kButton) {
                Handle  h     = (Handle) cdevValue;
                short   count = *(short *)(*h);     /* read count from handle */
                short   type, saveKind, k, lim;
                Handle  ih;
                Rect    box;
                Str255  buf;

                count++;                            /* update + write back now */
                *(short *)(*h) = count;

                SysBeep(5);                         /* audible: the click arrived */

                /* Show one '*' per click. No '/' or '%' (those call glue routines
                 * a code resource can't reach) — just a fill loop, fully inline. */
                lim = (count > 200) ? 200 : count;
                buf[0] = (unsigned char) lim;
                for (k = 1; k <= lim; k++) buf[k] = '*';

                /* Update the count statText. A cdev's window has the Control Panel's
                 * windowKind, not dialogKind, so SetDialogItemText needs a brief juggle. */
                GetDialogItem(cpDialog, numItems + kCount, &type, &ih, &box);
                saveKind = ((WindowPeek) cpDialog)->windowKind;
                ((WindowPeek) cpDialog)->windowKind = dialogKind;
                SetDialogItemText(ih, buf);
                ((WindowPeek) cpDialog)->windowKind = saveKind;
            }
            return cdevValue;                       /* carry storage forward */

        case closeDev:
            if (cdevValue) DisposeHandle((Handle) cdevValue);
            return 0;

        default:
            return cdevValue;
    }
}
