# Key Modifiers and Menu Selection — Design

Status: **design** (2026-07-02). Companion to the input-injection implementation
(`mac/src/events.c`, verbs in `mac/src/main.c`). Motivated live: driving Microsoft
FoxPro over the bridge, a synthetic click could **not** open a menu — so we could
reach FoxPro's Command Window (which takes plain keystrokes) but not its menus.

## The gap — two related shortfalls

1. **No modifiers.** `mac_key`/`mac_type` inject *unmodified* characters only. So
   Command-key shortcuts (Cmd-Q quit, Cmd-W close, Cmd-S save, and every app's menu
   equivalents) are unreachable, as are Option/Shift-modified inputs.
2. **A click can't open a menu.** Selecting a menu item is not a click — it is a
   *modal tracking loop*: on a mouse-down in the menu bar the **front app** calls
   `MenuSelect`, which spins reading the live mouse until release. That loop runs in
   the app's process; the background daemon is not scheduled during it, so a lone
   posted mouse-down/up never drives it to an item. (Same reason the daemon can't
   click its own Verbose window.) Proven live: a click on FoxPro's *Fenster* menu
   did nothing.

## Why "select a menu item by name" is the *hard* path

The obvious API — `mac_menu("Fenster", "Befehl")` resolved daemon-side to the item's
Cmd-key equivalent — **cannot be implemented that way**, because:

- The daemon is a **separate process**. Under the Process Manager the `MenuList` is
  per-process; when the daemon is scheduled, the current menu list is the daemon's
  own (it is faceless — effectively empty), **not** the front app's. The daemon
  therefore cannot enumerate FoxPro's menu titles, items, or command-key equivalents.
- Even with cross-process access, many useful items have **no** Cmd-key equivalent,
  so resolution-to-shortcut wouldn't cover them.

So name-based menu selection needs either (a) the *caller* to already know the
shortcut, or (b) a mechanism that navigates `MenuSelect` **visually**, without
reading the menu structure — a journaling hook (Phase 2).

The pragmatic, high-value step is therefore **modifier injection**, not a menu verb.

## Phase 1 — Key modifiers (recommended first)

Expose Command/Option/Shift/Control on key injection. Then *every Cmd-key menu
shortcut becomes reachable* — Cmd-Q, Cmd-W, Cmd-S, and app-specific ones the agent
can simply **read off the (screenshotted) menu**, since the shortcut is printed next
to the item. This addresses the bulk of "drive the menus" with no cross-process
menu access and no modal-loop trickery.

### Wire + MCP surface

- Extend the `KEY:` verb to carry an optional modifier mask:
  `KEY:<charCode>:<keyCode>:<modifiers>` — `modifiers` defaults to `0`; it is the sum
  of the classic Event Manager modifier bits: **cmdKey 256, shiftKey 512,
  optionKey 2048, controlKey 4096**. Old callers (`KEY:cc:kc`) are unchanged.
- MCP: add a `modifiers` argument to **`mac_key`** (e.g. `["cmd"]`, `["cmd","shift"]`).
  Optionally a thin sugar tool `mac_shortcut("cmd-q")` that maps to the same verb.
  `mac_type` stays modifier-free (it is for text).

### Daemon mechanism (`events.c`)

`InjectKey(charCode, keyCode, modifiers)`:

1. For each requested modifier, **set its key's bit in the low-memory `KeyMap`** (the
   128-bit keyboard-state bitmap the Event Manager reads). `PostEvent` supplies only
   `(what, message)`; the `EventRecord.modifiers` the app sees are computed by the
   Event Manager from the *current* key state at `GetNextEvent` time — so faking the
   modifier means holding its `KeyMap` bit while the app reads the event.
2. Post `keyDown` (through the existing `PostEventRetry`, so it stays lossless).
3. **Hold** the modifier across ~2 `SystemTask` yields, so the front app's
   `WaitNextEvent` returns the `keyDown` with the modifier present and calls
   `MenuKey`/dispatches it.
4. Post `keyUp`; then **clear** the `KeyMap` bits (restore keyboard state).

Modifier virtual keycodes: **Command `0x37`, Shift `0x38`, CapsLock `0x39`,
Option `0x3A`, Control `0x3B`**. `KeyMap` low-memory global `KeyMapLM` (`0x0174`),
16 bytes.

**To verify on-device (do NOT ship unverified):** the Mac `KeyMap` byte/bit ordering
is notoriously non-obvious (it is *not* a plain `byte[keycode>>3] & (1<<(keycode&7))`
in the intuitive order). Confirm empirically: inject **Cmd-Q** at a scratch front app
(e.g. SimpleText) and confirm it quits; inject **Cmd-Q** at FoxPro; confirm an
*unmodified* key still lands plain afterwards (the bit was cleared). Only then wire it
into the MCP surface.

### Caveats

- `KeyMap` is **global** keyboard state — hold the modifier only for the brief inject
  window, then restore, so it does not leak into subsequent input.
- The daemon is faceless/background, so it does not consume the event itself — the
  front app does. Confirm during verification.

## Phase 2 — Menu selection by name (future, journaling)

For items **without** a Cmd-key equivalent, a `MENU:<title>:<item>` verb backed by an
Event Manager **journaling playback** hook (`jGetMouse`/`jGetKeys` — the classic
QuicKeys/macro technique). The hook is consulted by `GetNextEvent`/`MenuSelect`
*regardless of process*, so it can feed `MenuSelect` a synthesized mouse path
(menu-bar title → down to the item → release) **inside** its modal loop — the one
thing posted events can't do — and it needs no cross-process menu read (it navigates
by position, matching the item visually or by counting). Higher risk and complexity;
deferred until a real workflow needs a shortcut-less item.

## Recommended sequence

1. **Phase 1 modifiers** — small, high-leverage, unlocks Cmd-key menu shortcuts.
   Build + verify on Basilisk (Cmd-Q self-test), then expose in `mac_key`.
2. **Phase 2 journaling menu verb** — only if a needed menu item lacks a shortcut.

This mirrors the project's usual order: design → on-device verify the fiddly low-mem
detail (here, the `KeyMap` bit layout) → wire the MCP surface. See the roadmap
ledger's *Input-injection completeness* item.
