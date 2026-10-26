# Many guests: one hub, one address, a port pair per guest

Design for running **n BasiliskII guests** (target: 10 and more) against one AppleBridge hub. It replaces the
two-guest arrangement of 2026-10-01 (a second host-server instance on a second address of the dev Mac), which
D-027 retired together with every alias. Status of the work lives on the ledger, not here.

## Rules this design starts from

- **One emulator per machine** (operator, 2026-10-26). A guest is identified with the Mac it runs on; two
  emulators on one machine are out of scope and stay unmeasured. This keeps every per-machine mechanism
  (real mouse over ssh, framebuffer export, hang/dialog watchdogs, the boot shepherd) one-to-one.
- **The host address is a real, router-reserved address — never an alias** (D-027). On the hub that is Wi-Fi's
  own `.232`; there is exactly one of it, so guests cannot be told apart by address.
- **A guest cannot reach a server on its own machine over `etherhelper`** (D-015, hairpin), and the guests
  need AppleTalk (AppleShare volumes), so they keep `etherhelper` on their own machine's NIC and dial the hub.
  Every host server therefore runs on the hub.

## Topology

```
 guest machine k (one BasiliskII, etherhelper on its own NIC)
   daemon  ──TCP──▶  hub .232 : <daemon port of k>      host_server instance k
                                                          control  127.0.0.1 : <control port of k>
 Claude / MCP / wish runs  ──▶  control port of k  (one per guest, as 9001 and 9011 are today)
```

One `host_server.py` **process per guest**, each with its own daemon port and its own control port, all bound
to the hub's one address. The process code does not change shape: it already serves exactly one daemon, and
the second-instance mechanism (`APPLEBRIDGE_CTRL_PORT`, `APPLEBRIDGE_LOG`, `APPLEBRIDGE_REMOTE_GUEST`) already
exists. What is new is that the **daemon port** becomes configurable on both ends.

### Why a process per guest, not one server for all

| | process per guest (chosen) | one multiplexing server |
|---|---|---|
| host code | unchanged in shape; ports from the environment | ~70 single-link sites in 2 736 lines rebuilt (heartbeat, reconnect, streaming, locks) |
| fault isolation | a wedged link or a crash takes down one guest | one fault reaches every guest |
| guest identity | the port | a name in HELLO, required, since slirp guests share one source address |
| cost | n processes and n launchd agents (each a few MB) | one process |

At 10–20 guests the process count is not a burden; the rebuild and the shared fate are.

## The guest table

One untracked, machine-specific file on the hub, `host/guests.conf` (shipped as `host/guests.conf.example`;
R1: no addresses in tracked runtime files). One line per guest:

```
# name      daemon  control  ssh                           emulator (on that machine)
dev2019     9000    9001     -                             /Users/…/Basilisk/BasiliskII-ab.app
mbp2013     9010    9011     pitforster@192.168.3.158      /Users/…/BasiliskII/BasiliskII-ab.app
claudia     9020    9021     claudiaforster@192.168.3.171  /Users/…/BasiliskII/BasiliskII-ab.app
```

- **Port pairs by convention, explicit in the table:** guest k gets `9000+10k` (daemon) and `9001+10k`
  (control). This keeps today's `9000/9001` for the first guest and `9011` for the 2013's control port. Ten
  guests use 9000–9091. The table, not the rule, is authoritative; the installer refuses duplicates.
- `ssh = -` marks the guest on the hub itself. Every other row is a guest on another Mac; its instance runs
  with `APPLEBRIDGE_REMOTE_GUEST=1`, which refuses the `HOST*` verbs (they act on the hub's own screen).
- `name` is the guest's identity everywhere: launchd labels (`de.390er.applebridge-host-<name>`,
  `…-hangwatch-<name>`, `…-dialogwatch-<name>`), log files (`/tmp/applebridge_server_<name>.log`), the wish
  runs' guest list, and the identity check below.

The table **replaces** the `APPLEBRIDGE_REMOTE_*` and `APPLEBRIDGE_LOCAL_GUEST_*` keys in `local.env` and
`install_remote_guest_service.sh`; `local.env` keeps what is about the hub (`APPLEBRIDGE_HOST_IP`, the AFP
password, alert targets).

## Daemon changes

1. **`TCPPORT=` in AppleBridge Prefs** — the port the daemon dials (`PORT=` is taken: it selects the serial
   port). Absent ⇒ `9000`, so every existing guest keeps working unchanged. `BRIDGE_PORT` becomes the default,
   not the only value; `ABConnect` and the status line take the configured port.
2. **`NAME=` in AppleBridge Prefs, reported in the HELLO reply** — the guest's name from the table.
3. **`SavePrefs` keeps lines it does not understand.** Today it rebuilds the file from its struct, and four
   programs compile `prefs.c` (daemon, AppleBridgeConfig, watchdog `ABwd`, installer). Any of them built
   before this change would silently drop `TCPPORT=` on its next save, and that guest would fall back to 9000
   — onto the first guest's server — without an error. Carrying unknown lines through makes every future key
   survive older binaries; the four programs are rebuilt together once, and from then on the problem is gone.
4. A changed `TCPPORT=` takes effect at the next connect (the same re-read that adopts `NET=`); a running link
   is not torn down for it.

The port and name are written into a powered-off image with the existing seeding path
(`install_bridge.py --seed-guest-prefs`, generalised from `IP=` to the three keys), or set in AppleBridgeConfig.

## Identity check (why the name is needed although the port identifies the guest)

A guest with a wrong port — a stale prefs file, an old binary that dropped `TCPPORT=`, a cloned image — reaches
**another guest's** server and is served as that guest with full health. The same trap was recorded for
addresses on 2026-07-29: with two hosts listening on :9000 on one LAN, a guest with the wrong `IP=` connects to the
other machine and reports full health — the "reports success, does nothing" class. So each instance knows the name its row expects (`APPLEBRIDGE_GUEST_NAME`), and:

- the daemon's HELLO reply carries `NAME=`; a mismatch **refuses the link** and logs both names;
- a daemon that reports no name (an old build) is accepted with a warning, so the migration is gradual;
- `bridge_doctor` reports the expected and the observed name per guest.

## Host changes

- `host_server.py`: `HOST_PORT` from `APPLEBRIDGE_DAEMON_PORT` (default 9000); expected name from
  `APPLEBRIDGE_GUEST_NAME`; the HELLO parse reads `NAME=`.
- **`host/install_guests.sh`** reads the table and writes, per row, the host-server, hang-watch and
  dialog-watch agents with that row's environment; rows removed from the table have their agents booted out.
  It replaces `install_host_service.sh`'s single agent, `install_remote_guest_service.sh`, and the per-port
  logic in `install_hang_watch.sh` / `install_dialog_watch.sh`.
- `deploy_host.sh` restarts every instance in the table (today: the first, plus the remote one if present).
- `start_stack.sh` checks every instance listens on its daemon port; it still launches only the hub's own
  emulator (one per machine — the other machines start their own).
- `bridge_doctor`: one section per guest (listener, peer, expected/observed name); `DAEMON_PORT` and
  `CONTROL_PORT` from the row.
- Consumers that hold a guest list read the table: `xavier-eval/wunsch_build.py` (`GUESTS`, today a fixed
  `local`/`remote` pair), the boot shepherd and the watchdogs (per row).

## MCP

Today one MCP server serves one control port; the 2013 needed a second, locally registered server. At ten guests
that becomes ten servers. The tools gain an optional **`guest`** parameter resolved through the table (default:
the first row, so every existing call is unchanged); the `REMOTE_GUEST` refusals apply per call instead of per
server. This is a phase of its own — the bridge does not depend on it.

## Phases, each with its acceptance test

1. **Port and name in the daemon; port in the host.** `TCPPORT=`/`NAME=`, `SavePrefs` round-trip, HELLO
   `NAME=`; `APPLEBRIDGE_DAEMON_PORT`/`APPLEBRIDGE_GUEST_NAME` in the server. *Accept:* the hub's own guest,
   seeded with `TCPPORT=9010`, connects to an instance on `.232:9010`; with a wrong `NAME=` the link is
   refused and both names are logged; an untouched guest without the keys still connects on 9000; saving
   prefs in the rebuilt AppleBridgeConfig keeps a foreign test key.
2. **The table and the generated agents.** `guests.conf`, `install_guests.sh`, migration of `local.env`.
   *Accept:* two rows (hub + 2013) come up from the table alone after a reboot of the hub, both answer `Echo`
   on their control ports, removing a row removes its agents.
3. **Consumers.** doctor per guest, `start_stack.sh`, `deploy_host.sh`, the wish runs' `GUESTS`.
   *Accept:* a wish run on the second guest addressed by name; the doctor names a guest that dials the wrong port.
4. **MCP `guest` parameter.** *Accept:* one MCP server drives two guests; a `HOST*` tool on a remote row is refused.

## Proposed decision (D-028, to be entered in DECISIONS.md once accepted)

- **Decision:** n guests, one emulator per machine, all dialling the hub's one router-reserved address; one
  `host_server` process per guest on its own daemon/control port pair from a guest table; the daemon reads its
  port and name from its prefs and the hub refuses a guest that reports the wrong name.
- **Why not the alternatives:** an address per guest needs aliases (D-027) or one real address per guest; a
  multiplexing server rebuilds the hardened single-link code and shares one fate across all guests; a host
  server on each guest machine is impossible over `etherhelper` on a single-NIC machine (D-015), and slirp
  would cost those guests AppleTalk.
- **Revisit if:** the guest count makes per-process cost visible (memory, launchd), two emulators per machine
  become a requirement, or a guest machine with two NICs makes a local host server worth having.
