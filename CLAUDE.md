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
1. **Host server** (first): `cd host && nohup ./run_server.sh & ` — uses **`/usr/bin/python3`**, never the venv (macOS firewall blocks the un-allowlisted venv binary). Log: `/tmp/applebridge_server.log`.
2. **Mac daemon**: launch `:bin:AppleBridge` in Basilisk II, and start **ToolServer ('MPSX')** (MPW Shell returns empty AE replies — only ToolServer gives output).
3. **MCP server**: registered in `.mcp.json` as `applebridge`. 7 tools: `mpw_execute`, `mac_read_file`, `mac_write_file`, `mac_list_files`, `mac_compile`, `mac_screenshot`, `launch_app`.

Smoke test: `cd host && /usr/bin/python3 send_command.py 'Echo HELLO'`.

## Hard rules (learned the hard way)
- **Link, not ILink.** `ILink` crashes Basilisk II — always `Link -model far`.
- **`/usr/bin/python3` for the host server** (firewall). Stdlib-only, so system Python suffices.
- **Re-run `Rez AppleBridge_res.r` after every link** — the `SIZE` resource (`isHighLevelEventAware`) is required or every command fails with `-903`.
- **Never `2>&1`** in MPW (crashes the shell) — use `≥ file.err` to capture stderr.
- **Build off the running daemon**: link to `:bin:AppleBridge.new`, then swap; a heavy link in the same ToolServer that serves the bridge can take it (and the AE layer) down.
- **Encoding**: host UTF-8/LF ↔ Mac MacRoman/CR — use `host/encoding_convert.py`.
- Long commands (e.g. `Link`) may return `-1712` (AE timeout) **yet still complete** — verify by the artifact, not the status.

## Status (2026-06-14)
All hardening scopes are **done, merged, and verified live** (PRs #1–#6 closed, `main` at the `QUIT` verb commit):
- **Scope 1** — host hardening.
- **Scope 2** — 68k daemon: overflow-guard, PING/LAUNCH/QUIT verbs, `-903` fix, AE debugger. Daemon is **v0.4.0 ("Verbs")** with colored RX/TX LEDs and a d/h/m/s uptime counter.
- **Scope 3** — large-response transfer: length-framed host read + defensive `kOTFlowErr` handling.
- **Scope 4** — responses **>64 KB** stream via dynamic buffer + length-framing (`e667f5b`).
- Screenshots stream the emulated screen and decode to PNG host-side.

Stack verified up on 2026-06-14: host server on :9000/:9001, daemon connected, ToolServer answering (`Echo HELLO` → `STATUS:0`). **Screenshot path verified live** the same day — daemon captured the 1024×768 emulated framebuffer, streamed it over the bridge, decoded to PNG host-side (daemon window showed v0.4.0, RX lit, `Alive: 6m 54s`). Sample committed at `docs/images/daemon-live.png` and shown in `README.md`.

Working tree cleaned (PR #8): the April scratch is gone and `nohup.out`/`*.rtf` are now git-ignored.

**Next / open:** nothing blocking — hardening arc complete. Future ideas live in `README.md` / `ARCHITECTURE.md`.

## More detail
- `README.md` — user-facing intro & examples
- `ARCHITECTURE.md` — full design
- `TROUBLESHOOTING.md` — failure modes & fixes
- Build recipes, trap defs, encoding tables: the user's global `~/.claude/CLAUDE.md`
