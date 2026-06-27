# AppleBridgeWatchdog

The keep-alive helper for the faceless AppleBridge daemon (Phase 6). A tiny
faceless (`onlyBackground`) app, creator `'ABwd'`.

## What it does

Every ~3 seconds it asks the Process Manager whether the daemon (creator `'ABrg'`)
is running. If not, it relaunches the daemon from its known path
(`MeinMac:MPW:AppleBridge:bin:AppleBridge`). That's all — no window, no Open
Transport, no Apple Events to the daemon. It quits cleanly on the shutdown
`kAEQuitApplication`.

## Where it sits in the service

The watchdog is the **single Startup Items entry** and owns the daemon's lifecycle:

```
boot ─▶ Startup Items: AppleBridge Watchdog ─▶ launches AppleBridge (daemon)
                                                   └─▶ chain-launches ToolServer
        (watchdog keeps checking; relaunches the daemon if it ever disappears)
```

So `AppleBridgeConfig`'s **Install Autostart** installs an alias to the *watchdog*
(not the daemon directly): at boot the watchdog comes up, finds no daemon, and
launches it; the daemon then chain-launches its helpers from the prefs. The prefs
`APP=` list stays helper-only (ToolServer) — the watchdog is **not** a chain-launch
entry, which would be circular.

## Scope / known limit

On the current Basilisk II + macOS (Sequoia) / SDL2 host, a daemon death usually
tears down Open Transport and takes the emulator down with it, so the
*relaunch-after-mid-session-death* path rarely gets to run here. The boot-time
"launch the absent daemon" path is the **same code**, and is what the reboot test
exercises. Keep-alive's mid-session value is realized on a more stable host (e.g.
after a Basilisk II rebuild against a newer SDL2) or on real hardware.

## Build

Run inside MPW from the AppleBridge project folder (`MeinMac:MPW:AppleBridge:`):

```mpw
Directory MeinMac:MPW:AppleBridge:
Execute :watchdog:BuildWatchdog.emu      # SC + Link + Rez + SetFile -c 'ABwd'
```

Produces `:bin:AppleBridgeWatchdog` (type `APPL`, creator `'ABwd'`). It links
against `Interface.o` + `MacRuntime.o` only — no prefs, no StdCLib.
