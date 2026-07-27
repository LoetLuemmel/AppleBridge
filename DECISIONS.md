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
- **Scope (narrowed 2026-07-27 by D-015):** this entry rejects slirp *as the default on a host where `etherhelper` can carry the bridge at all* — which requires two interfaces. On a single-interface host `etherhelper` cannot carry it, and slirp is not a worse option but the only local one. "No-go as default" was written on a two-NIC machine and silently assumed one.

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
- **Status:** superseded → D-013
- **Decision:** *(withdrawn — the evidence was a measurement artifact; see D-013.)*
- **Evidence:** claimed that 82 KB lost 30 of 82024 bytes over serial. Those bytes were the **file name inside the resource map**, which the Resource Manager rewrites when a resource fork is stored under a different name — not lost data. Comparing a resource fork byte-for-byte across a rename is not an integrity test.
- **Revisit if:** n/a — superseded.

## D-013 — bulk transfers are byte-exact on both transports

- **Date:** 2026-07-26
- **Status:** active
- **Decision:** with daemon 0.8d28 a file transfer is byte-exact over serial **and** MacTCP, for payloads well beyond the guest's serial input buffer; there is no known size limit to design around.
- **Evidence:** 82024 bytes of random data written and read back **byte-identical** (data fork, SHA compared) over MacTCP at ~111 KB/s; 3× 8 KB byte-identical over serial at 57600. The earlier "30 bytes lost" appeared on **both** transports at the same magnitude, which is what exposed it as an artifact of comparing resource-map name fields rather than a transport fault. Integrity tests use a **data fork with random content**, because a resource fork legitimately differs after a rename.
- **Corroborated 2026-07-27:** the rewritten bytes were finally *identified* rather than inferred. A clean-room run over slirp (`host/tools/q1_native_surface.py`) round-tripped random forks at 4 KB / 64 KB / 512 KB: every data fork came back byte-exact, and every resource fork came back at full length with **77–78 bytes altered between offsets 48 and 125**, stable across two reads. The altered region contains `<length><leaf name>` as a Pascal string, the length byte tracking the name — `ABQ1_4096` → `0x09`, `ABQ1_65537` → `0x0a`, `ABQ1_524288` → `0x0b`. That is the Resource Manager stamping the owning file's name into the map, exactly as this entry claimed from a 30-byte discrepancy. The tool now *checks* the mechanism (full length, differences confined to the stamp) instead of reporting it, so a truncation or a real corruption still fails.
- **Revisit if:** a byte-compared transfer of random data in a *data fork* ever differs — that would be a real fault. The serial input buffer (16 KB) is still finite, so a transfer far larger than it, on a badly stalled guest, remains the plausible failure mode to watch. Equally, a resource fork differing **outside** the name stamp would be new and would not be covered by this entry.

## D-008 — serial fallback defaults

- **Date:** 2026-07-02
- **Status:** active
- **Decision:** on a machine with no TCP stack the installer seeds `NET=Serial`, `PORT=A` (modem), `BAUD=57600`; a detected OT/MacTCP always wins over serial.
- **Evidence:** installer preflight design for Ethernet-less 68k targets, PR #54 (`docs/DEPLOY_SERIAL_HARDWARE.md`); 57600 verified over the pty harness and later on real SE/30 hardware.
- **Revisit if:** real-hardware runs show sustained framing errors at 57600 without RTS/CTS — then the shipped default drops to 19200 until handshaking lands.

## D-014 — real hardware is the periodic proof, the emulator is the workshop

- **Date:** 2026-07-26
- **Status:** active
- **Decision:** Basilisk II stays the daily build and test environment, but a claim about **guest behaviour** is not proven until it has run at least once on physical 68k hardware; each milestone includes one such run. The emulator is where we work, the metal is what the claim is about.
- **Evidence:** three defects surfaced on an SE/30 in a single session (2026-07-26) that were invisible in Basilisk II, each because the emulator does not reproduce the property under test: the Serial Manager's 64-byte input buffer (no timing pressure — 8150 of 8192 bytes silently wrong, [[applebridge-serial-64byte-buffer]]), the monitor window sized for 1024×768 on a 512×342 screen, and an AppleTalk seed router advertising inside the reserved startup range (MACNAT never asks for a routable net). Compiler output is *not* the difference — `SC` is deterministic and both machines emit identical objects; what differs is which environment the assertion covers.
- **Also:** every finding a hardware run produces must be converted into something CI can run *without* the hardware — a source guard, a value check, a checker — and the conversion is not optional, because the instrument is a wasting asset (mechanical disk, drying capacitors, a PRAM battery that can leak onto the board). What conversion cannot preserve is the discovery of *unknown* divergences; that capability ends with the machine, and no test written afterwards replaces it. When it ends, the claim shrinks from "AppleBridge works on a Macintosh" to "AppleBridge works on Basilisk II", and the documentation must say so rather than let the larger claim be inherited silently.
- **Revisit if:** three consecutive milestone hardware runs surface nothing the emulator missed — then the cadence is costlier than the divergence it catches and can be relaxed. Conversely, a hardware-only defect found *after* a milestone shipped argues for running the metal check earlier, not less often. **This entry also ends when the hardware does**, which is the termination condition the first draft omitted: it wrote a falsifier for "the check stopped being informative" and none for "the check stopped being possible".

## D-015 — the emulator backend follows the host's interface count

- **Date:** 2026-07-27
- **Status:** active
- **Decision:** the Basilisk II Ethernet backend is **derived from the host, never preset**. Where the interface `etherhelper` owns is *not* the interface carrying the host address, use `etherhelper` — the guest gets AppleTalk and full throughput. Where the host has only **one** usable interface, `etherhelper` cannot carry the bridge at all and the backend is `slirp`; the daemon then dials the host's **real LAN address**, not `10.0.2.2`, and the host server binds `0.0.0.0`.
- **Evidence:** clean-room bring-up on a second, independent machine (MacBook Pro 2013, Big Sur, one Wi-Fi interface; guest System 7.5.3 with Open Transport, no MPW, no ToolServer), 2026-07-27. On `etherhelper/en0` the guest reached everything *except* the machine it runs in: the AppleShare server, a LAN web server at `.133`, and the public internet all worked, while both the daemon and a browser in the guest failed against the host's own `.158:9000` — silently, with no reply. The control that isolates it was accidental: minutes earlier the *same* daemon, from the *same* guest, had connected successfully to a **different** host (`192.168.3.154`) across the same LAN, because the installer had seeded the developer machine's address. Frames leaving `en0` would have to return on `en0`; the access point does not reflect them, so guest→host is the one path a bridged backend cannot form. On this host the wire is `etherhelper/en8` and the address lives on `en0` — two interfaces — which is why the defect was unreachable here and why nothing in the documentation named the precondition. After the switch to `slirp` the daemon connected and negotiated protocol v2, the connection arriving from `192.168.3.158`; a browser reaching the same server via `10.0.2.2` arrived as `127.0.0.1`, so both source addresses occur and a narrower bind is wrong.
- **Also:** `10.0.2.2` is **not** a usable host address for the daemon on this Basilisk build — it is a router, not an alias for the host. The bench note in `host/bench_results/README.md` recorded the gateway path as the one that works; that observation was made with a browser-equivalent client and does not generalise to the daemon. Where the two disagree, this entry holds.
- **Revisit if:** an `etherhelpertool` build loops host-directed frames back internally, or the host gains a second usable interface — in both cases `etherhelper` is preferred again, because AppleTalk is worth more than slirp's lower latency. Conversely, if a single-interface host is ever seen forming a guest→host connection under `etherhelper`, this entry is wrong and the cause is elsewhere.

## D-016 — the etherhelper bridge is required, and is created before the emulator

- **Date:** 2026-07-27
- **Status:** active
- **Decision:** the `etherhelper` backend requires a bridge (`bridge100` by default) with the wired NIC as a member, created **before** BasiliskII starts. Tooling ensures it; nothing tears it down. The requirement belongs to that backend and to no other: a **slirp** machine has no bridge, needs none, and needs no privileged network setup at all — so tooling must condition the bridge on the configured backend rather than on being AppleBridge.
- **Evidence:** documented on Emaculation and confirmed by the operator, whose launcher `/Applications/BAII Netzwerk.app` consists of nothing but `ifconfig bridge100 create / addm en8 / up` under `osascript … with administrator privileges`, followed by an unprivileged `open -a`. Observed live 2026-07-27: `bridge100` up with `en8` as a member and an active address cache, `etherhelpertool` alive, the bridge fully working. This **reverses** the previous hard rule ("never pre-create a bridge", from an `etherhelpertool` SIGSEGV `fret == -10`): the crash comes from creating or altering the bridge *while the helper already owns the NIC*, not from having one beforehand. `host/start_stack.sh` had been destroying the bridge on that reading — a required component, removed by the repository's own launcher, silently repaired every time the operator's launcher ran next.
- **Revisit if:** a launch with **no** bridge is observed to reach an AppleShare server or answer `mac_appletalk_browse` — that would make the bridge optional rather than required. Check AppleTalk specifically, never TCP: TCP survives either way, which is exactly how the slirp backend disguised itself in D-015.
