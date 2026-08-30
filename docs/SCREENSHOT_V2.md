# Screenshot v2 — region, PackBits, XOR row delta (daemon 0.8d46, 2026-08-30)

The legacy `SCREENSHOT` verb streams the raw screen pixmap — 786 432 bytes at
1024×768×8 — for every look, and the host squeezes that into a ~20 KB PNG
afterwards. On the shipping `ether slirp` link (~200 KB/s measured) that is
**4.4 s per capture**, and the suspected base64 leg is milliseconds of it. The
compression happened on the wrong side of the slow link.

`SCREENSHOT2` moves the three savings to where the pixels are, so every target
benefits (Basilisk II, SheepShaver, an SE/30 over RS-422), and leaves the old
verb byte-identical for older hosts.

## Wire format

Request (host → daemon):

    SCREENSHOT2:<x>:<y>:<w>:<h>:<flags>:<baseGen>

- `w`/`h` of 0 = whole screen. Below 8 bpp the crop is rows-only (`x` forced
  to 0, the host slices columns).
- `flags` bit 0 = PackBits rows; bit 1 = delta against the retained frame if
  `baseGen` names it; bit 2 = Up predictor (enc 3) on a non-delta capture.
- `baseGen` = generation of the full frame the host holds (0 = none).

Reply:

    IMAGE2:<w>:<h>:<depth>:<rowBytes>:<clutCount>:<rx>:<ry>:<rw>:<rh>:<enc>:<gen>:<dataSize>\n
    <clutCount*3 bytes CLUT>
    <dataSize bytes payload>

`w`/`h`/`rowBytes` describe the screen, `rx`..`rh` the rectangle carried. The
header ends in CR on the wire (classic-Mac `'\n'`), the host accepts CR or LF.

| enc | payload |
|---|---|
| 0 | raw rows, contiguous (`rw*depth/8` bytes each) |
| 1 | per row: `<packedLen:2 BE><PackBits bytes>` |
| 3 | as enc 1, but each row (after the first) is **row XOR the row above it** before packing — PNG's "Up" filter. Default for every non-delta capture since 0.8d47. |
| 2 | runs of `<y0:2><count:2>` followed by `count` rows in enc-1 form, each row being **row XOR the previous frame's row** |

The daemon retains one full-screen copy (`gPrevFrame`, `mac/src/screenshot.c`)
and a generation counter. Only a full-screen capture advances them; a region
capture never becomes a delta base, and the host only advances its own base on
a full frame. A delta is sent only when `baseGen == gen` on the daemon;
otherwise a full enc-1 frame comes back, so the host never guesses. A delta
whose base the host does not hold drops the link rather than compositing onto
the wrong frame (`host/host_server.py`, `_request_screenshot_v2`).

**Why XOR:** PackBits on a dithered System 7 desktop packs only 1.33:1, and a
whole changed row is still 1 024 bytes. The rows that change between two
captures are mostly the daemon's own console logging the command; XOR-ing each
changed row with its predecessor turns the unchanged bytes into zero runs, and
the same 228 rows that cost 138 KB as a plain row delta cost 3 KB.

## Measured encoders (THINK C on the guest, 2026-08-30)

`host/bench/fbbench.c`, built by the THINK Project Manager on the guest and
run against the live 1024×768×8 framebuffer (Basilisk II, `jit false`, so
interpreted 68K time; `TickCount()` over K = 20 iterations; results in
`host/bench/results/`). Ratios are hardware-independent, the milliseconds are
this emulator's.

| method | bytes | ratio | ms / frame |
|---|---|---|---|
| copy (`BlockMoveData`) | 786 432 | 1 | 1.7 |
| row compare vs a copy (today's delta scan) | — | — | 7.5 |
| PackBits per row (enc 1) | 566 945 | 1.39 | 11.7 |
| **Up predictor + PackBits (enc 3)** | **57 907** | **13.6** | **32.5** |
| row dedup (hash, 16-row window) + PackBits | 498 912 | 1.58 | 88 |
| XOR previous frame + PackBits, frame unchanged (enc 2 rows) | 13 824 | 57 | 15 |
| 2×2 nearest downsample (bytes only) | 196 608 | 4 | 6 |
| LZSS (Okumura, 4 KB window) | 98 280 | 8.0 | 1 230 |
| Up predictor + LZSS | 99 529 | 7.9 | 1 220 |

The System 7 desktop is a dither with no horizontal runs and a two-row
vertical period, which is why PackBits alone does nothing and the Up
predictor does almost everything; LZSS finds the same structure 40× more
slowly, and the predictor gives it nothing further. Row dedup fails because
adjacent rows are *not* identical (the pattern alternates), a hypothesis the
measurement refuted. Decision: enc 3 ships; the first look on a link is now
0.45 s / 47 KB (was 2.7 s / 578 KB with enc 1, 4.4 s / 787 KB raw).

**Change detection, settled.** The full-frame row scan costs 7.5 ms here and
the copy 1.7 ms — together under 10 % of a 140 ms delta round trip, and on an
SE/30's 21 KB 1-bit screen the same scan is a few hundred microseconds against
a serial line that needs 4 s for a frame. A drawing-trap hook that tracked
dirty rectangles would save nothing measurable on any target: **no-go**, by
numbers rather than taste.

**Host-side framebuffer export, measured.** A `SIGUSR1` hook in Basilisk's
`video_sdl2.cpp` (branch `fb-export` of the macemu fork) dumps `the_buffer`,
the SDL palette and the last scan's dirty 64×64 tiles to a file: **12–31 ms
from signal to a decoded PNG**, zero bytes over the bridge, zero guest CPU;
pixels identical to the daemon's capture (0 of 120 000 differ away from the
console; the palette is the *display* palette, gamma-adjusted, not the guest
CLUT). It is the fastest path there is and it exists for one target of three,
which is why it stays a spike (D-025).

## Host side

- `host/screenshot_decode.py`: `unpack_bits`, `unpack_rows`, `parse_delta`,
  `apply_delta`, and `raw_to_png_indexed` — an indexed PNG (colour type 3)
  written by row slice, no per-pixel loop: 0.31 s → 4 ms for a full frame.
- `host_server.py`: `request_screenshot(region, delta)` tries v2 first; a
  daemon that answers `Invalid command format` is remembered as legacy for
  that link. The `screenshot[:x:y:w:h]` control verb forwards the region to
  the guest and reports `enc`/`gen`/`wire_bytes`/`elapsed_ms` in a trailing
  `SHOTINFO` field, which `mcp/mac_connection.py` reads and `mac_screenshot`
  returns beside the image.

## Measured (2026-08-30, Basilisk II, System 7.6.1, `ether slirp`, 1024×768×8)

| capture | before | after | wire bytes |
|---|---|---|---|
| full screen, first look | 4.4 s | 2.7 s (enc 1) | 787 KB → 578 KB |
| full screen, next look (console logging) | 4.4 s | **0.14 s** (enc 2) | 787 KB → **2.8 KB** |
| full screen, next look, nothing changed | 4.4 s | 0.13 s | 815 B |
| region 400×300 | 4.4 s | 0.45 s (enc 1) | 787 KB → 85 KB |
| host PNG encode | 0.31 s | 0.004 s | — |

Correctness check: a delta-composited full frame cropped host-side matched an
independent guest-cropped region of the same moment in all 120 000 pixels.

## Build gotcha met on the way

`ProcessRequest` in `main.c` sits at the 68K 32 KB frame-displacement edge:
adding 14 bytes to `ScreenshotData` made SC fail with an internal error naming
a function the diff never touched. Both screenshot verbs now live in their own
functions (`ScreenshotVerb`, `Screenshot2Verb`). Details and the control
experiment: `docs/OPERATING_NOTES.md`, 2026-08-30.
