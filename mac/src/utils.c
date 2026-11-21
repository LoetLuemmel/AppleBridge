/*
 * AppleBridge - Utility Functions
 * Logging and helper functions
 */

#include <applebridge.h>
#include <stdio.h>
#include <Events.h>
#include <Memory.h>      /* FreeMem, ApplicationZone - the heap watermark */

/*
 * Log a message (simple version without timestamps)
 * Uses TickCount for basic timing
 */
void LogMessage(const char *message)
{
    /* For now, just a no-op since we use StatusMessage in main.c */
    /* Could write to a log file if needed */
}

/*
 * Log an error with OSStatus code
 */
void LogError(const char *message, OSStatus err)
{
    /* For now, just a no-op */
    /* Could write to a log file if needed */
}

/* ---- heap watermark ------------------------------------------------------ */
/* The PEAK of bytes in use, not the lowest free value. The application heap on
 * System 7 is not created at its full size: it grows toward the partition's limit
 * on demand, so FreeMem() is "free in the part grown so far" and a low-water mark
 * of it measured nothing (first run on dev2019: heap 121 K, free 2.5 K, while a
 * 12 MB partition stood behind it). In use = zone size - FreeMem(), at its peak. */
static long gHeapPeak = 0;

void NoteHeap(void)
{
    long used = HeapZoneSize() - FreeMem();
    if (used > gHeapPeak) gHeapPeak = used;
}

long HeapPeak(void)
{
    return gHeapPeak;
}

/* How far the heap may still grow: the application limit minus the zone start. */
long HeapLimit(void)
{
    return (long)GetApplLimit() - (long)ApplicationZone();
}

/* Bytes in the application zone: the partition's heap, between the zone header
 * and its limit. Minus FreeMem() this is what is in use. */
long HeapZoneSize(void)
{
    THz z = ApplicationZone();
    return (long)z->bkLim - (long)z;
}
