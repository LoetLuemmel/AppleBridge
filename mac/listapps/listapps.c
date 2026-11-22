/* ListApps 3 - MPW tool: the applications ('APPL') on a volume, largest first.
 *
 *   ListApps "<volume>:" <outfile> [top=50] [seconds=120]
 *
 * Version 1 walked folder by folder with PBGetCatInfo and never yielded: on a
 * PowerBook Duo 230 (33 MHz, a 1993 disk) it froze the whole Mac for minutes,
 * and Command-period could not stop it (2026-11-22). This one:
 *   - asks the File Manager itself: PBCatSearch for fdType 'APPL', one
 *     sequential pass over the catalog, each call limited to 100 ms and
 *     resumed from its catalog position;
 *   - yields after every call (SpinCursor): other processes run, the cursor
 *     spins, Command-period cancels;
 *   - stops after <seconds> and writes what it has, marked PARTIAL;
 *   - builds full paths only for the <top> entries it writes;
 *   - keeps only the <top> largest in memory (ListApps 2 reserved room for 800
 *     and stopped with "out of memory" in the Duo's small ToolServer, 2026-11-22).
 * Output: a summary line, then <size> TAB <disk size> TAB <creator> TAB <path>.
 *
 * Build (MPW, LIBS = "<disk>:Interfaces&Libraries:Libraries:"; NO Stubs.o - it
 * silences a tool's stdio):
 *   SC -model far listapps.c -o listapps.o
 *   Link -model far -t MPST -c "MPS " -o ListApps listapps.o "{LIBS}Libraries:MacRuntime.o" ∂
 *        "{LIBS}Libraries:IntEnv.o" "{LIBS}Libraries:Interface.o" "{LIBS}Libraries:ToolLibs.o" ∂
 *        "{LIBS}CLibraries:StdCLib.o"
 * Measured 2026-11-22: Duo 230 (System 7.1), 266 applications in 15 s, the bridge
 * stayed connected throughout; dev2019, 669 in 0.3 s. */
#include <Files.h>
#include <Events.h>
#include <CursorCtl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXTOP    200             /* the most entries ever kept */
#define BATCH     16              /* matches per PBCatSearch call */
#define CALL_MS   100             /* each call's time budget (Time Manager format: ms) */

typedef struct {
    long   size;                  /* logical: data + resource fork */
    long   disk;                  /* physical */
    OSType creator;
    FSSpec spec;
} App;

static App  *gApps;               /* the <top> largest so far, largest first */
static long  gKept;               /* entries in gApps */
static long  gCount;              /* applications seen */

/* Keep a if it is among the <top> largest so far (insertion into a sorted list). */
static void Keep(const App *a, long top)
{
    long i;
    if (gKept == top && a->size <= gApps[gKept - 1].size) return;
    i = (gKept < top) ? gKept++ : gKept - 1;
    while (i > 0 && gApps[i - 1].size < a->size) { gApps[i] = gApps[i - 1]; i--; }
    gApps[i] = *a;
}

/* Full path "Vol:Folder:...:Name" for a spec, by walking the parent chain. */
static void FullPath(const FSSpec *s, char *out, size_t max)
{
    char  tail[512];
    Str63 name;
    CInfoPBRec pb;
    long  dir = s->parID;

    memcpy(tail, s->name + 1, s->name[0]);
    tail[s->name[0]] = '\0';
    while (dir != fsRtParID) {
        char tmp[512];
        memset(&pb, 0, sizeof(pb));
        pb.dirInfo.ioNamePtr   = name;
        pb.dirInfo.ioVRefNum   = s->vRefNum;
        pb.dirInfo.ioFDirIndex = -1;            /* info about the directory itself */
        pb.dirInfo.ioDrDirID   = dir;
        if (PBGetCatInfo(&pb, false) != noErr) break;
        if (name[0] + strlen(tail) + 2 >= sizeof(tmp)) break;
        memcpy(tmp, name + 1, name[0]);
        tmp[name[0]] = ':';
        strcpy(tmp + name[0] + 1, tail);
        strcpy(tail, tmp);
        dir = pb.dirInfo.ioDrParID;
    }
    strncpy(out, tail, max - 1);
    out[max - 1] = '\0';
}

int main(int argc, char *argv[])
{
    Str255      vol;
    FSSpec      root, found[BATCH];
    CInfoPBRec  spec1, spec2, info;
    CSParam     cs;
    OSErr       err;
    FILE       *out;
    long        top = 50, seconds = 120, i, calls = 0;
    unsigned long t0 = TickCount(), limit;
    Boolean     partial = false;
    char        path[512];

    if (argc < 3) { fprintf(stderr, "usage: ListApps \"<volume>:\" <outfile> [top] [seconds]\n"); return 1; }
    if (argc > 3) top = atol(argv[3]);
    if (top < 1) top = 1;
    if (top > MAXTOP) top = MAXTOP;
    if (argc > 4) seconds = atol(argv[4]);
    limit = (unsigned long)seconds * 60UL;
    vol[0] = (unsigned char)strlen(argv[1]);
    memcpy(vol + 1, argv[1], vol[0]);
    if (FSMakeFSSpec(0, 0, vol, &root) != noErr) { fprintf(stderr, "ListApps: no volume %s\n", argv[1]); return 2; }
    gApps = (App *)NewPtr((Size)sizeof(App) * top);
    if (gApps == NULL) { fprintf(stderr, "ListApps: out of memory\n"); return 3; }
    InitCursorCtl(NULL);

    /* files only (attribute bit 4 clear), Finder type exactly 'APPL' */
    memset(&spec1, 0, sizeof(spec1));
    memset(&spec2, 0, sizeof(spec2));
    spec1.hFileInfo.ioFlAttrib = 0x00;
    spec2.hFileInfo.ioFlAttrib = 0x10;
    spec1.hFileInfo.ioFlFndrInfo.fdType = 'APPL';
    spec2.hFileInfo.ioFlFndrInfo.fdType = (OSType)0xFFFFFFFF;

    memset(&cs, 0, sizeof(cs));
    cs.ioVRefNum       = root.vRefNum;
    cs.ioMatchPtr      = found;
    cs.ioReqMatchCount = BATCH;
    cs.ioSearchBits    = fsSBFlAttrib + fsSBFlFndrInfo;
    cs.ioSearchInfo1   = &spec1;
    cs.ioSearchInfo2   = &spec2;
    cs.ioSearchTime    = CALL_MS;
    cs.ioCatPosition.initialize = 0;         /* start at the beginning */

    for (;;) {
        err = PBCatSearchSync(&cs);
        calls++;
        for (i = 0; i < cs.ioActMatchCount; i++) {
            memset(&info, 0, sizeof(info));
            info.hFileInfo.ioNamePtr = found[i].name;
            info.hFileInfo.ioVRefNum = found[i].vRefNum;
            info.hFileInfo.ioDirID   = found[i].parID;
            if (PBGetCatInfo(&info, false) == noErr) {
                App a;
                a.size    = info.hFileInfo.ioFlLgLen + info.hFileInfo.ioFlRLgLen;
                a.disk    = info.hFileInfo.ioFlPyLen + info.hFileInfo.ioFlRPyLen;
                a.creator = info.hFileInfo.ioFlFndrInfo.fdCreator;
                a.spec    = found[i];
                gCount++;
                Keep(&a, top);
            }
        }
        if (err == eofErr) break;                             /* whole catalog searched */
        if (err == catChangedErr) { cs.ioCatPosition.initialize = 0; gCount = gKept = 0; continue; }
        if (err != noErr) { fprintf(stderr, "ListApps: PBCatSearch error %d\n", err); partial = true; break; }
        if (TickCount() - t0 > limit) { partial = true; break; }
        SpinCursor(1);                                        /* yield; Command-period lands here */
    }

    out = fopen(argv[2], "w");
    if (out == NULL) { fprintf(stderr, "ListApps: cannot write %s\n", argv[2]); return 4; }
    fprintf(out, "# ListApps 3: %ld applications on %s%s, %ld calls, %ld ticks\n", gCount, argv[1],
            partial ? " (PARTIAL)" : "", calls, (long)(TickCount() - t0));
    for (i = 0; i < gKept; i++) {
        OSType c = gApps[i].creator;
        FullPath(&gApps[i].spec, path, sizeof(path));
        fprintf(out, "%ld\t%ld\t%c%c%c%c\t%s\n", gApps[i].size, gApps[i].disk,
                (char)(c >> 24), (char)(c >> 16), (char)(c >> 8), (char)c, path);
        if ((i & 7) == 7) SpinCursor(1);
    }
    fclose(out);
    printf("ListApps 3: %ld applications%s, %ld calls, %ld ticks\n", gCount,
           partial ? " (PARTIAL)" : "", calls, (long)(TickCount() - t0));
    return 0;
}
