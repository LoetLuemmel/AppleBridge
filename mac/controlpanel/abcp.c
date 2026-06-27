/*
 * AppleBridge Control Panel - cdev proof of concept.
 *
 * Step 1: macDev/initDev + a statText (the host calls us; the DITL draws).
 * Step 2: a pushButton dispatched through hitDev (beep + handle-counted tally).
 * Step 3: pop the modal Standard File picker FROM hitDev, then show the chosen
 *   file's name in a statText. This is the one real unknown of the port — a
 *   nested modal dialog driven from a Control Panel message — and it works.
 *
 *   Code-resource gotcha (the same rule as every other step): a cdev can only
 *   reach INLINE Toolbox traps, never glue. StandardGetFile (the FSSpec call) is
 *   GLUE — it massages params and calls the package — so it can't be linked into
 *   a code resource. SFGetFile is the inline-trap form ({0x3F3C,0x0002,0xA9EA} =
 *   push selector 2, _Pack3), so we use it; its SFReply carries the old
 *   vRefNum + Str63 name, which is all the PoC needs.
 *
 * Strictly self-contained and A4-free, by necessity for a code resource:
 *   - no globals/statics (state is in the cdevValue handle),
 *   - no string literals in code (UI text is in the DITL; the empty Standard File
 *     prompt is built on the stack, not stored as a "\p" literal — a string
 *     constant in a code resource would need the A4 world we don't have),
 *   - no calls to glue routines or helper functions — only INLINE Toolbox traps.
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
#include <StandardFile.h>   /* SFGetFile, SFReply, SFTypeList (inline-trap form) */

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
            if (h) *(short *)(*h) = 0;             /* files-picked count = 0 */
            return (long) h;                       /* becomes cdevValue */
        }

        case hitDev:
            if (item - numItems == kButton) {
                Point       where;
                Str255      prompt;
                SFReply     reply;
                SFTypeList  types;
                short       type, saveKind;
                Handle      ih;
                Rect        box;

                where.v = 90;                       /* top-left of the dialog */
                where.h = 100;
                prompt[0] = 0;                      /* empty prompt, built on stack */

                /* The modal Standard File "open" dialog, run from inside hitDev.
                 * numTypes = -1 -> show all files; no filter / no hook procs (we
                 * must not hand a code-resource function pointer to a trap). */
                SFGetFile(where, prompt, (FileFilterProcPtr) 0,
                          -1, types, (DlgHookProcPtr) 0, &reply);

                if (reply.good) {
                    Handle h = (Handle) cdevValue;
                    if (h) (*(short *)(*h))++;       /* count the pick in our handle */
                    SysBeep(5);                      /* audible: a file was chosen */

                    /* Show the chosen file's name. Same windowKind juggle as step 2:
                     * a cdev's window carries the Control Panel's windowKind, so
                     * SetDialogItemText needs dialogKind briefly. reply.fName is a
                     * Str63 pascal string. */
                    GetDialogItem(cpDialog, numItems + kCount, &type, &ih, &box);
                    saveKind = ((WindowPeek) cpDialog)->windowKind;
                    ((WindowPeek) cpDialog)->windowKind = dialogKind;
                    SetDialogItemText(ih, reply.fName);
                    ((WindowPeek) cpDialog)->windowKind = saveKind;
                }
            }
            return cdevValue;                       /* carry storage forward */

        case closeDev:
            if (cdevValue) DisposeHandle((Handle) cdevValue);
            return 0;

        default:
            return cdevValue;
    }
}
