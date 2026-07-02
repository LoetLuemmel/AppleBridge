# Bridge-Drivable Daemon Self-Update (SWAPSELF)

Status: **implemented; verified on System 7 (Basilisk II, 2026-07-02). OS 9
validation pending** — see *Bootstrap* below.

## The problem

Updating a *running* AppleBridge daemon used to need hands on the guest:

- **Basilisk (68K):** swap via ToolServer `Rename` on the open file — needs ToolServer.
- **SheepShaver (OS 9):** no ToolServer, and the OS **locks a running application's
  file**, so the 0.8d3 rollout required a manual **Shift-boot (Startup Items off)
  + Finder rename**. The installer can't help: it *copies*, and opening the running
  daemon's file for write fails with `fBsyErr`.

## The insight

A **rename** is not a copy. The File Manager renames a file by editing its catalog
entry, not by touching the open forks — so a running application can rename **its
own** file. That is exactly what ToolServer's `Rename` did on Basilisk; the daemon
can do the same with `FSpRename`, with no ToolServer and no manual step.

## Mechanism

New wire verb **`SWAPSELF`** (daemon, `SwapSelf()` in `mac/src/fileio.c`):

1. Resolve the daemon's own file spec (`GetCurrentProcess` → `processAppSpec`).
2. Require a sibling **`<name> new`** in the same folder (the host stages it there
   first via `mac_put_file` — a fresh file, so no lock).
3. Delete any stale `<name> old` backup.
4. `FSpRename` self → `<name> old` (rename the **open, running** file).
5. `FSpRename` `<name> new` → `<name>`.
6. On step-5 failure, roll back (`<name> old` → `<name>`) and report the error.

The running process keeps executing from its now-renamed file. The caller then
**reboots**; the watchdog launches the current `<name>` — the new binary. One
rollback copy (`<name> old`) is left behind.

`STAT` now also reports `home=<install folder>` so the host can locate where to stage.

## Usage — `mac_update_daemon`

```
mac_update_daemon(host_path="…/AppleBridge.bin")     # host_path: fork-aware MacBinary
# then:
mac_reboot()                                         # watchdog launches the new binary
# then verify the daemon's vers (DeRez 'vers', or grep the pulled binary)
```

`mac_update_daemon` stages `host_path` as `<home>/AppleBridge new` (auto-locating
`home` from `mac_status`, or pass `mac_dir`), then sends `SWAPSELF`. It does **not**
reboot — that stays an explicit step (Basilisk needs the AppleShare boot login fed
host-side). Failure reports the File Manager code: **`-43`** = the staged binary was
missing; any other code = the OS refused the rename.

## Verification (System 7)

Built the daemon at 0.8d4 (with `SWAPSELF`), ran it, staged a 0.8d5-stamped copy as
`AppleBridge new`, sent `SWAPSELF` → the daemon renamed its own running file to
`AppleBridge old` and the staged copy to `AppleBridge` (confirmed by directory
listing + `DeRez 'vers'`), then rebooted → the watchdog launched the swapped-in
**0.8d5** binary, which reconnected with ToolServer up. Host-edge tests in
`tests/test_self_update.py`.

## Bootstrap (the one manual step that remains)

Self-update only works once a `SWAPSELF`-capable daemon (0.8d4+) is **already
running** on the target. Getting that *first* 0.8d4 onto a machine still uses the
old method (ToolServer swap on Basilisk; Shift-boot + Finder rename on OS 9). From
then on, every further update is bridge-drivable. Validating this on OS 9 therefore
needs a one-time manual bootstrap of 0.8d4 on SheepShaver, then a `SWAPSELF` test —
the remaining open step. `FSpRename` of a running app is expected to behave the
same on OS 9 as System 7 (same File Manager call), but is not yet proven on-device.
