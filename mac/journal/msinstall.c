/*
 * msinstall.c -- Route B spike installer: put the mspatch.a MenuSelect head
 * patch into the system heap and drive it from the bridge.
 *
 *   msinstall install <rsrc>                 copy 'MSPT' 128 to sys heap, patch $A93D
 *   msinstall read    <hex>                  dump Calls/Hits/Armed/Result/Real/live-trap
 *   msinstall arm     <hex> <menuID> <item> [oneshot]
 *                                            next MenuSelect returns (menuID<<16|item)
 *   msinstall disarm  <hex>                  stop intercepting (patch stays resident)
 *   msinstall uninstall <hex>                restore $A93D if we are still the head
 *
 * Build (MPW tool, via ToolServer):
 *   SC msinstall.c -o msinstall.c.o
 *   Link -o msinstall -t MPST -c 'MPS ' msinstall.c.o \
 *        "{LIBS}CLibraries:StdCLib.o" "{LIBS}Libraries:IntEnv.o" \
 *        "{LIBS}Libraries:Interface.o" "{LIBS}Libraries:MacRuntime.o"
 */
#include <Types.h>
#include <Memory.h>
#include <Resources.h>
#include <Patches.h>
#include <Traps.h>
#include <OSUtils.h>
#include <Menus.h>
#include <Quickdraw.h>
#include <stdio.h>
#include <string.h>

/* block layout -- keep in sync with mspatch.a */
#define oMagic    2
#define oArmed    4
#define oOneShot  6
#define oResult   8
#define oReal     12
#define oCalls    16
#define oHits     20
#define oLastRes  24
#define kMagic    0x4D53

static void c2p(const char *c, Str255 p)
{
    short n = (short)strlen(c);
    if (n > 255) n = 255;
    p[0] = (unsigned char)n;
    memcpy(p + 1, c, n);
}

static unsigned long hexParse(const char *s)
{
    unsigned long v = 0;
    for (; *s; s++) {
        char c = *s;
        if (c >= '0' && c <= '9')      v = (v << 4) | (unsigned long)(c - '0');
        else if (c >= 'a' && c <= 'f') v = (v << 4) | (unsigned long)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v = (v << 4) | (unsigned long)(c - 'A' + 10);
        else break;
    }
    return v;
}

static long numParse(const char *s)
{
    long v = 0;
    for (; *s >= '0' && *s <= '9'; s++) v = v * 10 + (*s - '0');
    return v;
}

static unsigned char *checkBlk(const char *hex)
{
    unsigned long a = hexParse(hex);
    unsigned char *b = (unsigned char *)a;
    if (!a || *(short *)(b + oMagic) != (short)kMagic) {
        printf("bad block addr '%s' (magic mismatch)\n", hex);
        return NULL;
    }
    return b;
}

static void dump(unsigned char *b)
{
    unsigned long live = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
    printf("calls=%ld hits=%ld armed=%d oneshot=%d\n",
           *(long *)(b + oCalls), *(long *)(b + oHits),
           *(short *)(b + oArmed), *(short *)(b + oOneShot));
    printf("result=%08lX lastRes=%08lX real=%08lX\n",
           *(unsigned long *)(b + oResult), *(unsigned long *)(b + oLastRes),
           *(unsigned long *)(b + oReal));
    printf("live $A93D=%08lX  head=%s\n",
           live, (live == (unsigned long)b) ? "YES (ours)" : "no (someone else)");
}

int main(int argc, char **argv)
{
    if (argc >= 3 && !strcmp(argv[1], "install")) {
        Str255        pn;
        short         resFile;
        Handle        h;
        Size          sz;
        Ptr           blk;
        unsigned long real;
        c2p(argv[2], pn);
        resFile = OpenResFile(pn);
        if (resFile == -1) { printf("OpenResFile failed err=%d\n", ResError()); return 2; }
        h = Get1Resource('MSPT', 128);
        if (!h) { printf("Get1Resource MSPT 128 failed err=%d\n", ResError()); return 2; }
        HNoPurge(h);
        LoadResource(h);
        sz  = GetHandleSize(h);
        blk = NewPtrSys(sz);
        if (!blk) { printf("NewPtrSys(%ld) failed\n", (long)sz); return 2; }
        BlockMove(*h, blk, sz);
        CloseResFile(resFile);
        if (*(short *)(blk + oMagic) != (short)kMagic) {
            printf("copied block magic mismatch - aborting\n"); return 2;
        }
        real = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
        *(unsigned long *)(blk + oReal) = real;
        NSetTrapAddress((UniversalProcPtr)blk, _MenuSelect, ToolTrap);
        {
            unsigned long back = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
            printf("installed blk=%08lX real=%08lX readback=%08lX %s (disarmed)\n",
                   (unsigned long)blk, real, back,
                   (back == (unsigned long)blk) ? "OK-head" : "NOT-head");
        }
        return 0;
    }
    if (argc >= 5 && !strcmp(argv[1], "arm")) {
        unsigned char *b = checkBlk(argv[2]);
        long menuID = numParse(argv[3]);
        long item   = numParse(argv[4]);
        short oneshot = (argc >= 6) ? (short)numParse(argv[5]) : 1;
        if (!b) return 2;
        *(unsigned long *)(b + oResult) = ((unsigned long)(menuID & 0xFFFF) << 16)
                                          | (unsigned long)(item & 0xFFFF);
        *(short *)(b + oOneShot) = oneshot;
        *(short *)(b + oArmed)   = 1;
        printf("armed: next MenuSelect -> menuID=%ld item=%ld (result=%08lX) oneshot=%d\n",
               menuID, item, *(unsigned long *)(b + oResult), oneshot);
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "disarm")) {
        unsigned char *b = checkBlk(argv[2]);
        if (!b) return 2;
        *(short *)(b + oArmed) = 0;
        printf("disarmed (patch stays resident)\n");
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "selftest")) {
        /* Prove the patch's INTERCEPT path in-process (ToolServer reverts the
         * trap table on tool exit, so persistence must come from the daemon or
         * an INIT -- but the mechanism itself is testable here). Install, arm
         * with a known (menuID,item), then call MenuSelect ourselves: the armed
         * path returns immediately with no tracking, so this is safe. */
        Str255        pn;
        short         resFile;
        Handle        h;
        Size          sz;
        Ptr           blk;
        unsigned long real, want;
        long          r;
        Point         pt;
        c2p(argv[2], pn);
        resFile = OpenResFile(pn);
        if (resFile == -1) { printf("OpenResFile failed err=%d\n", ResError()); return 2; }
        h = Get1Resource('MSPT', 128);
        if (!h) { printf("Get1Resource failed err=%d\n", ResError()); return 2; }
        HNoPurge(h); LoadResource(h);
        sz = GetHandleSize(h); blk = NewPtrSys(sz);
        if (!blk) { printf("NewPtrSys failed\n"); return 2; }
        BlockMove(*h, blk, sz); CloseResFile(resFile);
        real = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
        *(unsigned long *)(blk + oReal) = real;
        NSetTrapAddress((UniversalProcPtr)blk, _MenuSelect, ToolTrap);
        /* arm: return menuID=42 item=7 on the next call */
        want = ((unsigned long)42 << 16) | 7;
        *(unsigned long *)(blk + oResult) = want;
        *(short *)(blk + oOneShot) = 1;
        *(short *)(blk + oArmed)   = 1;
        pt.v = 10; pt.h = 40;
        r = MenuSelect(pt);                 /* -> our patch intercepts, no tracking */
        NSetTrapAddress((UniversalProcPtr)real, _MenuSelect, ToolTrap);  /* restore now */
        printf("selftest: MenuSelect returned %08lX want=%08lX -> %s\n",
               (unsigned long)r, want, ((unsigned long)r == want) ? "PASS" : "FAIL");
        printf("  calls=%ld hits=%ld armed(after)=%d\n",
               *(long *)(blk + oCalls), *(long *)(blk + oHits), *(short *)(blk + oArmed));
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "callinit")) {
        /* Run the INIT install code (msinit.a) at RUNTIME to prove the asm works
         * before it ever runs at boot -- a bug crashes THIS tool, not startup.
         * Process-local here (app-installed), so we restore the trap after. */
        Str255 pn;
        short  resFile;
        Handle ih;
        unsigned long before, after;
        void (*initproc)(void);
        c2p(argv[2], pn);
        resFile = OpenResFile(pn);
        if (resFile == -1) { printf("OpenResFile failed err=%d\n", ResError()); return 2; }
        ih = Get1Resource('INIT', 128);
        if (!ih) { printf("no INIT 128 err=%d\n", ResError()); return 2; }
        HLock(ih);
        before = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
        initproc = (void (*)(void))(*ih);
        initproc();                         /* execute the boot install code */
        after = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
        printf("callinit: MenuSelect before=%08lX after=%08lX -> %s\n",
               before, after, (after != before) ? "CHANGED (installed)" : "unchanged");
        if (after != before) {
            unsigned char *b = (unsigned char *)after;
            printf("  block magic=%04X (want 4D53) real=%08lX\n",
                   (unsigned short)*(short *)(b + 2), *(unsigned long *)(b + 12));
            NSetTrapAddress((UniversalProcPtr)*(unsigned long *)(b + 12), _MenuSelect, ToolTrap);
            printf("  restored (process-local test)\n");
        }
        CloseResFile(resFile);
        return 0;
    }
    if (argc >= 4 && !strcmp(argv[1], "armlive")) {
        /* arm whatever MSPT patch is currently on $A93D (e.g. the boot INIT's) */
        unsigned long live = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
        unsigned char *b = (unsigned char *)live;
        long menuID = numParse(argv[2]), item = numParse(argv[3]);
        if (*(short *)(b + 2) != (short)kMagic) {
            printf("live $A93D=%08lX is not our patch (magic mismatch)\n", live); return 2;
        }
        *(unsigned long *)(b + 8) = ((unsigned long)(menuID & 0xFFFF) << 16) | (unsigned long)(item & 0xFFFF);
        *(short *)(b + 6) = 1;
        *(short *)(b + 4) = 1;
        printf("armlive: blk=%08lX menuID=%ld item=%ld\n", live, menuID, item);
        return 0;
    }
    if (argc >= 2 && !strcmp(argv[1], "scan")) {
        /* Did the boot INIT run? Scan the system heap for a copied patch block
         * (starts with 601A 4D53 = BRA.S Go + 'MS' magic). Found => the INIT
         * executed and NewPtrSys'd the block, even if it is not $A93D head. */
        unsigned char *p   = (unsigned char *)(*(unsigned long *)0x02A6L); /* SysZone */
        unsigned char *end = (unsigned char *)(*(unsigned long *)0x02AAL); /* ApplZone */
        long hits = 0;
        printf("scanning SysZone %08lX..%08lX for 601A4D53\n",
               (unsigned long)p, (unsigned long)end);
        for (; p + 4 < end; p += 2) {
            if (*(unsigned short *)p == 0x601A && *(unsigned short *)(p + 2) == 0x4D53) {
                printf("  hit @%08lX  calls=%ld hits=%ld real=%08lX\n",
                       (unsigned long)p, *(long *)(p + 16), *(long *)(p + 20),
                       *(unsigned long *)(p + 12));
                if (++hits >= 8) break;
            }
        }
        printf("total hits=%ld\n", hits);
        return 0;
    }
    if (argc >= 2 && !strcmp(argv[1], "readlive")) {
        unsigned long live = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
        unsigned char *b = (unsigned char *)live;
        if (*(short *)(b + 2) != (short)kMagic) {
            printf("live $A93D=%08lX is not our patch (magic mismatch)\n", live); return 0;
        }
        printf("live blk=%08lX calls=%ld hits=%ld armed=%d lastRes=%08lX\n",
               live, *(long *)(b + 16), *(long *)(b + 20),
               *(short *)(b + 4), *(unsigned long *)(b + 24));
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "read")) {
        unsigned char *b = checkBlk(argv[2]);
        if (!b) return 2;
        dump(b);
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "uninstall")) {
        unsigned char *b = checkBlk(argv[2]);
        unsigned long  live, real;
        if (!b) return 2;
        live = (unsigned long)NGetTrapAddress(_MenuSelect, ToolTrap);
        real = *(unsigned long *)(b + oReal);
        if (live != (unsigned long)b) {
            printf("NOT head: live $A93D=%08lX (block %08lX) - refusing to unhook\n",
                   live, (unsigned long)b);
            return 0;
        }
        NSetTrapAddress((UniversalProcPtr)real, _MenuSelect, ToolTrap);
        printf("uninstalled: $A93D restored to %08lX\n", real);
        return 0;
    }
    printf("usage: msinstall install <rsrc> | arm <hex> <menuID> <item> [oneshot] | "
           "disarm <hex> | read <hex> | uninstall <hex>\n");
    return 1;
}
