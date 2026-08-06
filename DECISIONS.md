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
- **Status:** superseded → D-017
- **Decision:** *(withdrawn the same day — the falsifier below fired within the hour; see D-017.)*
- **Evidence:** claimed the bridge is required. It is not, in the mode this stack runs: with `bridge100` destroyed and no bridge anywhere on `en8`, NBP found `ApfelNetz`, the guest's Chooser listed it, and three AFP volumes mounted at boot. The entry was written from the operator's launcher and the Emaculation guidance without checking which *mode* they describe.
- **Revisit if:** n/a — superseded.

## D-017 — the bridge belongs to the tap mode, not to `etherhelper/<if>`

- **Date:** 2026-07-27
- **Status:** active
- **Decision:** with `ether etherhelper/<interface>` the helper owns that NIC directly and **no bridge is on the path**; tooling neither creates nor destroys one. A bridge is required only in the **tap** configuration (`etherhelper/tap0/bridge0/en0`), where it joins the tap device to the physical NIC. Which mode is configured decides whether a bridge means anything.
- **Evidence:** measured both ways on 2026-07-27 with `ether etherhelper/en8`. **With** `bridge100` (en8 a member, address cache learning): NBP found `ApfelNetz` at 3.79.128. **Without** any bridge on en8 — `bridge100` destroyed, only macOS's own Thunderbolt `bridge0` (en1–en4) present, `etherhelpertool` alive and not crashing: NBP found the same server, the guest's Chooser listed it with AppleTalk Active, and `Archiv`/`Projekte`/`AppleShare` were mounted during that bridge-less boot. The operator's launcher `/Applications/BAII Netzwerk.app` does create `bridge100`, and the Emaculation guidance describes a bridge as necessary — both are consistent with the **tap** setup, which the operator's own `AppleTalk Start Beispiel.sh` still shows (`bridge1 addm en1`, `bridge1 addm tap0`, prefs `etherhelper/tap0/bridge0/en0`). In `etherhelper/en8` that bridge is inert but harmless.
- **Also — the price of this backend, which no measurement had named:** the `etherhelper` path costs **two interactive password prompts per launch** on this setup: one for the bridge (the operator's launcher) and one for **BasiliskII itself**, which elevates its built-in `etherhelpertool` through Authorization Services. Neither can be answered by a script, so `start_stack.sh` was never truly one-shot on this path, and an unattended or headless start is impossible here. **slirp costs none** — no bridge, no alias, no privileged step at all. The preflight must probe the app bundle before it probes the network. That is a stronger argument for the single-interface branch of D-015 than any throughput number.
- **Corrected 2026-07-30 — the "specific build" precondition was wrong.** This entry claimed `etherhelpertool` is **not part of a stock BasiliskII**, comes only from the kanjitalk755 fork, and that a normal user's copy cannot take the `etherhelper` path at all. Measured against the published binaries: all three universal builds on emaculation (`20210801`, `20240228`, `20260717`) ship `Contents/Resources/etherhelpertool`, signed *Developer ID Application: Ronald P Regensburg*. Installing an emulator is enough to have it. The real precondition is **architecture**: the app is universal, the helper is thin arm64 (`file` → `Mach-O 64-bit executable arm64`), so on an Intel host the kernel refuses to exec it — `bad CPU type in executable`, exit 127, measured on a Core i7-8569U / macOS 15.7.7. Cause: the Xcode Run Script phase compiles it with no `-arch` flags. On Apple Silicon a stock download **does** offer the branch. The `etherhelpertool.arm64.bak` beside the helper in the operator's bundle is consistent with a locally built x86_64 replacement, the original kept aside. The cause being upstream and one line long, the fix was sent there rather than worked around: <https://github.com/kanjitalk755/macemu/pull/314> (2026-07-31, open) derives the flags from `$ARCHS` in both BasiliskII and SheepShaver.
- **Revisit if:** an `etherhelper/<if>` launch is seen to lose AppleTalk when no bridge is present — check the Chooser or `mac_appletalk_browse`, never TCP, which survives either way. Equally, if BasiliskII stops prompting for the helper, re-measure the password cost before repeating it. If upstream ships a universal helper — PR #314 above is the candidate — the architecture precondition disappears **for builds published after it**, and only the password prompt remains. Re-measure with `file` on the actual bundle rather than inferring it from the merge.

## D-018 — the host-side installer targets the slirp branch only

- **Date:** 2026-07-27
- **Status:** active
- **Decision:** the host-side installer configures **`ether slirp` and nothing else**. It probes for `etherhelpertool` in the emulator bundle, and where it finds one it *names* that path as manual and stops rather than configuring it. `etherhelper` remains fully supported and fully documented — it is simply set up by hand, as it always has been.
- **Evidence:** the branch cannot be installed unattended. It needs **an interactive password prompt at every launch** — BasiliskII elevating its built-in helper through Authorization Services, plus a second for the bridge where a launcher creates one — and no script can answer either. slirp needs none of it: no bridge, no alias, no privileged step. On a single-interface host it is in any case the only path that can reach its own host at all (D-015). Measured on a machine that had never run AppleBridge, 2026-07-27: the full ToolServer-less surface passed eleven checks over slirp, including byte-exact fork transfer to 512 KB.
- **Corrected 2026-07-30 — one leg of the evidence was wrong, and one falsifier has fired.** This entry also argued that "most users cannot take it at all" because `etherhelpertool` is not part of a stock Basilisk II. It is: every published emaculation build ships it, signed (details in D-017's correction). The "revisit if" below anticipated exactly this — *"a fork build ships widely enough that a stranger plausibly has one"* — and that condition is met. **The decision still stands**, on the two legs that survive: the password prompt makes unattended start impossible on any host, and the shipped helper is thin arm64, so on an Intel host it cannot execute at all. What changes is who the branch is closed to: not "most users", but Intel users — and on Apple Silicon it is a hand-configuration away rather than a build away.
- **Also:** this halves the remaining work — three to four sessions instead of five to eight — and the saving is not the reason. The reason is that an installer whose output cannot start without a human at the keyboard has not finished the job it exists for. Scoping to slirp is what makes "installed" mean "running".
- **Cost, stated rather than buried:** the slirp branch has **no AppleTalk**. No Chooser, no AFP mounts, no `mac_appletalk_browse`. A user who wants those configures `etherhelper` by hand, and the installer must say so plainly instead of leaving them to discover it — TCP keeps working either way, which is exactly how that gap disguised itself in D-015.
- **Revisit if:** BasiliskII stops prompting for its helper (then `etherhelper` becomes scriptable and the argument changes), or a fork build ships widely enough that a stranger plausibly has one, or the first acceptance run shows people reaching for AppleTalk immediately — in which case the second branch is worth its own decision rather than a silent extension of this one.

## D-019 — slirp is the shipping configuration; etherhelper is set aside

- **Date:** 2026-07-28
- **Status:** active
- **Decision:** AppleBridge **ships on `ether slirp`**. That is the configuration the installer produces, the one the documentation leads with, the one acceptance is measured against, and the one the development host now runs. `etherhelper` stays **documented and supported** — nothing is removed and nothing is broken — but it is no longer a branch this project maintains toward parity, restores by default, or holds work for. D-018 scoped the *installer*; this scopes the *product*.
- **Evidence:** D-018's own falsifier asked what the first acceptance run on someone else's machine would show. It ran on 2026-07-28 — MacBook Pro 2013, single Wi-Fi interface, Gatekeeper-translocated emulator, **no MPW at all** — and went from nothing to a working bridge with **no password prompt at any point**, 11/11 on the native surface, 285 KiB/s at 512 KB against the development machine's 296 the same morning, `err=0`. Nobody reached for AppleTalk. Two further measurements moved the balance the same day: slirp answers **BOOTP/DHCP** and hands out all four guest values *including the name server* (so the guest-side setup is one dropdown, and the field most often left empty cannot be), and this development host has now run **two days of real work** on slirp — including compiling, `ILink`ing, `SWAPSELF`ing and rebooting the daemon twice over the bridge itself. The branch is not merely adequate for a demonstration; it is adequate for developing the thing.
- **Cost, stated rather than buried, and it is a real one:** the shipping configuration has **no AppleTalk**. `NBPLOOK`, `AFPMOUNT`/`AFPUNMOUNT` and `mac_appletalk_browse` are built, tested and documented, and they are **not exercisable in the configuration we ship**. That is a capability the project has and does not deliver by default — worth saying in those words rather than as "slirp has no AppleTalk", because the two sentences feel different and only one of them is honest about what a user gets.
- **Also:** the development host stays on slirp; it is not restored after testing. Two things follow, both recorded outside the repo since they are machine-specific (R1): the restore needs the Thunderbolt adapter physically present (`en8` is absent, and without a wired NIC the helper has nothing to own), and **no `etherhelper` `local.env` survives in any backup** — checked 2026-07-28, the installer's timestamped backups captured slirp over slirp, so a restore is a rewrite, not a copy.
- **Revisit if:** a real task needs AppleTalk or an AFP mount — the verbs exist, so the cost of switching back is configuration, not development, and one such task is enough to reopen this. Equally if a `libslirp`-based emulator build lands and changes the throughput picture materially (the old −80 % figure was largely the ToolServer detour, not the transport), or if BasiliskII stops prompting for its helper, which is what made `etherhelper` unscriptable in the first place.

## D-020 — operating notes are owned by the repository, not the CMS

- **Date:** 2026-08-03
- **Status:** active
- **Decision:** the operating notes — what an agent must **verify** before believing the bridge — live in **`docs/OPERATING_NOTES.md`** and are edited there. The WorkMode page keeps its existing entries as a dated snapshot with a banner pointing at the file, in the shape D-006 already prescribes for an overtaken article; new entries are not appended to it. `CLAUDE.md` and `session_brief.py` point at the file. The session channel (`notes.py`) stays coordination-only: nothing durable is recorded only there.
- **Evidence:** on 2026-08-03 one finding — the arming rule for the `_ModalDialog` walk — was written **three times**: into the channel, onto the CMS page, and into agent memory. That is the only class of fact in this project with three owners, and preventing exactly that is why D-006 exists. Three structural findings came with it: the page is behind Authelia and writable only through an MCP tool, so it **cannot be grepped at the moment it is needed**, which is mid-debug; both sessions publish to it independently with no review and no conflict detection, the CMS having no version history by its own account; and sixteen appended, never-rewritten entries force linear reading where grep was wanted. In the repository the file also comes under `tests/test_doc_claims.py`, which already enforces provenance on hard rules.
- **Cost, stated rather than buried:** the notes stop being readable without a checkout. That is a real loss for a human who wants them on a phone, and it is accepted because the reader who needs them *at the moment of need* is an agent with the repo already open. A generated publish step could restore it; **deliberately not built now**, because a hand-maintained mirror is precisely the shape that drifts and this project has paid for that once already (D-006). Second cost: the Jetson-side session published through its own CMS tooling and now edits the repository through its worktree instead — a change to its workflow, not a restriction on it; it confirmed by measurement that the worktree path works for it and that the CMS was never its only way to write. Third cost, **named by that session and not by this one**: the migration removes the CMS tool as an *ssh-less* read path for the notes. It judged this negligible because ssh to the Mac is its baseline and `grep` over that connection covers it — but the cost is real for any future reader whose baseline is not ssh, and it is recorded here rather than discovered later.
- **Revisit if:** a reader **without** a checkout turns out to actually use the page — then the page is the product, the file is the draft, and this decision inverts. Equally if the `docs/**` rules make the notes awkward to write: they carry dated measurements as *provenance*, and should `test_doc_claims` ever read those as progress journalling, the constraint is wrong for this file and one of the two must give.

## D-021 — a defect at the bound is back-computed, a defect in the prefix discards the arm

- **Date:** 2026-08-06
- **Status:** active
- **Decision:** when a defect is found in a measurement that has already produced data, what happens to that data is decided by **where the defect can reach**, not by whether anybody could find an effect:
  - it reaches **only the suffix** after the point at which a termination condition should have fired — the arm is **kept** and the affected runs are **back-computed by truncation**, a pure function over the trace;
  - it reaches the **prefix** — task list, instruction, remedy text, tool block, sampling, guard hull — the arm is **discarded** and re-run, *without* a check for whether it had an effect;
  - it reaches **only the interpretation** — the evaluator, an admission rule — nothing is discarded; the reading state gets a new number and the report says which one computed the figures.
  One number is not back-computable and is therefore set to **not decidable**, named and counted: a claim in a closing sentence the model wrote in a state a correctly bounded run would never have reached.
- **Evidence:** the compile budget counted only `mac_compile` calls while `mac_build` compiles too, so two of forty runs in arm 1 were granted extra attempts (six and five). Both sessions first argued *empirically* — "checked, no result changed" — which is a claim the interested party re-establishes every time, and the measuring side disclosed that the strict reading would cost it two hours and that this would be its **second** reading of its own rule at the edge, both in the same direction. The question was therefore put to a third party that had not built the apparatus. Its answer replaced the empirical criterion with a structural one: **a bound is a termination condition, so a miscount at it can only change what happens after it should have fired; the prefix is bit-identical with the run a correct bound would have produced.** It also showed the exoneration had been checked against one of five figures — under a correct bound one affected run would have ended *by the bound* rather than on a failed sixth call, which is exactly the third termination outcome — and that this too is reconstructible from the prefix.
- **Why the structural half holds, in the form it should have had from the start** (sharpened by the third party 2026-08-06, after the measurement had exposed the weaker wording): the back-computation **re-scores a recorded execution and does not produce a second one.** *Nothing is newly generated, so nothing can differ.* The earlier phrasing — "the prefix is bit-identical" — is true here and reads like a claim about the machine, which on the other axis it would be and where it is false: generation on this node is not repeatable, measured at 7.5 % over forty paired tasks with a provably identical stimulus. Same two words, structural on one axis and empirical on the other; naming the axis is what makes the sentence a proof rather than an assumption.
- **Precondition, checked and not assumed:** the ruling holds only while the bound is **purely terminal**. Were the model told how many attempts remained, the miscount would have changed the prefix from attempt one and no back-computation would be admissible. Verified from both sides with different patterns over 302 tool results across both arms: no key relating to the count, no occurrence of the word in any result, and the single hit in model output was `"remaining problems"` — a false positive of the wider pattern, reported by the side that found it rather than rounded away.
- **Cost, stated rather than buried:** the affected runs are scored as the runs they would have been and not as the runs that happened, so their tails exist in the trace and in no figure. The rule is also *harder* than the one it replaces in one direction: a wrong remedy text would forbid back-computation entirely, however harmless it looked — and that is the class of defect which demonstrably flipped a run on the morning this was decided. A criterion that never bites is none.
- **Revisit if:** a bound is ever built that reports its own state to the model, or a defect appears that is neither prefix nor suffix but both — then the three-way split is short a case, the same way "defect found" was short one before this entry existed.

## D-022 — where a training run computes, and what the project is actually for

- **Date:** 2026-08-06
- **Status:** active
- **Decided by:** the operator, in the article
  [*Wie die Optimierung weitergeht*](https://pit.390er.de/nvidia/wie-die-optimierung-weitergeht-klassen-entfernen-statt-quoten/),
  section *Der Trainingsort*. That wording is the source; this entry mirrors it
  because decisions of record live here (D-006), not because it restates them
  better.
- **Decision:**
  - **The project's purpose is Phase 5, and it is a question, not a metric:** *can
    a system be taught and optimised to drive an MPW or THINK C development
    environment over AppleBridge?* Everything upstream of that — gate, rewriter,
    fixed chain, bank — is preparation for it.
  - **The training runs on a 7B.** A smaller base model is **explicitly not an
    alternative**: the question is what a *fine-tuned 7B* can do for this purpose,
    and substituting a 3B answers a different one.
  - **Order of places:** preprocessing on the Xavier where technically possible,
    further steps on an **M4 Pro with 24 GB**. The Xavier alone looks limited or
    unsuitable for the training itself; the Orin's shared memory is too small.
  - **A rented 24 GB card stays in scope as a fallback**, should Apple's Metal
    turn out incompatible or unsuitable. This is a deliberate reversal of the
    reading proposed by this session, which would have ruled rented compute out
    altogether.
  - **The location is therefore no longer a blocker.** Training begins once the
    preparations for it are made.
- **Evidence:** on 2026-08-06 a published instruction proposed a rented 24 GB card
  for Phase 5. Three instances read that text and **none objected** — one of them
  had checked its denominators, found two errors in its rewriter rules and run its
  acceptance condition, and had even written a section headed *"what is expressly
  not disputed"* with four points, without noticing the fifth. The reason is
  checkable and was checked: a search of `DECISIONS.md` (21 entries at the time),
  `CLAUDE.md`, `README.md`, `ARCHITECTURE.md` and `docs/*.md` for any wording of
  the condition returned **nothing**. It lived in the project's name and in every
  prior decision, and therefore in no text a review could be held against. That is
  the same class as the day's wrong denominators, one level up: **not a number
  without its basis, but a basis without its sentence.** A condition nobody ever
  had to say out loud is invisible to every review, and the more self-evident it
  is, the more certainly it is missing.
- **What this entry is not:** a prohibition. This session drafted one — *"computation
  stays in-house, rented compute is excluded"* — and asked the operator to pick the
  boundary. The answer was narrower in one direction and wider in the other: the
  purpose fixes the **model size**, and the place is ordered by preference with an
  external fallback rather than closed. A rule written from the assumption instead
  of from the answer would have forbidden the fallback the operator wants to keep.
- **Revisit if:** the M4 Pro path proves workable end to end (then the fallback is
  dead weight and should be struck), or a 7B turns out not to fit anywhere
  available (then the model-size question reopens — but as a *finding*, not as a
  substitution made for convenience).
