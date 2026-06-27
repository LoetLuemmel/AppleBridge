/*
 * AppleBridge Control Panel - minimal cdev (cdev port, step 1).
 *
 * Proves an MPW-built, A4-free 'cdev' code resource works end to end: the
 * Control Panel host calls our single entry with numbered messages, we answer
 * macDev (appear on this machine) and initDev (allocate private storage), and a
 * statText from the DITL draws in the shared panel window.
 *
 * Deliberately minimal and disciplined, to dodge the code-resource traps the
 * presence INIT hit: NO globals or static data (state lives in the cdevValue
 * handle), NO string literals in code (the text is in the DITL resource), and NO
 * function pointer handed to a trap. So no A4/A5 world is needed and the linker
 * has nothing to mangle. And a cdev runs only when opened — it cannot affect boot.
 *
 * Entry must be the only/first function: the host jumps to offset 0 of the
 * 'cdev' resource. Links Interface.o only (Toolbox traps are inline).
 */

#include <Types.h>
#include <Quickdraw.h>
#include <Dialogs.h>
#include <Events.h>
#include <Memory.h>

#define macDev    8      /* "do you run on this machine?"   */
#define initDev   0      /* opened: allocate private storage */
#define closeDev  2      /* closed: free it                  */

pascal long CDevMain(short message, short item, short numItems, short rsrcID,
                     EventRecord *event, long cdevValue, DialogPtr cpDialog)
{
#pragma unused(item, numItems, rsrcID, event, cpDialog)
    switch (message) {
        case macDev:
            return 1;                          /* yes, appear */
        case initDev:
            return (long) NewHandle(4);        /* storage -> becomes cdevValue */
        case closeDev:
            if (cdevValue) DisposeHandle((Handle) cdevValue);
            return 0;
        default:
            return cdevValue;                  /* carry storage forward */
    }
}
