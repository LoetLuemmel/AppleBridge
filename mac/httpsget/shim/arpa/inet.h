/* shim for classic Mac 68K: big-endian, so the byte-order functions are identities */
#ifndef _SHIM_ARPA_INET_H
#define _SHIM_ARPA_INET_H
#define htons(x) ((unsigned short)(x))
#define ntohs(x) ((unsigned short)(x))
#define htonl(x) ((unsigned long)(x))
#define ntohl(x) ((unsigned long)(x))
#endif
