/* tlsfetch — http(s) GET for classic Mac OS: MacTCP driver API for TCP and UDP
 * (native, or through Open Transport's compatibility layer), a DNS A-query over
 * UDP, TLS 1.2/1.3 via Crypto Ancienne with the certificate chain verified
 * against a built-in root bundle. Built with Retro68; cryanc needs 64-bit
 * integers, which MPW SC does not have.
 *
 * The caller supplies three callbacks: yield (run the event loop once — every
 * wait in here yields, so the daemon and the UI stay alive), a log line sink,
 * and a data sink for the decrypted response bytes (headers and body, as they
 * arrive). Nothing is buffered here beyond one network chunk. */
#ifndef TLSFETCH_H
#define TLSFETCH_H

typedef void (*tf_yield_fn)(void);
typedef void (*tf_log_fn)(const char *line);
/* return 0 to keep reading, non-zero to stop (the connection is closed) */
typedef int  (*tf_sink_fn)(const unsigned char *data, long len);

typedef struct {
    tf_yield_fn yield;
    tf_log_fn   log;
    tf_sink_fn  sink;
    unsigned long dns_server;   /* IPv4, host order; 0 = default 10.0.2.3 (slirp) */
    int         tls12;          /* offer TLS 1.2 instead of 1.3 */
    int         verify;         /* 1 = verify the chain (default); 0 = spike mode */
} tf_config;

/* parsed URL */
typedef struct {
    int  https;
    char host[128];
    int  port;
    char path[512];
} tf_url;

int  tf_parse_url(const char *url, tf_url *u);               /* 1 ok, 0 malformed */
int  tf_resolve(const tf_config *cfg, const char *host, unsigned long *ip); /* 0 ok, else error */
/* Full GET. Returns 0 on success (response delivered to sink), negative on error;
 * the log carries the reason. Timings (ticks) are logged. */
int  tf_get(const tf_config *cfg, const tf_url *u);

/* Timings of the last tf_get, in ticks (60/s) */
extern long tf_ticks_dns, tf_ticks_connect, tf_ticks_handshake, tf_ticks_total;
extern int  tf_read_timeout, tf_last_state;
extern char tf_cipher[64];

#endif
