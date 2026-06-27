# MinQDC — QuickDraw in C (the easy path for graphical guest apps)

`MinQDC.c` is the C counterpart to [`../MinAsm`](../MinAsm): a real graphical
68k Mac application that opens a window and draws a spread of QuickDraw
primitives — framed/painted/pattern-filled rectangles, a rounded rect, ovals, a
fan of lines, and styled text — then runs a short event loop and exits cleanly.
It launches under Basilisk II without crashing the emulator.

It exists to show the **recommended** path: for anything graphical, write the
guest app in **C**. The MPW C compiler and runtime set up the A5 world, the entry
point, and the segment/jump-table for you — none of the four hand-fixes the
assembly path needed (see `../MinAsm`).

Verified 2026-06-27: compiled with `SC`, linked, launched, and rendered the window
live (screenshot captured over the bridge); BasiliskII stayed up across launches
with zero crash reports.

## The one C-specific gotcha

The headers only **declare** the QuickDraw globals (`extern QDGlobals qd;`), so a
C app that calls `InitGraf(&qd.thePort)` must **supply the storage itself**:

```c
QDGlobals qd;          /* one line, at file scope */
```

Without it the link fails with `### Link: Error: Undefined entry, name: "qd"`.
With it, `InitGraf` and all of QuickDraw link against just `Interface.o` +
`MacRuntime.o` — no extra library needed. (Apple's own sample apps, and this
project's `counter.c`, do exactly this.)

## Build it (over the AppleBridge bridge)

The source uses host (UTF-8 / LF) conventions; convert it to Mac (MacRoman / CR)
on the way in — see `host/encoding_convert.py`.

```bash
cd host

# 1. Host -> Share, then onto the Mac as MPW text
uv run python encoding_convert.py to-share ../examples/MinQDC/MinQDC.c
python3 send_command.py 'Duplicate -y Unix:MinQDC.c MeinMac:MPW:OurTest2:MinQDC.c'
python3 send_command.py "SetFile -t TEXT -c 'MPS ' MeinMac:MPW:OurTest2:MinQDC.c"

# 2. Compile
python3 send_command.py 'SC MeinMac:MPW:OurTest2:MinQDC.c -o MeinMac:MPW:OurTest2:MinQDC.c.o'

# 3. Link (C runtime: Interface.o + MacRuntime.o, far model)
python3 send_command.py 'Link -model far MeinMac:MPW:OurTest2:MinQDC.c.o "{Libraries}Interface.o" "{Libraries}MacRuntime.o" -o MeinMac:MPW:OurTest2:MinQDC -t APPL -c "????"'

# 4. Launch (stays up ~25s or until a click) and screenshot it
printf 'LAUNCH:MeinMac:MPW:OurTest2:MinQDC\n\n' | nc localhost 9001
```

Expected: a 400×440 window titled "QuickDraw POC" filled with shapes and text,
and BasiliskII still running. No crash report is written.

## C vs. assembly, in one line

`MinQDC` (graphical, C) built clean on the first try; `MinAsm` (minimal, asm)
needed four separate fixes to stop crashing. For guest apps: **C for anything with
a UI, assembly for MPW command-line tools.**
