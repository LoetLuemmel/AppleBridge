/*
 * jprobe2.c -- JPROBE2 spike (v3): app-installed jGNE filter + Time-Manager
 * unhook watchdog, to safely probe FOREIGN process contexts (incl. Finder).
 *
 * The resident filter runs in the calling app's context and counts per-context.
 * v3 adds a watchdog: `armwd` primes a Time Manager task that restores $29A to
 * the prior filter after <delayMs>. If a Finder switch freezes the main loop,
 * the tick interrupt fires the watchdog and auto-unhooks us -- recovering the
 * guest without a hard-kill. WDFired shows whether it ran.
 *
 *   jprobe2 zones                     report zones + current $29A
 *   jprobe2 install <rsrc>            install filter, NO watchdog (safe demo)
 *   jprobe2 armwd   <rsrc> <delayMs>  install filter + prime unhook watchdog
 *   jprobe2 read    <hexaddr>         dump totals, WDFired, per-context slots
 *   jprobe2 disarm  <hexaddr>         stop counting (still resident)
 *   jprobe2 uninstall <hexaddr>       restore $29A if we're still the head
 *
 * Build (MPW tool, via ToolServer):
 *   SC jprobe2.c -o jprobe2.c.o
 *   Link -o jprobe2 -t MPST -c 'MPS ' jprobe2.c.o \
 *        "{LIBS}CLibraries:StdCLib.o" "{LIBS}Libraries:IntEnv.o" \
 *        "{LIBS}Libraries:Interface.o" "{LIBS}Libraries:MacRuntime.o"
 */
#include <Types.h>
#include <Memory.h>
#include <Resources.h>
#include <Timer.h>
#include <Devices.h>
#include <stdio.h>
#include <string.h>

#define JGNE_LM  (*(volatile unsigned long *)0x29AL)
#define SYSZONE  (*(unsigned long *)0x02A6L)
#define APPLZONE (*(unsigned long *)0x02AAL)
#define HEAPEND  (*(unsigned long *)0x0114L)

/* block layout -- keep in sync with jgne.a */
#define oMagic     2
#define oDisarm    4
#define oOld       6
#define oTotal     10
#define oSysLo     14
#define oSysHi     18
#define oTgt       22
#define oTMProcOff 26
#define oWDFired   28
#define oTMTask    32   /* qLink@32 qType@36 tmAddr@38 tmCount@42 */
#define oTMAddr    38
#define oSlots     48
#define oSnapReq   112
#define oSnapDone  114
#define oSnapA5    116
#define oSnapA5Out 120
#define oSnapCount 124
#define oSnapBufOff 126   /* header word: offset of SnapBuf within the block */
#define kNSlots    4
#define kMagic     0x4A32

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

static const char *classify(unsigned long p)
{
    if (p == 0) return "null";
    if (p >= SYSZONE && p < APPLZONE) return "SYSTEM-heap (safe to chain)";
    if (p >= APPLZONE) return "APP-heap (unsafe -> skipped)";
    return "below-SysZone (ROM/lowmem)";
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
    unsigned long old = *(unsigned long *)(b + oOld);
    long i;
    printf("total=%ld disarm=%d WDfired=%d\n",
           *(long *)(b + oTotal), *(short *)(b + oDisarm), *(short *)(b + oWDFired));
    printf("oldFilter=%08lX  live$29A=%08lX  hooked=%s\n",
           old, JGNE_LM, (JGNE_LM == (unsigned long)b) ? "YES" : "no(unhooked)");
    for (i = 0; i < kNSlots; i++) {
        unsigned char *s  = b + oSlots + i * 16;
        unsigned long  a5 = *(unsigned long *)s;
        long           cnt = *(long *)(s + 4);
        unsigned char *nm = s + 8;
        char name[8];
        short n = nm[0];
        if (n > 7) n = 7;
        memcpy(name, nm + 1, n);
        name[n] = 0;
        if (a5)
            printf("slot%ld a5=%08lX count=%ld name='%s'\n", i, a5, cnt, name);
    }
    if (*(short *)(b + oSnapDone)) {
        unsigned char *p = b + *(short *)(b + oSnapBufOff);
        short cnt = *(short *)(b + oSnapCount), k;
        printf("SNAPSHOT ctx a5=%08lX menus=%d:\n",
               *(unsigned long *)(b + oSnapA5Out), cnt);
        for (k = 0; k < cnt; k++) {
            short id, ln, j;
            char  t[40];
            id = *(short *)p; p += 2;
            ln = *p++;
            j = ln; if (j > 39) j = 39;
            memcpy(t, p, j); t[j] = 0;
            p += ln;
            printf("  menuID=%d '%s'\n", id, t);
        }
    } else if (*(short *)(b + oSnapReq)) {
        printf("SNAPSHOT pending (req set, target a5=%08lX, not yet fired)\n",
               *(unsigned long *)(b + oSnapA5));
    }
    {
        short sb = *(short *)(b + oSnapBufOff);
        unsigned char *drv = b + sb + 1024;
        if (*(short *)(drv + 0) || *(short *)(drv + 2))
            printf("DRIVE req=%d did=%d target a5=%08lX\n",
                   *(short *)(drv + 0), *(short *)(drv + 2),
                   *(unsigned long *)(drv + 4));
    }
}

/* copy 'JGNE' 128 into the system heap, fill chain bounds; returns block or 0 */
static unsigned char *installFilter(const char *rsrc)
{
    Str255        pn;
    short         resFile;
    Handle        h;
    Size          sz;
    Ptr           blk;
    unsigned long old;

    c2p(rsrc, pn);
    resFile = OpenResFile(pn);
    if (resFile == -1) { printf("OpenResFile failed err=%d\n", ResError()); return NULL; }
    h = Get1Resource('JGNE', 128);
    if (!h) { printf("Get1Resource JGNE 128 failed err=%d\n", ResError()); return NULL; }
    HNoPurge(h);
    LoadResource(h);
    sz  = GetHandleSize(h);
    blk = NewPtrSys(sz);
    if (!blk) { printf("NewPtrSys(%ld) failed\n", (long)sz); return NULL; }
    BlockMove(*h, blk, sz);
    CloseResFile(resFile);
    if (*(short *)(blk + oMagic) != (short)kMagic) {
        printf("copied block magic mismatch - aborting\n"); return NULL;
    }
    old = JGNE_LM;
    if (old & 1) { printf("existing JGNEFilter %08lX is odd - aborting\n", old); return NULL; }
    if (old && *(short *)((unsigned char *)old + oMagic) == (short)kMagic) {
        /* Spike discipline: never chain to a LEFTOVER jprobe2 block from an
         * earlier iteration -- an outdated block executed blind by our Done
         * chain is the classic error-10 setup. A NON-jprobe2 head is fine:
         * on System 7 that is normally the Notification Manager's own GNE
         * hook (sys-heap stub -> BNMQHd walker), which is built to be
         * chained onto (identified live 2026-07-05). */
        printf("$29A holds a stale jprobe2 block (%08lX) - refusing to chain;\n"
               "run 'jprobe2 uninstall %08lX' first, then retry\n", old, old);
        DisposePtr(blk);
        return NULL;
    }
    *(unsigned long *)(blk + oOld)   = old;
    *(unsigned long *)(blk + oSysLo) = SYSZONE;
    *(unsigned long *)(blk + oSysHi) = APPLZONE;
    return (unsigned char *)blk;
}

int main(int argc, char **argv)
{
    if (argc >= 2 && !strcmp(argv[1], "zones")) {
        unsigned long f = JGNE_LM;
        printf("SysZone=%08lX ApplZone=%08lX HeapEnd=%08lX\n", SYSZONE, APPLZONE, HEAPEND);
        printf("current $29A JGNEFilter=%08lX -> %s\n", f, classify(f));
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "install")) {
        unsigned char *blk = installFilter(argv[2]);
        if (!blk) return 2;
        JGNE_LM = (unsigned long)blk;
        printf("installed blk=%08lX oldFilter=%08lX (no watchdog)\n",
               (unsigned long)blk, *(unsigned long *)(blk + oOld));
        return 0;
    }
    if (argc >= 4 && !strcmp(argv[1], "armwd")) {
        unsigned char *blk = installFilter(argv[2]);
        long           delayMs = numParse(argv[3]);
        short          off;
        QElemPtr       tm;
        if (!blk) return 2;
        off = *(short *)(blk + oTMProcOff);
        *(long  *)(blk + oTMTask)  = 0;                 /* qLink  */
        *(short *)(blk + oTMTask+4) = 0;                /* qType  */
        *(long  *)(blk + oTMAddr)  = (long)(blk + off); /* tmAddr = TMProc */
        *(long  *)(blk + oTMTask+10) = 0;              /* tmCount */
        tm = (QElemPtr)(blk + oTMTask);
        InsTime(tm);
        PrimeTime(tm, delayMs);       /* fire once after delayMs; then TMProc unhooks */
        JGNE_LM = (unsigned long)blk; /* go live AFTER the watchdog is primed */
        printf("armed blk=%08lX oldFilter=%08lX watchdog=%ldms TMProc=%08lX\n",
               (unsigned long)blk, *(unsigned long *)(blk + oOld),
               delayMs, (unsigned long)(blk + off));
        return 0;
    }
    if (argc >= 4 && !strcmp(argv[1], "snap")) {
        unsigned char *b = checkBlk(argv[2]);
        unsigned long  a5 = hexParse(argv[3]);
        if (!b) return 2;
        *(short *)(b + oSnapDone) = 0;
        *(unsigned long *)(b + oSnapA5) = a5;
        *(short *)(b + oSnapReq) = 1;
        printf("snapshot requested: target a5=%08lX (0=any); bring that app front\n", a5);
        return 0;
    }
    if (argc >= 5 && !strcmp(argv[1], "drive")) {
        /* drive <blk> <a5hex> <drvrRsrcPath> : configure ABJournalDRVR to target
         * the Apple menu's About item, then ask the filter to inject the entry
         * mouseDown at the Apple title into <a5>'s GetNextEvent + arm playback. */
        unsigned char *b = checkBlk(argv[2]);
        unsigned long  a5 = hexParse(argv[3]);
        Str255         pn;
        short          resRef, ref = 0, sb;
        OSErr          oe;
        DCtlHandle     dh;
        long          *jb;
        unsigned char *drv;
        if (!b) return 2;
        c2p(argv[4], pn);
        resRef = OpenResFile(pn);
        oe = OpenDriver("\p.ABJournal", &ref);
        dh = (DCtlHandle)GetDCtlEntry(ref);
        jb = (long *)NewPtrSys(32);
        if (!jb) { printf("NewPtrSys(journal blk) failed\n"); return 2; }
        jb[0] = ((long)28 << 16) | 40;  /* itemPt = About item (v=28, h=40) */
        jb[1] = 200;                     /* thresh */
        jb[2] = 0; jb[3] = 0; jb[4] = 0; jb[5] = 0; jb[6] = 0;
        jb[7] = 0;                       /* mode 0 = menu */
        if (dh) (**dh).dCtlStorage = (Handle)jb;
        sb  = *(short *)(b + oSnapBufOff);
        drv = b + sb + 1024;            /* drive fields live just past SnapBuf */
        *(short *)(drv + 2) = 0;                 /* DriveDid */
        *(unsigned long *)(drv + 4) = a5;        /* DriveA5  */
        *(short *)(drv + 8)  = 10;               /* DriveV = title v (menu bar) */
        *(short *)(drv + 10) = 12;               /* DriveH = Apple title x */
        *(short *)(drv + 0)  = 1;                /* DriveReq (arm last) */
        printf("drive armed: DRVR openErr=%d ref=%d jblk=%08lX target a5=%08lX; "
               "bring that app front (Apple->About)\n", oe, ref, (unsigned long)jb, a5);
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "peek")) {
        /* read-only hex dump -- identify foreign code hooked at $29A etc. */
        unsigned long  a = hexParse(argv[2]);
        long           n = (argc >= 4) ? numParse(argv[3]) : 64;
        unsigned char *p = (unsigned char *)a;
        long i, j;
        if (!a) { printf("bad addr '%s'\n", argv[2]); return 2; }
        if (n < 1) n = 64;
        if (n > 512) n = 512;
        printf("peek %08lX (%ld bytes) -> %s\n", a, n, classify(a));
        for (i = 0; i < n; i += 16) {
            printf("%08lX:", a + i);
            for (j = 0; j < 16 && i + j < n; j++) printf(" %02X", p[i + j]);
            printf("  ");
            for (j = 0; j < 16 && i + j < n; j++) {
                unsigned char c = p[i + j];
                putchar((c >= 32 && c < 127) ? (char)c : '.');
            }
            printf("\n");
        }
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "uninstall")) {
        unsigned char *b = checkBlk(argv[2]);
        unsigned long  old;
        if (!b) return 2;
        old = *(unsigned long *)(b + oOld);
        if (JGNE_LM != (unsigned long)b) {
            printf("already unhooked: $29A=%08lX (block %08lX not head)\n",
                   JGNE_LM, (unsigned long)b);
            return 0;
        }
        JGNE_LM = old;
        printf("uninstalled: $29A restored to %08lX\n", old);
        return 0;
    }
    if (argc >= 3 && (!strcmp(argv[1], "read") || !strcmp(argv[1], "disarm"))) {
        unsigned char *b = checkBlk(argv[2]);
        if (!b) return 2;
        if (!strcmp(argv[1], "disarm")) {
            *(short *)(b + oDisarm) = 1;
            printf("disarmed (block stays resident)\n");
        }
        dump(b);
        return 0;
    }
    printf("usage: jprobe2 zones | install <rsrc> | armwd <rsrc> <delayMs> | "
           "snap <hex> <a5hex> | drive <hex> <a5hex> <drvrPath> | "
           "read <hex> | disarm <hex> | uninstall <hex> | peek <hex> [n]\n");
    return 1;
}
