# Deploying AppleBridge to Real 68k Hardware over Serial

Status: **draft** (2026-07-02). Design + deployment guide for the drag-install
package. Companion to [SERIAL_TRANSPORT.md](SERIAL_TRANSPORT.md).

Every deployment so far has pushed files to the guest *over the running bridge*
(`mac_put_file`). That is a bootstrap paradox for a **fresh** machine: there is no
bridge yet, and for the serial target there is often no Ethernet at all. This
document designs a **self-contained drag-install package** that a user installs
*on the target Mac*, delivered by whatever offline medium that machine can read,
and configured for the serial transport.

## The bootstrap problem, stated plainly

- **No network to bootstrap over.** A Plus/SE/Classic has a serial port and
  nothing else; even a networked 68k Mac isn't on the bridge until AppleBridge is
  installed and configured. So the package must be runnable by double-clicking on
  the target — no host-driven `mac_put_file`.
- **Resource forks must survive the trip.** The daemon, config app, watchdog, and
  installer are classic applications whose code lives in the **resource fork**. A
  plain macOS folder cannot hold a resource fork; any transfer path must preserve
  both forks (an HFS volume, MacBinary/BinHex, or a fork-aware tool). This is the
  single most common way a hand-carried classic install is silently corrupted.

## Package contents (the drag-install folder)

A folder — `AppleBridge Install ƒ` — that the user copies onto the target's hard
disk and opens:

```
AppleBridge Install ƒ
  AppleBridge Installer   ('ABis')  — run this; installs the rest + autostart
  AppleBridge             ('ABrg')  — the faceless daemon
  AppleBridgeConfig       ('ABcf')  — control app: transport, port, autostart
  AppleBridgeWatchdog     ('ABwd')  — relaunches the daemon if it dies
  AppleBridge Prefs                 — a template pre-set to NET=Serial (see below)
  Read Me                           — the on-machine steps (a text subset of this doc)
```

ToolServer and MPW are **not** bundled — they are large and separately licensed,
and not every target can run them (see *Capability tiers*). The installer
preflights for them and the Read Me states the requirement.

## Capability tiers (what the target must be)

Serial removes the *network* floor but not the *toolchain* floor. Two tiers:

| Tier | What works | Minimum target |
|---|---|---|
| **Connectivity** | The daemon connects over serial, negotiates protocol v2, answers `PING`/`STAT`/`HELLO`, injects input, screenshots. **No `Echo`/build** — those need ToolServer. | System 7.0+, Apple Events (Gestalt), a serial port, ~1–2 MB free. Reaches a Plus/SE/Classic with a hard disk. |
| **Full command** | Everything, including `mpw_execute` / compiles — because MPW **ToolServer** is running to return output. | Realistically a **68030, hard disk, ~16 MB RAM** (MPW/ToolServer's floor). A bare Plus cannot host MPW. |

The serial round-trip verification (`negotiated protocol v2` + a `PING`/`STAT`
exchange over the wire) is a **connectivity-tier** test and does *not* require
ToolServer on the target — useful, because it lets a small machine prove the
transport before committing to a full MPW install. `Echo HELLO` returning output
is a full-tier test.

## Installer adaptation

The shipped installer (`'ABis'`, `mac/installer/installer.c`) already does a
Gestalt preflight, a fork-aware copy **from its own folder** (it resolves its own
directory via `GetCurrentProcess` → `GetProcessInformation` → `processAppSpec` and
copies the sibling binaries), a `HOME=`-relocatable install, and autostart
(Startup Items alias to the watchdog). So the drag-install payload model is
*already* supported — the only gap was serial. **One change** (done, this branch):

- **Serial-aware preflight + default.** The transport preflight was a *critical*
  "TCP stack" check that **failed** — and disabled Install — on a machine with no
  Open Transport / MacTCP. It now probes the serial driver (`.AOut`) too and
  passes as "Serial (modem port)", so an Ethernet-less machine installs. When no
  TCP stack is detected the seeded prefs default to `NET=Serial` / `PORT=A` /
  `BAUD=9600`; a detected OT/MacTCP still wins. Everything else (the `HOME=` copy,
  the autostart alias, the faceless bring-up) is unchanged.

A follow-up would add a **Serial radio** to AppleBridgeConfig (it currently offers
OT/MacTCP); until then the serial port/baud are set by the installer default or by
editing `AppleBridge Prefs`.

## Getting the folder onto an Ethernet-less machine

Pick whatever the target can read; **all must preserve both forks**:

- **BlueSCSI / SCSI2SD** — put the folder on an HFS image on the SD card, mount on
  the target. Forks preserved natively (it's a real HFS volume). *Recommended* for
  a machine with a SCSI port.
- **Floppy** (real or FloppyEmu) — an 800K/1.4M HFS floppy holds the package
  (~150–250 KB of apps). Forks preserved. Universal for machines with a floppy.
- **Serial terminal transfer** (ZTerm/Kermit + XModem/ZModem, or Kermit) — send the
  apps as **MacBinary** (`.bin`) over the *same serial cable*, then decode on the
  target with a MacBinary tool. Forks preserved by MacBinary framing. Works when
  serial is the *only* port, at the cost of a manual decode step.
- **LocalTalk / AppleShare** — if the target has LocalTalk (most do), an AppleShare
  volume or a LocalTalk-bridged Mac can copy the folder with forks intact.

Do **not** transfer via a modern flat filesystem (FAT USB, plain SMB) without a
fork-preserving wrapper — the resource forks vanish and the apps won't launch
(a 0-byte-data-fork app whose CODE is gone; the classic "-192" at launch).

## Host side — wiring and serial mode

**Cable + adapter.** A classic Mac serial port is RS-422 on a mini-DIN-8. To reach
a modern host: a **Mac mini-DIN-8 → DB-9/DB-25 serial cable** into a **USB↔RS-232
adapter**. For reliability at higher baud, wire **hardware handshaking** (Mac
HSKo/HSKi ↔ RTS/CTS); otherwise keep the baud modest (9600–19200) and rely on the
length-framed protocol's tolerance. (Real-hardware reliability — RTS/CTS and an
optional checksum/resend layer — is the deferred item in SERIAL_TRANSPORT.md.)

**Run the host in serial mode** on the adapter's device:

```bash
APPLEBRIDGE_SERIAL=/dev/tty.usbserial-XXXX APPLEBRIDGE_BAUD=9600 /usr/bin/python3 host/host_server.py
```

The `:9001` control port and all 20 MCP tools are unchanged — only the daemon
transport is a serial fd instead of a socket. (For a no-hardware dry run first,
`host/serial_harness.py` bridges two ptys; see SERIAL_TRANSPORT.md.)

## Configure the target

After running the installer, either edit **AppleBridge Prefs** (Preferences
folder) or use **AppleBridgeConfig**:

```
NET=Serial
PORT=A          # A = modem port, B = printer port
BAUD=9600       # match the host's APPLEBRIDGE_BAUD
```

`IP=` is ignored under serial. Leave `TOKEN=` empty for first bring-up (auth is
opt-in and the round-trip is easier to debug without it). Reboot so the daemon
loads the serial config and the watchdog brings it up.

## Bring-up and verification

1. Host: start `host_server.py` in serial mode on the adapter device.
2. Target: reboot; the watchdog launches the daemon, which opens the serial port.
3. Host log (`/tmp/applebridge_server.log`) should show the version handshake:
   `HELLO: negotiated protocol v2`.
4. `mac_status` (or a `STAT` over `:9001`) confirms the daemon is responding —
   the **connectivity-tier** pass.
5. If ToolServer is installed (full tier), `Echo HELLO` returns output.

If the daemon never appears: check the cable/handshaking, that baud matches both
ends, and that `PORT=` names the port the cable is on (A = modem, B = printer).

## What I can produce now vs. what needs hardware

- **Now (host-side, testable):** the `Read Me`, the serial-default `AppleBridge
  Prefs` template, the package folder layout, and the installer-from-own-folder +
  serial-default code changes (buildable on-device through MPW, like PR2/serial).
- **Assembling the payload:** pull the current `'ABrg'`/`'ABcf'`/`'ABwd'`/`'ABis'`
  binaries from the emulator fork-aware (MacBinary) into the package folder.
- **Needs your hardware:** the actual serial round-trip on the real Mac — the one
  step I can't run. The pty harness proves the code first; this doc + package get
  it onto the metal.

## Decisions to confirm

1. **Installer scope:** adapt the existing `'ABis'` to install-from-own-folder +
   serial-default (recommended, one file), or ship the package with manual
   drag-install steps in the Read Me and no installer changes?
2. **Payload assembly:** should I pull the current binaries into a staging
   `AppleBridge Install ƒ` now (as MacBinary), or wait until the installer changes
   are built so the package ships a matching set?
3. **Delivery image:** you chose a drag-install *folder*; do you also want a ready
   **HFS floppy/disk image** built around it (so it's one file to write to
   BlueSCSI/FloppyEmu), or will you wrap the folder yourself?
