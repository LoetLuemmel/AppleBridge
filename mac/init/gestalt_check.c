/*
 * gestalt_check - verify the AppleBridge INIT's Gestalt selector.
 *
 * An MPW tool that queries Gestalt('ABrg') and reports the result as its EXIT
 * STATUS (no stdio — an MPW tool's stdout is not returned over the bridge, and
 * StdCLib stdio under ToolServer is unreliable):
 *
 *     0  AppleBridge INIT present, version 1.0 (response 0x0100)
 *     1  present, but an unexpected version
 *     2  not present (Gestalt returned an error, e.g. gestaltUndefSelectorErr)
 *
 * Read it over the bridge with:  Set Exit 0; GestaltCheck; Echo "rc={Status}"
 *
 * The detection logic itself is the one line any program uses to tell whether
 * AppleBridge is installed: Gestalt('ABrg', &v) == noErr.
 */

#include <Gestalt.h>

int main(void)
{
    long  v = 0;
    OSErr e = Gestalt('ABrg', &v);

    if (e != noErr)         return 2;   /* selector not registered */
    if (v != 0x00000100L)   return 1;   /* present, unexpected version */
    return 0;                           /* present, version 1.0 */
}
