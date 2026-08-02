/*
 * dlgwalk.c — A5-free DITL walk for the `dlgpatch` _ModalDialog head patch.
 *
 * Runs in the FOREGROUND application's context (where FrontWindow and the DITL
 * are valid — WindowList is per-process, which is the whole reason this patch
 * exists). Writes a fixed BINARY record layout into the shared system-heap
 * block; the daemon formats JSON from it via the DLGTREE verb.
 *
 * A5-FREE ON PURPOSE: no globals, no statics, no string literals, no long
 * multiply/divide (record pointer is advanced, not indexed) — so the linked
 * code resource is fully position-independent and survives the BlockMove into
 * the system heap, exactly like mspatch.a / menuled. Toolbox calls are inline
 * traps (position-independent); they use the FRONT app's A5 (already loaded),
 * which is precisely the context we need. Same calls as main.c's MACUITREE
 * walk, which is already proven to compile and produce correct rects.
 *
 * Offsets below MUST match the block header in dlgpatch.a and the reader in
 * mac/src/main.c (DLGTREE). Built with -model near (small resource → PC-relative
 * intra-resource calls).
 */
#include <Types.h>
#include <Quickdraw.h>
#include <Windows.h>
#include <Controls.h>
#include <Dialogs.h>

#define oDialogUp    14
#define oItemCount   18
#define oTruncated   20
#define oDlgRect     22
#define oRecords     30
#define RECSIZE      48
#define MAXITEMS     24
/* Generation is bumped in dlgpatch.a (the asm head) only, so it counts once per
 * armed trap call (#182 finding 1). The walk no longer touches it. */

void DlgWalk(unsigned char *blk)
{
    WindowPtr      w;
    WindowPeek     wp;
    DialogPtr      dlg;
    GrafPtr        save;
    Rect           r, gr;
    Point          tl, br;
    Handle         ih;
    Str255         tx;
    short          n, i, itype, btype, count, k, L, defItem;
    unsigned char *rec;

    *(short *)(blk + oItemCount)  = 0;
    *(short *)(blk + oDialogUp)   = 0;
    *(short *)(blk + oTruncated)  = 0;

    w = FrontWindow();
    if (w == NULL) return;
    wp = (WindowPeek)w;
    if (wp->windowKind != dialogKind) return;
    dlg = (DialogPtr)w;
    defItem = ((DialogPeek)dlg)->aDefItem;       /* the dialog's REAL default item,
                                                  * not a hardcoded 1 — clicking the
                                                  * wrong default at a save dialog
                                                  * means Don't Save instead of Save */

    GetPort(&save);
    SetPort((GrafPtr)dlg);

    if (wp->contRgn != NULL && *(wp->contRgn) != NULL)
        gr = (**(wp->contRgn)).rgnBBox;              /* global content rect */
    else
        gr = w->portRect;                            /* fallback (local) */
    *(short *)(blk + oDlgRect + 0) = gr.top;
    *(short *)(blk + oDlgRect + 2) = gr.left;
    *(short *)(blk + oDlgRect + 4) = gr.bottom;
    *(short *)(blk + oDlgRect + 6) = gr.right;

    /* Item count from the DITL handle's first word (= count - 1). This avoids
     * CountDITL — the walk's ONLY Interface.o glue reference (DumpObj, notes
     * channel 2026-08-02) — so the linked resource needs no library, is fully
     * A5-free and PC-relative, and survives the BlockMove into the system heap,
     * as self-sufficient as mspatch without writing the walk in assembly. */
    {
        Handle itemList = ((DialogPeek)dlg)->items;
        n = (itemList != NULL && *itemList != NULL)
                ? (short)(*(short *)(*itemList) + 1) : 0;
    }
    if (n > MAXITEMS) {                          /* record the cap, don't hide it */
        *(short *)(blk + oTruncated) = 1;        /* #182 finding 3 */
        n = MAXITEMS;
    }
    count = 0;
    rec = blk + oRecords;
    for (i = 1; i <= n; i++) {
        GetDialogItem(dlg, i, &itype, &ih, &r);
        btype = itype & 0x7F;
        tl.h = r.left;  tl.v = r.top;    LocalToGlobal(&tl);
        br.h = r.right; br.v = r.bottom; LocalToGlobal(&br);
        tx[0] = 0;
        if (btype >= (ctrlItem + btnCtrl) && btype <= (ctrlItem + resCtrl)) {
            if (ih != NULL) GetControlTitle((ControlHandle)ih, tx);
        } else if (btype == statText || btype == editText) {
            if (ih != NULL) GetDialogItemText(ih, tx);
        }
        *(short *)(rec + 0)  = i;
        *(short *)(rec + 2)  = btype;
        *(short *)(rec + 4)  = tl.v;   /* global top    */
        *(short *)(rec + 6)  = tl.h;   /* global left   */
        *(short *)(rec + 8)  = br.v;   /* global bottom */
        *(short *)(rec + 10) = br.h;   /* global right  */
        *(short *)(rec + 12) = (short)(((itype & 0x80) ? 0 : 1) | (i == defItem ? 2 : 0));
                                        /* bit0=enabled, bit1=default (dialog aDefItem) */
        L = tx[0];
        if (L > 31) L = 31;
        *(short *)(rec + 14) = L;
        for (k = 0; k < L; k++) rec[16 + k] = tx[1 + k];
        rec += RECSIZE;
        count++;
    }
    *(short *)(blk + oItemCount) = count;
    *(short *)(blk + oDialogUp)  = 1;
    SetPort(save);
}
