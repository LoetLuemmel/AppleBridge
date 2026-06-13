/*
 * AppleBridge - Utility Functions
 * Logging and helper functions
 */

#include <applebridge.h>
#include <stdio.h>
#include <Events.h>

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
