# httpsget — the guest fetches `https://` itself

A 68K application for System 7 that performs a verified TLS 1.2/1.3 handshake and an
HTTP/1.0 GET, driven by the `mac_https_get` MCP tool. The classic Mac does the
cryptography; the host only resolves the name and moves the files.

- **TLS:** [Crypto Ancienne](https://github.com/classilla/cryanc) (C89, socket-agnostic),
  with the two patches in this directory — client-side HelloRetryRequest
  (upstream [#24](https://github.com/classilla/cryanc/pull/24)) and the TLS 1.3 certificate
  parser fix without which **no certificate is ever verified in TLS 1.3**
  (upstream [#25](https://github.com/classilla/cryanc/pull/25)). Apply with `git apply`
  in a cryanc checkout, or use the fork branch `LoetLuemmel/cryanc:system7`.
- **Socket layer:** the MacTCP driver API (`MacTCP.h` here is a TCP-only rewrite —
  Retro68's Multiversal interfaces ship neither MacTCP nor Open Transport headers).
  Works natively and through Open Transport's MacTCP compatibility.
- **Trust:** `roots/roots.pem` (13 public roots exported from a macOS keychain, 2026-08-30)
  compiled in as `roots.h`; `tls_default_verify` checks dates, chain signatures, the subject
  against the SNI name and the root. Regenerate `roots.h` from the PEM with a one-line
  script when the bundle changes.
- **Entropy:** cryanc's `TLS_ENTROPY_HOOK`, fed with tick-boundary iteration counts, the
  mouse, the clock and addresses. Not a proof of randomness; better than the date alone.

## Protocol with the tool

The app lives in one folder with three files, all in that folder (the app's default
directory at launch):

| File | Written by | Content |
|---|---|---|
| `https.req` | tool (`mac_write_file`) | six lines: nonce, IP, port, host name, path, `12`/`13` |
| `https.out` | app | the raw HTTP response, headers and body |
| `https.log` | app, at the END of the run | the run's log; first lines echo `nonce <nonce>` |

The tool launches the app (`LAUNCH:` verb), then polls `https.log` until it carries this
request's nonce — the only way to tell a fresh result from the previous run's file without
a delete verb. The app draws a window with the same log lines and quits half a second
after writing the log.

## Measured (Basilisk II, 68030 model, System 7.6.1)

| Step | Ticks (60/s) |
|---|---|
| TCP connect | 7–10 |
| TLS 1.3 handshake, HelloRetryRequest → P-521, chain verified (3 RSA) | 270–335 |
| TLS 1.3 handshake, P-256/X25519 accepted first, unverified | 17–39 |
| whole `mac_https_get` call, 27 KB page | 6–7 s |

## Building

Retro68 and CMake, see `CMakeLists.txt`. The console library (`CONSOLE`) must not be used —
those apps die silently on this guest. `-fno-jump-tables` is required with gcc 16.
