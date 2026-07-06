# AppleBridgeConfig

A small foreground control-panel app for the **faceless** AppleBridge daemon
(creator `'ABrg'`, `onlyBackground` — no window of its own). The daemon is meant
to run continuously, so this app is where a human configures and supervises it.

## What it does

- **Status** — shows whether the daemon is running (walks `GetNextProcess` for
  creator `'ABrg'`) and whether autostart is installed.
- **Install / Remove Autostart** — drops (or deletes) an alias to the daemon in
  the System Folder's **Startup Items**, so the daemon launches at boot. The
  daemon in turn chain-launches its helper apps (ToolServer first), so a single
  Startup Items entry is enough.
- **Add Helper App…** — a Standard File picker; the chosen app's full HFS path is
  appended as an `APP=` line in the shared **AppleBridge Prefs** file (in the
  Preferences folder). The daemon chain-launches these on startup.
- **Networking service** — three radios (**Open Transport** / **MacTCP** /
  **Serial**), written as `NET=OT` / `NET=MacTCP` / `NET=Serial` in the prefs.
  Open Transport is the default; MacTCP is the lighter, pre-OT-capable backend;
  **Serial** reaches Ethernet-less machines over the modem/printer port.
- **Serial options** (dimmed unless **Serial** is selected) — a **port** pair
  (**Modem (A)** / **Printer (B)** → `PORT=A` / `PORT=B`) and a **baud** selector
  (**9600** / **19200** / **38400** / **57600** → `BAUD=`). 9600 is the default,
  safe first-contact rate; bump higher once the link is proven. **The host must
  be set to the same baud** (`APPLEBRIDGE_BAUD=`) — there is no autobaud.
  The change **takes effect on the daemon's next launch** (reboot or relaunch) —
  `NET=` hot-swaps live, but `PORT=`/`BAUD=` are read at startup.
- **Quit** — quits the config app.

There are intentionally **no Launch/Stop Daemon buttons**: the daemon is a
continuously-running service. Start it via autostart (or the Finder); it is not
designed to be stopped and restarted from the UI.

## How autostart works

"Install Autostart" creates a real Finder alias file named **AppleBridge Watchdog**
in `System Folder:Startup Items:` pointing at the *watchdog* binary (see
`../watchdog/`). The watchdog — not the daemon — is the boot entry, because it owns
the daemon's lifecycle:

```
boot ─▶ Startup Items: AppleBridge Watchdog ─▶ launches AppleBridge (daemon)
                                                   └─▶ chain-launches ToolServer
```

1. `NewAlias` builds an alias record to the watchdog's `FSSpec`.
2. `FSpCreateResFile` makes the alias file (type `APPL`, creator `'ABwd'`).
3. The alias record is added as an `'alis'` resource and written.
4. The file's `kIsAlias` Finder flag is set so the Finder resolves it at boot.

At startup the Finder launches everything in Startup Items, resolving the alias and
launching the watchdog faceless; the watchdog finds no daemon and launches it, and
the daemon chain-launches its helpers (ToolServer) from the prefs.

### Manual placement (fallback)

If you'd rather not use the button, make the alias by hand: select
`:bin:AppleBridgeWatchdog` in the Finder, **File ▸ Make Alias**, then drag the alias
into `System Folder:Startup Items:`. Same result.

## Build

Run inside MPW from the AppleBridge project folder (`MeinMac:MPW:AppleBridge:`),
after the daemon's `:obj:prefs.c.o` and `:obj:mystring.c.o` have been built:

```mpw
Directory MeinMac:MPW:AppleBridge:
Execute :config:BuildConfig.emu      # SC + Link + Rez + SetFile -c 'ABcf'
```

Produces `:bin:AppleBridgeConfig` (type `APPL`, creator `'ABcf'`). No Open
Transport — it talks to the daemon only through the prefs file and the Startup
Items alias.
