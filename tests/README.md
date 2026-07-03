# Tests

Two tiers, split by whether a live emulator is needed:

- **Host-edge** (`test_*.py`, `run_all.sh`) — pure, stdlib-only unit tests over
  the host modules. No emulator, no live bridge. This is the tier CI gates.
- **e2e-smoke** (`smoke_e2e.py`) — an end-to-end check that drives the **live
  stack** over the control port. Needs the emulator booted and the daemon
  connected, so it is **not** run in CI; you run it by hand (see below).

## Host-edge tests

Unit tests for the pure, stdlib-only host modules — the deterministic
bytes-in/bytes-out code where a silent regression corrupts data. No emulator
and no live bridge are involved.

## Run

```bash
./tests/run_all.sh            # whole suite, under /usr/bin/python3
python3 tests/test_macbinary.py   # one file
```

No pytest dependency: each file is a set of `test_*` functions with a stdlib
`__main__` runner, so it runs under the same `/usr/bin/python3` the host server
uses. `pytest tests/` also works if pytest is installed.

## Coverage

| File | Module under test | What it pins |
|------|-------------------|--------------|
| `test_macbinary.py` | `host/macbinary.py` | MacBinary II header offsets, CRC-16/XMODEM, fork padding, round-trip, corruption/truncation rejection |
| `test_screenshot_decode.py` | `host/screenshot_decode.py` | raw pixmap → PNG for depths 1/4/8/16/32, region crop, error paths (decodes the PNG back and checks exact RGB) |
| `test_encoding_convert.py` | `host/encoding_convert.py` | LF↔CR line endings both ways, MacRoman special chars, round-trip, unencodable → `?` |
| `test_framing.py` | `host/host_server.py` | length-framed reader: reassembly across arbitrary `recv()` splits, STDOUT read by declared length (embedded blank line survives), control-command terminator/EOF |
| `test_parse_response.py` | `mcp/mac_connection.py` | the "no false success" response-parsing contract (pre-existing) |

## e2e-smoke tier

`smoke_e2e.py` proves the whole pipe actually round-trips — host server →
daemon → (ToolServer) → back — over the loopback control port (`:9001`), the
same hop `send_command.py` uses. Pure stdlib; imports nothing from `host/`.

```bash
./host/start_stack.sh                 # bring the stack up first (once)
./tests/smoke_e2e.py                  # read-only smoke, assumes ToolServer up
./tests/smoke_e2e.py --no-toolserver  # skip the Echo tier (e.g. SheepShaver/OS 9)
./tests/smoke_e2e.py --full           # also do the file write/read round-trip
```

It self-adapts: if `MACSTATUS` reports `toolserver=0`, the Apple-Event Echo
check is skipped rather than failed, so the same script is meaningful on a
target without ToolServer. Exit code is non-zero only if a check **fails**
(a skip is fine). Checks, by tier:

| Tier | Check | What it proves |
|------|-------|----------------|
| `host` | control-port reachable | `host_server.py` is listening on `:9001` |
| `bridge` | daemon liveness (`MACSTATUS`) | daemon connected + `STAT` answering; reports `net`/`toolserver`/`home` |
| `toolserver` | AE echo | a fresh nonce round-trips through ToolServer (skipped if `toolserver=0`) |
| `bridge` | native `LISTDIR` | `PBGetCatInfo` directory listing (no ToolServer) of the daemon's folder |
| `bridge` | screenshot stream | the raw-pixmap→PNG binary path yields a valid PNG |
| `mutating` | file write/read round-trip | `WRITEFILE` then `READFILE` recovers the written token (only with `--full`) |

Because it needs a booted emulator, this is the manual pre-release / post-change
gate, kept out of `run_all.sh` (and therefore out of CI).

## Note

`test_encoding_convert.py` verifies `≈` → MacRoman `0xC5` (0xC7 is `«`). The
table in the global `~/.claude/CLAUDE.md` lists `≈` as `c7`, which is incorrect
— the bridge emits `0xC5`, matching Python's `mac_roman` codec.
