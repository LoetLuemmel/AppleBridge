/*
 * AppleBridge - Auth digest + nonce (protocol v0.2, see docs/PROTOCOL_v0.2.md)
 *
 * The shared-secret handshake proof is FNV-1a-64 of (nonce || token). MPW C has
 * no 64-bit integer type, so the hash is computed with two 32-bit halves; the
 * result is byte-for-byte identical to the host's host_server.fnv1a64(), which
 * the host-edge tests pin with the canonical FNV-1a vectors.
 *
 * Digest convention (must match the host): the NONCE is hashed as its ASCII-hex
 * string exactly as it travels on the wire — neither side decodes it to bytes.
 * So proof = FNV1a64( <nonce-hex-ascii> concatenated with <token-ascii> ).
 *
 * This is obfuscation-grade, sized for a NAT'd-LAN threat model. The ABDigestHex
 * seam lets a compact SHA-1 replace FNV later with no wire change.
 */
#ifndef AB_AUTH_H
#define AB_AUTH_H

/* FNV-1a-64 of msg[0..msgLen) followed by token[0..tokenLen), written to `out`
 * as 16 lowercase hex chars + a NUL (so `out` must hold >= 17 bytes). */
void ABDigestHex(const unsigned char *msg, long msgLen,
                 const char *token, long tokenLen, char *out);

/* Fill `outHex` with 16 hex chars + NUL: 8 bytes of connect-time entropy
 * (Microseconds + TickCount mixed). NOT cryptographically strong — a weak nonce
 * only weakens replay resistance, an accepted v0.2 limitation. `outHex` >= 17. */
void ABMakeNonce(char *outHex);

#endif /* AB_AUTH_H */
