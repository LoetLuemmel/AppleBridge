# mac/examples — small 68K programs built over the bridge

Self-contained demo programs compiled, linked, and run on the emulated System 7.6.1
entirely through AppleBridge (MPW/ToolServer over the wire). They double as worked
references for the link-time gotchas the bridge work keeps surfacing — see the CMS article
*"Link-Time, Not Compile-Time: The 68K Code Model, Code Resources, and a Broken stdio."*

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
