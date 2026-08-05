# Überholte Betriebsnotizen

Befunde, die eine spätere Messung widerlegt hat. Sie stehen hier, weil ein
falscher Glaube, der spurlos verschwindet, wiederkommt — jemand leitet ihn neu
her und zahlt dieselbe Zeit noch einmal. Sie stehen *hier* und nicht mehr in
`../OPERATING_NOTES.md`, weil ein überholter Befund beim Suchen nicht mehr
mitkommen soll: wer mitten in einem Fehler `grep` benutzt, braucht die geltende
Regel, nicht die drei, die es einmal waren.

Jeder Abschnitt sagt selbst, was ihn ersetzt hat. Verschoben am 2026-08-05, im
ersten Subtraktionsdurchgang seit Anlegen der Datei.

## Superseded: "a capture without Screen Recording consent is wallpaper" — 2026-08-05

Chased for an evening as a session-context problem, and it is simpler than
that. Since macOS 10.15, `screencapture` run by a process that has **not** been
granted Screen Recording returns the desktop picture and the menu bar, **with
every window missing** — exit 0, correct dimensions, a perfectly valid PNG.
Nothing in the output says a permission is missing.

Measured on this host, unlocked, within one minute:

| process | Screen Recording | result |
|---|---|---|
| the terminal a session runs in | granted | the emulator window, as expected |
| the launchd host server (`/usr/bin/python3`) | not granted | wallpaper only |
| an ssh session on the same machine | not granted | wallpaper only |

The tell that separates it from "there is no window": the returned image had
**exactly the right dimensions** for the requested rect — the capture found the
area and photographed it, it simply was not allowed to see what was on top.
A missing window would not produce a correctly sized picture of the desk.

The reason it stays hidden is that macOS **prompts** on first use, and a
background agent or an ssh session cannot show a prompt. It is handed the
degraded image instead, silently. So the failure is invisible from both ends:
the caller sees success, and the person who could grant the permission is never
asked.

**The fix is one grant per binary**, in System Settings → Privacy & Security →
Screen Recording. It is not a code change and no amount of retrying, escalating
or rewriting the capture path will substitute for it.

An earlier version of this file blamed the WindowServer session. That is a real
phenomenon and may also apply, but it was not what was measured here, and a
locked screen confounded the first attempt at measuring it — see the correction
above. Three explanations fitted one observation; the one that survived is the
one with a control.

## Superseded: "the keystroke cost is one post that always fails" — 2026-08-05

> **Correction to the note below, same morning.** It attributed the 1.69 s to a
> guest event queue with no room. **A reboot refuted that.** On a freshly booted
> guest — `uptime=0`, three processes, nothing typed yet — a single keystroke
> still costs **1.731 s**, and nine characters still hit the 15 s timeout. An
> empty queue cannot be full, so the cost is not waiting for a slot.

What the measurement actually separates, once a comparison exists:

| verb | cost | arithmetic |
|---|---|---|
| `CLICK` | **0.102 s** | `ShortDelay(4)` = 67 ms + round trip. Matches. |
| `KEY` (no modifiers) | 1.694 s | 67 ms deliberate + **1.58 s unexplained** |
| `KEY` (with modifiers) | 1.695 s | identical — so not the modifier handling |
| `TYPE:a` | 1.731 s | one keystroke, same cost |

A click posts events through the same Event Manager and is **sixteen times
faster**, so this is not a posting problem in general and not modifiers. And
1.58 s is almost exactly **one** exhausted retry budget: `PPostEventRetry` gives
up after 48 × `ShortDelay(2)` = 96 ticks = 1.60 s. Each keystroke posts twice
(keyDown, keyUp); one of the two burns its entire budget, every time, on an idle
freshly booted machine.

**And the daemon throws the answer away.** `main.c`:

```c
InjectType(request + base, n);
strcpy(responseBuffer, "STATUS:0
STDOUT:5
Typed
STDERR:0

");
```

`InjectType` returns the error from the exhausted retry and nothing reads it.
The verb answers `Typed` unconditionally. That is the third instance of one
shape in two days — after `QUIT` answering *Quit OK* for an event that was
merely delivered, and `append()` returning a False nobody looked at: **a return
value that can be ignored by omission, and was.**

So the open question moved rather than closed. Not *"why is the queue full"* —
it is not — but *"why does one of the two posts fail 48 times running on an idle
system"*. What is measured and can be built on: the cost is constant, is per
keystroke, is independent of guest uptime, does not affect clicks, and is
reported as success.

*Method note, since this is the second time in one morning: the arithmetic that
"matched to within half a percent" matched a wrong cause. 101 ticks is also what
you get from a retry loop exhausting for any other reason. An arithmetic
agreement is evidence about a MAGNITUDE, not about a mechanism — the reboot,
which cost one minute, is what actually decided it.*

## Superseded: "every synthetic keystroke costs 1.7 s on this guest" — 2026-08-05

"Nine characters take fifteen seconds" was an exact observation with the wrong
subject. Measured against the live bridge, a clean straight line:

| characters | 1 | 2 | 3 | 4 | 6 | 8 | 9 |
|---|---|---|---|---|---|---|---|
| seconds | 1.738 | 3.394 | 5.094 | 6.757 | 10.105 | 13.494 | **15.003** |

**~1.69 s per keystroke.** There is nothing special about nine — it is simply
the first count whose burst exceeds the host's 15 s timeout, after which the
reply is discarded as stale and the *next* command times out too. That
secondary desync is what makes it look like the bridge has fallen over.

The arithmetic in `mac/src/events.c` accounts for it to within half a percent:

    InjectKeyMod  ShortDelay(2) + ShortDelay(2)      =   4 ticks
    InjectType    ShortDelay(1) per character        =   1 tick
    PPostEventRetry   48 attempts x ShortDelay(2)    =  96 ticks
                                                       ---------
                                                        101 ticks = 1.683 s

So `PPostEvent` fails on nearly every attempt and each keystroke spends almost
its entire retry budget waiting for a slot: **the guest's OS event queue has no
room.** Controlled — after 30 s of complete quiet a single character still costs
1.715 s, so the jam is the standing state and not an artefact of the burst that
found it. `KEY:` costs the same 1.735 s, so it is per-KEYSTROKE, not per-verb.

Two consequences worth acting on, and one open question:

- **`mac_type` chunks at 12 characters, which cannot fit a 15 s timeout** at
  this cost (12 × 1.68 s = 20 s). The chunk size was chosen for losslessness and
  the timeout for round-trip sanity; nobody related them, so the default path is
  guaranteed to time out whenever the queue is jammed. Three constants in three
  languages — `CHUNK` in `mcp/tools.py`, `DEFAULT_TIMEOUT` in `host_server.py`,
  the retry budget in `events.c` — and nothing checked them against each other
  until `tests/test_typing_budget.py`.
- **The daemon answers `STATUS:0`** for a keystroke that needed 47 attempts, and
  `err=` does not move. A cost of this size that reports success is this file's
  recurring subject; a counter on retry exhaustion would make it visible.
- **Why the queue is permanently full is NOT established.** It needs a guest
  with a fresh event queue — a reboot — to separate "this guest, after 7 hours"
  from "always". Measure that before believing either.
