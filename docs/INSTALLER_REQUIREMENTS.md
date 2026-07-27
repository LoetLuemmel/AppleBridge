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

**Deliberately out of scope:** the derivation runs once, at install time. A
two-interface host *becomes* a single-interface host the moment its adapter is
unplugged — `etherhelpertool` dies, the emulator keeps running without a guest
NIC, and the daemon loops on connect timeouts — so the right backend for that
machine changes underneath a correct installation. Reacting to it is a later
feature, not a requirement of the first installer; the hook is already there,
since whatever probe identifies `etherhelper` at install time can be re-run.
Until then this stays a diagnosis (`bridge_doctor` reports the dead helper),
not an automatic switch.

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

**Resolved 2026-07-27: the emulator does not need elevated rights.** Launched
with a plain unprivileged `open -a`, BasiliskII ran as the normal user, its
`etherhelpertool` child came up **as root**, opened a BPF device, and the guest
daemon connected — so `start_stack.sh` is right to launch it unprivileged and
the `sudo open -a` in the operator's own start script is ritual, like the kext
in R9.

The mechanism matters more than the verdict, because "it works" without one is
what made the kext survive for years. `etherhelpertool` is **not** setuid and
`/dev/bpf*` are `root:wheel 0600`, yet the helper runs as root: BasiliskII
elevates it itself, evidently through Authorization Services. No password was
requested during that launch, which means a stored authorisation already exists
on this machine.

**What the installer must therefore anticipate:** a *first* launch on a machine
with no stored authorisation will most likely prompt, and a headless or
scripted install cannot answer that prompt. That case is untested here — the
answer above is for a machine that has already been authorised once.

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

## R12 — the server's mode is chosen by `isatty()`, and the choice is silent

`host_server.py` branches on `sys.stdin.isatty()`: a TTY gets `interactive_mode`
(a `Command>` prompt), anything else gets `run_control_server`. The two are
mutually exclusive, so a server started the obvious way — in a terminal, to
watch it come up — listens on `:9000`, reports a healthy daemon, negotiates the
protocol, and has **no control port at all**. Every MCP tool and every script
then fails with `Connection refused` against a server that looks perfect.

The workaround is `./run_server.sh < /dev/null`: output stays on the terminal,
stdin is not a TTY, the control port comes up. Nothing says so.

**Requirement:** the banner names the mode and what it costs, or the two modes
stop excluding each other.

## R13 — diagnostics must not describe the developer's machine

The daemon-down message reads *"Host server agent de.390er.applebridge-host is
not loaded"*. That launchd job exists on one machine. Elsewhere the server is
started by hand — correctly — and the hint sends the reader after a job that was
never supposed to exist. A diagnostic that names a component the installation
does not have is worse than none: it is a false lead with an authoritative tone.

**Requirement:** every remedy a diagnostic proposes is one that applies to the
installation it is running in.

## What the tier actually delivers

Measured on the clean-room machine with `host/tools/q1_native_surface.py`
(11 checks, all passing), guest System 7.5.3 over slirp, no ToolServer:

| | |
|---|---|
| data fork, 4 KB / 64 KB / 512 KB | byte-exact, well past the daemon's 64 KB buffer |
| resource fork | full length, differences confined to the name stamp (D-013) |
| screenshot | valid PNG, ~15 s |
| `DISKINFO`, `LISTDIR` | real data, no toolchain involved |
| round-trip throughput | 84 → 179 → 291 KiB/s, rising with payload size |

Two of these deserve a second look.

**Throughput contradicts the expectation set by D-001.** The 2026-06-28 bench
measured ~0.2 MiB/s over slirp — but through `Catenate`/`DumpFile`, i.e.
ToolServer. The native `WRITEFILE`/`READFILE` path beats it at 512 KB on
slower hardware, so a good part of that figure was the detour, not the
transport. D-001's throughput argument stands for the path it measured; it does
not describe the path a ToolServer-less machine actually uses.

**The screenshot is the weak point of this tier**, and it is the one operation a
GUI-driving loop repeats constantly. The bridge carries the **raw** PixMap while
the resulting PNG is ~17 KB, which bounds how compressible the content is. The
compressor already exists in `host/tools/gif_to_rez.py`, applied in the other
direction. For this tier that is less an optimisation than a precondition.

## R14 — the daemon overwrites the prefs file it is reading

Editing `AppleBridge Prefs` over the bridge does not reliably stick: the daemon
holds its own copy in memory and writes it back, so an external edit can be
silently replaced by the values the daemon started with. Observed 2026-07-27
while demonstrating R2 — a `mac_write_file` reported 115 bytes written and a
read immediately afterwards returned the *previous* content; the file recovered
from the powered-off disk image later carried the daemon's own header and its
`WIN=` geometry, with only the field it had never held in memory changed.

This is the reason the demonstration had to blank the address twice, and it is
a trap for anyone who edits configuration over the bridge and reads success
back. A write is only durable once the daemon has re-read it — which for `IP=`
means at startup, since the periodic re-read adopts transport fields only.

**Requirement:** a configuration change made over the bridge is applied through
the daemon (so its in-memory copy is the one that gets written), or the daemon
re-reads before every save. Writing the file underneath it is not a supported
edit, and the tooling should not present it as one.

## R15 — the two launchers contradict each other about the bridge

The operator starts the emulator with `/Applications/BAII Netzwerk.app`, whose
entire privileged step is:

```
ifconfig bridge100 create
ifconfig bridge100 addm en8
ifconfig bridge100 up
```

followed by an **unprivileged** `open -a BasiliskII.app`. The bridge is created
*first*, deliberately, and that is the arrangement that works.

`start_stack.sh` does the opposite: it **destroys** `bridge100`, describing it as
"stale state from older (wrong) runs", on the strength of a hard rule that said
never to pre-create one. The rule has been corrected (the crash it came from
happens when the bridge is touched *while* the helper owns the NIC, not when it
is created beforehand), but the teardown is still in the script. The two
launchers therefore undo each other, and which one ran last decides the state.

**What is not known:** whether the bridge is *required* or merely harmless. Every
observation on 2026-07-27 — including a launch that was believed to be
bridge-less — had `bridge100` present, left over from an earlier run of the
operator's launcher. So the evidence cannot separate the two, and no claim
either way belongs in the documentation yet.

**The experiment that settles it:** destroy `bridge100`, launch, and check
AppleTalk specifically (the Chooser or `mac_appletalk_browse`) rather than the
bridge — TCP would keep working in either case, which is exactly how the slirp
backend fooled us in R6.

**Requirement:** the installer establishes one launch path and one bridge policy.
Two launchers with opposite beliefs about the same interface is a configuration
that repairs itself into whichever state ran most recently.
