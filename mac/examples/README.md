# mac/examples — small 68K programs built over the bridge

Self-contained demo programs compiled, linked, and run on the emulated System 7.6.1
entirely through AppleBridge (MPW/ToolServer over the wire). They double as worked
references for the link-time gotchas the bridge work keeps surfacing — see the CMS article
*"Link-Time, Not Compile-Time: The 68K Code Model, Code Resources, and a Broken stdio."*

## `counter10i.a` — the same idea in 68K assembly

Counts `1 → COUNTMAX` (20 as shipped) in a window, one second per step, `SysBeep` at each
step, the current number drawn at 72pt bold. A mouse click quits. Also a real `APPL`, and
**built like a C application**: `MacRuntime.o` sets up the A5 world and calls `main`, so the
assembly never has to build one itself.

**Why it paces with a busy-loop, which looks wrong and is not.** Under this emulator every
tick-based wait — `_Delay`, a `WaitNextEvent` sleep, a `TickCount` deadline — elapses in
about *zero* wall-clock time, so none of them can pace anything. A CPU busy-loop does take
real host time. But a bare busy-loop starves the cooperative scheduler: the application
never reaches the foreground and the bridge goes with it. The shape that works is in the
source, in three phases: settle with pure `WaitNextEvent` until the window paints, then
count with the busy-loop *broken into chunks with yields between them*, then idle without
burning. That is the same yield rule the whole project runs on, in its most demanding form.

**Build + run (MPW, over the bridge):**

```mpw
Asm  counter10i.a -o counter10i.a.o
Link -o Counter10i counter10i.a.o "{Libraries}Interface.o" "{Libraries}MacRuntime.o" \
     -t APPL -c 'Cn10'
```

No `Rez` step — the window is built in code rather than from a resource. `Asm` warns "a
short branch could be used here" a few times; that is style, not an error.

**Number formatting is hand-written**, because the Toolbox routine of that name lives in a
library this example deliberately does not link. `DIVU #10` returns quotient *and* remainder
packed into one register, so the routine runs twice over the value: once to count digits,
once to fill them in backwards from the end of the buffer. The digit-counting pass must
continue with the **quotient** — an earlier revision carried the remainder forward instead,
which undercounts above 99 and then writes past the front of the buffer, clobbering the
length byte and the string in front of it. Corrected and re-verified on the guest at 998 →
1002; the buffer holds five digits.

## `count_ten.c` — a Toolbox application that counts 1..10

Opens a window and counts `1 → 10`, one second per step, with a `SysBeep` at each step,
then exits. A genuine standalone application (`APPL`), not a tool.

**Why no `printf`:** in this MPW install, `StdCLib.o` does not resolve the C stdio symbols
(`fclose`, `fflush`, `_iob`, …), so a `printf`/SIOW console app won't link — *near or far*.
The daemon only links because it carries its own `mystring.c` and never touches stdio. The
fix is to skip stdio entirely and draw with the Toolbox (`DrawString`), building the digit
string by hand so even number formatting needs no library.

**Build + run (MPW, over the bridge):**

```mpw
SC -model far count_ten.c -o count_ten.c.o
Link -model far count_ten.c.o "{Libraries}Interface.o" "{Libraries}MacRuntime.o" \
     -o CountTen -t APPL -c '????'
# then launch it (e.g. the launch_app MCP tool, or from the Finder)
```

`Interface.o` + `MacRuntime.o` is the same library line the daemon uses (minus
OpenTransport). `-model far` is incidental here — it matches the daemon's recipe; a small
application links happily near as well. The real requirement was avoiding the broken stdio
library, *not* the code model.
