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

## D-008 — serial fallback defaults

- **Date:** 2026-07-02
- **Status:** active
- **Decision:** on a machine with no TCP stack the installer seeds `NET=Serial`, `PORT=A` (modem), `BAUD=57600`; a detected OT/MacTCP always wins over serial.
- **Evidence:** installer preflight design for Ethernet-less 68k targets, PR #54 (`docs/DEPLOY_SERIAL_HARDWARE.md`); 57600 verified over the pty harness and later on real SE/30 hardware.
- **Revisit if:** real-hardware runs show sustained framing errors at 57600 without RTS/CTS — then the shipped default drops to 19200 until handshaking lands.
