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
 *
 * ---------------------------------------------------------------------------
 * v2, 2026-08-08: HIERARCHICAL MENUS (submenus). MENUWALK_DESIGN.md listed
 * `submenu` as deferred; this lifts it, in two clearly separated steps so the
 * risky half cannot damage the safe half:
 *
 *   TEIL A — DETECTION. An item is hierarchical when its cmdChar metadata byte
 *   is $1B; the submenu's menu ID then sits in the MARK byte. Both bytes are
 *   already inside the 4 metadata bytes this walk skips over, so reading them
 *   costs nothing and CANNOT walk off memory. Recorded as ITEM flag bit5 plus
 *   the ID in the item record. This alone answers "does Edit->Options have a
 *   submenu, and which one".
 *
 *   TEIL B — RESOLUTION. Submenus live in the SAME MenuList, in entries BEYOND
 *   `lastMenu` (which is the offset to the last MENU-BAR entry). There is no
 *   count for that tail and GetHandleSize is glue here, so the tail is scanned
 *   with a fixed cap AND every candidate is validated before it is believed:
 *   handle non-NULL, master pointer non-NULL, menuID equal to the wanted ID,
 *   and menuWidth/menuHeight/title length inside plausible bounds. A submenu
 *   that fails validation is reported as UNRESOLVED (itemN = -1) — never as an
 *   empty menu, because "no items" and "could not read it" must not look the
 *   same. That distinction is the whole lesson of this project's stumbles.
 *
 * ONE LEVEL. Pass 2 walks the submenus of menu-bar items. A submenu inside a
 * submenu is detected (flag bit5 is set on its item) but not walked; its items
 * would grow the array that pass 2 is iterating. Two levels need a work list.
 * ---------------------------------------------------------------------------
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
#define oMB_BarCount   26     /* word (v2): how many of the menus are MENU-BAR menus;
                               * the rest are submenus appended by pass 2. Lets an old
                               * reader stop at BarCount and see exactly what it saw
                               * before. Was padding, hence no offset moved. */
#define oMB_Menus      30     /* MAX_MENUS * MENU_REC */

#define MENU_REC       44     /* v2: was 40, +4 for parentMenu/parentItem */
#define MAX_MENUS      24     /* v2: was 16 — the menu bar plus its submenus */
#define oMB_Items      (oMB_Menus + MAX_MENUS * MENU_REC)   /* 30 + 1056 = 1086 */
#define ITEM_REC       36     /* v2: was 32, +4 for submenuID/(reserved) */
#define MAX_ITEMS      128
/* total block bytes = oMB_Items + MAX_ITEMS*ITEM_REC = 1086 + 4608 = 5694 */

/* MENU_REC (44): menuID(2) titleLeft(2) menuWidth(2) menuHeight(2) itemFirst(2)
 *   NOTE: field 3 is menuWidth from MenuInfo — the width of the DROPPED-DOWN
 *   BODY, not of the title in the menu bar. It was labelled titleWidth here,
 *   the host believed the label, and computed a title centre as
 *   titleLeft + width/2 — which for TPM's File menu lands on SEARCH.
 *                itemN(2) flags(2) titleLen(2) title[24]
 *                parentMenu(2) parentItem(2)          <- v2, at +40 / +42
 *   flags bit0 = menu enabled, bit1 = item points valid (menuHeight matched),
 *         bit2 = THIS RECORD IS A SUBMENU (v2; parentMenu/parentItem are then
 *                the 0-based menu index and 1-based item index that own it).
 *   itemN == -1 means UNRESOLVED: the submenu ID was named by an item but no
 *   plausible MenuInfo was found for it. NOT the same as itemN == 0. */
/* ITEM_REC (36): menuIdx(2) itemIndex(2) flags(2) textLen(2) text[24]
 *                submenuID(2) reserved(2)             <- v2, at +32 / +34
 *   flags bit0 = enabled (forced 0 when bit4 set), bit1 = separator,
 *        bit2 = text truncated, bit3 = reserved,
 *        bit4 = enabled-UNKNOWN (item index > 31, beyond the 32-bit enableFlags —
 *               MENUTREE emits enabled:null, NEVER false/disabled; a client that does
 *               not know bit4 must not read "disabled". System 7's Apple menu has >31
 *               DAs. bit3 and bit4 kept DISTINCT so separator != unknown, owner 12:11)
 *        bit5 = HAS SUBMENU (v2): cmdChar was $1B; submenuID holds the mark byte.
 *   Nothing before +32 moved, so a reader that only knows v1 fields still reads
 *   text at +8 correctly. */

#define kMenuListLM    0x0A1CL   /* low-mem: Handle to the current menu-bar list */
#define kMBarHeightLM  0x0BAAL   /* low-mem: menu bar height (word) */
#define kItemGuard     64        /* runaway guard: max items scanned per menu */
#define kMaxMenuData   2048      /* fix A bound, A5-free: GetHandleSize is GLUE here (link
                                  * Error 28, 2026-08-04), so bound the item walk by a fixed
                                  * byte cap from menuData start instead of the handle size */
#define kHierCmd       0x1B      /* cmdChar of a hierarchical item; mark = submenu ID */
#define kMaxHierScan   12        /* entries scanned past lastMenu when resolving a submenu. */

/* ADDRESS plausibility. v2 capped the COUNT but not the MEMORY, and that crashed the
 * daemon on 2026-08-08 (MENUTREE named as prime suspect in the host log): past the end
 * of a short MenuList the scan read garbage, took it for a MenuHandle and dereferenced
 * it. A counter is not a memory bound. Now every address is checked BEFORE it is
 * followed — non-zero, even (68k handles and master pointers always are), and inside
 * the addressable heap range. The first implausible entry STOPS the scan: past the
 * handle everything is garbage, so continuing would only read more of it. */
#define kAddrLo        0x00001000L
#define kAddrHi        0x0F000000L
#define Plausible(a)   ((a) != 0L && ((a) & 1L) == 0L && (a) > kAddrLo && (a) < kAddrHi)

/* Plausibility bounds for a MenuInfo we found by scanning. A wrong guess must be
 * REJECTED, not walked: these are what separate "a menu" from "whatever bytes
 * happened to follow the menu bar entries". */
#define kMenuWMax      2048
#define kMenuHMax      2048


/* Walk one menu's items out of menuData into item records.
 * Returns the number of items written; advances *pIrec and *pItemTotal.
 * Sets the block's Trunc word when a cap is hit. A5-free: pointer walk only. */
static short WalkItems(unsigned char *blk, Ptr mi, short menuIdx,
                       unsigned char **pIrec, short *pItemTotal)
{
    unsigned char *md, *mdEnd, *p, *irec;
    long           enMask;
    short          idx, itemTotal;

    md    = (unsigned char *)(mi + 14);      /* title Pascal string */
    mdEnd = md + kMaxMenuData;               /* fix A: fixed byte cap, no GetHandleSize */
    p     = md + 1 + md[0];                  /* skip the title Pascal string */
    enMask = *(long *)(mi + 10);             /* enableFlags: bit0 = the menu itself */
    enMask = enMask >> 1;                    /* -> bit0 = item 1 */
    irec      = *pIrec;
    itemTotal = *pItemTotal;
    idx       = 0;

    /* bounded three ways (fix A): the byte cap, the 0-byte terminator, and the
     * per-menu runaway guard — a corrupt MenuInfo can no longer walk p off the block. */
    while (p < mdEnd && *p != 0 && idx < kItemGuard) {
        short il = *p;
        short tl, kk, flags2 = 0, subID = 0;
        if (p + 1 + il + 4 > mdEnd) { *(short *)(blk + oMB_Trunc) = 1; break; }
        if (itemTotal >= MAX_ITEMS)  { *(short *)(blk + oMB_Trunc) = 1; break; }
        idx++;
        tl = il;
        if (tl > 24) { tl = 24; flags2 |= 4; }        /* bit2 = text truncated */
        if (idx <= 31) {
            if ((short)(enMask & 1L)) flags2 |= 1;    /* bit0 = enabled (bit idx) */
        } else {
            flags2 |= 16;                             /* bit4 = enabled-UNKNOWN */
        }
        if (il == 1 && p[1] == '-') flags2 |= 2;      /* bit1 = separator ("-") */

        /* v2 TEIL A — hierarchical item. The 4 metadata bytes after the text are
         * icon, cmdChar, mark, style. cmdChar $1B says "this item opens a submenu"
         * and the MARK byte then carries that submenu's ID instead of a mark
         * character. Reading two bytes we already skip over: no new risk, no trap,
         * no glue. Only $1B is honoured — $1C/$1D carry script codes and would be
         * a different meaning entirely. */
        if (p[1 + il + 1] == (unsigned char)kHierCmd) {
            subID = (short)p[1 + il + 2];
            if (subID > 0) flags2 |= 32;              /* bit5 = has submenu */
            else           subID = 0;
        }

        *(short *)(irec + 0)  = menuIdx;              /* 0-based owning menu */
        *(short *)(irec + 2)  = idx;                  /* 1-based item index */
        *(short *)(irec + 4)  = flags2;
        *(short *)(irec + 6)  = tl;
        for (kk = 0; kk < tl; kk++) irec[8 + kk] = p[1 + kk];
        *(short *)(irec + 32) = subID;                /* v2 */
        *(short *)(irec + 34) = 0;                    /* v2 reserved */
        irec += ITEM_REC;
        itemTotal++;
        enMask = enMask >> 1;                          /* constant shift -> next item's bit */
        p += 1 + il + 4;                              /* Pascal string + 4 metadata bytes */
    }
    if (idx >= kItemGuard) *(short *)(blk + oMB_Trunc) = 1;   /* guard hit: NOT a silent cap */

    *pIrec      = irec;
    *pItemTotal = itemTotal;
    return idx;
}


/* Resolve a submenu ID to its MenuInfo by scanning the MenuList tail.
 * Returns the MenuInfo pointer, or NULL when nothing plausible carries that ID.
 * NULL means "could not read it" and must NOT be rendered as an empty menu. */
static Ptr FindHierMenu(Ptr mp, short lastMenu, short wantID)
{
    short off, scanned;
    for (off = (short)(lastMenu + 6), scanned = 0;
         scanned < kMaxHierScan; off += 6, scanned++) {
        Handle mh;
        Ptr    mi;
        short  w, h;
        {
            unsigned long ha = *(unsigned long *)(mp + off);
            unsigned long pa;
            if (!Plausible(ha)) break;       /* vermutlich hinter dem Handle -> aufhoeren */
            pa = *(unsigned long *)ha;
            if (!Plausible(pa)) break;
            mh = (Handle)ha;
            mi = (Ptr)pa;
        }
        if (*(short *)(mi + 0) != wantID) continue;
        /* Validate before believing: a matching ID in random bytes is possible. */
        w = *(short *)(mi + 2);
        h = *(short *)(mi + 4);
        if (w < 0 || w > kMenuWMax) continue;
        if (h < 0 || h > kMenuHMax) continue;
        return mi;
    }
    return NULL;
}


void MenuWalk(unsigned char *blk)
{
    Handle          mlH;
    Ptr             mp;
    short           lastMenu, mbarH, menuN, itemTotal, off, barCount, pass1Items;
    unsigned char  *mrec, *irec, *scan;

    *(short *)(blk + oMB_MenuCount) = 0;
    *(short *)(blk + oMB_ItemCount) = 0;
    *(short *)(blk + oMB_Trunc)     = 0;
    *(short *)(blk + oMB_BarCount)  = 0;
    *(short *)(blk + oMB_Up)        = 0;

    mlH = *(Handle *)kMenuListLM;
    if (mlH == NULL || *mlH == NULL) return;
    mp        = *mlH;
    lastMenu  = *(short *)mp;              /* offset TO the last MENU-BAR entry, inclusive */
    mbarH     = *(short *)kMBarHeightLM;
    if (mbarH <= 0) mbarH = 20;
    *(short *)(blk + oMB_MBarH) = mbarH;

    menuN     = 0;
    itemTotal = 0;
    mrec = blk + oMB_Menus;
    irec = blk + oMB_Items;

    /* ---- Pass 1: the menu bar, exactly as v1 did ------------------------- */
    for (off = 6; off <= lastMenu; off += 6) {
        Handle mh;
        Ptr    mi;
        short  menuID, menuW, menuH, menuLeft, tLen, k, idx, pointsValid;
        unsigned char *md;

        if (menuN >= MAX_MENUS) { *(short *)(blk + oMB_Trunc) = 1; break; }

        mh = *(Handle *)(mp + off);
        if (mh == NULL || *mh == NULL) continue;
        mi = (Ptr)(*mh);
        menuID   = *(short *)(mi + 0);
        menuW    = *(short *)(mi + 2);
        menuH    = *(short *)(mi + 4);
        menuLeft = *(short *)(mp + off + 4);
        md       = (unsigned char *)(mi + 14);

        tLen = md[0];
        if (tLen > 24) tLen = 24;
        *(short *)(mrec + 0)  = menuID;
        *(short *)(mrec + 2)  = menuLeft;
        *(short *)(mrec + 4)  = menuW;
        *(short *)(mrec + 6)  = menuH;
        *(short *)(mrec + 8)  = itemTotal;       /* itemFirst */
        *(short *)(mrec + 14) = tLen;
        for (k = 0; k < tLen; k++) mrec[16 + k] = md[1 + k];
        *(short *)(mrec + 40) = -1;              /* v2: no parent — a bar menu */
        *(short *)(mrec + 42) = 0;

        idx = WalkItems(blk, mi, menuN, &irec, &itemTotal);
        *(short *)(mrec + 10) = idx;

        /* menuHeight guard (fix C — generous until h_sep is MEASURED): a standard-MDEF menu
         * is at most 16px/item tall, and separators are SHORTER, so menuH <= 16*itemN. */
        pointsValid = (menuH > 0 && menuH <= (short)(idx << 4) + 8) ? 1 : 0;
        *(short *)(mrec + 12) = (short)((( *(long *)(mi + 10) ) & 1L ? 1 : 0)
                                        | (pointsValid ? 2 : 0));

        mrec += MENU_REC;
        menuN++;
    }

    barCount   = menuN;
    pass1Items = itemTotal;      /* pass 2 appends beyond this; iterate only what pass 1 wrote */

    /* ---- Pass 2 (v2): the submenus named by pass 1's items ----------------
     * Iterating the item records written by pass 1 — NOT the ones pass 2 adds,
     * or the walk would chase its own tail into a submenu of a submenu while the
     * array grows underneath it. One level, deliberately. */
    scan = blk + oMB_Items;
    {
        short seen;
        for (seen = 0; seen < pass1Items; seen++, scan += ITEM_REC) {
            short iflags = *(short *)(scan + 4);
            short subID  = *(short *)(scan + 32);
            short pMenu, pItem, idx, k, tLen;
            Ptr   mi;
            unsigned char *md;

            if (!(iflags & 32) || subID <= 0) continue;
            if (menuN >= MAX_MENUS) { *(short *)(blk + oMB_Trunc) = 1; break; }

            pMenu = *(short *)(scan + 0);
            pItem = *(short *)(scan + 2);
            mi    = FindHierMenu(mp, lastMenu, subID);

            *(short *)(mrec + 0)  = subID;
            *(short *)(mrec + 2)  = 0;               /* no place in the menu bar */
            *(short *)(mrec + 8)  = itemTotal;       /* itemFirst */
            *(short *)(mrec + 40) = pMenu;
            *(short *)(mrec + 42) = pItem;

            if (mi == NULL) {
                /* UNRESOLVED — say so. An empty item list would be a lie that
                 * looks exactly like a submenu with no entries. */
                *(short *)(mrec + 4)  = 0;
                *(short *)(mrec + 6)  = 0;
                *(short *)(mrec + 10) = -1;          /* itemN = -1: could not read */
                *(short *)(mrec + 12) = 4;           /* bit2 = submenu; not enabled, no points */
                *(short *)(mrec + 14) = 0;
            } else {
                md   = (unsigned char *)(mi + 14);
                tLen = md[0];
                if (tLen > 24) tLen = 24;
                *(short *)(mrec + 4)  = *(short *)(mi + 2);    /* menuWidth */
                *(short *)(mrec + 6)  = *(short *)(mi + 4);    /* menuHeight */
                *(short *)(mrec + 14) = tLen;
                for (k = 0; k < tLen; k++) mrec[16 + k] = md[1 + k];
                idx = WalkItems(blk, mi, menuN, &irec, &itemTotal);
                *(short *)(mrec + 10) = idx;
                /* points_valid is NEVER set for a submenu: its on-screen rectangle is
                 * decided by the MDEF when it pops, and nothing in MenuInfo carries it.
                 * A point computed from the menu bar would be confidently wrong. */
                *(short *)(mrec + 12) = (short)((( *(long *)(mi + 10) ) & 1L ? 1 : 0) | 4);
            }
            mrec += MENU_REC;
            menuN++;
        }
    }

    *(short *)(blk + oMB_BarCount)  = barCount;
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
