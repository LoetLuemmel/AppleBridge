/*
 * AppleBridge - Auth digest + nonce (see auth.h / docs/PROTOCOL_v0.2.md).
 *
 * FNV-1a-64 with no 64-bit integer type: the 64-bit hash state is carried as
 * two `unsigned long` halves (hhi, hlo). Only the full 64-bit product is needed
 * for hlo*prime_low; the two cross terms need just their low 32 bits, which a
 * native 32-bit `unsigned long` multiply already yields. Validated against the
 * host's FNV-1a-64 vectors ("" , "a", "foobar") before this was written.
 */

#include "auth.h"
#include <Events.h>    /* TickCount */
#include <Timer.h>     /* Microseconds, UnsignedWide */

/* FNV-1a-64 prime 0x100000001b3 split into high/low 32-bit halves. */
#define FNV_PRIME_HI 0x00000100UL
#define FNV_PRIME_LO 0x000001b3UL

/* 32x32 -> 64 unsigned multiply (hi:lo), using only <=32-bit intermediate
 * products, so it is correct whatever the compiler's `int` width. */
static void mul32(unsigned long a, unsigned long b,
                  unsigned long *hi, unsigned long *lo)
{
    unsigned long a0 = a & 0xFFFFUL, a1 = (a >> 16) & 0xFFFFUL;
    unsigned long b0 = b & 0xFFFFUL, b1 = (b >> 16) & 0xFFFFUL;
    unsigned long p00 = a0 * b0;
    unsigned long p01 = a0 * b1;
    unsigned long p10 = a1 * b0;
    unsigned long p11 = a1 * b1;
    unsigned long mid = (p00 >> 16) + (p01 & 0xFFFFUL) + (p10 & 0xFFFFUL);
    *lo = (p00 & 0xFFFFUL) | (mid << 16);
    *hi = p11 + (p01 >> 16) + (p10 >> 16) + (mid >> 16);
}

/* One FNV-1a byte step on the 64-bit state (hhi:hlo). */
static void fnvStep(unsigned long *hhi, unsigned long *hlo, unsigned char byte)
{
    unsigned long thi, tlo;
    unsigned long lo = *hlo ^ (unsigned long)byte;   /* XOR first (FNV-1a) */
    unsigned long hi = *hhi;
    mul32(lo, FNV_PRIME_LO, &thi, &tlo);             /* full 64-bit hlo*plo */
    *hlo = tlo;
    /* high half = thi + low32(hlo*phi) + low32(hhi*plo); native mul truncates
     * to 32 bits, which is exactly the low word each cross term contributes. */
    *hhi = thi + (lo * FNV_PRIME_HI) + (hi * FNV_PRIME_LO);
}

static const char kHex[] = "0123456789abcdef";

static char *putHex32(char *p, unsigned long v)
{
    short i;
    for (i = 28; i >= 0; i -= 4) *p++ = kHex[(v >> i) & 0xFUL];
    return p;
}

void ABDigestHex(const unsigned char *msg, long msgLen,
                 const char *token, long tokenLen, char *out)
{
    unsigned long hhi = 0xcbf29ce4UL, hlo = 0x84222325UL;   /* FNV offset basis */
    char *p = out;
    long i;

    for (i = 0; i < msgLen; i++)   fnvStep(&hhi, &hlo, msg[i]);
    for (i = 0; i < tokenLen; i++) fnvStep(&hhi, &hlo, (unsigned char)token[i]);

    p = putHex32(p, hhi);
    p = putHex32(p, hlo);
    *p = '\0';
}

void ABMakeNonce(char *outHex)
{
    UnsignedWide us;
    unsigned long t = (unsigned long)TickCount();
    unsigned long a, b;
    char *p = outHex;

    Microseconds(&us);
    /* Mix microseconds + ticks; Knuth multiplicative spread on the ticks so a
     * near-identical Microseconds pair across a fast reconnect still differs. */
    a = us.lo ^ t;
    b = us.hi ^ (t * 2654435761UL);

    p = putHex32(p, a);
    p = putHex32(p, b);
    *p = '\0';
}
