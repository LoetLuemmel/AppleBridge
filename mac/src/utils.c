/*
 * AppleBridge - Utility Functions
 * Logging and helper functions
 */

#include "applebridge.h"
#include <stdio.h>
#include <stdarg.h>
#include <time.h>

/*
 * Log a message to stdout
 * In a real MPW tool, you might want to log to a file
 */
void LogMessage(const char *message)
{
    time_t now;
    char timeStr[64];

    time(&now);
    strftime(timeStr, sizeof(timeStr), "%Y-%m-%d %H:%M:%S", localtime(&now));

    printf("[%s] %s\n", timeStr, message);
    fflush(stdout);
}

/*
 * Log an error with OSStatus code
 */
void LogError(const char *message, OSStatus err)
{
    char buffer[512];

    sprintf(buffer, "%s (error: %d)", message, (int)err);
    LogMessage(buffer);
}
