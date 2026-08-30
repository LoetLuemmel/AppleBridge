/* shim: newlib's <string.h> includes <strings.h>, which on a case-insensitive
 * filesystem resolves to Multiversal's Toolbox Strings.h. Keep it POSIX. */
#ifndef _SHIM_STRINGS_H
#define _SHIM_STRINGS_H
#include <stddef.h>
int strcasecmp(const char *, const char *);
int strncasecmp(const char *, const char *, size_t);
#endif
