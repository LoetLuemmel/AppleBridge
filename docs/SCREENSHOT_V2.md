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
  `baseGen` names it.
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
