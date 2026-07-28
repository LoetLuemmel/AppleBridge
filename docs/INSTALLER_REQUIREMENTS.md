# Installer requirements — what a fresh machine actually needs

> **Scope (D-018): the host-side installer configures the `slirp` branch only.**
> Where it finds an `etherhelpertool` in the emulator bundle it names that path
> as manual and stops. `etherhelper` stays supported and documented; it is set
> up by hand. The reason is not effort but capability: that branch needs two
> interactive password prompts per launch and a self-built fork emulator, so its
> output cannot start without somebody at the keyboard. The cost is stated
> rather than buried — **the slirp branch has no AppleTalk**.
>
> Requirements below that concern the `etherhelper` path (R6's second half, R15)
> therefore describe *diagnosis and documentation*, not configuration the
> installer performs.

What the host-side installer must do, derived by installing AppleBridge on a
machine that had never run it. Every requirement below is something that was
missing, wrong, or silently misleading during that bring-up (2026-07-27,
MacBook Pro 2013 / Big Sur / one Wi-Fi interface; guest System 7.5.3 with Open
Transport, no MPW, no ToolServer). None of them is speculative.

The one-line summary: **the installer's job is mostly derivation, not
installation.** Every value this project hardcoded is correct on exactly one
machine, and the failures they cause do not look like configuration errors.

## Where the requirements live in code

The installer is `host/install_bridge.py`, built on the `bridge_doctor.py` shape:
`run`/`read`/`write`/`exists` are injectable, so every branch — the refusals
above all — is driven from canned output by `tests/test_installer.py` with no
live stack. `decide()` returns a declarative plan and `apply_plan()` is the only
code that writes, so `--dry-run` is the same computation minus its last stage
rather than a second path that can drift from it.

| requirement | mechanism |
|---|---|
| R1, R2 | `host_config.resolve_host_ip()`; `render_local_env()` emits **no** address |
| R3 | `guest_checklist()` names `:System Folder:Preferences:AppleBridge Prefs` |
| R5 | the checklist labels every field by *whose* address it is |
| R6, R8 | `probe_emulator_bundle()` — the bundle is asked before the NIC count |
| R7 | the slirp triple, resolver included, and the `0.0.0.0` bind in `local.env` |
| R9 | no kernel extension is loaded, and no bridge created, on this branch |
| R10 | `exposure_report()` — the token pair, guest first, host second |
| R11 | `tier_report()` — ToolServer is a tier you may not have, not a failure |
| R12, R13 | `--no-agent` prints the `< /dev/null` form; the default installs the agent, so the doctor's launchd advice is true here |
| R14 | `seed_guest_prefs()` refuses while an emulator runs |
| R15 | one launch path per installation; `start_stack.sh` skips the privileged block entirely on slirp |
| R20 | `rewrite_ip_line()` works in bytes and preserves CR endings |

Whether any of this has *shipped* is the ledger's question, not this document's.

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
daemon connected — so `start_stack.sh` is right to launch it unprivileged, and
so is the operator's real launcher, `/Applications/BAII Netzwerk.app`, which
elevates only its bridge setup (R15) and then starts the emulator with a plain
`open -a`. The `sudo open -a` this requirement was written against belongs to
`AppleTalk_Start.sh` — a script that is **not** the one in use, and which also
carries the unnecessary kext of R9. Both launchers in actual use agree with the
measurement; only the abandoned script disagreed.

The mechanism matters more than the verdict, because "it works" without one is
what made the kext survive for years. `etherhelpertool` is **not** setuid and
`/dev/bpf*` are `root:wheel 0600`, yet the helper runs as root: BasiliskII
elevates it itself through Authorization Services, and it **prompts every
launch**. This requirement first recorded that no password was requested; that
was an artefact of measuring from a terminal while the operator typed it at the
screen.

**Two costs follow, and they matter more than the verdict.**

**A password per launch, twice.** One prompt for the bridge in the operator's
launcher, one for BasiliskII's helper elevation. Neither can be answered by a
script, so `start_stack.sh` was never truly one-shot on this path and an
unattended or headless start is impossible on it.

**A specific build.** `etherhelpertool` is not part of a stock BasiliskII. It
comes from the **kanjitalk755 macemu fork** (<https://github.com/kanjitalk755/macemu>),
where the backend originated and was extended; the running bundle here reports
*"Basilisk II 1.0, SDL2 port"* and carries `etherhelpertool` plus an
`etherhelpertool.arm64.bak` in `Contents/Resources`, while a second, different
BasiliskII binary in the same folder has no helper at all. The build in use here was **compiled by the operator**,
incorporating that backend — which raises the bar for anyone else from "install
an emulator" to "find a fork build, or compile one". A normal user's copy does
not have the helper, so for them the `etherhelper` branch does not exist at all,
whatever their interfaces look like.

The preflight must therefore probe the **app bundle** — is `etherhelpertool`
present in `Contents/Resources`? — and not just the host's NICs. An absent helper
decides the branch before the interface count is consulted, and it is the cheaper
check of the two.

**slirp needs none of it:** no bridge, no alias, no privileged step, no special
build. For the single-interface machine of R6/D-015 that is a stronger argument
than any throughput figure — that branch is not merely the only one that works
there, it is the only one that starts without a human at the keyboard, and the
only one a stock emulator can offer.

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

**Resolved: the bridge is required.** Confirmed by the operator and documented
on Emaculation — the `etherhelper` backend needs it, which is why their launcher
consists of nothing else. That turns this from a conflict of opinion into a
defect: `start_stack.sh` was **removing a required component**, and the only
reason it never showed is that the operator's launcher re-created it on the next
start. A stack that repairs itself on the next run hides the fault rather than
surviving it.

`start_stack.sh` now ensures the bridge instead of destroying it — created if
absent, member added if missing, brought up, inside the existing privileged block
and before the emulator launches — but **only when the configured backend is
`etherhelper`**. A bridge is a property of that backend, not of AppleBridge: a
slirp machine has none, needs none, and needs no privileged network step at all.
That sharpens the derivation of R6/D-015 rather than complicating it — the
single-interface path is not merely the only one that works there, it is also the
one that asks the user for nothing. The interface and bridge names come from
`host/local.env` (`APPLEBRIDGE_WIRED_IF`, `APPLEBRIDGE_BRIDGE`), since `en8` is
as machine-specific as the addresses in R1.

Worth keeping in view: today's evidence could not have settled this on its own.
Every observation had `bridge100` present — including a launch described at the
time as bridge-less, where it was simply left over. The answer came from the
operator and a source, not from the measurements.

**Requirement:** the installer establishes one launch path and one bridge policy.
Two launchers with opposite beliefs about the same interface is a configuration
that repairs itself into whichever state ran most recently.

## R16 — a host-reachable verb must not be able to kill the guest

`LAUNCH` builds a `LaunchParamBlockRec` with `launchNoFileFlags` — which tells
the Launch Manager explicitly *not* to check the file's flags — and then calls
`LaunchApplication` on whatever path it was given. Handing it a document is not
an error that comes back as a status: on 2026-07-27 a `LAUNCH` of a THINK C
project file (`'PROJ'`) took the **whole emulator down**, and the guest's disk
was left unflushed.

Having disabled the system's own check, the daemon owes one of its own. It now
reads the Finder info and refuses anything whose type is not `'APPL'`.

**Requirement:** every verb that reaches a Toolbox call from the network
validates its argument before making it. The blast radius of a wrong argument
here is the entire guest, not the command.

### The same requirement, a second verb: `AESEND` waiting for a reply

Later the same day, driving THINK C over Apple Events: a bare `KAHL/RUN` was
sent to the Project Manager. The project does not link (`undefined: atexit`), so
the application needed to interact, could not, and never replied. The daemon
was inside `AESend` with `kAEWaitReply` and `AE_SCRIPT_TIMEOUT` — **five
minutes** — and on a cooperative scheduler an application that is not yielding
starves everything behind it. The guest stopped switching windows entirely; the
host logged `command timeout after 240s`, and the emulator had to be force-quit
with the disk image open.

The timeout is not careless: ~5 min was chosen for `dosc`, where a
`kAEDefaultTimeout` of ~60 s returned a spurious `-1712` on long `Link`/`SC`
builds (`mac/include/applebridge.h`). The defect is that a value reasoned about
for **ToolServer**, an application we control and that always answers, was
inherited by a verb that can address **any** application.

Two guards follow, neither of which needs the argument validation R16 added.
**Both shipped in 0.8d31**, and the wire format grew one optional field to carry
them: `AESEND:<target>:<class>:<id>:<doLen>[:<waitTicks>]`.

* **Do not wait for a reply that the vocabulary says is empty.** `RUN`, `MAKE`
  and most of the THINK suite declare `reply: 'null'`; `command.c` already has a
  `kAENoReply` path for the other sender. Waiting is the caller's choice, so it
  belongs in the verb, not in a constant. `waitTicks = 0` takes that path, and
  `mac_send_apple_event(expect_reply=False)` is how a caller asks for it.
* **Bound the wait by what the caller can tolerate.** Five minutes is a build;
  an interactive event is seconds. An omitted field now means 30 s
  (`AE_SEND_DEFAULT_TIMEOUT`), not five minutes, and 180 s is the ceiling
  (`AE_SEND_MAX_TIMEOUT`) whatever the caller asks for.

  **Correction to this section as first written:** it said the *host's* timeout
  must be the shorter of the two. That is the wrong way round. A host that gives
  up first reports a timeout while the daemon is still inside `AESend` and the
  guest is still starving — a true statement about the wrong layer, and the
  guest is no better off. The **daemon** must be the side that gives up, so its
  ceiling sits below the host's read timeout and the host's read budget is
  derived from the bound it sent (`_ae_read_timeout`).

**Requirement:** a verb that blocks the daemon states how long it may block, and
the default is the interactive one. "It answered last time" is not a property of
an application the bridge does not own.

## R17 — an acknowledged write is not a durable write

`WRITEFILE` reported success and the `READFILE` immediately afterwards returned
the new content — and the change was gone after the crash, with the file's
original modification date intact. The write had reached the guest's disk cache;
the read that "confirmed" it read **the same cache**.

This is not a bridge defect, it is what a cache is. But it means a
read-back over the bridge proves the guest *agrees* about the content, not that
the content survives a power cut.

**Requirement:** where a write must survive, it is verified after a clean
shutdown, or the guest is asked to flush. Never treat a confirmed read-back as
proof of durability — it is the strongest available evidence that is still not
the claim being made.

## R18 — an open application outranks the file on disk

THINK C's Project Manager builds from its **editor buffer**, not from the file.
A source changed over the bridge while its window is open is ignored by the
build and — worse — is overwritten if the user then saves. The 2026-07-27
session spent three build attempts on this before the window was closed.

**Requirement:** before writing a file the guest may have open, close it in the
owning application, or expect the write to be invisible and possibly reverted.
Tooling that deploys sources should say so rather than let it be discovered.

## R19 — suppressing the interface suppresses the errors

`KAHL/NOUI` is exactly what a headless build wants, and it is also what hides
the compiler's error dialog. A malformed source failed to compile and the build
reported nothing at all; the failure only became visible after switching back to
`KAHL/UI`.

**Requirement:** a headless build is judged by its **artifact** — a changed code
size, a new timestamp, a running binary that shows the new string — never by the
absence of complaints. Suppressing the interface removes the channel the
complaint would have used.

## R20 — move classic sources as bytes, never as host text

A source read from the guest, held briefly in a host-side file and read back in
Python's text mode arrives with every `CR` silently rewritten to `LF`. The guest
then sees one enormous line: the editor shows box characters instead of breaks,
and the compiler fails — quietly, per R19. `#include` on the same line as the
rest of the program does not survive the preprocessor.

The project's encoding rule names character sets (MacRoman ↔ UTF-8) and line
endings, but says nothing about *intermediate handling on the host*, which is
where this happened.

**Requirement:** classic-Mac text moves as bytes end to end. Where a host-side
copy is unavoidable it is opened in binary mode, and line endings are converted
once, deliberately, at a named boundary.

### Corollary: do not assume which ending a guest file has

`AppleBridge Prefs`, recovered from a powered-off image on 2026-07-27, contains
**seven `LF` bytes and no `CR` at all** — although `prefs.c` writes `"\r"` after
every key. Both facts are true because MPW C swaps the escapes: `'\n'` is `0x0D`
and `'\r'` is `0x0A`, so the source's `"\r"` emits an `LF`. The daemon reads back
what it wrote, so nothing was ever wrong; but a tool that "helpfully" converted
this file to `CR` on the way past would corrupt the configuration the whole
bridge depends on.

So the rule is not "convert to CR" — it is **preserve what is there**.
`install_bridge.rewrite_ip_line()` splits on `\r\n | \r | \n` and re-emits the
separator it found, which is why seeding this file worked byte-for-byte: 128 B
in, 128 B out, one line changed, endings untouched.
