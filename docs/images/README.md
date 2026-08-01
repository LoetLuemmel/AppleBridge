# Screenshots

Captures of the guest, used by `README.md`, `docs/SETUP.md` and
`TROUBLESHOOTING.md`. Nearly all of them are taken **by the daemon** — the
emulated framebuffer, streamed over the bridge and decoded by
`host/screenshot_decode.py` — so they show the guest's own screen rather than a
window on somebody's Mac.

**A picture ages faster than the prose around it, and silently.** The config
panel was photographed on 2026-07-31 at 16:46; its editable host-address field
landed at 17:42, and the image went on showing a panel without that field in
`docs/SETUP.md` for a day, under a caption that never mentioned it. Where a file
depicts something versioned, the version is in its **name**
(`config-panel-0.8d33.png`), so the mismatch is visible in a directory listing
rather than only to someone who reads the pixels.

## Deliberately unreferenced

- `installer-animated-logo.png` — no document links it. **Kept on purpose**
  (decided 2026-08-01); it is not an orphan awaiting cleanup, so a subtraction
  pass should leave it alone.
