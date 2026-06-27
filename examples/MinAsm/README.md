# MinAsm — a minimal, verified-working 68k assembly GUI app

`MinAsm.a` is the smallest standalone Macintosh application that proves the
AppleBridge build pipeline can produce a 68k **assembly** app that launches on
System 7.6.1 under Basilisk II **without crashing the emulator**.

It does almost nothing on purpose: set up the QuickDraw globals, `SysBeep`, and
exit cleanly through the MPW runtime. The point is not the program — it is the
**build recipe**.

## Why this exists

The long-standing "GUI apps crash BasiliskII" problem was misdiagnosed for a while
as a macOS Sequoia / SDL2 *window-management* bug. It is not. It is a **broken guest
binary**: a malformed assembly app executes garbage or an uninitialised global, the
68k CPU faults (`SIGSEGV`), Basilisk quits, and SDL2's at-exit window teardown trips a
*secondary* `SIGILL` in `NSWMWindowCoordinator` — which is the only frame the crash
report headlines. See `TROUBLESHOOTING.md` ("Emulator (Basilisk II) Crashes") for the
full analysis.

`MinAsm` was built to nail down the fix. Verified 2026-06-27: it launched **4× with
zero crashes**; stock `SimpleText` is likewise stable, while the original malformed
`CounterAsm` crashed on every launch.

## The four rules that make it work

Each was a real defect in the original `CounterAsm`. Get all four right and an
assembly GUI app runs:

1. **Mac (CR) line endings.** LF-terminated source makes the MPW assembler read the
   file as one line and emit an *empty* object (only First/Last records in `DumpObj`,
   plus a misleading "END supplied by Assembler" warning). Convert before building.
2. **Link against the runtime.** Add `MacRuntime.o` + `Interface.o` so `%_MAIN` /
   `_DataInit` / `%A5Init` set up the A5 world (globals, jump table, QuickDraw globals)
   and then call your `main`. A bare-linked asm app has no A5 world and faults on the
   first global/Toolbox access. `DumpFile -h` on a good build shows `A5Init` /
   `DataInit` / `RTInit` (~4 KB fork), not ~700 B.
3. **`CASE OBJECT`.** The C runtime calls lowercase `main`; the assembler folds
   symbols to uppercase `MAIN` by default, so the entry never resolves. `CASE OBJECT`
   keeps the exported name case-exact.
4. **`InitGraf` wants `&thePort`.** `thePort` is the *last* field of the QDGlobals
   struct (offset `QDSize-4`); the other globals grow downward from it. Passing the
   buffer base puts them below SP, where later pushes clobber them. Use
   `PEA QDSize-4(SP)`.

> A successful **link** is necessary but **not** sufficient. Verify by launching and
> watching BasiliskII survive (and the crash-report count not increase), not by exit
> status.

## Build it (over the AppleBridge bridge)

The source here uses host (UTF-8 / LF) conventions. Convert it to Mac
(MacRoman / CR) on the way in — see `host/encoding_convert.py`.

```bash
# 1. Host -> Share folder (UTF-8/LF -> MacRoman/CR)
cd host
uv run python encoding_convert.py to-share ../examples/MinAsm/MinAsm.a

# 2. Copy onto the Mac and mark it as MPW text
python3 send_command.py 'Duplicate -y Unix:MinAsm.a MeinMac:MPW:OurTest2:MinAsm.a'
python3 send_command.py "SetFile -t TEXT -c 'MPS ' MeinMac:MPW:OurTest2:MinAsm.a"

# 3. Assemble
python3 send_command.py 'Asm MeinMac:MPW:OurTest2:MinAsm.a -o MeinMac:MPW:OurTest2:MinAsm.o'

# 4. Link against the runtime (rule 2)
python3 send_command.py 'Link MeinMac:MPW:OurTest2:MinAsm.o "{Libraries}MacRuntime.o" "{Libraries}Interface.o" -o MeinMac:MPW:OurTest2:MinAsm -t APPL -c "????"'

# 5. Launch — and watch the emulator survive
printf 'LAUNCH:MeinMac:MPW:OurTest2:MinAsm\n\n' | nc localhost 9001
```

Expected: a short beep, a clean exit, and BasiliskII still running. No crash report
is written.

## What this is not

`MinAsm` deliberately avoids windows, dialogs, and menus. The original `CounterAsm`
dialog app needs all four fixes above *plus* correct Toolbox usage (NewDialog / window
setup) — those are left as an exercise. For real GUI work, building in **C** is the
path of least resistance (the C runtime handles rules 2–4 for you); assembly shines
for MPW command-line tools. This example exists to prove the assembly path is *possible*
and to document exactly what it takes.
