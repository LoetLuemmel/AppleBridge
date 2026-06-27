# AppleBridge Control Panel (cdev) — minimal proof of concept

A minimal System 7 Control Panel (`'cdev'`, creator `'ABcp'`) that proves the
toolchain and approach for porting `AppleBridgeConfig` from an app into a Control
Panel. **Step 1 of the port** (see the CMS article *"Settings Belong in a Control
Panel"*): it does nothing useful yet — it answers the host's `macDev`/`initDev`
messages and shows one `statText` from its DITL.

**Proven on-device (2026-06-27):** built via MPW over the bridge, installed into
`System Folder:Control Panels:`, opened from Apple menu ▸ Control Panels — it
appears in the list (`macDev`), opens (`initDev` allocates the `cdevValue` handle),
and draws its text (`DITL`). A screenshot confirmed the panel rendering.

## Why this matters

Unlike the presence INIT (which C couldn't build — see `../init/README.md`), a cdev
is tractable in MPW because the architecture sidesteps the code-resource traps:

- **No globals** — per-instance state lives in the `cdevValue` handle (allocated in
  `initDev`), so no A4/A5 world is needed.
- **No string literals in code** — UI text lives in the `DITL` resource.
- **No function pointer handed to a trap** — the host *calls* the single cdev entry,
  so there's nothing for the linker to mangle.
- **No boot exposure** — a cdev runs only when opened; a bad one is removed by
  dragging it out of the folder.

## Files

- `abcp.c` — the cdev: `pascal long CDevMain(msg, item, numItems, id, event, cdevValue, dp)`.
  Answers `macDev` (return 1), `initDev` (`NewHandle` → `cdevValue`), `closeDev`
  (dispose), default (carry `cdevValue` forward). No globals, no strings, A4-free.
- `abcp.r` — `DITL` (one statText), `nrct` (the panel rectangle), `mach` (`FFFF 0000`
  = "ask macDev"). The `nrct`/`mach` formats were lifted from a working sample cdev
  on the AppleShare dev server.

## Build (MPW, over the bridge)

```mpw
Directory MeinMac:MPW:AppleBridge:
SC -model far -i :include: :src:abcp.c -o :obj:abcp.c.o
Link -rt cdev=-4064 -m CDEVMAIN :obj:abcp.c.o "{Libraries}Interface.o" -o :bin:"AppleBridge CP"
Rez abcp.r -a -o :bin:"AppleBridge CP"
SetFile -t cdev -c 'ABcp' :bin:"AppleBridge CP"
```

Note: `CDevMain` is `pascal`, so its linker symbol is **uppercased** to `CDEVMAIN`
(`-m CDEVMAIN`). Install by copying into `System Folder:Control Panels:`.

## Next steps (the rest of the port)

2. `hitDev` dispatch on a pushButton (item `numItems + index`).
3. `StandardGetFile` from a `hitDev` handler (the one real unknown — modal, expected
   to work).
4. The full panel: daemon status on `nulDev`, helper list `userItem`, the autostart
   actions — porting `AppleBridgeConfig`'s logic unchanged.
