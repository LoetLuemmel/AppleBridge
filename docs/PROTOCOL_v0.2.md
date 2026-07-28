# AppleBridge Wire Protocol v0.2 — Design & Migration

Status: **draft** (2026-07-02). Companion to Phase 2 of the roadmap ledger.
Supersedes the implicit v0.1 protocol described in `CLAUDE.md` / `ARCHITECTURE.md`.

This document specifies three additions and the path to ship them **without a
lockstep host+daemon upgrade**:

1. **Version negotiation** (`HELLO:` handshake) — must land first.
2. **Shared-secret auth + bounded reads** — the P3 security backlog.
3. **Persistent control-port sessions** — host-side only (see the framing note).

---

## 0. v0.1 as it actually is (grounded in the code)

Establishing the true baseline, because two roadmap phrasings are slightly off.

### Connection model

```
MCP / send_command.py ──:9001 (127.0.0.1)──▶ host_server.py ──:9000 (LAN .154)──▶ Mac daemon
      one command per TCP conn                 accept-loop, one daemon             persistent socket
      (accept → read → reply → close)          at a time (listen(1))               + app-level heartbeat
```

- **`:9000`** — the Mac daemon dials OUT and holds the socket open for the whole
  session (`main.c` main loop: one `ABConnect`, then `ABRecv` in a loop; host
  re-`accept()`s only on drop). **This link is already persistent.** The host is
  the active heartbeat party (PING every 10 s; `HEARTBEAT_*` in `host_server.py`).
- **`:9001`** — bound to `127.0.0.1` only (`run_control_server`). Each control
  request is one connection: `accept → _recv_control_command (read to \n\n or EOF)
  → reply → close`. **This** is the "one-request-per-connection" the ledger means;
  it is loopback, so its threat surface is local processes, not the LAN.

⇒ Roadmap correction: "persistent connections (highest guest risk)" splits into
**(a)** persistent `:9001` sessions — *host-only, low risk*, and **(b)** the
`HELLO`/auth handshake — *the actual guest rebuild and the real risk item*. The
guest link needs no "persistence" work; it is already persistent.

### Request framing (host → daemon)

- **Length-framed:** `COMMAND:<len>\n<payload>` — reassembled by declared length
  (`TopUpCommand`), validated `0 < len < MAX_COMMAND_LENGTH` (8192) in
  `ParseCommand`. Also length-framed: `AESEND:…\n<do>`, `CLIPSET:<len>\n<data>`,
  `WRITEFILE:…\n<forks>`. `AESEND` is
  `AESEND:<targetHex8>:<classHex8>:<idHex8>:<doLen>[:<waitTicks>]\n<do>`; the
  wait is **optional and additive**, so an older daemon parses the request
  unchanged and applies its own default (0.8d31 — see R16 in
  `docs/INSTALLER_REQUIREMENTS.md` for why the wait is bounded at all).
- **Single-recv verbs**, prefix-matched by `strncmp` in `ProcessRequest`:
  `PING`, `STAT`, `SCREENSHOT`, `LAUNCH:`, `QUIT:`, `QUITDAEMON`, `REBOOT`,
  `KEY:`, `TYPE:`, `CLICK:`, `CLIPGET`, `READFILE:`, `LISTDIR:`.

### Response framing (daemon → host)

- Command/verb: `STATUS:<code>\rSTDOUT:<olen>\r<olen bytes>\rSTDERR:<elen>\r<elen bytes>\r\r`
  — read by **declared length** (`_read_framed_response`); CR or LF accepted.
- Screenshot: `IMAGE:<w>:<h>:<depth>:<rowBytes>:<clutCount>:<dataSize>\n<CLUT><pixels>`.
- File: `FILE:<typeHex8>:<creatorHex8>:<dataLen>:<rsrcLen>\n<data><rsrc>`.

### Verified backward-compat lever

A v0.1 daemon answers an **unknown verb** (anything not matching a `PROTO_`
prefix and not `COMMAND:`) with a clean, length-framed
`STATUS:-1 … STDERR: Invalid command format` (`main.c:1329`). **No desync.** This
is exactly what lets a new host detect an old daemon: send `HELLO:…`, and the
*absence* of an `ABVERSION:` reply means "legacy peer".

### Current gaps (what v0.2 closes)

- **No identity check.** `:9000` is LAN-bound; any host on the subnet can accept
  the daemon's dial (a fake daemon can feed Claude bogus files/screenshots) or,
  as a rogue server at `.154`, issue commands the real Mac executes.
- **Host trusts daemon-declared lengths unboundedly.** `_read_exact(olen)`,
  READFILE `dataLen`/`rsrcLen`, IMAGE `dataSize` have no host-side ceiling — a
  buggy/hostile daemon can pin the host in a read until timeout.
- **Control port has no request-size bound** (`_recv_control_command` reads to
  `\n\n`/EOF with only a 6 s timeout).

---

## 1. Version negotiation — `HELLO:`

**Goal:** every session begins by agreeing a protocol version, so a v0.2 host can
serve a v0.1 daemon (and vice-versa) during a staged rollout.

### Sequence (host is the prober; it already speaks first)

```
daemon connects to :9000
host → "PING"                     (existing priming send; absorbs the known
host ← <reply, discarded>          first-packet-corruption on a fresh MACNAT conn)
host → "HELLO:2:<hostNonceHex>\n"  (hostNonce = 8 random bytes → 16 hex, or empty if no auth)
host ← STATUS frame, STDOUT =
       "ABVERSION:2;FEAT=auth;NONCE=<daemonNonceHex>;PROOF=<proofHex>"
```

- **v0.2 daemon:** replies with the `ABVERSION:` capability line in STDOUT of a
  normal STATUS frame (so old host readers parse it as ordinary output — no new
  response shape).
- **v0.1 daemon:** `HELLO:` is an unknown verb → `STATUS:-1 … Invalid command
  format`. Host sees no `ABVERSION:` → **negotiated version = 1 (legacy)**, no
  auth, proceed exactly as today.

Negotiated version = `min(hostVer, daemonVer)`. `FEAT=` is a CSV of optional
capabilities (`auth`, later `persist`, `compress`) so features can be added
without bumping the integer each time.

**Why HELLO rides in STDOUT of a STATUS frame** (not a brand-new top-level frame):
it reuses the length-framed reader on both sides, and an old host (v0.1) that ever
meets a v0.2 daemon just sees a normal command reply it can ignore.

---

## 2. Auth — shared-secret challenge/response

**Opt-in:** keyed by a token present on *both* sides. No token ⇒ auth skipped ⇒
today's zero-config behaviour is unchanged. Set to require it once both peers are
v0.2.

- Guest: `TOKEN=<secret>` line in **AppleBridge Prefs** (new `prefs.h` field).
- Host: `APPLEBRIDGE_TOKEN` env var (read in `host_server.py`; never a file in the
  TCC-protected repo).

### Control-port guard (`:9001`, a separate boundary)

The token above guards the **daemon ↔ host** link. The local **control port**
(`:9001`, loopback-only) is a *different* trust boundary — it defends against other
local processes/users on the same host — and has its own independent, opt-in guard:

- Host: `APPLEBRIDGE_CTRL_TOKEN` env var (read in `host_server.py`). Unset ⇒ the port
  is open and behaviour is unchanged (the default).
- Clients (`send_command.py`, `build.py`, the MCP layer via `mac_connection.py`, the
  `smoke_e2e.py` tier) read the same env var and, when it is set, lead each request
  with an `AUTH:<token>\n` line. The line is always stripped when present, so a
  token-configured client works against an open server too.
- Enforcement is **fail-closed**: with `APPLEBRIDGE_CTRL_TOKEN` set on the host, a
  request with a missing or mismatched token gets `STATUS:-1 … control auth required`
  and never reaches the daemon. Compared in constant time (`hmac.compare_digest`).

The two tokens are independent — you can guard either boundary alone, or both with
different secrets. Pure-logic helpers `split_ctrl_auth` / `ctrl_authorized` are
unit-tested in `tests/test_ctrl_auth.py`.

### Who authenticates whom (and why)

| Direction | Purpose | Nonce generated by |
|---|---|---|
| **Daemon authenticates host** | stop a rogue server issuing commands to the Mac | daemon (`daemonNonce`) |
| **Host authenticates daemon** | stop a fake daemon feeding Claude bad data | host (`hostNonce`) |

Both, folded into the HELLO round trip + one follow-up:

```
host → "HELLO:2:<hostNonceHex>\n"
host ← "ABVERSION:2;FEAT=auth;NONCE=<daemonNonceHex>;PROOF=<H(hostNonce||token)>"
        ── host verifies PROOF proves the daemon knows the token ──
host → "AUTH2:<H(daemonNonce||token)>\n"
host ← "STATUS:0 …"   (daemon verified the host; now authenticated)
        ── on mismatch either side drops the link; daemon reconnects ──
```

- Mismatch on the daemon side: close the connection and fall back to the normal
  reconnect/backoff loop (an attacker gets no oracle beyond "rejected").
- Mismatch on the host side: drop, log `auth failed from <addr>`, re-accept.

### Nonce hashing convention (pinned)

The nonce is hashed **as its ASCII-hex string exactly as it travels on the wire**
— neither side decodes it to raw bytes first. So:

```
proof = H( <nonce-hex-ascii-bytes> concatenated with <token-ascii-bytes> )
```

This keeps the 68K side trivial (it hashes the received `hostNonce` characters
directly) and removes any endian/decoding ambiguity. Cross-implementation golden
vectors (host `ab_digest` == daemon `ABDigestHex`), for regression pinning:

| nonce (hex ascii) | token | proof |
|---|---|---|
| `1122334455667788` | `s3cret` | `cfcf7d300083ee67` |
| `deadbeefcafef00d` | `hunter2` | `0b16a20e04ade276` |

### Digest function `H(msg)`

Constraint: must be implementable in plain C on a 68K Mac (no crypto toolbox in
System 7.6). Recommendation, in order:

1. **FNV-1a-64** over `nonce_bytes || token_bytes`, emitted as 16 hex chars.
   ~10 lines of C, no tables. **Obfuscation-grade, not cryptographic** — it defeats
   casual connection and non-capture replay, which matches a NAT'd-LAN hobby
   threat model. Recommended default.
2. **Compact public-domain SHA-1** (one `.c`, ~200 lines, fits 68K) truncated to
   128 bits if we want real pre-image/replay resistance. Heavier; propose as a
   follow-up if the threat model hardens.

Nonces: host uses `os.urandom(8)` (strong). The daemon has no strong RNG — mix
`TickCount()`, `Microseconds()`, `LMGetTicks`, and the mouse position into a
64-bit seed. **Caveat:** the daemon nonce is weakly random, so the
*host-authenticates-daemon* direction is replay-weak across reboots if an attacker
can both capture and predict; documented as an accepted limitation for v0.2, closed
later by the SHA-1 option + a monotonic per-session counter.

---

## 3. Bounded reads

Both directions reject an oversized *declared* length **before** allocating or
reading, so a corrupt/hostile length can neither OOM nor hang-until-timeout.

### Host side (`host_server.py`)

- New ceiling `MAX_DECLARED = 8 * 1024 * 1024` (matches the guest's
  `MAX_FILE_BYTES` / `MAX_DYNAMIC_RESPONSE`).
- In `_read_framed_response`, READFILE, and screenshot readers: if the parsed
  `olen` / `dataLen` / `rsrcLen` / `dataSize` exceeds `MAX_DECLARED` → log, drop
  the daemon link (`_mark_disconnected`), return `None`. Never `_read_exact` an
  unbounded declared length.
- `_recv_control_command`: cap accumulation at `MAX_CTRL_REQUEST` (e.g. 12 MB to
  cover base64'd `mac_put_file`); over-limit → reject with an error frame.

### Daemon side

- `COMMAND:` is already bounded (`ParseCommand` < 8192).
- Verify/enforce `WRITEFILE`/`CLIPSET`/`AESEND` declared lengths ≤ `MAX_FILE_BYTES`
  and reject with a framed error (not a silent truncate) when exceeded.
- `HELLO`/`AUTH2` payloads are fixed-width hex; parse into fixed buffers with a
  hard cap.

---

## 4. Persistent control-port sessions (host-only)

Independent of the guest; ship any time after §1. Turns `:9001` from
one-command-per-connection into a keep-alive session:

- Loop on the same `ctrl_conn`, reading successive `\n\n`-delimited (or
  `LEN:<n>\n`-framed) requests until EOF. `send_command.py`'s shutdown-on-EOF path
  is unaffected; the MCP client can pipeline.
- **Serialise against the single daemon link:** only one command in flight to the
  daemon at a time. A simple mutex/queue also satisfies the separate
  "multi-client arbitration" P3 item (queue or cleanly reject a second concurrent
  client) — do them together.

Low risk, but sequence it *after* auth so a persistent session inherits the
authenticated link rather than re-opening the design surface twice.

---

## 5. Migration path

The invariant: **either side upgrades independently; no lockstep.** Guaranteed by
§1 (probe → fall back to legacy) and by making the v0.2 daemon tolerate a v0.1
host (no HELLO before the first real request ⇒ run legacy, no auth).

### Compatibility matrix

| Host ↓ / Daemon → | v0.1 daemon | v0.2 daemon |
|---|---|---|
| **v0.1 host** | legacy (today) | daemon sees no `HELLO` → legacy, no auth |
| **v0.2 host** | probe: no `ABVERSION` → legacy | full v0.2; auth iff both hold a token |

### Steps

1. **Ship the v0.2 host first (non-breaking).** Adds the HELLO probe (falls back
   to legacy against the *currently deployed* v0.1 daemon), host-side bounded
   reads, and `APPLEBRIDGE_TOKEN` plumbing (unused until a daemon supports it).
   Runs against the live guest with **zero guest change**. Bounded reads are safe
   immediately — they only reject lengths a healthy daemon never emits.
2. **Rebuild the v0.2 daemon.** Add the `HELLO`/`AUTH2` responder, `TOKEN=` pref,
   capability advertisement, and daemon-side length rejects. Build to
   `:bin:AppleBridge.new`, swap, redeploy via the installer, reboot. It speaks
   v0.2 to the new host and legacy to an old host.
3. **Turn on auth — only once the PR3 host is deployed.** Set `TOKEN=` in guest
   prefs *and* `APPLEBRIDGE_TOKEN` on the host. Unauthenticated connections are
   now rejected. Two safety rails and one sharp edge:
   - A token on **only one side** ⇒ auth skipped (the daemon engages auth only
     when the host's HELLO carries a *non-empty* nonce **and** the daemon holds a
     token). A half-configured rollout never locks you out.
   - ⚠️ **Do not set a token on BOTH sides while the host is still PR1.** The PR1
     host sends a nonce (when `APPLEBRIDGE_TOKEN` is set) but never completes the
     handshake with `AUTH2`, so a PR2 daemon would negotiate auth and then block
     every command (except `PING`) waiting for a proof that never comes. The host
     verifier + `AUTH2` sender land in **PR3**; enable auth only after that.
   - ⚠️ **The PR3 host fails closed.** Once `APPLEBRIDGE_TOKEN` is set, the host
     *drops* any peer that is v0.1 or does not offer `FEAT=auth`, and any peer
     whose `PROOF` doesn't verify — it will not run unauthenticated. So set the
     host token only when every daemon it serves is a v0.2 build with a matching
     `TOKEN=`. Clearing the env var reverts to open (zero-config) operation.
4. **Persistent control sessions + arbitration** (host-only), any time after §1.

### Rollback

- Host: revert the file, restart the launchd agent (`deploy_host.sh` +
  `launchctl kickstart`). Easy.
- Daemon: reinstall the prior binary via the installer. Because a v0.2 daemon
  already works with a v0.1 host and vice-versa, you can revert one side without
  touching the other.

---

## 6. Code touch-points

| File | Change |
|---|---|
| `mac/include/applebridge.h` | `PROTO_HELLO "HELLO:"`, `PROTO_AUTH2 "AUTH2:"`, `AB_PROTOCOL_VERSION 2`, digest cap constants |
| `mac/src/main.c` `ProcessRequest` | handle `HELLO:` / `AUTH2:` before the command fall-through; gate command verbs on an `authed` flag when a token is set |
| `mac/src/main.c` main loop | per-connection auth state; reset on reconnect |
| `mac/src/prefs.c` / `prefs.h` | `TOKEN=` field (load/save/default empty) |
| `mac/src/protocol.c` (or new `auth.c`) | `AB_Digest()`, nonce mixing, hex helpers, length-guarded verb parsers |
| `host/host_server.py` | HELLO probe in `accept`; negotiated-version state; `APPLEBRIDGE_TOKEN`; `MAX_DECLARED` guards in all readers; `_recv_control_command` cap; (later) persistent-session loop + daemon-link mutex |
| `host/tests/` | golden transcripts for HELLO (v0.1 vs v0.2), auth pass/fail, over-cap rejects |

---

## 7. Test plan (feeds the "golden-transcript protocol tests" ledger item)

Recorded byte-exact transcripts as executable specs (stdlib only, no emulator):

- **HELLO, v0.2 daemon:** host probe → `ABVERSION:2` parsed; version = 2.
- **HELLO, v0.1 daemon:** canned `STATUS:-1 … Invalid command format` → host
  concludes legacy; no auth attempted; a normal command still round-trips.
- **Auth success:** fixed token + fixed nonces → known digest; `AUTH2` accepted.
- **Auth failure:** wrong token → daemon-side reject path; host re-accept.
- **Bounded read:** a frame declaring `STDOUT:99999999` → host aborts + drops,
  does not read 95 MB.
- **Control cap:** a `:9001` request over `MAX_CTRL_REQUEST` → error frame, socket
  stays healthy for the next request.

---

## 8. Decisions (locked 2026-07-02)

1. **Digest: FNV-1a-64** over `nonce || token`, 16 hex chars. Obfuscation-grade,
   sized for the NAT'd-LAN threat model; a compact SHA-1 is a future swap behind
   the same `AB_Digest()` seam if the threat model hardens.
2. **Auth: opt-in via token presence.** Auth runs only when *both* sides hold a
   token; a half-configured rollout skips auth and never locks the bridge out.
   Today's zero-config default is preserved.
3. **PR1 scope: §1 HELLO version negotiation + §3 host-side bounded reads.** Both
   are non-breaking and host-only, ship value immediately (hardened reads against
   the live v0.1 daemon) and unblock the daemon PR. Sequence:
   - **PR1 (host):** HELLO probe + legacy fallback, `MAX_DECLARED` guards in every
     reader, `_recv_control_command` cap, `APPLEBRIDGE_TOKEN` plumbing (dormant),
     golden-transcript tests (no emulator needed).
   - **PR2 (daemon):** `HELLO:`/`AUTH2:` responder, `TOKEN=` pref, `AB_Digest()`,
     daemon-side length rejects. Build → `:bin:AppleBridge.new` → swap → installer.
   - **PR3 (host):** complete the auth handshake end-to-end — verify the daemon's
     `PROOF` over the host nonce, send `AUTH2` = `H(daemonNonce || token)`, and
     **fail closed** (drop the link) on any mismatch, missing capability, or
     rejected `AUTH2`. Gated on `APPLEBRIDGE_TOKEN`; no token ⇒ unchanged.
   - **Deferred (a later PR):** persistent `:9001` sessions + multi-client
     arbitration. The single-threaded control server already *serialises* clients
     (one command in flight at a time), and the MCP client opens a socket per
     command — so persistent sessions give no benefit without a coordinated client
     change and would add head-of-line blocking to a working server. Not bundled
     into the security PR; revisit only if a measured need appears.
