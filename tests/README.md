# Host-edge tests

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

## Note

`test_encoding_convert.py` verifies `≈` → MacRoman `0xC5` (0xC7 is `«`). The
table in the global `~/.claude/CLAUDE.md` lists `≈` as `c7`, which is incorrect
— the bridge emits `0xC5`, matching Python's `mac_roman` codec.
