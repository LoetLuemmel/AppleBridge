# AppleBridge — Project Guide for Claude

## What this is
**AppleBridge connects Claude Code to a real Mac System 7.6.1 running in Basilisk II**, so an AI can build, compile, link, and run authentic 68k Macintosh software via natural language. The bridge carries MPW/ToolServer commands to the emulated Mac and returns their output.

## What we want to achieve
A **reliable, self-healing development bridge** that lets Claude drive the classic Mac like a normal toolchain:
- Write C / 68k assembly on the host → compile (SC/Asm) → link → run on System 7.6.1.
- Get **real command output back** (errors, listings, file dumps) for closed-loop debugging.
- Expose it all as **MCP tools** so any Claude session can use it without glue code.
- Survive the rough edges of a 1990s emulator (dropped connections, flow control, AE quirks) **without manual restarts**.

The north star: *talking to a 30-year-old Mac should feel as dependable as a local shell.*

## Architecture (NAT-reversed)
```
Claude ⇄ MCP server (stdio)  ──:9001──▶  host_server.py  ◀──:9000──  Mac daemon (AppleBridge)  ──Apple Events──▶  ToolServer / MPW
```
The **Mac daemon connects OUT** to the host (the emulator sits behind NAT, so the host can't dial in). `host_server.py` serves **:9000** (daemon) + **:9001** (control/MCP). Wire protocol: request `COMMAND:<len>\n<payload>`; response `STATUS:<code>\rSTDOUT:<len>\r<data>\rSTDERR:<len>\r<data>\r\r` (read **by declared length**, not by terminator).

## Start the stack
**One-shot:** `cd host && ./start_stack.sh` — aliases `.154` onto the default-route interface (the freeze-avoidance rule below), (re)starts the host server, and launches Basilisk II. Then do the two in-emulator steps (daemon + ToolServer) it prints. Manual steps:
1. **Host server** (first): `cd host && nohup ./run_server.sh & ` — uses **`/usr/bin/python3`**, never the venv (macOS firewall blocks the un-allowlisted venv binary). Log: `/tmp/applebridge_server.log`.
2. **Mac daemon (faceless, v0.6.0)**: with autostart installed it launches **automatically on boot** — no window — and **chain-launches** its helper apps (ToolServer first) from the prefs file. Otherwise launch `:bin:AppleBridge` by hand. Only **ToolServer ('MPSX')** returns command output (MPW Shell gives empty AE replies). Configure the host IP, the helper list, and autostart via **AppleBridgeConfig** (`:bin:AppleBridgeConfig`) — see `mac/config/README.md`.
3. **MCP server**: registered in `.mcp.json` as `applebridge`. 7 tools: `mpw_execute`, `mac_read_file`, `mac_write_file`, `mac_list_files`, `mac_compile`, `mac_screenshot`, `launch_app`.

Smoke test: `cd host && /usr/bin/python3 send_command.py 'Echo HELLO'`.

## Hard rules (learned the hard way)
- **`Link -model far` is the default linker** — verified and leaner output. `ILink` is *not* broken: with the correct `{CLibraries}`/`{Libraries}` paths it links, runs, and round-trips commands cleanly (verified 2026-06-26). The old "ILink crashes Basilisk II" belief was a misdiagnosis — the committed `BuildIt` used an empty `{LIBS}`, so its lib paths resolved to nothing → a broken binary that crashed *on launch*. ILink is fine to use; it just yields a slightly larger binary plus a big `.NJ` incremental file, so `Link` stays the default.
- **`/usr/bin/python3` for the host server** (firewall). Stdlib-only, so system Python suffices.
- **Re-run `Rez AppleBridge_res.r` after every link** — the `SIZE` resource (`isHighLevelEventAware`) is required or every command fails with `-903`.
- **Never `2>&1`** in MPW (crashes the shell) — use `≥ file.err` to capture stderr.
- **Build off the running daemon**: link to `:bin:AppleBridge.new`, then swap; a heavy link in the same ToolServer that serves the bridge can take it (and the AE layer) down.
- **Encoding**: host UTF-8/LF ↔ Mac MacRoman/CR — use `host/encoding_convert.py`.
- Long commands (e.g. `Link`) may return `-1712` (AE timeout) **yet still complete** — verify by the artifact, not the status.
- **Host `.154` must live on the default-route interface** (where the guest's MACNAT exits — normally Wi-Fi `en0`), *not* a second NIC. If it's on the wrong interface, the daemon hangs on "CONNECTING" and freezes the emulator at 100% CPU (synchronous `OTConnect` starving the cooperative scheduler). `host/start_stack.sh` sets this up. **Never pre-create a bridge** — `etherhelpertool` owns `en8` directly; a manual `bridge100` SIGSEGVs it (`fret == -10`). The guest is behind MACNAT, so it is **never pingable** — diagnose via the *outbound* connection, not ICMP. See `TROUBLESHOOTING.md` → "Daemon hangs on CONNECTING".

## Status (2026-06-27)
AppleBridge runs as a **faceless System 7 background service**. Daemon is **v0.6.0**; the host server auto-starts via launchd.

**Faceless-service arc (PRs #21–#23), verified live by a cold reboot.** The daemon is `onlyBackground` (no window, ever), reads its config from an **AppleBridge Prefs** text file, **chain-launches** its helpers (ToolServer first) on startup, and installs itself into the System Folder's **Startup Items** as a self-made Finder alias — so a cold boot brings the bridge up invisibly and automatically, ToolServer and all, ready for `Echo HELLO` → `STATUS:0` with zero manual launching. Three components + one shared file:
- **AppleBridge** — the faceless daemon (`onlyBackground`, creator `'ABrg'`): OT transport + AE-client to ToolServer + chain-launch. Quits cleanly via `kAEQuitApplication` and the `QUITDAEMON` wire verb.
- **AppleBridgeConfig** — foreground control panel (creator `'ABcf'`): shows daemon up/down + autostart state, picks helper apps (Standard File → `APP=`), and installs/removes autostart (alias in Startup Items). **No Launch/Stop Daemon buttons** — quitting the faceless daemon tears down Open Transport and trips a Sequoia/SDL2 host crash, and the daemon is meant to run continuously anyway. See `mac/config/README.md`.
- **AppleBridge Prefs** — flat `IP=`/`DEBUG=`/`APP=` text in the Preferences folder, shared by daemon + config app. (Gotcha: an `APP=` entry that opens a fullscreen presentation — e.g. *About Mac OS 7.6.1 Update* — freezes the emulator at chain-launch; keep the list to real helper apps like ToolServer.)

**Prior hardening (v0.5.x, still in force).** Async `OTConnect` + application-level heartbeat (no host-down freeze; PRs #10–#13); v0.5.7 audit hardening (PR #19): length-framed *receive* reassembly, teardown-on-send-failure, an off-stack 64 KB buffer, a longer build AE timeout (no spurious `-1712`), watchdog reconnect backoff. Host side: length-framed reads, `kOTFlowErr` handling, **>64 KB** heap-streamed responses; the MCP false-success fix (PR #17); repo cleanup of pre-NAT + Swift code (PR #18). Screenshots stream the emulated screen → PNG host-side. The old "GUI apps crash BAII" was a **broken guest binary**, not a window bug (PR #15) — build GUI apps in **C** (`MinQDC`), assembly for **MPW tools** (`MinAsm`).

**Next / open:** Phase 6 (optional) — a keep-alive **INIT** that relaunches the daemon if it dies (the riskiest phase; autostart already covers cold boot). Backlog: P3 security (daemon handshake / bounded reads), P4 tests; the roadmap then points at decoupling from the LAN (slirp transport). A CMS article on the faceless-service arc would close out the series. Future ideas also in `README.md` / `ARCHITECTURE.md`.

## More detail
- `README.md` — user-facing intro & examples
- `ARCHITECTURE.md` — full design
- `TROUBLESHOOTING.md` — failure modes & fixes
- Build recipes, trap defs, encoding tables: the user's global `~/.claude/CLAUDE.md`
