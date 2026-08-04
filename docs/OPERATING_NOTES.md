# Operating Notes — what to verify before believing the bridge

**This file is the owner of the operating notes (D-020).** What an agent must
*check* before trusting a result, as opposed to what it must *know* to work —
that is `CLAUDE.md`. Decisions are `DECISIONS.md`; status of work is the ledger.

Read it when a command reports success and nothing happened.

It is **appended to, not rewritten**: an entry that is overtaken gets a dated
correction underneath it, never a silent edit, because the wrong belief is
usually more instructive than the right one. Add what cost you a session.

Entries up to 2026-08-03 were written on the WorkMode page at
`pit.390er.de/applebridge/workmode/agent-operating-notes-verification-and-traps/`
and moved here verbatim; that page keeps them as a dated snapshot and is no
longer appended to.

---

The other WorkMode entries describe how the collaboration is *organised* — the
artefact as evidence, the process organs kept without a tracker, the
collaborator that starts every session without memory. This one is the
operational counterpart: what an agent must actually check before believing the
bridge.

Several sessions drive this bridge, each with its own context and none able to
see what the others learned. The same traps therefore get rediscovered, and the
expensive ones are not the errors — they are the operations that **report
success and do nothing**. Each entry below states a claim, the evidence for it,
and the practice that follows. This page is meant to be appended to, not
rewritten.

## Two line endings inside a single response

The framing of a control-port reply and the payload it carries **do not use the
same line terminator**. A `LISTDIR` response, dumped byte for byte on
2026-08-02:

```
00000000: 5354 4154 5553 3a30 0d53 5444 4f55 543a  STATUS:0.STDOUT:
00000010: 3333 350d 466f 7274 4170 6f63 0941 5050  335.FortApoc.APP
00000030: 3432 3231 3732 380a 466f 7274 4170 6f63  4221728.FortApoc
```

The protocol fields (`STATUS:`, `STDOUT:`) are separated by **CR (0x0D)**. The
listing rows are separated by **LF (0x0A)**. Fields within a row are tabs.

The cause is a property of the compiler, not a defect in the daemon: **classic-
Mac C maps `'\n'` to CR (0x0D) and `'\r'` to LF (0x0A)** — the opposite of every
host-side convention. The daemon's source reads `line[p++] = '\r';` and the
comment directly above it documents the format as
`name<TAB>type<TAB>creator<TAB>dataSize<TAB>modSecs<CR>`. Source, comment and
wire therefore contradict one another, and only the wire is authoritative.

**Practice:** read the payload **by its declared length**, never up to a
terminator, and split rows on *either* CR or LF. A parser that assumes one of
the two works against half the verbs and fails against the other half — and it
fails as an *empty directory* rather than as a parse error, which is why the
same trap can be rediscovered three times within one session.

## Silent success is the default failure mode

A verb with no host-side route is not rejected. It falls through to ToolServer,
which does not recognise it either, and the caller receives `STATUS:0` with an
empty body — indistinguishable from a command that worked. Two probes,
2026-08-02:

```
printf 'HELP\n\n' | nc localhost 9001   ->  MPW Help Summaries -- Copyright Apple Computer 1986-2002
printf 'CAPS\n\n' | nc localhost 9001   ->  STATUS:0 STDOUT:0 STDERR:0
```

`HELP` returns **MPW's** help text, because MPW happens to have a command by
that name. `CAPS` returns success and nothing at all. There is no verb registry
to consult and no way for a client to ask whether a verb exists: the 36 verbs
live as an `elif` chain in the host server and as string comparisons in the
daemon.

**Practice:** never treat `STATUS:0` as evidence. Confirm by the artefact the
command was supposed to produce. When a new verb "works but does nothing",
inspect the host dispatch first and read the daemon's verbose log, where
`initAE / found=TS / send=0` identifies the fall-through.

## An empty reply from a silent tool is success, not absence

The previous entry says an empty reply is suspicious. This one is its
counterpart, and confusing the two costs a session in the opposite direction:
**most MPW tools print nothing when they succeed.** `Asm`, `SC`, `Link`, `Rez`
and `SetFile` are all silent on success, so ToolServer's reply carries no output
descriptor and the daemon logs:

```
> SC … / Link … / Rez … / SetFile …
initAE=0
found=TS psn=8195     <- ToolServer WAS found
send=0                <- the Apple Event went out cleanly
getDesc=-1701         <- reply carries no result descriptor
len=0
```

`-1701` (`errAEDescNotFound`) here means "the tool printed nothing", not "no
ToolServer". A session reading that trace concluded ToolServer had died, and
began diagnosing a process that had been running the whole time.

**The distinguishing signal is `found=`.** An absent ToolServer logs
`found=NONE`, and when neither it nor MPW Shell is up the daemon says
`initAE=0 found=NONE 0 no-ToolServer/MPW`. `found=TS` means the command was
delivered.

**The counter-proof takes one command.** Through the same process number that
produced every `-1701` above:

```
Echo TS-MARKER-9   ->  found=TS psn=8195, getDesc=0, rsize=12, "TS-MARKER-9"
```

Same ToolServer, same code path, a tool that prints. Prefer a marked `Echo` as
the liveness question over a single status field — a status probe can race with
a process that is still starting, an `Echo` cannot.

**The consequence for verification is the uncomfortable part.** "Compiled, no
diagnostics" is *not* evidence of a clean compile: silence looks identical
whether the compiler ran perfectly or never ran at all. Two things recover the
evidence, and both are needed.

First, capture the diagnostics explicitly with the `≥` operator, which survives
the bridge intact — verified 2026-08-02:

```
SC :src:nosuch.c -o :obj:nosuch.o ≥ :probe.err   ->  reply: "(no output)", as always
Catenate :probe.err                              ->  Command line error: unable to
                                                     open input file ':src:nosuch.c'
```

Never `2>&1`; it crashes the shell.

Second, check the artefact, as the next entry describes. Silence plus a correct
creator code and a plausible size is evidence. Silence alone is not.

## Verify a build by the artefact, never by the status

Three separate signals lie here, and each has cost a session:

- **A long command may time out and still complete.** `Link` can return `-1712`
  (Apple Event timeout) with the executable correctly written. The status says
  nothing; the file does.
- **A data fork of zero bytes is normal for a 68K application.** The code lives
  in `CODE` resources in the resource fork. Treating an empty data fork as a
  failed link is a misdiagnosis that survived months.
- **The creator code is the honest tell.** A link that did not complete leaves
  `????` where the project's creator should be. Checking type, creator and a
  plausible size distinguishes a real build from a truncated one in a single
  listing.

**Practice:** after any link, list the output and compare creator, type and size
against a known-good build of the same program. For a resource-level check,
`DeRez -only 'CODE'` on a working binary gives the baseline.

## A recipe you intend to mirror must first exist as a recipe

"I will stop guessing and mirror the proven recipe" is the right instinct, and
it fails when the proven thing was never a recipe. A session building a new
`'DPAT'` code resource went looking for how the working `'MSPT'` menu patch is
linked. There is no such command anywhere in the tree, because the shipping INIT
does not link it — it carries the bytes:

```
* ---- embedded 'MSPT' 128 patch, byte-exact from mspatch.a (DeRez) ----
PatchData
        DC.W    $601A,$4D53,$0000,$0001,…
```

The patch was built once, read back with `DeRez`, and pasted into the INIT's
assembly as thirty-seven `DC.W` literals. Searching the repository for
`Link -rt MSPT=` returns nothing.

**A correction, left visible because the mistake is the better lesson.** This
entry first said "and no amount of further searching will change that". That
was wrong. The recipe did exist — `Asm mspatch.a` → `Link -rt MSPT=128 -o
ABMenuPatch`, with the block layout beside it — recorded in an agent's project
memory, a channel outside the repository that a `grep` over the tree does not
reach. So the rule is not "establish that it does not exist" but the sharper
one: **before concluding something was never written down, establish which
channels your search cannot see.** Here they were the memory directory and this
article corpus; neither is in the tree.

**Practice:** when a recipe cannot be found, establish whether it exists before
concluding you are looking in the wrong place. `grep` for the *product* — the
resource type, the output name — not only for the command you expect.

The same search does, however, turn up the real analogues. Every code resource
in this tree that contains **C** is linked in one shape:

```
Link -rt INIT=0   -m ABInitMain -ra =resSysHeap,resLocked :obj:<x>.c.o "{Libraries}Interface.o" -o <out>
Link -rt cdev=-4064 -m CDEVMAIN                           :obj:<x>.c.o "{Libraries}Interface.o" -o <out> ≥ lk.err
```

Three things are load-bearing there.

**`"{Libraries}Interface.o"` is not optional** for a code resource that calls the
Toolbox — `CountDITL`, `GetDialogItem`, `GetControlTitle` and their neighbours
resolve from it. A pure-assembly patch needs no library, which is exactly why
the `'MSPT'` source is a poor template for a C-backed one: it has no `IMPORT` at
all and keeps every byte of its data inline, because it has to survive being
`BlockMove`d into the system heap as one position-independent blob.

**`-m <entry>` rather than `-sn Main=<name>`.** `-sn` appears in this tree only
for the journaling `DRVR`, where the resource *name* carries meaning because a
driver is opened by name. For a multi-function code resource the entry point
must sit at offset 0, and `-m` is what puts it there.

**The trailing `≥ lk.err` in the cdev recipe is part of the recipe.** Without it
a link that resolves nothing still answers `status 0` and prints nothing, and
"links cleanly, no undefined symbols" becomes a conclusion drawn from silence —
the failure the earlier entries describe, arrived at from the other direction.
One `≥ lk.err` plus `Catenate` names the missing symbol in a single step, in
place of several rounds of swapping linker flags in the dark.

**A closing note on that same investigation**, because a wrong explanation is
worse than none: the missing output file was blamed on a truncated directory
listing, on the theory that a lowercase name sorts past the uppercase ones and
falls off the end. Neither half holds. `ListDirVerb` enumerates until
`PBGetCatInfo` fails and applies no cap of any kind, and HFS orders entries
**case-insensitively** — a real listing interleaves `bigtest.txt`, `bin`,
`c.err`, `data` and `fort.r` among the capitalised names. When an entry is
missing from a listing, the file is missing. That was, in the end, the true
finding: the linker had produced nothing at all.

## A file without a type is not a file, and the redirect that hid it

A session spent a long sitting on a code resource that would not build. `Asm`,
`SC` and `Link` all answered `status 0` and printed nothing; no object files and
no output resource ever appeared. It concluded, in order, that the linker flags
were wrong, that the proven recipe had to be mirrored, that `Link` was broken,
and finally that its own stderr capture was destroyed in transit. All four were
wrong, and the actual causes took two commands to find.

**The first cause is one field in the catalogue.** A directory listing of the
source folder:

```
dlgpatch.a   type: \0\0\0\0   creator: \0\0\0\0    2502 B
dlgwalk.c    type: TEXT       creator: R*ch        3758 B
```

`dlgpatch.a` has **no file type at all**, and MPW's tools open only `TEXT`.
Running the same `Asm` with its diagnostics captured says so plainly:

```
### Cannot open ":src:dlgpatch.a"
# Not a text file (OS Error -31001)
Asm - Execution terminated!
```

The assembler never opened the file. With no object there was nothing to link,
which is why every step downstream also produced nothing while reporting
success. The type was lost by `Duplicate` from the shared `Unix:` volume — which
carried `dlgwalk.c` across as `TEXT` and `dlgpatch.a` as four NUL bytes, so the
transfer cannot be trusted to be uniform even within one batch.

**Practice:** `SetFile -t TEXT -c 'MPS '` after any `Duplicate` out of `Unix:`,
and when a tool "does nothing", read the *type* column of a listing before
suspecting the tool. `-31001` is the error to recognise.

**The second cause was an ordinary compile error** — also never seen:

```
File ":src:dlgwalk.c"; line 64 #Error: 'port' is not a member of struct 'GrafPort'
        gr = dlg->port.portRect;
```

A `DialogPtr` *is* a `GrafPtr` in this world; `->port.portRect` belongs to a
`DialogPeek`. Two defects, two lines, both stated by the tools on the first
attempt.

**Why neither was visible is the part worth keeping.** The session had concluded
its `≥` capture was broken in transit, and stopped using it. It is not broken.
Measured the same day, both routes:

```
mpw_execute:        SC :src:kaputt99.c … ≥ :chk99.err   -> chk99.err holds the error
raw control port:   printf '… ≥ :chk98.err\n\n' | nc localhost 9001
                    (the ≥ travelling as UTF-8 e2 89 a5) -> chk98.err holds the error
```

What actually fails is putting the redirect and the read on **one command
line**. `SC … ≥ f.err ; Catenate f.err` comes back empty; issued as two separate
commands, the content is there. That is a one-line habit producing a conclusion
about the transport layer — and from there, a plan to rewrite a working design
in hand-written assembly to avoid a linker that was never at fault.

**Practice:** redirect and read in **separate** calls, always. And before
concluding that a layer is broken, test that layer directly with a case whose
answer you already know.

## Presence is not installation, and each single check is wrong on its own

A trap patch has two facts about it, and they are not the same fact: the code
is **resident**, and the trap **points at it**. A check that establishes one and
is read as establishing the other reports success about a machine that will
never call the patch.

The `'DPAT'` head patch on `_ModalDialog` is verified by scanning the system
heap for the block's own signature:

```c
for (; p + 6 < end; p += 2)                      /* SysZone .. ApplZone  */
    if (*(unsigned short *)p == 0x6000 &&        /* BRA.W at +0          */
        *(unsigned short *)(p + 4) == 0x4450)    /* magic 'DP' at +4     */
        return (Ptr)p;
```

That finds a block whether or not anything was ever hooked. The first guess at
*which* block was too kind: not one that an install allocated and copied and
then failed to patch, but **the pristine resource itself**. `'DPAT'` is linked
`-ra =resSysHeap,resLocked`, so merely *loading* it puts an exact copy of the
signature in the system heap. Measured by the session that hit it
(2026-08-02): `DeRez` of `DPAT` 128 gives the header `6000 0496 4450 0000 0000`,
with `generation: 0` — the entry code had never run — after a reboot, so no
leftover from an earlier attempt could account for it.

That distinction decides how strong the rule has to be. A stale block from a
failed install is a case you could hope to avoid; a resource that looks like an
installed patch the moment it is loaded is one a heap scan **cannot ever**
separate, no matter how carefully written. The old code's failure follows
directly: `blk = FindDlgPatch()` came back non-null, the install branch was
skipped, `NSetTrapAddress` was never called — reported installed, hooked
nothing.

**The obvious repair is the opposite error.** Checking the trap head alone —
`NGetTrapAddress` against the expected address — is unreliable here for a
recorded reason: **ToolServer restores the Toolbox trap table around every tool
run.** A patch installed from an MPW tool reads back correctly inside that
process and is gone by the next command, so the trap head reports *absent*
about a patch that is fine. That is why the earlier `'MSPT'` spike settled on
the heap scan in the first place.

**Practice: require the conjunction, never one of them.**

1. the heap scan finds a candidate block — the code is resident;
2. the trap vector equals that block's entry — it is actually hooked;
3. the block's saved-original field is non-zero and is not the block itself —
   an "adopted" block has a zero there, which separates *installed by us* from
   *lying around*.

The general form is worth carrying past this one patch: when a thing has two
independent properties, a check that can only see one of them is not a weak
check, it is a check of a different question.

## MPW Shell's empty reply is not an absence of output

MPW Shell returns empty Apple Event replies, so any step that must run there —
a `Link`, where ToolServer's linker is not trusted for the job — appears to
produce nothing. The output is not lost. It is in the **Worksheet** window
(`MeinMac:System Folder:Preferences:MPW:Worksheet`).

Bring the process to the front and take a screenshot. A link that reported
`(no output)` over the bridge had in fact printed two
`### Link: Warning: File was not needed for link: (Error 52)` lines naming the
two libraries that were superfluous — harmless, and invisible by any other
route.

## Foregrounding, and why it matters more than it looks

- **Synthetic input reaches the front application only.** Key, type and click
  verbs post into the OS event queue; whatever is frontmost consumes them.
  Sending a keystroke to a background application silently addresses the wrong
  process.
- **Launching an application that already runs re-foregrounds it.** The daemon's
  `LAUNCH` verb calls `LaunchApplication` without `launchDontSwitch`, and the
  Process Manager will not start a second instance. This is the cheapest way to
  bring a specific process forward — and the reason a rebuild can appear not to
  take effect: relaunching a stale binary just foregrounds the old one.
- **`SetFrontProcess` is asynchronous.** The layer switch lands only once the
  caller services the event system. Pump `WaitNextEvent` until `GetFrontProcess`
  confirms, and abandon the attempt if it never does — otherwise the process
  spins at full CPU waiting for something that will not happen.
- **Tracking loops poll the hardware pointer.** Menus, Standard File dialogs and
  modal dialogs cannot be reached by synthetic clicks at all; they need the
  host's real mouse.

## Absent tiers are not broken installations

Two layers are optional by design. Without MCP the bridge is still a host server
and a guest daemon reachable by any socket client. Without ToolServer the
command tier disappears — execute, compile, link — while everything native
continues to work: screenshots read out of the guest's framebuffer, volume
information, directory listings, input injection, the clipboard, fork-aware file
transfer, launching and shutdown.

**Practice:** when a command returns `found=NONE`, establish which tier is
missing before diagnosing anything else. The status verb answers even when the
daemon is down, which is what makes it the correct first question.

## A resource in the system heap looks exactly like an installed patch

A discovery scan that keys only on a block's signature will match the pristine
code **resource** as readily as an installed patch, because a resource built
with `=resSysHeap,resLocked` is loaded into the very zone the scan walks.

Building the `'DPAT' 128` dialog patch, `DLGTREE` reported an installed patch the
instant the daemon came up — with no `DLGINSTALL` ever run, and straight after a
reboot that clears every process-local block:

```
DLGTREE      -> {"installed":true,"dialog_up":false,"generation":0,"rect":[0,0,0,0],"items":[]}
DLGUNINSTALL -> not-head          <- the _ModalDialog trap was never hooked
```

`FindDlgPatch` — a clone of `FindMSPatch`, scanning `SysZone`→`ApplZone` for
`6000` (`BRA.W`) and `4450` (`'DP'`) — had matched the resource's **own bytes**,
resident in the system heap because the resource carries the sysheap attribute.
`generation:0` is the tell: the patch's entry code bumps that counter on every
`_ModalDialog`, and it had never run. The block was the un-installed resource,
not a live patch.

**Practice:** see the conjunction in *Presence is not installation, and each
single check is wrong on its own* above — this entry first suggested matching
magic **and** `Real != 0`, or "asking the trap directly", and both are
incomplete: the trap head alone is unreliable because ToolServer restores the
trap table around every tool run. The robust test needs all three at once — a
resident block, the trap vector equal to it, **and** `Real` non-zero and not the
block itself. Verified 2026-08-02 at the live system: the adopted block's `Real`
field read `0x00000000` — the resource's own `DC.L 0`, never overwritten because
nothing was ever hooked, which is exactly what separates it from an install.

`FindMSPatch` never hit this because the menu patch's `'MSPT'` ships as inline
`DC.W` bytes inside the INIT, not as a `sysheap` resource loaded into the same
zone the scan walks — so the resource copy and an installed block never coexist
there. A C-backed patch delivered as its own resource does put both in reach of
one scan, and the signature stops being enough to tell them apart.

## Firing and reaching are two questions, and a dead mechanism answers neither

A mechanism that produces no visible effect has failed in one of two ways, and
they are not the same failure: either it **never ran** — a defect in the
mechanism itself — or it ran and **never reached its target** — correct code,
wrong scope. From outside the two are identical, both are *nothing happened*, and
they have opposite repairs. Mistaking one for the other sends you into a
mechanism's internals when the fix was its reach, or the reverse.

The `'DPAT'` head patch on `_ModalDialog` read `generation: 0` after a *foreign*
application's save-alert stood open and was dismissed: the patch's entry code,
which bumps that counter first thing, had never run for that dialog — even though
the conjunction check said the trap was genuinely hooked. Two explanations fit
the one reading. The tempting one was about the mechanism's internals:
*`CautionAlert` must call `ModalDialog` directly, bypassing the trap table* — a
claim that would have sent the next step into patching lower traps
(`_GetNewDialog`/`_NewDialog`). The cheaper one was about reach: a patch installed
at runtime from the daemon is **process-local** (a recorded Route-B finding), so
it was simply not present in the foreign process — the same `generation: 0` a
firing bug would produce.

**The discriminator holds reach constant and asks only whether the thing fires.**
A verb (`DLGSELFMODAL`) had the daemon call a real `ModalDialog` **in its own
process**, where a process-local patch is guaranteed to be in scope, with a
filter that returns on the first pass so a background modal cannot spin. Measured
2026-08-02 by the session driving the dialog-patch work: `generation` went
`0 → 2` and the dialog was walked — the code fires and walks correctly. That one
measurement decided it. The cross-app `0` was reach, not a firing bug, and the
internals theory was never worth pursuing. The repair was reach — a boot INIT
(`AppleBridgeDlgINIT`, in `System Folder:Extensions`) that installs the patch
globally before any application runs. After it, the same foreign save-alert read
`generation: 2` with its five items and exact global rects, out of the daemon, a
different process. The bypass theory was falsified by the very same run:
`Alert`/`CautionAlert` **do** go through the `_ModalDialog` trap; the earlier
silence was only scope.

**Practice:** when a mechanism has no effect, do not begin by debugging the
mechanism. First run it where reach is guaranteed — its own process, a
controlled caller you wrote — and watch for the smallest sign of life: a counter,
a log line, one side effect. If it fires there, the bug is in reach — scope,
process, install timing, wiring — and the mechanism's internals are a wrong turn.
If it does not fire even there, reach is irrelevant until the mechanism itself
works. The two failures look identical from outside and cost the most when the
wrong one is assumed; one measurement that fixes reach and varies nothing else
tells them apart. It generalises past this patch: whenever *nothing happened*,
the first question is not *why is it broken* but *did it run at all* — and those
are answered by different experiments.

## A comment is a claim about the code, not a measurement of it

A comment is prose that was true when it was written and that nothing holds to
the code beneath it. The other entries here are about a *check* reporting on the
wrong evidence; this is the cheapest wrong evidence of all — a sentence beside
the code, read *instead of* the code. Two sessions hit it the same day, in
opposite directions, from two different files.

**The comment read as the mechanism (2026-08-02).** `tests/run_all.sh` carries the
line *"every `tests/test_*.py` belongs in this list"*. A session adding a new test
read that as **auto-discovery** — "the runner globs `test_*.py`, so my file just
runs" — and shipped it. The runner does the opposite: it enumerates an explicit
`files=(...)` array, and
`test_process_mutations.py::test_every_test_file_is_registered_in_the_runner`
exists precisely to fail on an *unregistered* file. The comment was a
**prescription to the human** ("put it in the list"), read as a **description of
the machine** ("the machine finds it"). Measured: the PR's checks were red at that
commit until the filename was added to `files=(...)`, then green. The comment
never lied — it simply was not the mechanism.

**The comment read as the wire (also 2026-08-02).** In the other direction,
`fileio.c`'s `LISTDIR` builds each row and a comment documents the separator as
`<CR>`. On the wire the rows are separated by **LF (0x0A)** — classic-Mac C maps
`'\n'` to CR and `'\r'` to LF, the inverse of every host convention, so the source
writes `'\r'` and the byte that lands is LF. A parser that trusted the comment
split on CR, found nothing, and reported an **empty directory** — rediscovered
three times in one sitting, because an empty listing is a plausible answer for an
empty folder. (The byte dump is in *Two line endings inside a single response*
above; the point here is narrower — the comment was the thing trusted, and it was
wrong.)

**One shape.** A count in a doc, a separator in a code comment, a "belongs in this
list" note beside a runner — each is a statement *about* the artefact that drifted
from it, or never matched it, while still reading true. The compiler does not
check comments; nobody does.

**Practice:** when a comment makes a *mechanically checkable* claim — a count, a
file's content, a wire format, whether a file is registered, which branch is
checked out — take the measurement from the **artefact**, not the sentence: run
the thing and read what returns, dump the bytes, `grep` the list,
`git branch --show-current`. Read the comment for *intent*; read the code for
*fact*. Both errors above — a red PR and three empty directories — were one
command of checking away.

## Two things never to do

**Never `2>&1` in MPW.** It crashes the shell. Redirect diagnostics with the
`≥` operator instead.

**Never hard-kill the emulator.** It can corrupt the guest image. The clean stop
is the `SHUTDOWN` verb or Special → Shut Down inside the guest. An unclean stop
also leaves a modal "not shut down properly" dialog on the next boot — a
tracking loop that no synthetic input can dismiss, on a machine that may have no
one at the keyboard.

## The shape all of these share

The entries above were written on one day, from six separate defects in five
different layers, and they are the same defect. It is worth naming, because the
name is what lets you recognise the seventh.

**Something reports on evidence that is not the thing it claims.**

| what it claimed | what it actually saw |
|---|---|
| `mac_compile`: the compile succeeded | an Apple Event was delivered — the compiler's exit status cannot cross the bridge at all |
| `build.py`'s `file_exists`: the artefact is there | a token this protocol has never emitted, so it answered False for every successful build |
| the decider ratchet: this function is tested | the string `main.c` in five unrelated tests, which covered eleven functions through one called `main` |
| `FindDlgPatch`: the patch is installed | the patch's signature in the system heap — which the resource itself has, merely by being loaded |
| the notes channel: you have been told | a question was deposited; an answer closes the question and dropped off the only surface |
| a required setting: the session has an identity | a human remembering to set it, for an id the platform already exported into the environment |

Every one of them passes review. Read the code and it does what it says: it
runs a check and reports the result. The gap is one level down, between the
evidence and the claim, and it is invisible from inside — a wrong verdict looks
exactly like a right one.

**Two things follow, and they are the whole practice.**

*Ask what the check can see, not whether it runs.* "Does this test pass" is the
wrong question; "what would this test still pass with" is the right one. The
ratchet counted a filename. The heap scan counted a resource. Both ran
perfectly.

*Use the thing.* Not one of the six was found by review, and five had survived
weeks or months of it. Every single one surfaced the first time somebody
performed the actual operation and looked at what came back: a compile of a file
with the wrong type, a listing that lacked an entry, an answer that never
arrived, two questions landing in the same second. The pattern is strong enough
to plan around — when a mechanism is finished, the next step is not another read
of it, it is one real run with the result inspected.

The repair is always one of two moves. Either make the check see the thing it
claims — the artefact, the trap vector, the delivery, the arrival — or say
plainly that it cannot, and let the caller decide. What must not survive is the
third option: a report that sounds like the first and is the second.

## A dialog walk fires only on a trap entry that happens while armed

The `_ModalDialog` head patch that gives `DLGTREE` its cross-application dialog
perception is armed explicitly (`DLGARM`) and disarms itself after one walk.
That much is by design. What is *not* obvious, and what an agent will otherwise
infer wrongly, is **when** the walk can still be caught. Measured on
2026-08-03 against a real SimpleText "Save changes?" alert, three runs:

| Situation | Result |
|---|---|
| Armed, **then** the dialog appears | walk happens, `generation` +1, `armed` self-clears |
| Disarmed when a new dialog appears | **no walk**, `generation` unchanged — while a real second dialog stands on screen |
| `DLGARM` **while** a dialog already stands | **no walk**, `generation` unchanged, `armed` stays `true` |

The rule behind all three: the walk fires **only on a `_ModalDialog` trap entry
that occurs while the patch is armed**. Arming must strictly *precede* the
dialog it is meant to capture.

**Practice:** re-arm after handling a dialog and *before* the next one is
expected — not at an arbitrary point in the loop. An arm that arrives late is
not delayed, it is **inert**: the dialog it was meant to catch will never be
walked, and `generation` will never move for it. A driver that treats
"`generation` greater than the last handled value" as its only test for "a new
dialog is up" is therefore blind to every dialog that appeared while the patch
was disarmed, and blind to any dialog that was already on screen when the loop
started. Reading the screen is not a refinement of that test; for those two
cases it is the **only** source of truth.

### A free corollary about where the alert's loop runs

Case three carries an inference that costs nothing to obtain and needs neither a
rebuild nor ToolServer. When `DLGARM` was issued while the alert was standing,
`armed` stayed `true` — so the one-shot never fired, so the patch head never
ran, so **the `_ModalDialog` trap was not entered at any point during the
running alert**. A modal loop of the usual shape (`do ModalDialog(…) until item
is enabled`) would have entered the trap many times per second and produced a
walk immediately.

This is independent evidence for a claim otherwise inferred from a *negative*
result — that the alert's teardown bypasses `_CloseDialog`/`_DisposDialog`. The
corollary widens it: not only the teardown, **the loop itself runs off-trap**.

**Practice:** when a patch appears not to fire, check whether the *arming* state
observably changed rather than concluding anything about the trap. `armed`
staying `true` is a positive measurement that the head did not run; a flag that
merely stayed unset would have been consistent with three different causes.
Distinguishing "the trap was never entered" from "the trap ran and my handler
was wrong" is what separates a diagnosis from a guess, and it was available here
for the price of reading one field.

#### Correction, 2026-08-03 — the corollary above is wrong, the rule is not

The **observation** stands: arming while an alert is standing produces no walk,
and `armed` stays `true`. The **inference** drawn from it — "the `_ModalDialog`
trap was not entered at any point during the running alert, so the loop itself
runs off-trap" — does not follow, and it is retracted.

`ModalDialog` is entered **exactly once per dialog**. It does not return on each
event: it runs its *own* event loop inside the trap and comes back only when an
enabled item has been hit. So the single trap entry happens when the dialog goes
up, before any later arming, and there is no second entry while it stands. That
a mid-alert `DLGARM` changes nothing therefore says nothing whatever about
on-trap versus off-trap — it says the one entry was already past.

The section above this one records the same question being settled by
measurement: `Alert`/`CautionAlert` **do** go through the `_ModalDialog` trap;
the silence was scope, not bypass. This corollary contradicted a finding already
in this file, and was written anyway.

Two things made it possible, both worth naming because they are cheap to repeat.
The measurement that produced it — `generation` moving by exactly one per dialog
— fits *both* explanations, the one-shot self-disarm and the single trap entry;
one was picked without a test to separate them. And the same author had asserted
elsewhere that `ModalDialog` "is called in a loop, so one dialog produces many
entries", which is the opposite of what the same run had just shown.

**What survives, and is now better founded:** arming must strictly precede the
dialog. The reason is no longer "the loop may be off-trap" but the sharper one —
**there is exactly one trap entry, at the moment the dialog appears.** Arriving
after it is not late, it is inert. The practice above is unchanged; only its
justification was wrong, which is its own lesson: a rule can be right for a
reason that is not.

## The keyboard reaches a modal dialog; the synthetic mouse does not

A modal tracking loop **polls the hardware pointer**, which is why `mac_click`
never reaches one — the daemon sets the low-memory mouse for an instant and the
emulator immediately restores the real cursor position. That rule is old. What
was not written down, and reframes what a driver has to do, is the other half:
the same loop takes **keystrokes from the event queue**, and the event queue is
exactly where `PostEvent` writes.

Measured 2026-08-03 on Basilisk against a ROM `Alert()` — SimpleText's *"Save
changes to the document … before closing?"*:

| Key sent with `mac_key` | Result |
|---|---|
| `escape` | **Cancel** — the alert closed, the document stayed open with its unsaved text |
| `d` + `command` | **Don't Save** — the alert closed and the document was discarded |

Both confirmed by screenshot, and **no coordinates were involved in either**.
The same trick had been recorded once for a Standard File dialog on an SE/30,
i.e. a machine with no host-mouse channel at all; it is not a property of that
machine, it is a property of where the two input kinds are read from.

**Practice:** for a dialog whose item rectangles are unknown, stale, or were
never captured, do not reach for pixel coordinates first. The keyboard turns
*"where is the button"* into *"which named action"* — a question a screenshot
answers far more reliably than any coordinate estimate, and one whose failure
mode is a no-op rather than a click somewhere unintended.

**And never probe with Return.** It fires the *default* button, which on any
save-changes alert is the destructive or escalating one — here `Save`, which
opens a Standard File dialog and leaves a second tracking loop owning the
machine. `Escape` is the safe probe: its worst case is that nothing happens.
The buttons that matter live in one row, and `Save` and `Erase Disk` are drawn
by the same code — a driver that guesses in a modal is not risking a misplaced
pixel, it is risking an irreversible action. Report and stop instead of
guessing; a visible gap beats a confident wrong click.


## The trap table is per-process here, but the jGNE filter is not — measured 2026-08-03

The Route-B finding above — *a patch installed at runtime from the daemon is
process-local* — was reproduced from a second, independent angle, and then
qualified by a result that moves where cross-app work can live.

A counter probe (`CPINSTALL`/`CPARM`/`CPREAD`: a disarmed-by-default system-heap
block, armed only for a ~2 s window) head-patched `_GetNextEvent` (`$A970`) and
`_WaitNextEvent` (`$A860`) with a pure counter that also stamped `CurrentA5`
(low-mem `$0904`) — so a climbing count, meaningless on globally-hot traps that
every app pumps thousands of times a minute, became *which process went through*.
Filtered to skip the daemon's own A5, the foreground count (`OtherCount`) stayed
**zero** in every scenario: the idle Finder, a standing SimpleText modal, even
ToolServer kept busy in the window. The daemon's own calls counted; no foreign
process ever did. The tool-trap table is effectively **per-process** here — a
runtime `NSetTrapAddress` reaches only the installing process, exactly the reach
the boot INIT exists to give the `_ModalDialog` patch. Two measurements, different
trap pairs, one statement.

**But the level below the trap dispatch is a different question, and its answer is
the opposite.** The `jGNEFilter` low-memory hook (`$029A`), which the Event Manager
calls from *inside* every event fetch, was installed at runtime by the daemon with
the same counter and self-filter. Its foreground count climbed at once: the idle
Finder's A5 appeared with no activity at all, and while a SimpleText save-alert
stood open, **12 of 12 samples carried SimpleText's A5** at ~150 calls/second — the
modal dialog's own `ModalDialog` loop pumping the Event Manager, seen from the
daemon's block in a foreign process. `$029A` is **not** in the per-process-swapped
set (`CurrentA5`, `CurApName`, `WindowList`); a runtime jGNE hook is global where a
runtime trap patch is not.

**Consequence.** Cross-app perception of an *already-standing* dialog — the one
case the `_ModalDialog` entry patch cannot serve, because that trap is entered
exactly once per dialog, before anything can be armed — is reachable at **runtime**
through a jGNE walk-on-request, with no boot INIT and its boot-wedge risk: Route B,
and a reboot clears `$029A` and the system-heap block. "Everything cross-app needs a
boot INIT" was too strong; it holds for the trap table, not for jGNE.

**Retraction (2026-08-03), the same shape as the corollary correction above.** The
session driving the ApfelPilot loop had concluded that a ROM `Alert()` *tears its
dialog down off-trap*, from the observation that a daemon-installed head patch on
`_CloseDialog`/`_DisposDialog` never cleared `DialogUp` for SimpleText's alert. That
was the bypass theory again, one trap level below `_ModalDialog`, and wrong for the
same reason: the close patch is **process-local**, so it never fired in SimpleText's
process at all — `DLGSELFMODAL` cleared the flag only because it disposes the
daemon's *own* dialog in the daemon's *own* process. Scope, not off-trap. The
teardown was never measured to bypass the traps; it was never in scope to be seen.

**Method, reusable.** Two guards made the jGNE reading trustworthy and are worth
keeping. (1) A **self-filter** — stamp the caller's A5 only when it is *not* the
daemon's own — because a background daemon pumps these hot paths so fast it
otherwise owns every sample. (2) **Three distinguishable identities** — the daemon,
the background app that keeps running behind a modal (the Finder here), and the
target — because once you know a global filter catches *idle background processes
too*, a bare non-self A5 proves nothing: the Finder's A5 during a standing alert
would look like success and mean the wrong process. The question is never *did
something foreign go through* but *did the specific target go through*, and that
needs the target's identity pinned first, in a quiet reference window, before the
event you care about.

**A boot INIT that hooks a hot-patched trap will not be at the head — scan the
heap, do not trust the vector (2026-08-03).** The counter block above was also
installed the other way, to confirm from the counter side what `dlgpatch` already
shows functionally: a boot-installed trap patch is global. A boot INIT (`cpinit`,
`INIT` id 0, `resSysHeap`/`resLocked`) head-patched `_GetNextEvent` (`$A970`) and
`_WaitNextEvent` (`$A860`) at boot. The confirmation came back inconclusive for a
reason worth recording. After a clean, non-wedging boot, the daemon's
`FindCounterProbe` — which checks the trap **head** alone, `NGetTrapAddress($A970)`
less the stub offset against the block magic — reported the block **absent**
(`installed=0`), while `dlgpatch` (which hooks `_ModalDialog`, `$A991`) reported
**present** on the very same boot (`DLGTREE installed=1`), and the `cpinit`
resource was verifiably in `Extensions:` and had run.

The block was there; it was simply not at the head. `_GetNextEvent` and
`_WaitNextEvent` are the most-patched traps in the system — the Notification
Manager, desk utilities, and countless extensions all chain there, and they
install *after* a boot INIT, so they become the head and chain **downward** to the
INIT's block. A head check therefore false-negatives: the patch is alive, it is
just no longer first. This is the same signature already recorded above for
`_MenuSelect` (the Route-B block was not head after boot though the INIT had
provably run); for a trap this hot it is not a risk but the expected outcome, and
the method note *"checking the trap head alone is unreliable here"* is exactly this
case.

The trustworthy check is the **conjunction, never one half.** A **heap scan** for
the magic (as `FindDlgPatch` does) finds the block regardless of chain position —
but the heap scan *alone* deceives in the other direction: a `resSysHeap` block is
in the system heap the instant its resource is *loaded*, magic and all, with its
install code never run. Presence in the heap does not prove the install ran;
absence from the head does not prove it failed. Confirm **both** — a heap-resident
block *and* evidence its code executed (a field the install sets, or the block
reached by walking the chain down from the current head) — before concluding
anything about a boot INIT on a hot trap.

**Consequence.** The counter confirmation was retired, not repaired. It would
re-prove from a third angle a statement already carried by two — runtime trap =
process-local (measured twice, two trap pairs), runtime jGNE = global (12/12), boot
INIT = global (functional via `dlgpatch`, re-confirmed on this very boot) — and the
perception path actually chosen, a runtime jGNE walk-on-request, needs no boot INIT
at all. `cpinit` is kept only as the template for the day a boot INIT must hook a
hot-patched trap for an unrelated reason. This note is what that day will need:
scan the heap, verify the code ran, and do not read a head check as install state.

## Seeing the guest when the daemon is down

Every screen-reading tool in this project goes *through* the daemon —
`mac_screenshot` has the daemon capture the emulated framebuffer, and
`guest_input.py shot` asks `System Events` for the emulator's window geometry
first. Both fail exactly when the answer matters most: the daemon is not
dialling in and nobody can say whether the guest is wedged, sitting on a modal,
or perfectly fine.

Measured 2026-08-03, during a daemon that had not connected for over 150 s:

- `guest_input.py shot` failed with `-1719` — *"window 1 of process BasiliskII
  cannot be read, invalid index"*. `System Events` reported **0 windows** for a
  process that plainly had one. AX is an unreliable narrator for SDL2 apps.
- A plain full-screen `screencapture` showed the host desktop and **no
  emulator** — the window was on another Space, and `screencapture` only ever
  captures the current one.

**The path that works** goes around both, and touches nothing:

```python
import Quartz
wins = Quartz.CGWindowListCopyWindowInfo(
    Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
# match kCGWindowOwnerName == "BasiliskII", take the one whose Width > 500
```

`CGWindowListCopyWindowInfo` sees windows **across Spaces** and independently of
Accessibility. It yields the window id, which `screencapture` will grab
directly:

```
screencapture -x -o -l <windowid> guest.png
```

That produced a full, readable 1024×768 guest screen with the emulator on
another Space, the app not frontmost, and the daemon dead.

**Practice:** when the bridge is down, reach for this **before** reaching for
the mouse. It is a pure read — no click, no key, no activation, nothing that can
change what you are trying to diagnose. On the run above it showed the guest was
healthy and the daemon's own console carried the cause, which no amount of
host-side guessing would have produced.

**And it earns its place by the counter-example.** On that same run the next
step was a real-mouse click, aimed by *computing* guest coordinates from the
window bounds instead of calibrating them. The pointer landed ~120 px off, in
the daemon's Verbose console — a window that is `getFrontClicks` and therefore
takes the foreground when clicked. The emulator hung shortly after, and whether
the click caused it could not be ruled out. Calibration costs one `cliclick m:`
and a capture; the ordering is not a detail. **Read first, calibrate second,
click last** — and if reading answers the question, do not proceed to the other
two at all.

## An error message can name the wrong machine

`OTOpenEndpoint` is a **local** call: it creates the guest's own Open Transport
endpoint, before any connection to anything is attempted. When it fails, nothing
about the host has been touched yet.

The daemon's console nevertheless prints, verbatim (observed 2026-08-03 after a
`SWAPSELF` + reboot):

```
Opening TCP endpoint...
OTOpenEndpoint failed!
*** HOST SERVER NOT REACHABLE - bridge i…
  the connection attempt failed
  check on the HOST, in this order:
   1. is host_server.py running?
   2. is 10.0.2.2 on the default-route N…
   3. emulator NIC alive? quit BasiliskI…
```

All three instructions point at the host. The host was fine throughout — `:9000`
and `:9001` listening, the log accepting control connections and rejecting them
with *"no daemon connected"*. A session that followed the message would have
searched the machine that was never at fault, and the retry loop reuses the same
broken Open Transport state, so it repeats the misdirection every 30 s.

**Practice:** read the *last successful* line, not the loudest one. Here
`Opening TCP endpoint…` with no `Connecting to host…` after it places the
failure inside the guest before the wire is ever used; the healthy sequence is
`Opening TCP endpoint… / Connecting to host… / SYNC-OK / connected to
10.0.2.2:9000`. A diagnosis banner is a claim about a cause, and a claim is not
a measurement — the same rule this file applies to code, applied to the
project's own error text. The message should say Open Transport in the guest is
not answering, and it does not.

**Re-verifying "a runtime trap patch is process-local" — supported on three legs,
but the in-window control was not obtained (2026-08-03).**

The claim above — a trap patch installed at runtime from the daemon reaches only
the daemon's own process — is well supported, and a fourth, tighter check was
attempted and honestly failed. Both halves belong on the record.

*Supported by:* (1) the Route-B counter measurement noted above (the foreground
`OtherCount` stayed zero); (2) `dlgpatch` reads a foreign app's dialog cross-app
ONLY once installed from the boot INIT — the runtime install did not reach it;
(3) an `_GetNextEvent`/`_WaitNextEvent` counter, armed with SimpleText confirmed
front, showed `OtherCount = 0` for the foreground across several runs while its own
`gne` climbed — so the patch was live, and still caught no foreign caller.

*Not obtained:* a paired, in-window proof that the foreground process ENTERED the
traps at the moment `OtherCount` read zero — i.e. that the zero means "the patch
did not see a caller that was there", not "no caller was there". This is exactly
the off-by-one-level ambiguity a naive standing-modal measurement hides: a modal's
`ModalDialog` loop fetches events below the trap layer through a ROM-internal path,
so it never exercises `$A970`/`$A860` at all — `jGNE` catches it, the trap counter
cannot. The first version of this finding rested on such a modal; that is why it
was re-checked rather than trusted.

*Five attempts to obtain the control failed on the EXPERIMENTAL SETUP, not the
finding* — and the failure modes are worth recording, because they are the shape of
this measurement:
- the counter block keeps a single `LastA5` slot, overwritten ~59×/second by a fast-polling FOREIGN process (A5 107480968, distinct from the daemon's) whose IDENTITY stayed open — first guessed as the health watchdog, but that guess confused a rate for a name: the watchdog is a confirmed `WaitNextEvent` app (`mac/watchdog/watchdog.c`) that sleeps ~60 ticks, i.e. makes ~1 call/second, not the ~59/s seen here. Whatever it is, a bridge-read almost always samples it and the foreground's ~2/s caret-blink is a needle in that haystack — 0 of ~130
  samples, not because SimpleText was absent but because it is never the *last*
  stamp before a read (the same signature as the morning's 10/10 `self`);
- driving the foreground to pump harder needs real keystrokes, and manual keyboard
  timing over the bridge lost, in turn, to: too few samples against the 59/s poller,
  an accidental Cmd-Z that erased the carrier, host keyboard focus landing on the
  terminal instead of the emulator, and an inaudible go-signal. Each round closed
  one condition and exposed the next; none was the finding.

*The clean closure, if a future question ever depends on it:* add a SECOND
self-filter to the counter block — skip that fast poller's A5 as well as the daemon's —
so `LastA5` stamps only a third party, the foreground, and the human, the focus and
the timing all fall out of the loop. That is a daemon rebuild + reboot; no current
question depends on it (the runtime-jGNE-walk that this whole reach question serves
is already proven end-to-end), so it is left undone deliberately, and this note is
the record of why. A later attempt to turn that fast poller into an in-window WITNESS — argue an application must reach the trap, so its absence from the trap count would prove the patch local — also did not hold: the counters did not reproduce run to run (jGNE-fires vs trap-calls swung from ~4.5 to ~0.03 across same-session runs, and the last-non-self A5 changed identity between them), so no aggregate could carry the argument either. **The conclusion holds, the reason given for it does not — see the 2026-08-04 entry at the end of this file: the counters reproduce to the digit when the process set is held still, and the swing was one application starting and stopping. It could not be seen at the time because nothing could enumerate processes.**

**Correction, 2026-08-04 — the closure above was taken, and NOT as written.**
The "second self-filter" cannot be built as specified, because its premise fell:
the poller was attributed to the health watchdog and that attribution was
refuted in source (`mac/watchdog/watchdog.c` sleeps ~60 ticks ⇒ ~1 call/s, not
59). It has **no name**, and its A5 is a heap address no reboot preserves — so
there is neither an identity to filter nor a constant to compile in. Asking for
"the second self-filter" therefore asks for a mechanism that cannot exist.

What was built instead needs no identity: the counter block keeps **two** slots,
`LastA5` and `PrevA5`, with a distinctness gate — an A5 equal to the one already
in `LastA5` does **not** shift. A process polling at any rate therefore occupies
exactly ONE slot, and the foreground's rare call parks in the other and stays
there until a THIRD distinct A5 appears. "Did the target enter this trap in the
window?" is answered by `last` **or** `prev`, and the noisy neighbour names
itself as a side effect — which is what the identification still owes. Block
magic went `CPRB` → `CPR2` so a new daemon cannot adopt an old, shorter block
and write past its end. Daemon 0.8d35; the geometry is held by
`tests/test_counter_probe_contract.py`, which decodes the stubs and re-derives
every displacement from the declared offsets rather than comparing against a
stored copy.

*Generalise the method, not the note:* the request named a **mechanism**
("filter that process"), and the mechanism's precondition was gone while the
**goal** ("get the foreground out from under the noise") was still reachable by
other means. When a recorded closure stops being buildable, check whether what
it was FOR is still buildable before reporting it blocked.

**The fast poller is ToolServer — measured 2026-08-04, differentially.**
Built, deployed (0.8d35) and run the same day. With the **global jGNE** hook
installed (`CPJINSTALL`; the two trap stubs are process-local and can never see
a foreign caller — measuring on them gives `other=0` by construction, which cost
two runs and a wrong conclusion here):

| | jGNE fires / 8 s | rate | `last` |
|---|---|---|---|
| ToolServer running | 328 | ~41/s | 115373528 |
| after `QUIT:MPSX`  | 8   | ~1/s  | 129785544 |

Quitting ToolServer collapses the rate by a factor of 41, and the remainder
arrives from a **different** A5 at the ~1/s that `mac/watchdog/watchdog.c`'s
~60-tick sleep produces. So the FIRST attribution (ToolServer), which was made
and then abandoned, was right; the second (the watchdog) was wrong; and the
watchdog is the ~1/s remainder. Honest residual: this is a differential — the
rate collapses when ToolServer quits — not a direct name-to-A5 binding. A
Process-Manager listing verb (`GetNextProcess`/`GetProcessInformation`) would
supply that, and does not exist yet.

`prev` stayed 0 in **both** windows, and that is itself the result the single
slot could not produce: `other=328` with only ONE distinct A5 says *one process
hammering*, which a single overwritten slot cannot tell apart from *many
processes overwriting each other*. The two-slot gate answers a question the
one-slot version could only pose.

*Method note, because it bit here too:* the acceptance run was first pointed at
the **process-local** trap path, where `other=0` is guaranteed regardless of what
is running — and that zero was briefly read as "the poller is not present on a
fresh boot". Wrong instrument, wrong conclusion, same shape as everything else
in this file. `jcnt=0` in the reply was the tell.

**An autonomous actuator's verb allowlist must exclude any verb that can cancel
its own preconditions (2026-08-03).**

The perceive→reason→act loop that drives a *foreign* application rests on one
precondition: the TARGET owns the foreground. Both halves of perception read
foreground state — the target A5 is pinned from whichever process is pumping the
Event Manager, and the DITL walk fires in the front app's context. A verb that can
move the foreground therefore does not belong in the loop's action space.

The concrete case: the conductor's allowlist carried `monitor`, which shows the
daemon's Verbose window. A faceless daemon cannot own the foreground; a daemon
WITH a window can, and can steal it. So a loop able to emit `monitor(show=true)`
can put the daemon in front and silently invalidate the ground its own next
perceive stands on — the pin then reads the wrong process, or the walk finds the
wrong front, and NO error fires. It was removed from both the conductor allowlist
and the planner's grammar (a grammar-constrained model cannot emit what the
grammar forbids). It was also the likely cause of the Verbose window reappearing
before an autonomous run — seen, but unattributable at the time.

The general rule: a verb that touches FOCUS, PROCESS MEMBERSHIP or DAEMON STATE
must be excluded from an autonomous loop's action space *by construction* — unless
the loop explicitly re-establishes the precondition afterward and verifies it. The
danger is that the failure is SILENT: the loop removes its own footing and keeps
going as if nothing changed, so it cannot be caught by an error check downstream.
Exclude such verbs at the grammar, do not hope to catch them at runtime. (First
autonomous run of the conductor against a standing dialog, 2026-08-03: qwen chose
Cancel, the leash held, and the stale-`dialog_up` re-walk correctly resolved to
"gone" with no second click — the actual danger the allowlist protects against.)

**A counter is not a health indicator: `err_count` is cumulative for the daemon
PROCESS (2026-08-04).**

The operator saw 61 errors on the bridge and reasonably suspected the other
machine. Sixty were mine, from one polling loop; one was the parallel session's
probe of a verb that did not exist yet. Later the same day the number reached
237 for the same reason. Nothing was wrong with the bridge at any point.

`err_count` counts every `STATUS != 0` reply since the daemon started, so a
single caller with a malformed verb drowns the history of everybody else, and it
keeps doing so until the next guest reboot. Read it as "how many badly formed
requests has anyone made since boot", never as "how healthy is the link". The
liveness questions are answered by `idle_seconds`, `missed_heartbeats` and
`link_id`.

**There is no liveness verb on `:9001` except `PING` (2026-08-04).**

`STATUS` and `STAT` both look like control-port verbs and neither is routed. An
unrouted verb is passed to the daemon as an MPW command line, which hands it to
ToolServer — so with ToolServer down you get `no-ToolServer/MPW`, an answer about
entirely the wrong layer, and with ToolServer UP you get `STATUS:0` and empty
output, which reads as success. `mac_status` works because the MCP tool uses a
different path, not because the verb is routed.

Two loops built on `STATUS`/`STAT` produced 237 daemon errors in one afternoon.
Use `PING` (or `mac_status`), and read the server log: a routed verb is logged as
`verb:`, an unrouted one as `cmd:`. That one word is the whole diagnosis. Since
this note was written the fall-through also says so itself.

**The launchd host server is a SEPARATE COPY; a repo edit does not reach it
(2026-08-04).**

`~/Library/Application Support/AppleBridge/host_server.py` is what actually runs.
Editing `host/host_server.py` in the repo — or in a worktree — changes nothing
until you copy it across and `launchctl kickstart -k gui/$(id -u)/de.390er.applebridge-host`.

This cost an hour twice in one day, in both directions. The parallel session
added a route for its new verb, deployed a freshly built daemon, saw the verb
fall through, and concluded the deploy had failed — the daemon was fine and the
host was not routing. Then the same trap caught the author of this note on the
next deploy, having warned about it an hour earlier.

**MPW's assembler needs CR line endings; the C compiler does not (2026-08-04).**

`SC` compiles a source file with LF endings and UTF-8 comments without
complaint — the guest's `main.c` is byte-identical to the repository copy and
builds. Generalising from that to `Asm` is wrong: an assembly file pushed raw
with LF produces a **6-byte object file** and no error anybody sees, because the
assembler reaches the end of the input without an `END` and gives up quietly.
The link then fails for a reason that has nothing to do with the real problem.

The rule is not "always convert" but "convert when a special character or a line
ending is part of the SYNTAX". A `.c` file whose only non-ASCII lives in comments
survives a raw push. A Makefile does not (`ƒ` is one MacRoman byte and two in
UTF-8, so every dependency line breaks and `Make` emits an EMPTY script that then
"runs" without complaint). An `.a` file does not either.

**Runtime safety and linkability are different questions (2026-08-04).**

`GetHandleSize` moves nothing, is safe to call from a jGNE filter, and was
recommended in review as a second bound for a walk that had only a terminator.
It broke the link: `Undefined entry (Error 28) GETHANDLESIZE`. A code resource
that must stay A5-free links with NO library, so any call that goes through
Interface.o glue is an unresolved symbol — which is exactly why `dlgwalk.c`
avoids `CountDITL` (`dlgwalk.c:77`).

Before adding a Toolbox call to an A5-free resource, ask whether it compiles to
an inline trap or to glue. The mechanical answer is `DumpObj` on the linked
object: an empty externals list is the proof, and "it does not move memory" is
not an argument about linking.

**A constraint names what you must not touch, not how little you may do
(2026-08-04).**

Told to hold off while the other session deployed, this session read the
prohibition as paralysis and watched the other one stall for two hours on a file
it could have been handed. The hold covered the guest and `main`. It did not
cover the channel, the shared folder (`~/Desktop/Share`, `Unix:` on the guest,
host-writable and guest-readable), or any host-side tool.

When told to wait, enumerate what the wait actually forbids. Everything else is
still available, and the other side usually needs something from exactly there.

**When a measurement cannot answer the question, too many hypotheses fit
(2026-08-04).**

A verb was falling through on the host, so every test of it measured the host.
Against that one observation, three different explanations of the guest fitted
equally well — the watchdog launching the wrong binary, the reboot verb not
doing a full restart, an ambiguity between two files with the same creator. All
three were wrong, and none could be eliminated by more of the same test.

The tell is not that a hypothesis fails to fit. It is that too many fit. When
that happens, stop generating explanations and go find an instrument that can
distinguish them — here, a negative control: a verb that certainly does not
exist, to see what "not implemented" actually looks like.

**"The counters do not reproduce" was an uncontrolled variable, not an
instrument limit — and the variable had a name nobody could read (2026-08-04).**

The trap-locality note above records that the jGNE-to-trap ratio "swung from
~4.5 to ~0.03 across same-session runs" and concludes that no aggregate could
carry the argument. That conclusion stands; the reason given for it was wrong.

Measured on demand, once `PROCLIST` made the running processes enumerable:

| | ToolServer absent | ToolServer running |
|---|---|---|
| `other` / `jcnt` in 8 s | 8 | **326** (×41) |
| `last` bound to a name | Finder | **ToolServer** |
| `jcnt / gne` | 0.049 | **4.18** |

Against the note's own figures — 0.03 and 4.5 — that is the same swing,
produced deliberately by starting and quitting one application. Two consecutive
runs with the process set unchanged were identical to the digit: `gne` 162,
`other` 8, the same `last`. The counters reproduce perfectly. What did not
reproduce was the machine.

The variable was invisible because nothing could enumerate processes: a
foreground application, a helper, and the daemon all showed up as bare A5
values. `processLocation` + `processSize` bound each one to a name by
containment, and the poller stopped being "an unidentified fast process".

Two consequences for how to run one of these:

  A measurement without a process list is incomplete. Take `PROCLIST` before and
  after, bind every observed A5 by containment, and record the process set as
  part of the result — not as context.

  And when a number refuses to reproduce, ask what else changed before
  concluding the instrument is noisy. Noise is a property of the instrument;
  irreproducibility usually is not. Here the instrument was exact and the
  experiment was uncontrolled, which looks the same from the inside and is the
  opposite problem.

Unchanged by this: the in-window control for trap locality is still not
obtainable this way. A trap patch installed at RUNTIME is process-local by
construction, so `other = 0` on the trap path is a property of the design and
never evidence about the foreground. Naming the callers does not change that —
only a boot INIT would.

**Two ledger items cited two different figures for the same machine, and both
were wrong (2026-08-04).**

A screenshot over the control port costs **4.7 s** on this host — ten runs,
median 4.70 s with ToolServer running and 5.02 s without, spread 4.15–5.08 s.
The two open items that rest on that cost said, of the same machine:

    Compression        "one capture takes ~15 s on the slirp tier"
    Region argument    "~1–2 s on Basilisk"

Three times too high and two and a half times too low, in items sitting a
screenful apart. Neither was measured; each was carried forward from a different
session's impression, and nobody compared them because they were never read side
by side.

Two things worth keeping from re-measuring it:

*The process set is not always the variable.* It explained the counter swing
completely (see the entry above), so the temptation is to reach for it again.
Here it changes almost nothing — 0.3 s, and in the unexpected direction. The
cost is in the transfer and the PNG encode, not in the cooperative scheduling.
A rule that just worked is not a rule that always works.

*Know which leg you are talking about.* The 768 KB of raw PixMap crosses the
GUEST→HOST link; the control-port reply is only ~30 KB of base64 PNG. The two
open items address different legs — compression relieves the bridge, a region
argument relieves both — and stating a single "screenshot cost" without saying
which leg is how they drifted apart in the first place.

The priority did not change. 4.7 s per look is what a GUI-driving loop pays on
every step, and that is reason enough to keep both items at P2. What changed is
that the next person plans against a measured number rather than against two
that disagree.

*Method note, since this measurement needed three attempts:* the first timing
harness computed shell arithmetic across two `python3` invocations and reported
`-0.00s`; the second waited for EOF on a socket the server never closes and hung
in its own 90-second timeout. Both would have been just as capable of returning
a plausible wrong number as an obvious one. Read a control-port reply by its
DECLARED LENGTH — the protocol carries it for exactly this reason — and never
time anything across two processes.

And the reason those two cost minutes rather than hours: **they failed
visibly.** `-0.00s` is nonsense on its face and a hang cannot be mistaken for a
result. Compare the expensive failures in this file — `Exists` answering true
after a link that died, the converter reporting `[BIN]` and copying raw, a
swallowed verb answering `STATUS:0` with empty output, counters that reproduced
beautifully while the machine underneath them did not. Every one of those
returned something plausible, and plausible is what gets written down and built
on.

So when choosing between two ways to measure something, prefer the one that
cannot fail quietly, even if it is clumsier. A harness that crashes costs an
afternoon at worst. A harness that returns a believable wrong number costs
whatever gets decided on it, and the bill arrives much later and somewhere
else.

## The shell eats the text before any tool can see it — measured 2026-08-04

Both sessions lost text to the shell within hours of each other on the same
day, in notes *about* the defect class this file exists for.

One wrote `$0A1C` — the `MenuList` low-memory address — inside double quotes.
The shell substituted an undefined variable and the address vanished from the
sentence explaining it. The other wrote a sentence containing a backquoted
`nc`; the shell **executed** it and deleted the subjects of two sentences, so
what arrived was `" schliesst halbseitig"`.

Neither was noticed, and neither *could* be by the channel: `notes.py list`
reported **zero unreadable lines**, correctly. The lines were syntactically
perfect — timestamp, `from=`, `to=`, `re=`, text. The damage happened in the
shell, before a single byte reached the tool, so there was nothing left to
detect. This is the file's own recurring theme arriving from a new direction:
a report on evidence that is not the thing it claims. Here the report was even
right about what it measured.

The fix is not to quote more carefully. It is to keep the text out of `argv`:

```bash
notes.py note --stdin <<'EOF'
`nc` half-closes, and $0A1C is the MenuList.
EOF
```

**The quotes around `EOF` are the mechanism, not decoration.** An unquoted
delimiter (`<<EOF`) still expands `$` and backquotes *inside* the heredoc —
the identical trap one layer down. `notes.py`'s own advice string is pinned by
a test for exactly that, because handing out `<<EOF` would have reopened the
hole while appearing to close it.

The general shape: **for any text that is content rather than a parameter,
prefer a channel the shell does not parse.** `argv` is a parameter list that
happens to accept prose. Two of its metacharacters destroy text silently and
leave no evidence, which puts this in the same class as `Exists` answering
true after a failed link — plausible output, no way to tell from the outside.

## What a gesture costs, and where the courtesy is paid — measured 2026-08-04

A `mac_host_click` was believed to have ~1.4 s unaccounted for. It did not.
Timing every subprocess a gesture actually makes:

| step | median | note |
|---|---|---|
| `osascript` frontmost check | 0.146 s | |
| `osascript` set frontmost (×2) | 0.249 s | there and back |
| `cliclick w:400` | 0.436 s | **deliberate 400 ms settle** |
| `osascript` window rect | 0.584 s | biggest single item |
| `cliclick m + w:120 + c` | 0.410 s | includes a deliberate 120 ms wait |
| **total** | **1.851 s** | sum 1.885 s, **unaccounted −0.034 s** |

The harness wrapped `guest_input._run`, so it timed the calls that *happened*
rather than the ones expected — anything it did not see would have shown up as
unaccounted. A breakdown that always sums to 100 % cannot find a missing
second, which is why it was built to be able to fail.

Two things fall out. **Four separate `osascript` invocations cost ~1.03 s**,
most of it interpreter start, so asking two questions in one script removes a
whole start. And **handing focus back costs 0.695 s on the NEXT gesture** —
37 % — because the emulator then has to be brought forward again (set
frontmost + the 400 ms settle + set back). In the documented loop *screenshot →
click → screenshot* that is paid at every step.

The restore is still the default. It exists because a stray click once landed
in the host's browser, and a driver that silently keeps the machine is worse
than a slow one. `--keep-front` / `keep_front=True` opts out for a run of
gestures; `guest_input.py front <app>` hands it back.

**Two side findings worth more than the timings.** The window origin was
`605,104` at the start of the session and `448,128` an hour later — it moved
while this very change was being measured, which is exactly why the rect is
re-read per gesture and never cached across them. And the process list taken
before and after showed the guest's Finder going `front=0` → `front=1`: proof
the click actually landed, rather than a number produced by a gesture that did
nothing. Take the process list around a measurement; a harness that measures a
no-op reports a beautiful, believable, wrong number.

## `noErr` from a no-reply Apple Event means DELIVERED, not done — 2026-08-04

`AESend(..., kAENoReply, ...)` returns as soon as the event is in the target's
queue. It says nothing whatever about the target. An application with no Apple
Event handler at all leaves the quit sitting there for ever, and the daemon's
`QUIT:` verb answered **`Quit OK`** while the app kept running — measured
against `AppleBridgeConfig`, which is exactly such an app.

The repair is the one this file keeps arriving at from new directions: **verify
by the artefact.** The process is gone, or it is not. Two things make that
verification honest rather than decorative:

- **The wait must yield.** A target starved of CPU can never process the quit,
  so a busy loop would report "still running" every time and prove only that it
  had not yielded. The daemon is cooperative; `WaitNextEvent` in the loop is not
  a nicety, it is what makes the answer mean anything.
- **The wait must be bounded.** An application that puts up *Save changes?* is
  never going away, and an unbounded loop would take the bridge with it.

Both directions verified live: `QUIT:ABwd` answered `Quit OK` in 0.15 s and the
process really was gone; `QUIT:ABcf` took 2.07 s and answered `STATUS:-1`,
*"quit event sent, but the app is STILL RUNNING 2s later"* — confirmed
independently by `PROCLIST`, not by the daemon's own account of itself.

The general form, for the next verb: **a send that cannot fail is not a
result.** `AESend` with `kAENoReply`, `PostEvent`, `PBVolumeMount` on a queue —
anything whose success means "handed over" needs a second question asked of the
world, or it will report success for a thing that never happened.

## A host-side window query can fail transiently — and I called it a loss — 2026-08-04

**Corrected the same evening; the first version of this note is kept below the
line because the mistake is the more useful half.**

What was measured, at 17:41: `BasiliskII` running, `visible` true, System Events
reporting **zero windows**, and `host/guest_input.py` refusing every gesture. All
true. What was written down was *"the emulator has lost its host window"* — a
standing condition — and two conclusions drawn from it: that the real mouse was
unavailable, and that `AppleBridgeConfig` could not be closed.

Both were wrong. At 17:46, with no intervening action, System Events reported
**one** window, `guest_input.py geometry` answered normally (origin 448,128, the
same as an hour earlier), and `PROCLIST` showed `AppleBridgeConfig` **gone** —
the Command-Q sent minutes before had worked; it was merely slower than the
single moment I looked. A host screenshot taken while the note was being written
showed the emulator window plainly on screen, which alone should have stopped
the sentence.

The failure is not the wrong reading. It is that **one sample of a changing
thing was reported as a state**, and then two further claims were built on top
of it without either being checked — the real mouse was never tried, and the
process list was never re-read. That is this file's own subject arriving from
the inside: a report on evidence that is not the thing it claims.

The practical rule: **before writing down that something is broken, sample it
twice, and check the claim you are about to derive rather than the observation
you started from.** A transient around a guest reboot looks exactly like a
failure if you only look once, and the difference costs nothing to establish.

What survives from the original note, unchanged and still worth having:

### The two capture paths are not redundant

`mac_host_screenshot` photographs a **window on this desk** and fails with
anything that happens to it — including a query that momentarily cannot see it.
`mac_screenshot` reads the guest's own framebuffer over the bridge and is
indifferent to all of that; it returned a perfect 1024×768 frame throughout,
while `PROCLIST`, `DISKINFO` and every command answered normally.

So when one of them returns nothing, **try the other before concluding anything
about the guest.** Here the guest was in no trouble whatsoever — the difference
between the two answers was entirely on the host side.

---

*Superseded — the original wording, 2026-08-04 17:41:*

## A guest can lose its host window and keep running — 2026-08-04

Observed while deploying 0.8d38: `BasiliskII` was running, `visible` was true,
and System Events reported **zero windows** for it. `host/guest_input.py`
refused every gesture — correctly, and that refusal is the whole reason the
front-app check exists.

What still worked, and this is the part worth keeping: the **daemon-side**
screenshot (`mac_screenshot`, the `screenshot` verb) returned a perfect 1024×768
frame, and `PROCLIST`, `DISKINFO` and every command answered normally. The
emulation and the bridge were untouched; only the host's SDL window was gone.

So the two capture paths are not redundant, they answer different questions.
`mac_host_screenshot` photographs a **window on this desk** and dies with it.
`mac_screenshot` reads the guest's own framebuffer over the bridge and survives
anything that happens to the host's window. When one returns nothing, try the
other before concluding the guest is in trouble — here it was in no trouble at
all.
