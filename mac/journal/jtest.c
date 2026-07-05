/*
 * jtest.c -- step-3 gate for the journaling DRVR.
 *
 * Proves (or disproves) that the Toolbox Event Manager consults a playback
 * journal driver on THIS system (Basilisk II ROM). Installs ABJournalDRVR,
 * arms playback (JournalFlag < 0), takes ONE synchronous Button() reading with
 * no intervening yield, then disarms. The driver feeds jcButton = TRUE, so:
 *
 *   Button() idle  -> FALSE   (no mouse pressed)
 *   Button() armed -> TRUE    ONLY if the Event Manager routed the query
 *                             through our driver == the mechanism works.
 *
 * Safe: the armed window spans a single Button() trap (no SystemTask /
 * WaitNextEvent between arm and disarm, so no other process is scheduled), and
 * the driver auto-disarms on the first jcEvent as a backstop.
 *
 * Build (MPW tool):
 *   SC jtest.c -o jtest.c.o
 *   Link -o jtest -t MPST -c 'MPS ' jtest.c.o \
 *        "{LIBS}CLibraries:StdCLib.o" "{LIBS}Libraries:Interface.o" \
 *        "{LIBS}Libraries:ToolLibs.o" "{LIBS}Libraries:Runtime.o"
 */
#include <Devices.h>
#include <LowMem.h>
#include <Resources.h>
#include <Events.h>
#include <stdio.h>

/* LowMem.h exposes LMGet/SetJournalRef but NOT the JournalFlag accessors on
 * this MPW, so poke $08DE directly (a word: 0 = off, negative = playback). */
#define JOURNAL_FLAG (*(volatile short *)0x08DEL)

int main(void)
{
    short       resFile, refNum = 0, jref;
    OSErr       err;
    Boolean     bBefore, bArmed;
    EventRecord ev;
    DCtlHandle  dh;

    resFile = OpenResFile("\pMeinMac:MPW:Journal:ABJournalDRVR");
    printf("OpenResFile -> refNum=%d ResError=%d\n", resFile, ResError());

    {   /* is the DRVR resource actually visible to the Resource Manager? */
        Handle h = Get1NamedResource('DRVR', "\p.ABJournal");
        printf("Get1NamedResource('DRVR','.ABJournal') -> %s ResError=%d\n",
               h ? "FOUND" : "nil", ResError());
        if (h) {
            short id; ResType ty; Str255 nm; SignedByte at;
            GetResInfo(h, &id, &ty, nm);
            at = GetResAttrs(h);
            nm[nm[0] + 1] = 0;
            printf("  DRVR id=%d attrs=0x%02X name='%s'\n",
                   id, (unsigned char)at, (char *)nm + 1);
        }
    }

    {   /* System 7 OpenDriver uses the DRVR resource ID as the unit-table slot.
         * Report the table size + a free high slot so we can pick a valid ID. */
        short   cnt   = *(short *)0x01D2L;        /* UnitNtryCnt */
        Handle *utab  = *(Handle **)0x011CL;      /* UTableBase  */
        short   i, freeHi = -1;
        for (i = cnt - 1; i >= 0; i--)
            if (utab[i] == 0L) { freeHi = i; break; }
        printf("UnitNtryCnt=%d  highest-free-unit=%d  (ID 128 valid? %s)\n",
               cnt, freeHi, (128 < cnt) ? "yes" : "NO -> badUnitErr");
    }

    err = OpenDriver("\p.ABJournal", &refNum);
    printf("OpenDriver('.ABJournal') -> err=%d driverRefNum=%d\n", err, refNum);

    jref = LMGetJournalRef();
    printf("JournalRef after open = %d (self-register OK if == %d)\n", jref, refNum);

    bBefore = Button();
    printf("Button() idle  = %d (expect 0)\n", bBefore);

    JOURNAL_FLAG = -1;              /* arm playback */
    bArmed = Button();             /* jcButton probe */
    EventAvail(everyEvent, &ev);   /* jcEvent probe (our driver auto-disarms here) */
    JOURNAL_FLAG = 0;              /* ensure disarmed (no yield above) */

    printf("Button() armed = %d (expect 1 if the driver was consulted)\n", bArmed);

    /* Did the Event Manager actually CALL our driver's Control routine, and with
     * what? The driver counts calls in dCtlStorage and captures the last
     * csCode/journal-code in dCtlPosition. */
    dh = (DCtlHandle)GetDCtlEntry(refNum);
    if (dh) {
        long calls = (long)(**dh).dCtlStorage;
        long p28   = (**dh).dCtlPosition;          /* raw long @ csParam+0 (pb 28) */
        long p32   = (long)(**dh).dCtlWindow;      /* raw long @ csParam+4 (pb 32) */
        printf("driver Control calls while armed = %ld\n", calls);
        printf("  csParam bytes: @28=0x%08lX  @32=0x%08lX  (csCode was 16)\n", p28, p32);
    } else {
        printf("GetDCtlEntry(%d) -> nil\n", refNum);
    }

    if (refNum) CloseDriver(refNum);

    if (bArmed && !bBefore)
        printf("RESULT: PASS - Event Manager consulted the playback journal DRVR\n");
    else
        printf("RESULT: FAIL - not consulted (armed=%d idle=%d jref=%d refNum=%d)\n",
               bArmed, bBefore, jref, refNum);
    return 0;
}
