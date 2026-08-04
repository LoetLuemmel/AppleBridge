# MENUWALK — foreground menu-bar perception (Punkt 3, strawman)

**Status:** design strawman. **No daemon assembly written yet** — the 68k parts
(jGNE-fired walk, low-mem `MenuList` read) are gated on owner review of the two open
questions at the bottom. This doc is what the owner reviews and what the daemon verb
+ client are built against. Everything here is adjustable.

> Path note: `mac/*` paths are in THIS repo; the `apfelpilot/*` files referenced
> below (`bridge_client.py`, `eval_menu_tree.py`, the conductor) are the ApfelPilot
> client on the **Jetson**, outside this repo. This doc was authored Jetson-side and
> placed here for the owner's review.

## Why a foreground walk (measured, not assumed)

Three measurements on 2026-08-04, SimpleText frontmost:

1. `MACUITREE` → `{"windows": []}` — the daemon cannot see the front app's windows
   (WindowList is per-process).
2. `cortex.describe_screen` (gemma3:4b) confabulated an *About* dialog with File/
   Edit/Help + OK/Cancel — none on screen. The VLM is unusable for precise free-UI
   perception; the structured Toolbox path is necessary.
3. `MENU:Font:…`, `MENU:Sound:…`, `MENU:File:…` → `found=0`; `MENU:Edit:…` →
   `found=1 menuID=130 nItems=3 titleX=34`. The daemon's `GetMenuBar()` walk sees
   the daemon's **own** menu bar (Edit 130), not SimpleText's. Menus are
   per-process, exactly like windows. `main.c`'s MENU verb even documents it:
   *"OWN menus only: MenuSelect uses the CALLING process's menu list."*

So perceiving the front app's menus needs code running **in that app's context** —
the same problem the dialog DITL walk solved. Reuse that machinery.

## Reuse map — most of this already exists in the repo

| Piece | Where it already lives | Role here |
|---|---|---|
| Foreground-context one-shot walk fired from `jGNE` (`$029A`), targetA5-gated | `mac/journal/jgnepatch.a` (+ `dlgwalk.c` pattern) | fire `MenuWalk` in the front app's context |
| Live menu-list walk: header `lastMenu@0`, 6-byte entries `MenuHandle@0`/`menuLeft@4`; title at `*mh`+14; `menuID@0`, `menuWidth@2` | `mac/src/main.c` MENU verb (~3300) | enumerate menus |
| Item read: `CountMItems`, `GetMenuItemText` | `main.c` MENU verb | enumerate items |
| Rect model: `itemY = MBarHeight + 16*(i-1) + 8`; title point `titleX + menuW/2, 10` | `main.c` MENU verb | coordinates for HOSTMENU |
| `MenuList` low-mem `$0A1C` scan (no allocation, filter-safe) | `mac/menuled/applebridge_menuled.a:69,265` | read the list **inside** the jGNE filter |
| Shared-block + LIST-verb-reads-JSON pattern | `DP` block + `DLGTREE`/`dialog_tree` | `MB` block + `MENUTREE` |
| Cross-app menu **actuation** by name | Route-B `_MenuSelect` patch (`MSINSTALL`/`mspatch.a`, boot INIT) — "reaches a FOREIGN app's MenuSelect" | actuation may already be solved; **perception is the real gap** |

New work is therefore small: compose the proven jGNE walk with the proven menu
enumeration, writing into a new `MB` block, plus a `MENUTREE` read verb. Estimate:
an afternoon of assembly once the two questions are settled.

## `MB` block layout (strawman, mirrors `DP`)

All fields big-endian, written by `MenuWalk` (A5-free), read by the daemon.

```
Header
  +0   2   magic 'MB' ($4D42)
  +2   2   generation      (++ on each successful walk; the freshness signal, like DP)
  +4   2   menuCount       (# menu records)
  +6   2   itemCount       (# item records total)
  +8   2   truncated       (1 if any cap hit)
  +10  2   mbarHeight      (from $0BAA, so the daemon need not assume 20)
  +12  ...  menuCount * MENU_REC (40 B), then itemCount * ITEM_REC (32 B)

MENU_REC (40 B)
  +0   2   menuID
  +2   2   titleLeft       (menuLeft, screen x of the title)
  +4   2   titleWidth      (menuWidth)
  +6   2   itemFirst       (0-based index into the item array)
  +8   2   itemN           (# items in this menu)
  +10  2   flags           (bit0 enabled)
  +12  28  title           (Str, Pascal, len byte + up to 27 chars; truncate)

ITEM_REC (32 B)
  +0   2   menuIdx         (0-based owning menu)
  +2   2   itemIndex       (1-based, Menu Manager numbering)
  +4   2   flags           (bit0 enabled [0 when bit4], bit1 separator, bit2 text-truncated,
                            bit3 reserved, bit4 enabled-UNKNOWN — itemIndex>31, emit enabled:null)
  +6   2   cmdChar         (command-key char, 0 if none)
  +8   24  text            (Str, Pascal, len byte + up to 23 chars; truncate)
```

Caps (strawman): `MAX_MENUS 16`, `MAX_ITEMS 128`. `truncated=1` if exceeded — never
silently drop (the no-silent-caps rule).

itemY is **not** stored — the daemon/client computes `mbarHeight + 16*(itemIndex-1) + 8`
so the model lives in one place. (Separators before the target skew the 16px model;
`main.c` already flags this. First round: report it, don't correct it.)

## Verbs (mirror the DLG* set, and the DLGWALK ⊥ DLGARM protocol)

- `MENUARM <targetA5>` — arm the jGNE menu walk for the process whose CurrentA5 ==
  targetA5. Refused while a DLG walk/arm is active (shared `$029A` slot) and vice
  versa. One-shot.
- `MENUTREE` — read the `MB` block, emit JSON (below). No arm.
- `MENUWDISARM` — clear the `$029A` hook.

Client `menu_walk(target_a5)` = MENUARM → poll MENUTREE until `generation` advances →
MENUWDISARM, exactly like `dialog_walk`. A5 pinned once via `pin_foreground_a5()`
(already exists), cached for the app lifetime.

## `MENUTREE` JSON (what `bridge.menu_tree()` returns)

```json
{
  "installed": true, "armed": false, "walked": true,
  "generation": 7, "truncated": false, "mbar_height": 20,
  "menus": [
    {"id": 1, "title": "", "title_x": 6, "width": 20, "enabled": true,
     "title_point": [16, 10],
     "items": [
       {"index": 1, "text": "About SimpleText…", "y": 28, "point": [20, 28],
        "enabled": true, "checked": false, "cmd": null, "separator": false}
     ]},
    {"id": 2, "title": "File", "title_x": 34, "width": 24, "title_point": [46, 10],
     "items": [ {"index": 1, "text": "New", "y": 28, "cmd": "N", ...}, ... ]}
  ]
}
```

`title_point` / item `point` are the HOSTMENU coordinates (title = open the menu,
point = the item). Perception is confirmed ACTIONABLE only when `generation`
advanced (a genuinely fresh walk), same rule as `dialog_walk`.

## Actuation (design, not built here)

`select_menu {menu, item}` planner action → conductor resolves `menu`+`item` against
the perceived tree to `title_point`+item `point` → **HOSTMENU** (real mouse; a
menu's tracking loop polls the hardware pointer, so synthetic input can't drive it —
same rule as modal dialogs). The Route-B `_MenuSelect` patch is an alternative
cross-app actuator already in the tree; which to use is a separate decision. Note
the action-space rule: `select_menu` changes app state (its purpose) but must not be
able to cancel its own perception preconditions.

## Planner / conductor wiring (strawman, NOT applied to the live loop yet)

- planner grammar: add `select_menu` to the enum, with `{menu: str, item: str}`.
- conductor: a `perceive_menus(c, a5)` branch returning the menu tree; on a
  `select_menu` action, look up the title/item point and `host_menu(...)`.
- Kept out of the running `planner.py`/`conductor.py` until MENUWALK is real, so an
  untested verb can't reach an autonomous run.

## Resolved questions (owner review, channel 2026-08-04 11:11 / 12:04)

1. **`MenuList` `$0A1C` layout — SAME as the `GetMenuBar()` copy.** Enumerate with
   `lastMenu = *(short*)mp; for (off=6; off<=lastMenu; off+=6)` — `lastMenu` is the
   offset TO the last entry, inclusive; there is no count to compute. Take it
   verbatim. Read `$0A1C` low-mem directly (no `GetMenuBar` allocation in the filter).
2. **v1 field scope — title + item-text + points, PLUS `enabled`.** `enabled` MUST be
   in v1: a disabled item does nothing, and the conductor would read the click as
   "done" (the #196 rule one level deeper). `checkmark`/`cmd-key`/`submenu` deferred.

## Review incorporated (owner 12:04 — "Sie trägt")

- **Item read is a DIRECT menuData handwalk, not `GetMenuItemText`/`CountMItems`** —
  owner self-corrected: those drag in Interface.o glue (`dlgwalk.c:77` names
  `CountDITL` as the walk's *only* glue reference, avoided for exactly this). The
  handwalk keeps the resource A5-free / PC-relative.
- **MECHANICAL GATE — `DumpObj` the linked resource for ANY unresolved external.**
  That is how `CountDITL` was found. Applies to `GetHandleSize` too (the second
  bound): normally an inline trap, but "normally" stopped being proof after the
  `CountDITL` surprise. An empty externals list is the proof; if `GetHandleSize`
  drags in glue, fall back to the `lastMenu`-only bound.
- **`jgnepatch.a` IS the (standing-)dialog path (DLGWALK, #194), not untouched.**
  Only `dlgpatch.a` stays byte-identical. The `BSR DlgWalk → BSR Walk` change adds
  one indirection to the proven path → run `tests/test_dlgpatch_contract.py` AND
  extend it to the MB block, so the reuse of `oDP_Up(+14)`/`oDP_Gen(+16)` is a
  checked coupling, not a silent one (move DP's offsets and MB must break loudly).
- **`enableFlags` is 32-bit; item ≥ 32 falls off the long.** Reporting those as
  *disabled* is the inverse of the bug `enabled` prevents (Apple menu has >31 DAs).
  → `ITEM_REC.flags` **bit4** = enabled-UNKNOWN for `itemIndex > 31` (bit3 stays
  separator — kept DISTINCT, owner 12:11); bit0 forced 0 and MENUTREE emits
  `enabled: null`, so a client that does not know bit4 never reads "disabled" (the
  conductor is such a client). (Implemented in `menuwalk.c`.)
- **Known boundary — standard MDEF 0 only.** The menuData handwalk assumes the
  standard menu def; a custom MDEF lays items out differently and the walk would read
  garbage. The `menuHeight` guard catches part, not all. The apps we target (Finder,
  SimpleText, ToolServer) all use MDEF 0 — documented boundary, not built against.

## Build-time gates (before the reboot)

1. `DumpObj` the linked jGNE menu resource → externals list MUST be empty.
2. `tests/test_dlgpatch_contract.py` green, extended to the MB block. Assert the
   full THREE-WAY contract (owner 12:15 — the bit layout lives in `menuwalk.c` writer,
   `main.c` MENUTREE emitter, and this doc; nothing else holds them in agreement):
   - MB/MENU/ITEM offsets across `menuwalk.c` and `main.c`.
   - the ITEM_REC **bit assignment** parsed from all three sources → a SINGLE mapping
     (else the drift we just found by hand recurs silently).
   - `bit3` asserted RESERVED (so no one double-assigns it later).
   - `bit4 && bit0` asserted a FORBIDDEN combination (the one combo that would tell a
     bit4-unaware client "disabled" again).
3. Full suite green (a `vers.r` bump pulls doc-version obligations — `test_doc_claims`).
4. Post the built code to the owner for review, THEN the final reboot heads-up.
