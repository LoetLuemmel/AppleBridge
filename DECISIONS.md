# Decisions of Record

One entry per decision that shapes how AppleBridge is built or operated. **This
file's wording is authoritative for decisions** — where an article, doc, or memory
note phrases a decision differently, this file wins. (Status of *work* is the
[roadmap ledger's](https://pit.390er.de/applebridge/applebridge-roadmap-ledger-progress-and-status-tracker/)
job; articles are dated and archival. See CLAUDE.md → Process.)

Format — every entry carries all five fields, checked by
`tests/test_doc_claims.py::test_decisions_register_is_wellformed`:

- **Date:** when decided.
- **Status:** `active`, or `superseded → D-NNN`.
- **Decision:** one authoritative sentence.
- **Evidence:** what established it (measurement, on-device test, incident).
- **Revisit if:** the observation that would reopen it. A decision without a
  falsifier is folklore waiting to happen — four unfalsifiable "rules" from the
  initial commit survived 82–110 days despite being wrong.

## D-001 — slirp transport

- **Date:** 2026-06-28
- **Status:** active
- **Decision:** slirp is a no-go as default transport; the stack stays on `etherhelper/en8`.
- **Evidence:** live A/B benchmark (`host/bench_transport.py`, results committed): legacy slirp −27 % latency but −80 % bulk throughput and +213 % screenshot time.
- **Revisit if:** a `libslirp`-based Basilisk II build exists; re-run the same benchmark — bulk throughput within ~20 % of `etherhelper` reopens the question. (The benchmark article's "adopt, then replace the transport underneath" phrasing is *not* of record; this entry is.)

## D-002 — default linker

- **Date:** 2026-06-26
- **Status:** active
- **Decision:** `Link -model far` is the default linker; ILink is permitted but not default.
- **Evidence:** on-device verification 2026-06-26 — ILink with correct `{CLibraries}`/`{Libraries}` paths links, runs, and round-trips; the old "ILink crashes Basilisk" belief was a misdiagnosis (empty `{LIBS}` → broken binary crashing at launch). ILink merely yields a larger binary plus a `.NJ` incremental file.
- **Revisit if:** build times grow to where incremental linking pays for the artifact overhead.

## D-003 — persistent bridge connections

- **Date:** 2026-07-02
- **Status:** active
- **Decision:** persistent host↔MCP connections are deferred, not planned; the control port stays one-socket-per-command.
- **Evidence:** the guest link is already persistent and the MCP client opens a socket per command — no gain without a coordinated client change, and a persistent session adds head-of-line blocking (protocol v0.2 design, `docs/PROTOCOL_v0.2.md`).
- **Revisit if:** measured latency attributable to connection setup, or a client that can hold a session.

## D-004 — stopping the emulator

- **Date:** 2026-07-03
- **Status:** active
- **Decision:** BasiliskII is never hard-killed; the clean stop is `mac_shutdown` (Shutdown Manager) or Special → Shut Down in the guest.
- **Evidence:** hard termination can corrupt the guest System 7 disk image — the accumulated state of months with no snapshot discipline; rule violated once 2026-07-03 ([[applebridge-never-kill-basilisk]]).
- **Revisit if:** a guest-image snapshot/backup routine exists that makes corruption recoverable — which would soften the rule to "prefer clean stop", not remove it.

## D-005 — no Launch/Stop buttons in the config app

- **Date:** 2026-06-30
- **Status:** active
- **Decision:** AppleBridgeConfig deliberately offers no Launch/Stop control for the daemon.
- **Evidence:** quitting the faceless daemon tears down Open Transport and trips a Sequoia/SDL2 host crash; the daemon is designed to run continuously (`mac/config/README.md`).
- **Revisit if:** the emulator is rebuilt against SDL2 2.32.x and the teardown path is re-tested crash-free.

## D-006 — where truth lives (precedence)

- **Date:** 2026-07-26
- **Status:** active
- **Decision:** the ledger wins on work status; DECISIONS.md wins on decisions; articles are archival and get a dated superseded banner when overtaken — never a silent edit.
- **Evidence:** three surfaces carried three wordings of the slirp decision (benchmark article vs ledger vs repository guide) with no rule to reconcile them — documented in the [Six Places survey](https://pit.390er.de/applebridge/applebridge-six-places-that-remember-progress-tracking/).
- **Revisit if:** a class of fact appears that none of the three owners fits.

## D-007 — host server interpreter

- **Date:** 2026-06-27
- **Status:** active
- **Decision:** the host server runs under `/usr/bin/python3`, never a venv interpreter.
- **Evidence:** the macOS application firewall blocks the un-allowlisted venv binary — the server is stdlib-only precisely so the system interpreter suffices (`host/run_server.sh`).
- **Revisit if:** the venv binary is firewall-allowlisted on the host and a dependency worth a venv actually appears.

## D-009 — the ledger holds state, not history

- **Date:** 2026-07-26
- **Status:** active
- **Decision:** the ledger records current state — the checkboxes — only; its per-session changelog is retired, and history is answered by `git log`, the merged pull requests, and the article corpus.
- **Evidence:** first subtraction pass — the changelog had reached 33 entries and 36,000 characters, **45 % of the page**, restating what git already records automatically and without drift. A ledger nobody can scan fails at its only job. Removing it cut the page 38 %; every checkbox was preserved.
- **Revisit if:** a narrative history is needed that git plus the article corpus cannot answer — in which case **generate** it from those sources, do not maintain a second copy by hand.

## D-010 — hard rules point, decisions explain

- **Date:** 2026-07-26
- **Status:** active
- **Decision:** a hard rule in `CLAUDE.md` states the imperative and points at its decision (`D-NNN`); the evidence and the falsifier live only in this file.
- **Evidence:** the same subtraction pass found three of eight decisions restating three hard rules verbatim (linker, host interpreter, hard-kill) — a duplicate introduced by the very change that added the guards against duplication. Reasoning stated twice drifts; stated once, it cannot.
- **Revisit if:** `CLAUDE.md` needs to be readable without this file — then inline the falsifiers and retire the split rather than keeping both.

## D-011 — ILink is the linker for the daemon

- **Date:** 2026-07-26
- **Status:** active
- **Decision:** the 68K daemon is linked with **`ILink`**; `Link` can no longer bind it.
- **Evidence:** the daemon's code has reached ~98 KB in a single segment. `Link` places every object in one segment, so a near-model library cross-reference spanning it exceeds the 32 KB PC-relative limit — Error 48, first `MacRuntime`'s `___MAIN` -> `main`, and after reordering `_RTInit` -> `Interface.o`. Reordering only moves which reference breaks. `ILink` segments differently and linked the same objects cleanly (0.8d28, verified on-device). This supersedes the older note that `Link` is the default and `ILink` merely an alternative.
- **Revisit if:** the code is split into explicit segments (`#pragma segment` or `SC -seg`), which would let `Link` work again — worth doing if `ILink`'s larger output or its `.NJ` incremental file becomes a problem.

## D-012 — serial payloads above the guest's input buffer are not yet reliable

- **Date:** 2026-07-26
- **Status:** active
- **Decision:** over serial, transfers up to the guest's serial input buffer (16 KB as of 0.8d28) are byte-exact; larger ones are not guaranteed and must be verified or split.
- **Evidence:** on an SE/30 at 57600 after 0.8d28, 3x 8 KB round-tripped byte-identical (before the fix: 8150 of 8192 bytes wrong), while a single 82 KB transfer completed in 14.4 s with **30 of 82024 bytes** wrong. The wire delivers ~5.7 KB/s, so a 16 KB buffer is ~2.8 s of slack; a longer stall inside the daemon still overruns it. Host-side pacing is not a fix on its own — pacing slow enough for a small buffer trips the daemon's "host silent" watchdog.
- **Revisit if:** RTS/CTS hardware handshaking is wired and enabled on both ends, or the protocol gains a windowed ack — either removes the dependency on drain speed. Re-run the 82 KB round-trip; byte-identical closes it.

## D-008 — serial fallback defaults

- **Date:** 2026-07-02
- **Status:** active
- **Decision:** on a machine with no TCP stack the installer seeds `NET=Serial`, `PORT=A` (modem), `BAUD=57600`; a detected OT/MacTCP always wins over serial.
- **Evidence:** installer preflight design for Ethernet-less 68k targets, PR #54 (`docs/DEPLOY_SERIAL_HARDWARE.md`); 57600 verified over the pty harness and later on real SE/30 hardware.
- **Revisit if:** real-hardware runs show sustained framing errors at 57600 without RTS/CTS — then the shipped default drops to 19200 until handshaking lands.
