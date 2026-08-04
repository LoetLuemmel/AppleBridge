/*
 * menuwalk.c — A5-free MENU-BAR walk for the jGNE ($029A) walk-on-request.
 * MENUWALK's foreground-context capture (Punkt 3). Sibling of dlgwalk.c.
 *
 * Runs in the FOREGROUND app's context: MenuList is per-process (the daemon's own
 * GetMenuBar sees only the daemon's Edit menu, not the front app's — measured
 * 2026-08-04, MENU:Font -> found=0 while SimpleText front). Reads the low-mem
 * MenuList ($0A1C) and each menu's MenuInfo + menuData DIRECTLY — no
 * GetMenuItemText / CountMItems, which would drag in Interface.o glue and break
 * the A5-free / PC-relative property (dlgwalk.c avoided CountDITL for the same
 * reason). Writes a fixed BINARY record layout into the shared MB block; the
 * daemon formats JSON via MENUTREE.
 *
 * A5-FREE ON PURPOSE: no globals/statics/string-literals, no long multiply/divide,
 * no VARIABLE-count shift (the enable bit is stepped with a running >>1 mask; the 16px
 * model uses a constant <<4), and NO TRAPS — every read is an absolute low-mem address
 * or a pointer walk. GetHandleSize was dropped because it links as GLUE here (not an
 * inline trap: link Error 28, 2026-08-04); the two bounds it fed use lastMenu-only and a
 * fixed byte cap instead. So the linked resource has an EMPTY externals list and survives
 * the BlockMove into the system heap.
 *
 * Reached via the jGNE stub's Walk() dispatcher when the block magic at +4 is 'MB'.
 * The stub reads oMB_Up(+14) and bumps oMB_Gen(+16) exactly as for the DP block, so
 * MenuWalk sets +14=1 on a successful capture (identical contract to DlgWalk).
 *
 * Offsets MUST match the MB reader in mac/src/main.c (MENUTREE). Built -model near.
 */
#include <Types.h>
#include <Memory.h>

/* ---- MB block layout (shared with main.c MENUTREE). +14/+16 are the jGNE stub's
 *      fixed oDP_Up / oDP_Gen — do NOT move them. ------------------------------ */
#define oMB_Magic      4      /* 'MB' $4D42 — the Walk dispatcher routes on this */
#define oMB_Up         14     /* word: MenuWalk sets 1 on capture (stub reads)   */
#define oMB_Gen        16     /* word: stub bumps once per fresh capture         */
#define oMB_MenuCount  18     /* word */
#define oMB_Trunc      20     /* word: 1 if a menu/item/text cap was hit         */
#define oMB_ItemCount  22     /* word */
#define oMB_MBarH      24     /* word: MBarHeight, so the daemon need not assume 20 */
#define oMB_Menus      30     /* MAX_MENUS * MENU_REC */

#define MENU_REC       40
#define MAX_MENUS      16
#define oMB_Items      (oMB_Menus + MAX_MENUS * MENU_REC)   /* 30 + 640 = 670 */
#define ITEM_REC       32
#define MAX_ITEMS      128
/* total block bytes = oMB_Items + MAX_ITEMS*ITEM_REC = 670 + 4096 = 4766 */

/* MENU_REC (40): menuID(2) titleLeft(2) titleWidth(2) menuHeight(2) itemFirst(2)
 *                itemN(2) flags(2) titleLen(2) title[24]
 *   flags bit0 = menu enabled, bit1 = item points valid (menuHeight matched) */
/* ITEM_REC (32): menuIdx(2) itemIndex(2) flags(2) textLen(2) text[24]
 *   flags bit0 = enabled (forced 0 when bit4 set), bit1 = separator,
 *        bit2 = text truncated, bit3 = reserved,
 *        bit4 = enabled-UNKNOWN (item index > 31, beyond the 32-bit enableFlags —
 *               MENUTREE emits enabled:null, NEVER false/disabled; a client that does
 *               not know bit4 must not read "disabled". System 7's Apple menu has >31
 *               DAs. bit3 and bit4 kept DISTINCT so separator != unknown, owner 12:11) */

#define kMenuListLM    0x0A1CL   /* low-mem: Handle to the current menu-bar list */
#define kMBarHeightLM  0x0BAAL   /* low-mem: menu bar height (word) */
#define kItemGuard     64        /* runaway guard: max items scanned per menu */
#define kMaxMenuData   2048      /* fix A bound, A5-free: GetHandleSize is GLUE here (link
                                  * Error 28, 2026-08-04), so bound the item walk by a fixed
                                  * byte cap from menuData start instead of the handle size */

void MenuWalk(unsigned char *blk)
{
    Handle          mlH;
    Ptr             mp;
    short           lastMenu, mbarH, menuN, itemTotal, off;
    unsigned char  *mrec, *irec;

    *(short *)(blk + oMB_MenuCount) = 0;
    *(short *)(blk + oMB_ItemCount) = 0;
    *(short *)(blk + oMB_Trunc)     = 0;
    *(short *)(blk + oMB_Up)        = 0;

    mlH = *(Handle *)kMenuListLM;
    if (mlH == NULL || *mlH == NULL) return;
    mp        = *mlH;
    lastMenu  = *(short *)mp;              /* offset TO the last 6-byte entry, inclusive.
                                           * (No GetHandleSize second bound: it links as GLUE
                                           * here, not an inline trap — link Error 28, 2026-08-04.
                                           * lastMenu-only is what main.c's MENU verb uses too.) */
    mbarH     = *(short *)kMBarHeightLM;
    if (mbarH <= 0) mbarH = 20;
    *(short *)(blk + oMB_MBarH) = mbarH;

    menuN     = 0;
    itemTotal = 0;
    mrec = blk + oMB_Menus;
    irec = blk + oMB_Items;

    for (off = 6; off <= lastMenu; off += 6) {
        Handle          mh;                /* a menu handle (kept as Handle to avoid
                                            * <Menus.h> — we never call a Menu Mgr trap) */
        Ptr             mi;                /* MenuInfo (dereferenced menu handle) */
        unsigned char  *md;               /* menuData: title, then packed items */
        unsigned char  *p;
        long            enMask;
        unsigned char  *mdEnd;             /* item-walk byte bound (fix A, A5-free cap) */
        short           menuID, menuW, menuH, menuLeft, tLen, k, idx, pointsValid;

        if (menuN >= MAX_MENUS) { *(short *)(blk + oMB_Trunc) = 1; break; }

        mh = *(Handle *)(mp + off);
        if (mh == NULL || *mh == NULL) continue;
        mi = (Ptr)(*mh);
        menuID   = *(short *)(mi + 0);
        menuW    = *(short *)(mi + 2);
        menuH    = *(short *)(mi + 4);
        enMask   = *(long  *)(mi + 10);    /* enableFlags: bit i = item i, bit0 = the menu */
        menuLeft = *(short *)(mp + off + 4);
        md       = (unsigned char *)(mi + 14);   /* title Pascal string */
        mdEnd    = md + kMaxMenuData;             /* fix A, A5-free: fixed byte cap (no GetHandleSize) */

        /* --- menu title into MENU_REC --- */
        tLen = md[0];
        if (tLen > 24) tLen = 24;
        *(short *)(mrec + 0)  = menuID;
        *(short *)(mrec + 2)  = menuLeft;
        *(short *)(mrec + 4)  = menuW;
        *(short *)(mrec + 6)  = menuH;
        *(short *)(mrec + 8)  = itemTotal;       /* itemFirst = current global item index */
        *(short *)(mrec + 14) = tLen;
        for (k = 0; k < tLen; k++) mrec[16 + k] = md[1 + k];

        /* --- items straight out of menuData (A5-free, no traps) --- */
        p = md + 1 + md[0];                      /* skip the title Pascal string */
        enMask = enMask >> 1;                    /* drop the menu's own bit -> bit0 = item 1 */
        idx = 0;
        /* bounded three ways (fix A): the handle end, the 0-byte terminator, and the
         * per-menu runaway guard — a corrupt MenuInfo can no longer walk p off the block. */
        while (p < mdEnd && *p != 0 && idx < kItemGuard) {
            short il = *p;
            short tl, kk, flags2 = 0;
            if (p + 1 + il + 4 > mdEnd) { *(short *)(blk + oMB_Trunc) = 1; break; }  /* item overruns the record (fix A) */
            if (itemTotal >= MAX_ITEMS) { *(short *)(blk + oMB_Trunc) = 1; break; }
            idx++;
            tl = il;
            if (tl > 24) { tl = 24; flags2 |= 4; }        /* bit2 = text truncated */
            if (idx <= 31) {                              /* enableFlags is a 32-bit long */
                if ((short)(enMask & 1L)) flags2 |= 1;    /* bit0 = enabled (bit idx) */
            } else {
                flags2 |= 16;                             /* bit4 = enabled-UNKNOWN: past the
                                                           * long's range -> bit0 left 0, MENUTREE
                                                           * emits enabled:null, NEVER disabled
                                                           * (owner 12:04/12:11, >31-DA Apple menu) */
            }
            if (il == 1 && p[1] == '-') flags2 |= 2;      /* bit1 = separator ("-") */
            *(short *)(irec + 0) = menuN;                 /* 0-based owning menu */
            *(short *)(irec + 2) = idx;                   /* 1-based item index */
            *(short *)(irec + 4) = flags2;
            *(short *)(irec + 6) = tl;
            for (kk = 0; kk < tl; kk++) irec[8 + kk] = p[1 + kk];
            irec += ITEM_REC;
            itemTotal++;
            enMask = enMask >> 1;                          /* constant shift -> next item's bit */
            p += 1 + il + 4;                              /* Pascal string + 4 metadata bytes */
        }
        *(short *)(mrec + 10) = idx;                       /* itemN for this menu */
        if (idx >= kItemGuard) *(short *)(blk + oMB_Trunc) = 1;   /* per-menu guard hit: NOT a silent cap (fix B) */

        /* menuHeight guard (fix C — generous until h_sep is MEASURED): a standard-MDEF menu
         * is at most 16px/item tall, and separators are SHORTER, so menuH <= 16*itemN. Trust
         * the item points only when menuH fits under that upper bound (+slack); a custom MDEF
         * with taller items exceeds it and is flagged. The exact separator-adjusted Y model
         * (predH = 16*(n-sep) + h_sep*sep) needs h_sep measured on a real menu — until then the
         * client derives the separator count from ITEM_REC bit1 and the carried menuH. */
        pointsValid = (menuH > 0 && menuH <= (short)(idx << 4) + 8) ? 1 : 0;
        *(short *)(mrec + 12) = (short)((( *(long *)(mi + 10) ) & 1L ? 1 : 0)
                                        | (pointsValid ? 2 : 0));   /* bit0 menu-enabled, bit1 points-valid */

        mrec += MENU_REC;
        menuN++;
    }

    *(short *)(blk + oMB_MenuCount) = menuN;
    *(short *)(blk + oMB_ItemCount) = itemTotal;
    *(short *)(blk + oMB_Up)        = (menuN > 0) ? 1 : 0;
}

/* Walk(blk): the jGNE stub's dispatcher. jgnepatch.a calls this (BSR Walk) instead
 * of DlgWalk; it routes on the block's magic so ONE jGNE stub serves both walks and
 * the proven dialog path (dlgwalk.c / dlgpatch.a) stays byte-identical. A5-free. */
extern void DlgWalk(unsigned char *blk);

void Walk(unsigned char *blk)
{
    if (*(unsigned short *)(blk + oMB_Magic) == 0x4D42)   /* 'MB' */
        MenuWalk(blk);
    else
        DlgWalk(blk);                                     /* 'DP' $4450 (or anything else) */
}
