# Proposal — find the prefs file the emulator actually reads

**Status: proposal. Nothing here is implemented.**

The host tooling assumes the emulator's preferences live at
`$HOME/.basilisk_ii_prefs`. That assumption is correct by construction for a
default launch and wrong for one flag. This document states the evidence, the
failure it permits, the smallest mechanism that closes it, and what would
falsify the whole thing.

## The reference

`https://github.com/kanjitalk755/macemu`, branch `master` (read at
`0dff5584`, 2026-07-30). That fork — not `cebix/macemu` — is the current
reference for the macOS build.

**The trap that makes this worth writing down:** the macOS Xcode target
compiles `src/SDL/prefs_sdl.cpp` and `src/Unix/main_unix.cpp`. The
plausibly-named `src/MacOSX/prefs_macosx.cpp` and `src/MacOSX/main_macosx.mm`
are **not** in the target's `Sources` phase. A reader who opens the `MacOSX`
directory — the obvious move — describes a binary nobody runs. Verified by
grepping `BasiliskII.xcodeproj/project.pbxproj` for `in Sources`.

## What the shipping build does

```c
/* src/SDL/prefs_sdl.cpp:40 */
const char PREFS_FILE_NAME[] = ".basilisk_ii_prefs";

/* LoadPrefs */
if (!vmdir) vmdir = SDL_getenv("HOME");
if (!vmdir) vmdir = "./";
SDL_snprintf(prefs_path, sizeof(prefs_path), "%s/%s", vmdir, PREFS_FILE_NAME);
FILE *f = fopen(UserPrefsPath.empty() ? prefs_path : UserPrefsPath.c_str(), "r");
```

Three consequences.

1. **The prefs path never follows the bundle name.** `PREFS_FILE_NAME` is a
   compile-time constant joined to `$HOME`. Renaming `BasiliskII.app` to
   `Kanji-2020-01-22.app` — or to anything else — cannot move it. The hardcoded
   `PREFS_PATH` in `host/install_bridge.py` is therefore right by construction,
   not by luck, and no name-based discovery is needed for it.

2. **`--config FILE` overrides, in both directions.** `main_unix.cpp:499` sets
   `UserPrefsPath`; `prefs_sdl.cpp` consults it in `LoadPrefs` *and* in
   `SavePrefs`. This is the hole.

3. **`vmdir` is dead in this build.** `main_unix.cpp:456` declares it `NULL`
   and never assigns it. No command-line route reaches it. Nothing to handle.

Not applicable, recorded so it is not "fixed" later: the XDG ladder
(`$XDG_CONFIG_HOME/BasiliskII/prefs`) exists only in `src/Unix/prefs_unix.cpp`,
inside `#ifdef __linux__`, in a file the macOS target does not compile.

## The failure this permits

An emulator launched as `BasiliskII --config /some/other/prefs` reads and writes
that file. Our tooling reads `$HOME/.basilisk_ii_prefs` regardless. Then:

- `bridge_doctor` reports an `ether` backend from a file the emulator ignores —
  a confident answer about the wrong thing.
- `install_bridge.py` **writes `ether slirp` into that ignored file** and
  reports the step as done. The emulator's actual backend is untouched, the
  guest still cannot reach the host, and every diagnostic says the host is
  configured.
- `--seed-guest-prefs` takes its disk image from `disk` lines in the ignored
  file, so it can seed the wrong image, or refuse for lack of one that is
  plainly there.

This is the failure class already recorded on this project: *reports success,
does nothing.* It is worse than an error because the report is the evidence
somebody would use to rule the host out.

**Who is actually exposed.** `host/start_stack.sh:192` launches with
`open -a "$BASILISK_APP"` and passes no flags, so an operator who uses our own
launch path cannot hit this. The realistic trigger is a hand-written launcher —
a shell script or a wrapper `.app` that runs the executable directly with
arguments. That is a normal thing to have when several guests share one host.

## Proposed mechanism

Three parts, smallest first. Each stands alone; none blocks a default install.

### 1. Read `--config` off the running process

`probe_emulator_bundle()` already runs `pgrep -fl "BasiliskII|SheepShaver"` and
the argv is in that same line. Parse `--config` out of it and, when present,
make it the prefs path. A running process is authoritative: it names the file
that is genuinely open.

Two details that will bite otherwise:

- **Take the remainder of the line, not the next whitespace token.** Prefs paths
  with spaces are normal on this project (`System761 weiter.dmg` is the
  precedent). `probe_emulator_prefs()` already does exactly this for `disk`
  lines.
- **`ps` truncates.** If `pgrep -fl` proves to clip long command lines, re-read
  the argv with `ps -ww -o command= -p <pid>`. **Unverified** — no emulator was
  running when this was written. Verify before implementing:
  `pgrep -fl 'BasiliskII|SheepShaver'` against a launch carrying a long
  `--config` path, and compare with `ps -ww`.

### 2. An operator override

`--prefs PATH` on `install_bridge.py` and `bridge_doctor`, mirroring the
emulator's own flag and the existing `--emulator-app`. A flag rather than a
prompt, for the same reason as D-018: this must be runnable with nobody at the
keyboard.

### 3. Say where the answer came from

Add `prefs_source` to `probe_emulator_prefs()`'s return, one of
`"--prefs"`, `"running process --config"`, `"$HOME default"`, `"absent"`, and
print it wherever a backend is reported. A path stated alongside its provenance
cannot be silently the wrong path.

Where the emulator is **down** and `$HOME/.basilisk_ii_prefs` is **absent**,
emit a NOTE — not a refusal — along the lines of: *a new prefs file will be
created at `$HOME/.basilisk_ii_prefs`; if you launch the emulator with
`--config`, pass the same path with `--prefs`, because a stopped emulator does
not say which file it will open.*

Deliberately a note. A fresh machine that has never launched the emulator also
has no prefs file, and that is the installer's primary case — refusing there
would break the common path to guard the rare one.

## Test plan

All of it runs against canned `run`/`read` output through the existing
injection seams in `tests/test_installer.py`; none of it needs a live stack.

- `pgrep` output carrying `--config /path/with spaces/prefs` → that path is
  probed, and `prefs_source` says so.
- The same, with no `--config` → `$HOME` default, unchanged behaviour.
- `--prefs` beats a running process; a running process beats the default.
- Emulator down, no prefs file, bundle found → the plan contains the NOTE and
  still proceeds.
- **The regression that motivates this:** given a `--config` launch,
  `apply_plan()` must not write to `$HOME/.basilisk_ii_prefs`.

## Revisit if

- kanjitalk755 wires `vmdir` to a command-line argument, or moves the macOS
  target onto `prefs_unix.cpp` — then the XDG ladder becomes live for macOS and
  part 3's `"absent"` case needs a fourth location.
- `pgrep -fl` proves to clip the command line, and no reliable argv source
  exists for a translocated or sandboxed launch. Then part 1 is unsound and the
  override in part 2 carries the whole proposal.
- Measurement shows nobody launches with `--config`. The cost of parts 2 and 3
  is small enough that they would still be worth it for the provenance alone,
  but part 1 would not be.
