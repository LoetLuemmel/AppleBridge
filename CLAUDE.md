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
2. **Mac daemon**: launch `:bin:AppleBridge` in Basilisk II, and start **ToolServer ('MPSX')** (MPW Shell returns empty AE replies — only ToolServer gives output).
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
The hardening arc is **done, merged, and verified live**. Daemon is **v0.5.7**; the host server auto-starts via launchd.
- **Host + large-response** (Scopes 1, 3–4): length-framed reads, `kOTFlowErr` handling, dynamic buffers; responses **>64 KB** stream from the heap.
- **68k daemon**: PING/LAUNCH/QUIT verbs, `-903` fix, colored RX/TX LEDs, uptime counter. v0.5.x added **async `OTConnect` + an application-level heartbeat** (no host-down freeze; PRs #10–#13) and the **v0.5.7 audit hardening** (PR #19): length-framed *receive* reassembly for fragmented commands, an off-by-one fix, teardown-on-send-failure (no wire desync), a 64 KB buffer moved off the stack, a longer AE timeout for builds (no spurious `-1712`), and watchdog reconnect backoff. Rollback binary at `:bin:AppleBridge.v056`.
- **MCP host path** (PR #17): a STATUS-less/truncated reply no longer reports false success. Repo cleaned of the pre-NAT code + the superseded Swift `MacintoshBridgeHost/` (PR #18).
- Screenshots stream the emulated screen and decode to PNG host-side.
- **Guest apps**: the old "GUI apps crash BAII" was a **broken guest binary**, not a macOS/SDL window bug (PR #15, `TROUBLESHOOTING.md`). Verified by example in `examples/`: build GUI apps in **C** (`MinQDC`), use assembly for **MPW tools** (`MinAsm`).

Verified live 2026-06-27: v0.5.7 daemon connected (`SYNC-OK`), `Echo` → `STATUS:0`, a 3000-byte fragmenting command round-tripped intact (reassembly), a C QuickDraw app compiled/linked/launched and drew its window, screenshots stream.

**Next / open:** daemon-side audit closed. Remaining backlog (from the interim audit, see the CMS): P3 security (daemon handshake / bounded reads) and P4 tests; the roadmap then points at decoupling from the LAN (slirp transport). Future ideas also in `README.md` / `ARCHITECTURE.md`.

## More detail
- `README.md` — user-facing intro & examples
- `ARCHITECTURE.md` — full design
- `TROUBLESHOOTING.md` — failure modes & fixes
- Build recipes, trap defs, encoding tables: the user's global `~/.claude/CLAUDE.md`
