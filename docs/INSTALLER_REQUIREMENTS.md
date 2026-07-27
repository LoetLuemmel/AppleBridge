# Installer requirements — what a fresh machine actually needs

What the host-side installer must do, derived by installing AppleBridge on a
machine that had never run it. Every requirement below is something that was
missing, wrong, or silently misleading during that bring-up (2026-07-27,
MacBook Pro 2013 / Big Sur / one Wi-Fi interface; guest System 7.5.3 with Open
Transport, no MPW, no ToolServer). None of them is speculative.

The one-line summary: **the installer's job is mostly derivation, not
installation.** Every value this project hardcoded is correct on exactly one
machine, and the failures they cause do not look like configuration errors.

## R1 — derive every address; ship none

`host/host_server.py` binds `HOST_INTERFACE = "192.168.3.154"`. That address
exists on one machine in the world. Elsewhere the server aborts with
`OSError: [Errno 49] Can't assign requested address`, which names neither the
address it wanted nor the interfaces it looked at.

Five further literals carry the same address: `bridge_doctor.py`
(`DEFAULT_HOST_IP`), `ensure_host_alias.sh`, `start_stack.sh`,
`install_alias_daemon.sh`, `install_host_service.sh`. `start_stack.sh`
additionally hardcodes `en8` and this machine's Basilisk path, so it is not
merely wrong elsewhere but actively harmful.

**Requirement:** the host address is read from the host, and `bind` failure
reports the address attempted and the addresses available.

## R2 — a wrong default address is worse than none

The guest-side installer seeds `IP=192.168.3.154` into `AppleBridge Prefs`. On
an isolated machine that produces a timeout and is obvious. On a LAN where
`.154` answers, the daemon **connects to the wrong computer** and reports
success: protocol negotiated, heartbeat running, zero errors on both consoles.
This happened during the bring-up and cost two rounds of diagnosis, while the
new machine's own server sat idle beside it.

**Requirement:** no shipped default host address. If one cannot be derived, the
installer stops and asks rather than guessing — an unanswerable question is
safer than a plausible wrong answer.

## R3 — say where the configuration lives

The daemon reads `AppleBridge Prefs` via `FindFolder(kPreferencesFolderType)`,
i.e. `<boot>:System Folder:Preferences:`. It is not in the installation folder,
which is where the binaries go and where a reasonable person looks first. The
installer folder also carries two *template* prefs files, one of them preset to
`NET=Serial` with a `HOME=` pointing at the developer's volume — editing a
template instead of the live file changes nothing and gives no feedback.

**Requirement:** the installer states the path of the file it wrote, and
templates are named so they cannot be mistaken for the live file.

## R4 — name the target on the success path

The daemon's Verbose console prints `Connecting to host...` without the address.
It prints the address only in the failure block. A console showing
`SYNC-OK` / `HELLO:2:` / `ERR 0` therefore looks identical whether it is talking
to the intended host or to a stranger's — which is exactly the state R2
produces.

**Requirement:** the address appears on connect, not only on failure.

## R5 — one word, two meanings

`IP Adresse` in the guest's TCP/IP control panel is the **guest's own** address.
`IP=` in `AppleBridge Prefs` is the **host's** address. In a setup instruction
the two values stand within three lines of each other, and swapping them is
silent: entering the slirp gateway `10.0.2.2` as the guest's own address raises
a Mac OS address-conflict alert against slirp's virtual router and disables the
TCP/IP driver.

**Requirement:** the installer sets both itself, so the ambiguity is never
exposed to a person.

## R6 — choose the backend by measuring, not by asking

See D-015. A host with two usable interfaces gets `etherhelper` and keeps
AppleTalk; a host with one gets `slirp`, because a bridged backend cannot form
the guest→host connection at all. The preflight must not infer this from the
machine model or the presence of a cable — it must establish whether the guest
can reach the host, which is a question with an answer.

The failure it prevents is not a networking error in any recognisable sense:
under `etherhelper` on a single-interface host the guest reaches file servers,
LAN web servers and the public internet, and fails **only** against the computer
it is running inside.

## R7 — slirp needs three values, and one of them is not obvious

Guest `10.0.2.15` / `255.255.255.0`, router `10.0.2.2`, **resolver `10.0.2.3`**.
The resolver field was empty after the switch, which surfaces as iCab network
error `-23045` (`authNameErr`) — a DNS failure that reads like a routing
failure. `10.0.2.2` is a router only: the daemon's connection to it is refused,
while the host's real LAN address works. The host server must bind `0.0.0.0`,
because connections arrive from `127.0.0.1` *and* from the host's LAN address
depending on which destination the guest used.

**Requirement:** the installer writes the guest's TCP/IP configuration together
with the prefs, as one derived set.

## R8 — privileges: required for one backend, not the other

`etherhelpertool` is not setuid and `/dev/bpf*` are `root:wheel 0600`, so the
emulator must be launched with elevated rights for that backend. `slirp` needs
none. `start_stack.sh` elevates only its `ifconfig` block and then launches the
emulator unprivileged, which contradicts the operating practice on both
machines — either the documented one-shot path starts an emulator without guest
networking, or the requirement is subtler than it looks. Unresolved.

A one-time `ChmodBPF`-style LaunchDaemon (`/dev/bpf*` → `root:access_bpf 0640`)
would remove the per-launch password. Untested here; the `access_bpf` group
already exists on the developer machine with the user in it, and nothing sets
the permissions, so it currently grants nothing.

## R9 — do not require a kernel extension

`AppleTalk_Start.sh` on both machines loads a tap kext (`tuntaposx` /
`net.tunnelblick.tap`) and asks for a password. It is unnecessary: the developer
machine has **no** tap kext loaded, no `/dev/tap*`, and AppleTalk works. The
kext belongs to a superseded tap+bridge configuration, whose remains are still
visible in `AppleTalk Start Beispiel.sh` — including the manual `bridge` creation
that the hard rules already forbid because it kills `etherhelpertool`.

**Requirement:** no kernel extension, no pre-created bridge. On current macOS an
unsigned legacy KEXT would make the product unusable rather than merely awkward.

## R10 — `0.0.0.0` widens the exposure

Binding all addresses is required by R7, and it publishes port 9000 to the whole
LAN. The control port is loopback-only, so command injection still needs local
access, but the daemon slot itself is reachable: any host on the segment can
occupy it or pose as a daemon. The bring-up demonstrated the mechanism from the
other side — a guest connected to a foreign host without either party noticing,
and a browser was accepted and served the v1 protocol fallback.

**Requirement:** where the backend is slirp, the installer offers the wire token
(`TOKEN=` plus `APPLEBRIDGE_TOKEN`), and the token pair is written in the order
that cannot lock the bridge out: guest first, host second.

## R11 — the ToolServer tier is optional, and must be reported as such

The guest-side preflight already treats ToolServer as a warning rather than a
failure. The host side must match: a machine with no MPW is a supported
configuration, not a broken one. What it loses is `mpw_execute`, `mac_compile`
and `mac_build`; what remains — screenshots, fork-aware file transfer, input
injection, `LISTDIR`, `DISKINFO`, clipboard, launch, shutdown — is the entire
native surface and needs no toolchain at all.

**Requirement:** the installer reports two tiers separately, and never treats an
absent ToolServer as a failed install.
