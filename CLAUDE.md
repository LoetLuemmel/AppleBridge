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
1. **Host server**: `cd host && nohup ./run_server.sh & ` — uses **`/usr/bin/python3`**, never the venv (macOS firewall blocks the un-allowlisted venv binary). Log: `/tmp/applebridge_server.log`. **Order vs. the emulator does not matter** (PR #75): a daemon that starts first just retries and connects within ~40 s, and the control port answers throughout. The old "server first or the daemon fouls the socket" rule was a blocking `accept()` bug, not a socket problem.
2. **Mac daemon (faceless, v0.6.0)**: with autostart installed it launches **automatically on boot** — no window — and **chain-launches** its helper apps (ToolServer first) from the prefs file. Otherwise launch `:bin:AppleBridge` by hand. Only **ToolServer ('MPSX')** returns command output (MPW Shell gives empty AE replies). Configure the host IP, the helper list, and autostart via **AppleBridgeConfig** (`:bin:AppleBridgeConfig`) — see `mac/config/README.md`.
3. **MCP server**: registered in `.mcp.json` as `applebridge`. **30 tools** (`len(TOOLS)` in `mcp/tools.py` — grew from the original 7 in 2026-06-29's MCP push):
   - *Surface A — drive a build, read output:* `mpw_execute`, `mac_compile`, `mac_read_file`, `mac_list_files` (column-parsed, handles spaces), `mac_build` (one-shot SC→Link→Rez→SetFile, verified by artifact), `mac_send_apple_event` (arbitrary AE to any scriptable app).
   - *Surface B — move bytes, run, observe, interact:* `mac_put_file` / `mac_get_file` (fork-aware binary, MacBinary), `mac_write_file` (text), `launch_app`, `mac_screenshot` (optional region crop), `mac_type` / `mac_key` / `mac_click` (synthetic input into the front app), `mac_menu` (a menu item by its Command-key equivalent) / `mac_menu_front` (the front app's menu by numeric id, via the Route-B `MenuSelect` trap patch — local Basilisk), `mac_clipboard_get` / `mac_clipboard_set` (guest TEXT scrap ↔ host pasteboard).
   - *Network discovery:* `mac_appletalk_browse` (NBP name lookup in the daemon — the Chooser's list of `AFPServer`/`LaserWriter`/`Workstation` entities, headless: no ToolServer, no GUI driving; ~3 s for NBP's retry window; AppleTalk being off is reported as an error, not as an empty list).
   - *Lifecycle / liveness:* `mac_status` (daemon/ToolServer/heartbeat; answers even when the daemon is down), **`bridge_doctor`** (cross-layer diagnosis: launchd job, :9000/:9001 listeners, `.154` alias placement vs. default route, BasiliskII + `etherhelpertool`, emulator `ether` backend, observed guest peer IP — runs host-side in the MCP process, so it answers even when the host server is down), `mac_reboot` (in-process `ShutDwnStart`), `mac_restart_toolserver`, `run_applescript` (host-side `osascript`).
   - New tools register on the next MCP-server restart (the running server caches the tool list).

Smoke test: `cd host && /usr/bin/python3 send_command.py 'Echo HELLO'`.

## Hard rules (learned the hard way)
- **`ILink -model far` is the linker for the daemon** — plain `Link` now fails it with Error 48 (one ~98 KB segment, 32 KB PC-relative reach). Small tools still link fine with `Link`. Why, and when to revisit: D-011 in `DECISIONS.md`.
- **`/usr/bin/python3` for the host server** — never a venv interpreter; stdlib-only, so system Python suffices. Why: D-007 in `DECISIONS.md`.
- **Re-run `Rez AppleBridge_res.r` after every link** — the `SIZE` resource (`isHighLevelEventAware`) is required or every command fails with `-903`.
- **Never `2>&1`** in MPW (crashes the shell) — use `≥ file.err` to capture stderr (learned 2026-04-06, error-capture notes in `~/.claude/CLAUDE.md`).
- **Build off the running daemon**: link to `:bin:AppleBridge.new`, then swap; a heavy link in the same ToolServer that serves the bridge can take it (and the AE layer) down (observed 2026-07-02: the 0.8d2 link dropped the bridge mid-command).
- **Never hard-kill BasiliskII** — the clean stop is **`mac_shutdown`** (the `SHUTDOWN` verb, Shutdown Manager `ShutDwnPower`) or Special → Shut Down in the guest. Why, and when to revisit: D-004 in `DECISIONS.md`.
- **Encoding**: host UTF-8/LF ↔ Mac MacRoman/CR — use `host/encoding_convert.py`.
- Long commands (e.g. `Link`) may return `-1712` (AE timeout) **yet still complete** — verify by the artifact, not the status (recurring since 2026-04-06; the artifact check is the only reliable signal).
- **Host `.154` must live on the default-route interface** (where the guest's MACNAT exits — normally Wi-Fi `en0`), *not* a second NIC. If it's on the wrong interface, the daemon hangs on "CONNECTING" and freezes the emulator at 100% CPU (synchronous `OTConnect` starving the cooperative scheduler). `host/start_stack.sh` sets this up. **No bridge with `etherhelper/<if>` — the helper owns that NIC directly** and passes AppleTalk too; tooling neither creates nor destroys one. A bridge belongs to the **tap** mode (`etherhelper/tap0/bridge0/en0`), where it joins tap to the physical NIC. This rule has now been wrong twice in both directions — first "never pre-create" from a real `etherhelpertool` SIGSEGV, then "required" from the operator's launcher — so it is settled by measurement in D-017 (2026-07-27, both ways) and not by inference. **The `etherhelper` path costs two interactive password prompts per launch** (the bridge, and BasiliskII elevating its built-in helper), so it cannot start unattended; **slirp costs none**. The guest is behind MACNAT, so it is **never pingable** — diagnose via the *outbound* connection, not ICMP. See `TROUBLESHOOTING.md` → "Daemon hangs on CONNECTING". **This rule presupposes two interfaces**; on a single-NIC host `etherhelper` cannot form a guest→host connection at all (the guest reaches the whole world *except* the machine it runs in) and the backend must be `slirp`: D-015 in `DECISIONS.md`, requirements in `docs/INSTALLER_REQUIREMENTS.md`.

## Where things stand
AppleBridge runs as a **System 7 background service** with an optional on-screen monitor. The daemon speaks **wire protocol v0.2** (version negotiation + optional mutual auth); the host server auto-starts via launchd. Current daemon **0.8d30** (`mac/vers.r` is the single source for that number); the MCP surface is **30 tools** (`len(TOOLS)` in `mcp/tools.py`). Validated live on **both** Basilisk II (System 7.6.1) and SheepShaver (PowerPC / Mac OS 9), and on real hardware — a Macintosh **SE/30** over RS-422.

**Progress and roadmap live on the [ledger](https://pit.390er.de/applebridge/applebridge-roadmap-ledger-progress-and-status-tracker/), not in this file.** What shipped when, which PR carried it, what is still open, what is blocked — that is the ledger's job, and `host/tools/ledger_diff.py` keeps it in step with the merged PRs. This file holds what an agent needs in order to *work*: the rules, the mechanisms, and the gotchas that each cost somebody a session. Where the two disagree about status, **the ledger wins** — a status narrative kept in two places drifts, which is precisely how a finished milestone sat here for three weeks marked as outstanding.

## Process (one programmer + one AI)
Each class of fact has exactly one owner; everything else links instead of restating (decided 2026-07-26, D-006):
- **Status of work** → the ledger. **Decisions** → `DECISIONS.md` (its wording is authoritative; every entry carries evidence *and* a "revisit if" falsifier). **Dated accounts** → the article corpus, archival: an overtaken plan gets a dated *superseded* banner linking its successor — never a silent edit.
- **Hard rules carry provenance** — a year or a pointer, enforced by `tests/test_doc_claims.py`; design docs in `docs/` journal *no* progress (same test — status markers there are a failure, not a habit). Rationale: four unfalsifiable rules from the first commit were wrong and held 82–110 days because obeying them suppressed the experiment that would have refuted them.
- **Session bracket**: a SessionStart hook prints `host/tools/session_brief.py` (version, branch, decisions, open ledger items — state, not history); the Stop hook runs `ledger_diff.py --quiet` (silent when in step).
- **Subtraction pass**: at each milestone the AI proposes deletions/consolidations with rationale; the human arbitrates. Addition is cheap here and deletion is judgement — left alone, this pairing only ever adds (2026-07-26 assessment).

## Capabilities and the gotchas that come with them
Durable knowledge, not a changelog. Every entry below is something that will bite again.

### Driving the guest
- **Synthetic input** — `mac_type` / `mac_key` / `mac_click` post into the OS event queue, delivered to the **front** app. The daemon stamps `evtQModifiers` via `PPostEvent` *and* holds the low-memory `KeyMap` bits, so Command shortcuts reach the front app's `MenuKey`.
- **A key-down message has two halves**, and applications disagree about which one to trust: the low byte is the character, the next byte the **physical key**. `mac_key`/`mac_menu` derive the key code from the character (`_CHAR_KEYCODES`); `key_code` overrides, and `APPLEBRIDGE_KEY_LAYOUT=de` swaps Y/Z for a QWERTZ `KCHR`. **Never probe key injection with Cmd-A** — `a` *is* key code 0, so that test passes even when the code is wrong. That blind spot hid a defect for three weeks. See `docs/INPUT_MODIFIERS_AND_MENUS.md`, [[applebridge-input-modifiers-menu]].
- **Tracking loops poll the hardware pointer**, so no synthetic click reaches a menu, a Standard File list, or a modal dialog. Two ways round it: the **journaling `DRVR`** on the daemon's own menus, or the host's real mouse (see *Driving the guest's real mouse* below).
- **Journaling driver** (`mac/journal/ABJournal.a`) — drives the daemon's **own** menu bar and modals: `JMENU` (popups), `JABOUT`, `MENU:<title>:<item>` by name, `JSF` for modal Standard File, plus `JSAFE`, an interrupt-time Time Manager watchdog that zeroes `JournalFlag` to recover a modal spinning at 100 %. **The mechanism that makes `JSF` work:** `SetFrontProcess(self)` is *asynchronous* and `ModalDialog` never yields, so the daemon pumps `WaitNextEvent` until `GetFrontProcess` confirms it is truly front — and **bails before opening the modal** if the switch never lands, because a failed foreground would peg the CPU. A spike (`JPROBE`) proved reaching an **arbitrary front app** is a dead end: `MenuSelect` uses the caller's menu list. Foreign shortcut-less menus stay host-`cliclick`-only. `docs/JOURNALING_MENU_BY_NAME.md`, [[applebridge-journaling-driver-feasibility]].
- **`mac_menu_front`** reaches a *front* app's menu by numeric id through the Route-B `MenuSelect` trap patch installed by a boot INIT — local Basilisk only.

### The daemon as a service
- Config comes from an **AppleBridge Prefs** text file (`IP=`/`DEBUG=`/`APP=`/`NET=`/`TOKEN=`/`HOME=`); the daemon **chain-launches** its helpers (ToolServer first) and installs itself into **Startup Items** as a self-made Finder alias, so a cold boot brings the whole bridge up unattended.
- Three components: **AppleBridge** (daemon, creator `'ABrg'`), **AppleBridgeConfig** (control panel, `'ABcf'` — see `mac/config/README.md`), **AppleBridge Prefs** (shared flat file).
- **The config app deliberately has no Launch/Stop buttons:** quitting the faceless daemon tears down Open Transport and trips a Sequoia/SDL2 host crash — and it is meant to run continuously anyway.
- **Gotcha:** an `APP=` entry that opens a fullscreen presentation (e.g. *About Mac OS 7.6.1 Update*) freezes the emulator at chain-launch. Keep the list to real helper apps.
- **Self-update (`SWAPSELF` / `mac_update_daemon`):** an installer *copying* over a running daemon fails with `fBsyErr`, but **renaming an open file is allowed** — it edits the catalog, not the forks. So the daemon renames **itself** aside and the host-staged `<name> new` into place; a reboot lets the watchdog start the new binary. Works on System 7 *and* Mac OS 9. `docs/SELF_UPDATE.md`, [[applebridge-daemon-self-update]].
- **Live transport hot-swap:** the daemon re-reads `NET=` between commands (~5 s throttle) and swaps the OT/MacTCP/Serial stack **without relaunching** (~4 s). Only transport-selecting fields are adopted from the re-read. On *real* hardware, prefer a reboot — tearing the SCC stack down live throws Error 11.
- **The Verbose monitor window** is `backgroundAndForeground` + `getFrontClicks` in the SIZE resource, **not** `onlyBackground` — so it is clickable, and it **steals the front app** when clicked. `MONITOR:0` hides it while driving a GUI. Gotcha bank in [[applebridge-verbose-console-ux]]: a styled `TERec` reports `lineHeight = -1` (breaks scroll math — keep your own); the scrollbar is hit-tested geometrically because `FindControl`/`TrackControl` do not engage here; injected clicks cannot reach the daemon's own window, so test it interactively.

### Transport and protocol
- **v0.2 handshake:** the host sends a `HELLO:` probe; a v0.1 daemon answers "Invalid command format" and the host falls back to legacy — so the two sides upgrade independently. With a token set on **both** sides (`TOKEN=` + `APPLEBRIDGE_TOKEN`) they run a **fail-closed** FNV-1a-64 challenge/response, computed in 32-bit halves because MPW C has no 64-bit int. No token (the default) changes nothing. `docs/PROTOCOL_v0.2.md`, [[applebridge-protocol-v0.2-progress]].
- **Hardening still in force:** asynchronous `OTConnect` plus an application-level heartbeat (a down host no longer freezes the guest), length-framed receive reassembly, teardown-on-send-failure, an off-stack 64 KB buffer, watchdog reconnect backoff. Host side: length-framed reads, `kOTFlowErr` handling, heap-streamed responses **>64 KB**.
- **Persistent connections were consciously deferred** in v0.2 — the guest link is already persistent and the MCP client opens a socket per command, so there is no gain without a coordinated client change.

### Building software for the guest
- **Build GUI apps in C** (`MinQDC`); use assembly for **MPW tools** (`MinAsm`). The old "GUI apps crash Basilisk" belief was a *broken guest binary* — an assembly app linked without the MPW runtime bootstrap — not a window bug.
- **A guest app must yield.** `WaitNextEvent` on every tick; a `Button()` spin starves the background daemon and freezes the bridge. This is not a style preference — it is why an animation demo hung the machine.
- **`host/tools/gif_to_rez.py`** turns a GIF into classic-Mac resources (`'clut'`/`'GFin'`/`'Gfrm'`, PackBits per row, 602 KB → 89 KB round-trip-verified) for playback from a hand-built off-screen 8-bit `PixMap` via `CopyBits`. Demo app in `mac/claudeapp/`, [[applebridge-claudeapp-animated-about-gif]].
- **Extracting icons and artwork:** a file's icon lives in its own resource fork, a system icon in the **System file** (trash: `ICN#`/`icl8` −3993 empty, −3984 full), and the menu-bar apple in **neither** — it is character `$14` of Chicago, whose strike is in ROM, coloured when the menu bar is drawn. [[applebridge-icon-artwork-extraction]].

## Volume info & the monitor window
```bash
printf 'DISKINFO\n\n' | nc localhost 9001              # every mounted volume
printf 'DISKINFO:AppleShare\n\n' | nc localhost 9001   # one, by name
printf 'MONITOR:0\n\n' | nc localhost 9001             # hide the Verbose console
printf 'MONITOR:1\n\n' | nc localhost 9001             # show it again
```
`DISKINFO` answers `name\tvRefNum\ttotalBytes\tfreeBytes` from `PBHGetVInfo` — no ToolServer, so it also works where none is installed, and it is the question that follows every `AFPMOUNT`. **A volume name must carry a trailing colon**: without one the File Manager reads it as a *file* on the default volume and quietly answers about **that** volume instead (`DISKINFO:AppleShare` reported `MeinMac` until fixed); both verbs normalise either spelling.

`MONITOR:0/1` gets the console out of the way while the guest's GUI is driven. Hiding uses `HideWindow`, not the close box's teardown, so **commands keep being logged while hidden** and the history is there when it returns. *Known cosmetic limitation:* after `MONITOR:1` the title bar stays blank until the window is clicked — a background application's window is never activated by `ShowWindow`/`SelectWindow`, and seven approaches (incl. `PaintOne`, `SetWTitle`, `HiliteWindow`, a full rebuild) did not paint it. Do **not** "fix" it with `MoveWindow`: that call takes the *structure* origin while the port gives the *content* origin, so nudging the window walks it up one title-bar height per show until the bar disappears under the menu bar.

The daemon's **Apple menu now opens desk accessories and control panels** (`OpenDeskAcc`). Before that it drew the entries but picking one did nothing at all — which is also why the Chooser could not be opened from the daemon.

## AppleShare without the Chooser
Two verbs on the control port (no ToolServer, no GUI):

```bash
printf 'NBPLOOK:=\n\n' | nc localhost 9001                      # who is out there
printf 'AFPMOUNT:*:ApfelNetz:AppleShare:::1\n\n' | nc localhost 9001   # guest mount
printf 'AFPMOUNT:*:<srv>:<vol>:<user>:<pw>:2\n\n' | nc localhost 9001  # cleartext login
```

`AFPMOUNT:<zone>:<server>:<volume>:<user>:<password>[:<uam>]` builds an `AFPVolMountInfo` and calls `PBVolumeMount`; `uam` is `1` (guest) or `2` (cleartext, default). Both verified live against a netatalk server (which *does* offer cleartext). Replies `<volume>\t<vRefNum>` — read the name back, a duplicate gets renamed by the server. Named errors: `-5062` already mounted, `-5023` login rejected (wrong password **or** cleartext refused), `-47` volume busy.

- **The password is in the clear** — nothing else fits in the mount record. It is masked in the daemon's Verbose console (`…:AppleShare:***`), the host logs only `AFPMOUNT <server>:<volume>`, and a *mistyped* AFP verb is redacted before the fall-through logger sees it. Don't undo any of those.
- **`AFPUNMOUNT:<volume>` usually returns `-47`** with the Finder running: it opens the volume's desktop database on mount, so files are always "open". The reliable unmount is Finder → **Put Away** (`mac_host_click` the volume icon + `mac_key y modifiers=[command]`).
- A verb with no host route is **not** an error: it falls through to ToolServer, which swallows it and answers `STATUS:0` with empty output. If a new verb "works but does nothing", check `host_server.py`'s dispatch first — and read `mac_verbose_log`, where `initAE / found=TS / send=0` gives it away.

## Driving the guest's real mouse (local emulator only)
Menus, Standard File lists and other **modal tracking loops poll the hardware pointer**, so synthetic `mac_click` never reaches them. Use **`host/guest_input.py`**, which takes **guest** coordinates (what a screenshot shows) and handles the host mapping itself:

```bash
host/guest_input.py geometry                     # origin, title bar, live mapping
host/guest_input.py click 300 472                # click the desktop -> Finder frontmost
host/guest_input.py menu 18 9 70 112             # Apple menu -> Chooser, ONE gesture
host/guest_input.py shot out.png --region 250,80,560,420
```

It exists because each of these bit once: it **refuses** to click when the emulator is not frontmost (a stray click landed in the host's browser), it re-reads the window origin **before every gesture** (the window moved 448,128 → 605,104 mid-session), it keeps a menu's press→drag→release in a **single** `cliclick` call (a menu held open across two screenshots starved the daemon into `OTSnd err=-3158` + a 30 s reconnect), and `shot` captures **host-side** because the daemon cannot answer while a tracking loop owns the machine. Out-of-guest coordinates are refused outright.

The same three gestures are exposed as MCP tools — **`mac_host_click`**, **`mac_host_menu`**, **`mac_host_screenshot`** — and the coordinates come straight off a **`mac_screenshot`** image: that capture *is* the guest framebuffer, so its pixels are guest coordinates 1:1, no conversion. The loop is *screenshot → read the target's pixel position → call the tool → screenshot again to confirm*. Verified end-to-end: Chooser opened, AppleShare selected from coordinates read off the previous capture, bridge untouched throughout. A refusal (off-screen point, emulator not frontmost) comes back as `success: false` — no gesture happened, which is the safe outcome. **`mac_host_menu` needs the item's position known in advance**: a menu may not be held open to look around (that starves the daemon), so read the item coordinates from an earlier capture of that same menu.

## More detail
- `README.md` — user-facing intro & examples
- `ARCHITECTURE.md` — full design
- `TROUBLESHOOTING.md` — failure modes & fixes
- Build recipes, trap defs, encoding tables: the user's global `~/.claude/CLAUDE.md`
