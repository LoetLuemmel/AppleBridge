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
  +4   2   flags           (bit0 enabled, bit1 checkmark, bit2 has-submenu, bit3 separator)
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

## Open questions for the owner (before any assembly)

1. **Reading `MenuList` `$0A1C` inside the jGNE filter** instead of `GetMenuBar()`
   (which allocates a handle — unwanted in the filter). `menuled` already scans
   `$0A1C`. Is the low-mem block's per-entry layout the SAME as the handle
   `GetMenuBar()` returns (header `lastMenu@0`/`lastRight@2`/`mbResID@4`, then
   `MenuHandle@0`/`menuLeft@4`), or is there a difference to account for?
2. **First-round field scope:** is title + item-text (+ the computed points) enough
   for v1, deferring enabled/checkmark/cmd-key/submenu to a second pass? Simpler
   records ship sooner.
