"""
AppleBridge MCP Tools
Tool implementations for classic Mac development.
"""

import base64
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

from .mac_connection import get_connection

# host/ holds the stdlib-only macbinary helper; make it importable regardless of
# how the MCP server is launched (mirrors how host_server.py imports it flat).
_HOST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host")
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)
import macbinary  # noqa: E402
import bridge_doctor  # noqa: E402  (stdlib-only host-side stack probes)
import guest_input  # noqa: E402  (real-mouse driving in guest coordinates)
import mpw  # noqa: E402  (build-step verification: the artefact is the oracle)
import loop_guard  # noqa: E402  (repetition made visible for a model-driven loop)
import pump_probe  # noqa: E402  (is the target reading, before a no-reply send)
import c89_lint  # noqa: E402  (name the C99 habits MPW's 1994 compiler rejects)


def _ostype(value, default="????") -> bytes:
    """Coerce a 4-char type/creator string to exactly 4 space-padded bytes."""
    s = value if value else default
    if isinstance(s, bytes):
        return s[:4].ljust(4, b" ")
    return (s.encode("mac_roman", errors="replace") + b"    ")[:4]

# Tool definitions for MCP
TOOLS = [
    {
        "name": "mpw_execute",
        "description": """Execute a command in MPW/ToolServer on the classic Mac.

The raw escape hatch: the command is sent exactly as written and never
rewritten. That also means `status: 0` here says only that the Apple Event was
DELIVERED — an MPW tool's own exit status never crosses the bridge, and its
stderr stays inside ToolServer. `SC`, `Asm`, `Link`, `Rez` and `SetFile` print
nothing on success and nothing on failure, so an empty reply from one of them
is not evidence of anything. A `hint` field is attached when that applies.

To get a real answer, either verify the artefact (`Exists <path>`), or capture
diagnostics with MPW's `≥` operator — as TWO commands, never one line:

    SC file.c -o file.o ≥ err.txt        (one call)
    Catenate err.txt                     (the next call)

Both on one line comes back empty. Never `2>&1`; it crashes the shell. For a
compile, prefer `mac_compile`, which does all of this and judges by the object
file.

Use MPW syntax:
- Paths use : separator (e.g., "MeinMac:Folder:File.c")
- Common commands: Directory, Files, Echo, SC (compile), ILink (link)
- ToolServer returns stdout; MPW Shell replies empty (its output goes to the
  Worksheet window instead)

Examples:
- Directory - show current directory
- Files "MeinMac:Temp:" - list files
- Echo "hello" > "MeinMac:Temp:test.txt" - write file""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "MPW command to execute"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30)",
                    "default": 30
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "mac_write_file",
        "description": """Write a text file to the Mac filesystem.

Path uses : separator (e.g., "MeinMac:Temp:myfile.c").
Content is converted to MacRoman with CR line endings.

Goes through the daemon's native WRITEFILE verb, so it needs no ToolServer
and multi-line content is safe. Returns the byte count actually sent, which
differs from len(content) when a character does not survive MacRoman.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Mac path (using : separator)"
                },
                "content": {
                    "type": "string",
                    "description": "File content to write"
                },
                "type": {
                    "type": "string",
                    "description": "4-char file type (default TEXT)"
                },
                "creator": {
                    "type": "string",
                    "description": "4-char creator (default 'MPS ', the MPW editor)"
                }
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "mac_read_file",
        "description": """Read a text file from the Mac filesystem.

Path uses : separator. Returns file content as string.
Uses MPW's Catenate command internally.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Mac path to read (using : separator)"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "mac_list_files",
        "description": """List files in a Mac directory.

Returns detailed file listing including type, creator, size, and dates.
Path uses : separator and should end with : for directories.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Mac directory path (using : separator, ending with :)"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "mac_compile",
        "description": """Compile a C source file with MPW's SC, verified by the object file.

`success` means the object file is on disk afterwards — NOT that the command
returned 0. SC is silent on success and on failure, and its exit status cannot
cross the bridge, so the artefact is the only honest oracle.

Returns `verified` (was a check possible at all), `errors` and `warnings` from
the compiler's own diagnostics, and `remedies` — the project rule that applies
to a diagnostic, e.g. a source file with no TEXT type (the usual result of
Duplicate out of `Unix:`) names the `SetFile -t TEXT -c 'MPS '` fix.

When no object appeared, and only then, ToolServer is probed once: the
resulting `toolserver_alive` separates "ToolServer is gone" from "ToolServer is
alive and rejected your input".

Output defaults to source.o (foo.c -> foo.o). Passing -o inside `options` hides
the path from this tool, which then reports `verified: false` rather than
checking a guess.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Path to C source file (using : separator)"
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional output path for object file"
                },
                "options": {
                    "type": "string",
                    "description": "Additional compiler options"
                },
                "lint": {
                    "type": "boolean",
                    "description": "Run the C89 pre-check before compiling "
                                   "(default true). Set false for the control "
                                   "arm of a with-lint/without-lint measurement; "
                                   "the value is echoed in the result, so the "
                                   "arm is readable from the trace afterwards."
                }
            },
            "required": ["source_path"]
        }
    },
    {
        "name": "mac_screenshot",
        "description": """Capture a screenshot of the emulated Mac screen.

Returns a base64-encoded PNG of the current desktop. Pass `region` as
[x, y, width, height] (screen pixels, origin top-left, screen is 1024x768) to
capture only that rectangle — the guest crops BEFORE the transfer (daemon
0.8d46+), so a dialog-sized region costs a fraction of a full frame on the
bridge, not just in the reply. Consecutive full-screen captures send only the
rows that changed (row delta), so the second look at an unchanged screen is
nearly free. The reply's `encoding` (raw/packbits/delta), `wire_bytes` and
`elapsed_ms` say what the capture cost on the guest->host leg.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "Optional crop [x, y, width, height] in screen pixels"
                }
            },
            "required": []
        }
    },
    {
        "name": "launch_app",
        "description": """Launch a GUI application on the classic Mac and bring it to the FOREGROUND.

Uses the daemon's LAUNCH verb (Process Manager LaunchApplication). Unlike
mpw_execute via ToolServer, this actually foregrounds a GUI app.

Path uses : separator and points at the application file, e.g.
"MeinMac:MPW:FortPoC:FortPoC".""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Mac path to the application (using : separator)"
                },
                "document": {
                    "type": "string",
                    "description": "Optional Mac path of a document the app opens at launch (odoc in the launch parameters; daemon 0.8d47+). Use it for apps whose cold start is a modal file picker, e.g. a THINK C project."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "mac_type",
        "description": """Type text into the FRONT application on the classic Mac.

Injects keystrokes (keyDown/keyUp per character) into the OS event queue, which
the Process Manager delivers to whatever app is frontmost — e.g. one just
brought up with launch_app. Pairs with mac_screenshot to drive-and-verify a GUI.

Text only (no Command/Option/Shift modifiers — use mac_key for special keys).
Include a carriage return (\\r) to press Return. Bounded to 1024 chars/call.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Characters to type into the front app"
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "mac_key",
        "description": """Press one key in the FRONT application on the classic Mac.

Injects a single keyDown/keyUp into the OS event queue — for keys mac_type can't
express as plain text (Return, Tab, Escape, the arrows, Delete, ...).

Give EITHER `key` (recommended) — a named special key or a single character — OR
the raw `char_code` (+ optional `key_code`). Named keys: return, enter, tab,
escape, space, delete, forwarddelete, left, right, up, down, home, end, pageup,
pagedown, help, f1..f12. Pass `modifiers` to hold Command/Shift/Option/Control —
e.g. ["command"]. For a menu command it's usually clearer to call mac_menu.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Named special key (return/tab/escape/left/right/up/down/delete/home/end/pageup/pagedown/f1..f12/...) or a single character. Preferred over char_code."
                },
                "char_code": {
                    "type": "integer",
                    "description": "Raw ASCII/MacRoman character code (alternative to `key`; e.g. 13 = Return)"
                },
                "key_code": {
                    "type": "integer",
                    "description": "Virtual key code — the PHYSICAL key. Optional: derived from the character by default. Only pass it to override (e.g. a non-US keyboard position)."
                },
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Modifier keys to hold: any of command/cmd, shift, option/alt, control/ctrl, caps (e.g. [\"command\"])"
                }
            },
            "required": []
        }
    },
    {
        "name": "mac_menu",
        "description": """Invoke a menu command. Two modes:

1) FRONT-app, by Command-key (`key`): selecting a menu item is a modal
   mouse-tracking loop inside the front app that a synthetic click can't drive,
   so the reachable path is the menu's KEYBOARD equivalent. This injects
   Command+<key> (add Shift/Option via `modifiers`), which the front app
   dispatches through MenuKey. `key` is the single character shown next to the
   item (read it off a screenshot): "Q" to Quit, "W" to close, "N" for New.
   Items with NO Command-key equivalent can't be reached this way.

2) BY NAME (`title` + `item`): journal-drives a menu on the DAEMON's OWN menu
   bar (Apple / Edit) — matching the menu title and the item by name (or item
   by 1-based index), then dispatching it. This is the ONLY way to reach a
   shortcut-LESS item, but it works only on the daemon's own menus: a background
   daemon's MenuSelect uses its own menu list and can't reach a front app
   (proven by the JPROBE spike). Use it for the daemon's own commands, e.g.
   title="Edit", item="Copy" (copies the Verbose log to the clipboard) or
   item="Show details". Reports the resolved menu id + selected item.

Give EITHER `key` OR (`title` and `item`).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "FRONT-app mode: single character of the menu item's Command-key equivalent (e.g. Q, W, N, S)"
                },
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extra modifiers beyond Command (e.g. [\"shift\"] for a Cmd-Shift item). Command is always included."
                },
                "title": {
                    "type": "string",
                    "description": "BY-NAME mode: menu title on the daemon's own menu bar (e.g. \"Edit\"). Requires `item`."
                },
                "item": {
                    "type": "string",
                    "description": "BY-NAME mode: menu item name (e.g. \"Copy\", \"Show details\") or 1-based index. Requires `title`."
                }
            },
            "required": []
        }
    },
    {
        "name": "mac_menu_front",
        "description": """Drive the FRONT app's menu via the Route B global MenuSelect trap patch -- the one path that reaches a FOREIGN front application's shortcut-less menu (mac_menu's by-name mode drives only the daemon's OWN bar).

Orchestrates the proven pieces: MSINSTALL (adopt the boot INIT's global patch, found by heap scan) -> MSDRIVE (arm the patch to return the target on the next MenuSelect) -> host cliclick a menu-bar title (so the front app calls MenuSelect; the patch returns the item with NO tracking loop, no journal, no window reorder, so no host crash) -> MSREAD (confirm the interception fired).

Requirements & limits: the `ABMenuInit` boot extension must be installed and the guest rebooted (a trap patch is global ONLY when installed at startup; an app-installed one is process-local). `menu_id`/`item` are NUMERIC -- the target app's real menu id + 1-based item; resolving a foreign app's menu BY NAME needs reading its menu list and is not wired here. The host trigger uses `cliclick` + `osascript` with BasiliskII visible locally -- LOCAL Basilisk only.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "menu_id": {
                    "type": "integer",
                    "description": "The front app's real menu ID (numeric) to dispatch."
                },
                "item": {
                    "type": "integer",
                    "description": "1-based item index within that menu."
                },
                "menu_x": {
                    "type": "integer",
                    "description": "Guest x of a menu-bar title to click as the trigger (default 45; any title works -- the armed patch returns the target regardless of where clicked)."
                }
            },
            "required": ["menu_id", "item"]
        }
    },
    {
        "name": "mac_click",
        "description": """Click at a point in the FRONT application on the classic Mac.

Moves the emulated mouse to (x, y) in global screen coordinates and posts a
mouse-down/up there, poking the low-memory button state so tracked controls
(buttons, menus) register a real press. Pair with mac_screenshot to read a
dialog, then click its button. Coordinates are screen pixels (origin top-left;
the screen is 1024×768).

Pass `count` = 2 for a double-click (open a Finder item, select a word) or 3 for
a triple-click. Pass `modifiers` for a shift-click (extend a selection) or
command-click (multi-select).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Horizontal screen coordinate (pixels)"},
                "y": {"type": "integer", "description": "Vertical screen coordinate (pixels)"},
                "count": {"type": "integer", "description": "Click count: 1 (default), 2 = double-click, 3 = triple-click"},
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Modifier keys to hold during the click: command/cmd, shift, option/alt, control/ctrl (e.g. [\"shift\"])"
                }
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "mac_status",
        "description": """Report AppleBridge liveness — instead of the opaque "Mac not connected".

Surfaces state the host and daemon already track, and answers even when the
daemon is down (so you can tell WHICH layer is broken):
  - host_server_running — is the host control port reachable
  - daemon_connected     — does the host have a live daemon socket
  - daemon_responding    — did the daemon answer a STAT this call
  - toolserver_running   — is ToolServer ('MPSX') alive (mpw_execute needs it)
  - idle_seconds / missed_heartbeats — link freshness
  - link_id ("<epoch>:<n>") — identifies THIS daemon link. It changes on every
    reconnect, and across a host-server restart too. Everything else here
    (uptime, rx, tx, err) is cumulative for the daemon PROCESS and continues
    unchanged through a redial, so link_id is the only field that answers "is
    this still the connection my long-running work started on?" Capture it
    before slow work and compare afterwards; a different value means whatever
    was in flight was orphaned.
  - rx_count / tx_count / err_count — daemon counters (err = STATUS != 0 responses)
  - last_latency_ms / uptime_seconds — last command's RX->TX time; daemon uptime

Diagnostic shortcut: daemon_connected but not toolserver_running => commands
will come back empty; daemon not connected => the bridge/emulator is down.""",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "mac_host_click",
        "description": """Click the guest's REAL mouse — for controls synthetic clicks cannot reach.

Menus, Standard File lists and other modal tracking loops POLL the hardware
pointer, so mac_click (which sets the low-memory mouse for an instant) never
reaches them. This moves the host's own cursor instead. LOCAL emulator only — a
remote guest has no host cursor to borrow.

Coordinates are GUEST coordinates: take a mac_screenshot, read the pixel
position of the target off that image, and pass it here unchanged — the capture
IS the guest framebuffer, so its pixels map 1:1.

Refuses rather than acts when the point lies outside the emulated screen, or
when the emulator cannot be brought to the front; either would put the click
into another application. Brings the emulator forward and restores the previous
front app afterwards.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Guest X (from a mac_screenshot image)"},
                "y": {"type": "integer", "description": "Guest Y (from a mac_screenshot image)"},
                "count": {"type": "integer", "description": "Clicks at that point (2 = double-click)"},
                "keep_front": {"type": "boolean", "description": "Leave the emulator frontmost instead of handing focus back. Saves ~0.7 s on EVERY following gesture (measured 2026-08-04: 1.85 s -> 1.16 s), because handing focus back means the next gesture must take it again. Use for a RUN of gestures; the last one should omit it, or the host machine stays on the emulator."},
                "modifiers": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Held during the click: cmd, shift, option/alt, control/ctrl"
                }
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "mac_host_menu",
        "description": """Pull down a menu with the REAL mouse: press on the title, release on the item.

The only reliable way to drive a menu in an arbitrary front app: MenuSelect is a
tracking loop that polls the hardware pointer, and mac_menu's Command-key path
only works for items that HAVE a shortcut.

Both points are GUEST coordinates read off a mac_screenshot. Issued as ONE
gesture — press, drag, release in a single motion. There is deliberately no
"open the menu and look around" mode: a menu left open blocks the guest's event
loop, which starves the background daemon and drops the bridge for ~30 s. So
the item's position must be known BEFORE the call (from an earlier capture of
that menu, or the app's known layout).

LOCAL emulator only. Refuses if either point is off-screen.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title_x": {"type": "integer", "description": "Guest X of the menu title in the menu bar"},
                "title_y": {"type": "integer", "description": "Guest Y of the menu title (~9 for the menu bar)"},
                "item_x": {"type": "integer", "description": "Guest X inside the dropped-down item"},
                "item_y": {"type": "integer", "description": "Guest Y of the item"},
                "keep_front": {"type": "boolean", "description": "Leave the emulator frontmost instead of handing focus back. Saves ~0.7 s on EVERY following gesture (measured 2026-08-04). Use for a RUN of gestures; the last one should omit it."}
            },
            "required": ["title_x", "title_y", "item_x", "item_y"]
        }
    },
    {
        "name": "mac_host_screenshot",
        "description": """Capture the guest screen HOST-side — works while the daemon is blocked.

mac_screenshot streams the framebuffer from the daemon, so it returns nothing
precisely when a modal dialog or an open menu owns the machine — which is when
a picture is most needed. This grabs the emulator's window from the host
instead, so it answers regardless.

Use mac_screenshot normally (it is the guest's own framebuffer, unaffected by
host window stacking); reach for this one when the bridge is stalled or the
guest is inside a tracking loop. `region` is [x, y, w, h] in guest coordinates.

LOCAL emulator only. Note the emulator window must be visible — an obscured
window captures whatever covers it.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "array", "items": {"type": "integer"},
                    "description": "Optional crop [x, y, width, height] in guest coordinates"
                }
            },
            "required": []
        }
    },
    {
        "name": "mac_appletalk_browse",
        "description": """List the AppleTalk entities the classic Mac can see — headless, no Chooser.

This is the Chooser's list (file servers, printers, other Macs) obtained via an
NBP name lookup in the daemon. It needs neither ToolServer nor GUI driving: the
Chooser's own list is built by a modal tracking loop a background daemon cannot
reach, and opening it host-side means taking over the real mouse.

  entity_type — NBP type. Default "AFPServer" (what the Chooser's AppleShare
                icon shows). Others: "LaserWriter", "Workstation", or "=" for
                every type on the network.
  zone        — AppleTalk zone; default "*" (this Mac's own zone, which on a
                single-zone network is the whole network).
  name        — entity name; default "=" (all names of that type).

Returns `entities`, each with name / type / zone / address (net.node.socket).
Takes ~3 s: NBP always runs its full retry window before it can answer.

An empty list means nothing answered; AppleTalk being switched off is reported
as an explicit error instead, since the two call for very different fixes.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_type": {
                    "type": "string",
                    "description": "NBP entity type (default \"AFPServer\"; \"=\" for all types)"
                },
                "zone": {
                    "type": "string",
                    "description": "AppleTalk zone (default \"*\" = this Mac's own zone)"
                },
                "name": {
                    "type": "string",
                    "description": "Entity name to match (default \"=\" = all names)"
                }
            },
            "required": []
        }
    },
    {
        "name": "bridge_doctor",
        "description": """Diagnose the WHOLE stack in one call — use this the moment anything looks down.

mac_status only sees the control port and the daemon link, so every deeper
cause reports identically as "not connected". This probes the layers beneath
and, because it runs host-side in this process, it still answers when the host
server itself is dead:

  - launchd job (loaded / absent / explicitly disabled)
  - listening sockets :9000 / :9001, plus the guest's OBSERVED peer IP
  - where the .154 alias lives vs. the default-route interface (a duplicate on
    a second NIC splits the MACNAT return path and freezes the emulator)
  - BasiliskII / SheepShaver and the etherhelpertool child (dead helper =>
    the guest has no NIC at all)
  - the emulator's "ether" backend vs. the intended one (slirp still passes
    TCP, so the bridge looks fine — but it drops AppleTalk, so the Chooser
    finds no AppleShare server, and bulk throughput falls ~80 %)

Returns `verdict` (ok/info/warn/error), a ranked `findings` list where each
entry carries a literal `fix` command, the raw `probes`, and a preformatted
`text` report. Also merges mac_status when the control port is reachable.""",
        "inputSchema": {"type": "object", "properties": {}, "required": []}    },
    {
        "name": "mac_build",
        "description": """Build a 68K project on the Mac in ONE verified call.

Folds the multi-step MPW recipe — SC compile (each .c, stderr via the safe '≥'
redirect) -> Link -> optional Rez -> SetFile -> verify-by-artifact — into a
single tool that returns structured pass/fail and parsed diagnostics. Verifies
by checking the artifact exists (a long Link can return -1712 yet still succeed),
not by status code.

project_dir is a Mac path to the folder holding the .c sources (trailing ':'
optional). By default every .c in it is compiled; pass `sources` to choose. On
failure the result names the stage (compile/link/rez) and the offending file's
errors. Set `run` to launch the result (foreground) afterwards.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string", "description": "Mac path to the project folder (holds the .c files)"},
                "app_name": {"type": "string", "description": "Output app name (default: last path component of project_dir)"},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Specific .c files to compile (names or full paths); default = all .c in project_dir"},
                "libraries": {"type": "array", "items": {"type": "string"}, "description": "Link libraries (default: Interface.o, MacRuntime.o, StdCLib.o)"},
                "rez_file": {"type": "string", "description": "Optional .r resource file to Rez onto the output (e.g. a SIZE resource)"},
                "file_type": {"type": "string", "description": "4-char file type for SetFile (default APPL)"},
                "creator": {"type": "string", "description": "4-char creator for SetFile (default ????)"},
                "model": {"type": "string", "description": "Link memory model (default far)"},
                "run": {"type": "boolean", "description": "Launch the built app (foreground) after a successful build"}
            },
            "required": ["project_dir"]
        }
    },
    {
        "name": "mac_send_apple_event",
        "description": """Send an arbitrary Apple Event to a scriptable app and return its reply.

Generalises the bridge beyond ToolServer: address any running app by 4-char
creator, with a 4-char event class + event id and an optional text direct
object, and harvest the reply text. Examples:
  - DoScript to ToolServer:  target='MPSX', class='misc', id='dosc', direct_object='Files'
  - Quit an app:             target='ttxt', class='aevt', id='quit'
  - Run an AppleScript-aware app's custom verb, etc.

4-char codes shorter than 4 are space-padded (Mac convention, e.g. 'MPS '). The
target app must be running. Returns the reply in `reply`.

WAITING IS A CHOICE, AND IT COSTS THE GUEST. System 7 schedules cooperatively:
while the daemon waits for a reply, an application that is not yielding holds
the whole machine, bridge included. Read the target's vocabulary first
(`host/tools/guest_explore.py aete <path>`) — if the event declares its reply
'null', as KAHL/RUN and KAHL/MAKE do, pass expect_reply=false and nothing can
block. Otherwise wait_seconds bounds it (default 30, max 180).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_creator": {"type": "string", "description": "4-char creator of the target app (e.g. MPSX, ttxt)"},
                "event_class": {"type": "string", "description": "4-char event class (e.g. misc, aevt, core)"},
                "event_id": {"type": "string", "description": "4-char event id (e.g. dosc, quit, getd)"},
                "direct_object": {"type": "string", "description": "Optional text direct object (e.g. a script or property spec)"},
                "expect_reply": {"type": "boolean", "description": "False = send kAENoReply and return at once (correct when the aete says reply 'null'; cannot block the guest). Default true."},
                "wait_seconds": {"type": "number", "description": "How long the DAEMON may block waiting for the reply, 1-180. Default 30 (interactive). Ignored when expect_reply is false."}
            },
            "required": ["target_creator", "event_class", "event_id"]
        }
    },
    {
        "name": "mac_clipboard_get",
        "description": """Read the classic Mac's clipboard (the 'TEXT' scrap).

Returns the guest clipboard text. Basilisk II mirrors this scrap with the host
pasteboard, so it doubles as a host<->guest text side-channel — handy for small
payloads when a file transfer is overkill.""",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "mac_clipboard_set",
        "description": """Set the classic Mac's clipboard (the 'TEXT' scrap) to `text`.

After this, the guest can Paste the text anywhere; Basilisk II also mirrors it
to the host pasteboard. Text only; intended for small payloads (bounded ~8 KB —
use mac_put_file for anything large).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to place on the Mac clipboard"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "mac_put_file",
        "description": """Copy a BINARY file from the host to the classic Mac, preserving both forks.

Unlike mac_write_file (text only, via Echo), this streams a real binary file
including its RESOURCE fork — so it can deploy a runnable 68K application (whose
CODE lives in the resource fork), an image, or any resource file.

If host_path is a MacBinary file (e.g. a Retro68 .bin), its forks + type/creator
are used automatically. Otherwise the host file is the data fork; pass type and
creator (4-char codes, e.g. APPL/Add5) to make a launchable app, and an optional
resource_path for a separate resource fork.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host_path": {"type": "string", "description": "Path to the file on the host"},
                "mac_path": {"type": "string", "description": "Destination Mac path (: separator)"},
                "type": {"type": "string", "description": "4-char file type (e.g. APPL, TEXT). Ignored if host_path is MacBinary."},
                "creator": {"type": "string", "description": "4-char creator code (e.g. Add5). Ignored if host_path is MacBinary."},
                "resource_path": {"type": "string", "description": "Optional host file whose bytes become the resource fork (flat-input case)."}
            },
            "required": ["host_path", "mac_path"]
        }
    },
    {
        "name": "mac_get_file",
        "description": """Copy a file FROM the classic Mac to the host, preserving both forks.

Pulls the data fork, resource fork, and type/creator. By default writes the data
fork to host_path and, when a resource fork is present, also writes a re-deployable
MacBinary alongside it (format='macbinary' forces a .bin, format='data' forces raw
data-fork only).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mac_path": {"type": "string", "description": "Source Mac path (: separator)"},
                "host_path": {"type": "string", "description": "Destination path on the host"},
                "format": {"type": "string", "description": "auto | data | macbinary (default: auto)"}
            },
            "required": ["mac_path", "host_path"]
        }
    },
    {
        "name": "mac_restart_toolserver",
        "description": """(Re)launch ToolServer on the classic Mac.

ToolServer is what actually executes mpw_execute / mac_compile commands; if it
crashes or is quit, commands come back empty or as 'no-ToolServer/MPW' and the
bridge looks hung. This relaunches it via the LAUNCH verb (no ToolServer needed
to do so) and verifies with an Echo. Default path MeinMac:MPW:ToolServer.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Mac path to ToolServer (default MeinMac:MPW:ToolServer)"}
            },
            "required": []
        }
    },
    {
        "name": "mac_verbose_log",
        "description": """Read the daemon's on-screen Verbose console log over the bridge.

Returns the monitor window's rolling text ring (the last ~60 lines: command lines,
their output, and the AE trace) as plain text — the reliable way to see what the
daemon logged. Prefer this over mac_screenshot for reading the log: the monitor
window is fragile to scroll and a screenshot only catches one screen-full. Pass
max_bytes (>0) to fetch only the last N bytes.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_bytes": {"type": "integer",
                              "description": "If >0, return only the last N bytes of the log (default: whole ring)"}
            },
            "required": []
        }
    },
    {
        "name": "mac_reboot",
        "description": """Restart the emulated classic Mac (System 7).

Sends the Finder restart Apple Event — the programmatic equivalent of
Special > Restart. Use it to re-activate a freshly built/swapped daemon without a
manual reboot. The bridge connection drops; the watchdog brings the daemon back
up on boot. After calling this, poll mpw_execute (e.g. Echo) until it answers
again before continuing.""",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "mac_shutdown",
        "description": """Cleanly power off the emulated classic Mac (System 7).

The daemon triggers the Shutdown Manager (ShutDwnPower) in-process — the equivalent
of Special > Shut Down. It flushes the disk volumes and powers the machine off, so
under Basilisk II the emulator then quits on its own. Use this to stop the guest —
NEVER hard-kill the emulator process, which risks an unclean HFS unmount and a
corrupted disk image.

Unlike mac_reboot, the daemon does NOT come back: the machine is off, and the bridge
connection drops for good until the emulator is launched again. The connection
dropping mid-shutdown is expected, not an error.""",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "mac_update_daemon",
        "description": """Self-update the running AppleBridge daemon, over the bridge.

Replaces the running daemon binary without a manual Shift-boot / Finder swap and
without ToolServer. The installer can't do this — it copies, and the OS locks the
running file — but the daemon can RENAME itself (renaming an open file is allowed),
so this stages the new binary beside it and has it rename the staged copy into
place. Steps: stage host_path (a fork-aware MacBinary of the new daemon, e.g. from
mac_get_file) as '<install>/AppleBridge new', then send SWAPSELF.

It does NOT reboot. After it returns success, call mac_reboot so the watchdog
launches the new binary, then verify the daemon's vers. mac_dir defaults to the
daemon's reported install folder (mac_status home=).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host_path": {"type": "string", "description": "Host path to the new daemon binary (fork-aware MacBinary, e.g. produced by mac_get_file)"},
                "mac_dir": {"type": "string", "description": "Mac install folder holding the running daemon (default: the daemon's reported home=)"},
                "staged_name": {"type": "string", "description": "Staging leaf name in that folder (default 'AppleBridge new'); must be '<daemon name> new'"}
            },
            "required": ["host_path"]
        }
    },
    {
        "name": "run_applescript",
        "description": """Run AppleScript on the HOST (macOS) via osascript.

This drives the *host*, not the guest — most usefully the BasiliskII emulator
window: e.g. type the AppleShare password at boot (keystroke "pit" then Return),
raise/activate the window, etc. It does NOT run AppleScript inside System 7.
First use may require Accessibility/Automation permission for the MCP process.

Example to type the boot password:
  tell application "System Events"
    set frontmost of process "BasiliskII" to true
    delay 0.5
    keystroke "pit"
    key code 36
  end tell""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "AppleScript source to run on the host"}
            },
            "required": ["script"]
        }
    }
]


def _mpw_execute_hint(command: str, stdout) -> Optional[str]:
    """What this particular command is about to be misread as, if anything.

    Both cases below cost a session a day on 2026-08-02, and both are decidable
    from the bytes the caller just sent — no extra round trip.
    """
    if mpw.redirect_and_read_on_one_line(command):
        return ("The stderr redirect and the read-back are on one command line; "
                "that returns empty (measured). Send them as two commands: "
                "first `<tool> … ≥ file.err`, then `Catenate file.err`.")
    tool = mpw.silent_tool(command)
    if tool and not (stdout or "").strip():
        return (f"`{tool}` prints nothing on success and nothing on failure, and "
                "STATUS:0 only means the Apple Event was delivered — the tool's "
                "own exit status never crosses the bridge. Verify with "
                "`Exists <artifact>`, or capture with `≥ file.err` plus a "
                "SEPARATE `Catenate`, or use mac_compile/mac_build, which do both.")
    return None


def mpw_execute(command: str, timeout: int = 30) -> Dict[str, Any]:
    """Execute MPW command and return result."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {
                "success": False,
                "status": -1,
                "output": None,
                "error": "Mac not connected. Make sure AppleBridge daemon is running on Mac and connected."
            }

        status, stdout, stderr = conn.send_command(command, timeout=float(timeout))

        result = {
            "success": status == 0,
            "status": status,
            "output": stdout if stdout else "(no output)",
            "error": stderr if stderr else None
        }
        # This is the raw escape hatch, so the command is never rewritten — a
        # tool that silently edits the line you typed is the same class of trap
        # we are closing. A hint costs nothing and arrives when it is true.
        hint = _mpw_execute_hint(command, stdout)
        if hint:
            result["hint"] = hint
        return result
    except Exception as e:
        return {
            "success": False,
            "status": -1,
            "output": None,
            "error": str(e)
        }


def mac_write_file(path: str, content: str, type: str = "TEXT",
                   creator: str = "MPS ") -> Dict[str, Any]:
    """Write a text file via the daemon's native WRITEFILE verb.

    This used to be `Echo '<content>' > '<path>'` through ToolServer, and that
    could not write a file with more than one line in it. A CR inside the
    quoted argument ends the whole ToolServer script: the redirect never runs,
    every later command in the same request is dropped, and ToolServer still
    answers STATUS:0 — so the tool reported success, having done nothing.
    `bytes_written` was `len(content)`, the length of what was ASKED, so it
    agreed. Measured 2026-07-29: a two-line write vanished, and the `Echo done`
    chained after it vanished with it.

    The native verb has none of that in the path — no shell, no quoting, no
    ToolServer at all, so this also works on a guest that has none. It is the
    same transport `mac_put_file` uses; the only thing added here is the text
    conversion the old docstring already promised: UTF-8 -> MacRoman, LF -> CR.
    """
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "path": path, "error": "Mac not connected"}

        # Normalise CRLF/LF to the Mac's CR, then encode. Done in this order so
        # a file that already has CRs (round-tripped off the guest) is not given
        # a doubled line ending.
        text = content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r")
        data = text.encode("mac_roman", errors="replace")

        cmd = "WRITEFILE:" + ":".join((
            base64.b64encode(path.encode("mac_roman", errors="replace")).decode("ascii"),
            _ostype(type, "TEXT").hex(),
            _ostype(creator, "MPS ").hex(),
            base64.b64encode(data).decode("ascii"),
            base64.b64encode(b"").decode("ascii"),      # no resource fork
        ))
        status, stdout, stderr = conn.send_command(cmd, timeout=120.0)

        return {
            "success": status == 0,
            "path": path,
            # The bytes actually put on the wire, not len(content): the two
            # differ whenever a character does not survive MacRoman.
            "bytes_written": len(data),
            "type": _ostype(type, "TEXT").decode("mac_roman", errors="replace"),
            "creator": _ostype(creator, "MPS ").decode("mac_roman", errors="replace"),
            "error": stderr if status != 0 else None
        }
    except Exception as e:
        return {
            "success": False,
            "path": path,
            "error": str(e)
        }


def mac_read_file(path: str) -> Dict[str, Any]:
    """Read a text file from the Mac through the daemon's native READFILE verb.

    It used to run `Catenate '<path>'` and trust `status == 0` — which says only
    that the Apple Event was delivered. `Catenate` on a file that does not exist
    writes its complaint to stderr, where it stays inside ToolServer, and answers
    with nothing. So a MISSING file came back as

        {"success": true, "content": ""}

    A false POSITIVE, and worse than the false negative found in `mac_compile`
    the same day: "I read it, it is empty" for something that is not there.
    Measured 2026-08-05 by the parallel session, and the distinction it destroys
    is exactly the one a caller needs — "nothing to do" versus "wrong path". Its
    local model then invented a filename its OWN previous tool result had ruled
    out, read the invention, and reported on it. No prompt prevents that; only a
    tool that says "not there" instead of "empty".

    READFILE fails cleanly (the daemon answers a non-zero status), needs no
    ToolServer at all, and returns the bytes rather than ToolServer's rendering
    of them. Text is decoded from MacRoman and CR is normalised to LF, mirroring
    what `mac_write_file` does on the way in.
    """
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "path": path, "error": "Mac not connected"}

        status, stdout, stderr = conn.send_command("READFILE:" + path, timeout=120.0)
        if status != 0:
            # The hint is APPENDED, not used as a fallback. The daemon really
            # does answer "READFILE failed", so `stderr or <hint>` would drop
            # the pointer in exactly the case it is needed — which is how the
            # first version of this repair was written, and what its own test
            # caught.
            said = stderr or f"READFILE failed (status {status})"
            return {
                "success": False,
                "path": path,
                "content": None,
                "error": f"{said} — the file may not exist; check with "
                         f"mac_list_files",
            }

        data = macbinary.decode(base64.b64decode(stdout))["data"]
        text = data.decode("mac_roman", errors="replace").replace("\r", "\n")
        return {"success": True, "path": path, "content": text, "bytes": len(data)}
    except Exception as e:
        return {
            "success": False,
            "path": path,
            "content": None,
            "error": str(e)
        }


def _parse_listdir(stdout: str) -> list:
    """Parse the daemon's native LISTDIR output: one tab-separated line per
    entry — name<TAB>type<TAB>creator<TAB>size<TAB>modSecs (Mac epoch seconds)."""
    import datetime
    epoch = datetime.datetime(1904, 1, 1)
    files = []
    for line in stdout.replace('\r', '\n').split('\n'):
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) < 5:
            continue
        name, ftype, creator, size, modsecs = parts[:5]
        try:
            modified = (epoch + datetime.timedelta(seconds=int(modsecs))).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OverflowError):
            modified = modsecs
        files.append({"name": name, "type": ftype, "creator": creator,
                      "size": size, "modified": modified})
    return files


def mac_list_files(path: str) -> Dict[str, Any]:
    """List files in a Mac directory.

    Prefers the daemon's native LISTDIR verb (PBGetCatInfo — works with NO
    ToolServer), and falls back to MPW ``Files -l`` via ToolServer if the daemon
    is too old to support it.
    """
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "path": path, "error": "Mac not connected"}

        # 1) Native path — no ToolServer needed. status==0 means the verb ran
        #    (empty stdout = empty directory, still a valid success).
        status, stdout, stderr = conn.send_command(f"LISTDIR:{path}", timeout=30.0)
        if status == 0 and stdout is not None:
            return {"success": True, "path": path,
                    "files": _parse_listdir(stdout), "raw": stdout, "via": "listdir"}

        # 2) Fallback — MPW Files -l through ToolServer.
        command = f"Files -l {mpw.quote(path)}"
        status, stdout, stderr = conn.send_command(command, timeout=30.0)

        if status == 0 and stdout:
            # Parse `Files -l` by COLUMN BOUNDARIES taken from the dashes row, so
            # filenames containing spaces ("System Folder") and dates ("12:47 PM")
            # are not split apart the way a naive whitespace split does.
            lines = stdout.replace('\r', '\n').split('\n')
            files = []
            # The separator row is dashes + the spaces BETWEEN columns.
            dash_idx = next((i for i, l in enumerate(lines)
                             if l.count('-') > 3 and set(l.strip()) <= {'-', ' '}),
                            None)

            if dash_idx is not None and dash_idx >= 1:
                # Column starts = beginning of each run of '-' in the dashes row.
                dash = lines[dash_idx]
                starts, i = [], 0
                while i < len(dash):
                    if dash[i] == '-':
                        starts.append(i)
                        while i < len(dash) and dash[i] == '-':
                            i += 1
                    else:
                        i += 1

                def col(line, k):
                    s = starts[k]
                    e = starts[k + 1] if k + 1 < len(starts) else len(line)
                    val = line[s:e].strip()
                    # MPW quotes names that contain spaces; drop the wrapping quotes.
                    if len(val) >= 2 and val[0] == "'" and val[-1] == "'":
                        val = val[1:-1]
                    return val

                for line in lines[dash_idx + 1:]:
                    if not line.strip():
                        continue
                    files.append({
                        "name": col(line, 0),
                        "type": col(line, 1) if len(starts) > 1 else "",
                        "creator": col(line, 2) if len(starts) > 2 else "",
                        "size": col(line, 3) if len(starts) > 3 else "",
                        "modified": col(line, 5) if len(starts) > 5 else "",
                    })
            else:
                # No columnar header (e.g. plain output) — fall back to per-line
                # names, still split on newlines only (never on inner spaces).
                for line in lines:
                    name = line.strip()
                    if name and not name.startswith(('Name', '-')):
                        files.append({"name": name, "type": "", "creator": "",
                                      "size": "", "modified": ""})

            return {
                "success": True,
                "path": path,
                "files": files,
                "raw": stdout,
                "via": "toolserver"
            }
        else:
            return {
                "success": False,
                "path": path,
                "error": stderr or "No files found or invalid path"
            }
    except Exception as e:
        return {
            "success": False,
            "path": path,
            "error": str(e)
        }


def mac_compile(source_path: str, output_path: Optional[str] = None,
                options: Optional[str] = None,
                lint: bool = True) -> Dict[str, Any]:
    """Compile a C source with SC, and report only what was verified.

    `SC` is silent on success AND on failure, and the bridge cannot carry its
    exit status (see host/mpw.py), so the old `status == 0` test reported a
    clean compile for a file the compiler never opened. Success here means the
    object file is on disk afterwards; the diagnostics come back with it.

    `lint=False` turns off the C89 pre-check — the CONTROL arm of the one
    experiment that decides whether training has any headroom left: first-attempt
    rate with the lint against without it, on the same task list (agreed with the
    Jetson session 2026-08-06). It exists as a parameter rather than as something
    a caller strips from the result, because the arm then travels IN the trace and
    can be checked afterwards; a caller doing the cutting has to remove exactly
    the c89 tail of `remedies` and leave the bridge remedies from `mpw.py`
    standing, and nobody can verify from the transcript that it did.

    The compiler stays the verdict either way. `lint` changes what the caller is
    TOLD, never what is built.
    """
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "verified": False, "source": source_path,
                    "error": "Mac not connected"}

        def send(command, timeout=30.0):
            try:
                _status, out, _err = conn.send_command(command, timeout=timeout)
                return out or ""
            except Exception as exc:                       # noqa: BLE001
                return f"__SENDERR__:{exc}"

        # The artefact must be known by construction, not guessed: the old code
        # derived `source + ".o"` while never passing -o, so an Exists on it
        # would have been a fresh invention rather than a check.
        caller_named_output = bool(options and re.search(r"(^|\s)-o(\s|$)", options))
        obj_path = output_path
        if not obj_path and not caller_named_output:
            obj_path = (source_path[:-2] + ".o") if source_path.endswith(".c") \
                else (source_path + ".o")

        # -o is passed for the path we are going to CHECK, synthesised or not.
        # Deriving a name and then not telling SC about it was the defect this
        # replaces: `SC x.c` without -o writes `x.c.o`, while the derivation
        # produced `x.o`, so `Exists` looked for a file SC never creates and a
        # successful compile came back `success: false`. A false negative, so
        # the harmless direction — and exactly the kind that sends a caller off
        # to repair a source that is not broken. Found 2026-08-05 by the
        # parallel session, on the first run of a local model through this tool.
        command = f"SC {mpw.quote(source_path)}"
        if obj_path:
            command += f" -o {mpw.quote(obj_path)}"
        if options:
            command += f" {options}"

        if not obj_path:
            # -o hidden inside `options`: run it, but do not claim a check we
            # cannot make. Saying "unverified" is the honest answer; guessing
            # the path and testing that guess is how this function lied before.
            out = send(command, 120.0)
            # `lint` reports whether the C89 pre-check RAN, not what was asked
            # for: this branch returns before reading the source, so it did not.
            return {"success": None, "verified": False, "source": source_path,
                    "object": None, "output": out or None,
                    "error": None, "lint": False,
                    "note": "-o is inside `options`, so the object path is unknown "
                            "here and nothing was verified. Pass output_path to get "
                            "a verified result."}

        # Read the source and name the C99 habits BEFORE compiling. Not a
        # gate: the compiler stays the verdict, and a regex over C has false
        # positives. What this adds is the REASON — `expression expected` does
        # not tell anyone to move a declaration out of a for-head, and the
        # rewrite is the part a caller can act on. Measured 2026-08-05: a local
        # model wrote exactly that, and a prompt naming the rule did not fix it.
        c89 = []
        if lint and source_path.lower().endswith((".c", ".cp", ".cpp")):
            try:
                st, out, _ = conn.send_command("READFILE:" + source_path,
                                               timeout=60.0)
                if st == 0 and out:
                    text = macbinary.decode(base64.b64decode(out))["data"] \
                        .decode("mac_roman", errors="replace")
                    c89 = c89_lint.check(text)
            except Exception:                                  # noqa: BLE001
                c89 = []          # a lint that breaks a compile is worse than none

        step = mpw.run_step(send, command, obj_path,
                            f"{obj_path}.err", timeout=120.0)
        return {
            "success": step["success"],
            "verified": True,
            "source": source_path,
            "object": obj_path,
            "errors": step["errors"],
            "warnings": step["warnings"],
            "remedies": step["remedies"] + c89_lint.remedies(c89),
            "c89": c89 or None,
            "lint": bool(lint),
            "toolserver_alive": step.get("toolserver_alive"),
            "commands": step["commands"],
            "output": "\n".join(step["errors"] + step["warnings"]) or None,
            "error": step["errors"][0] if step["errors"] else None,
        }
    except Exception as e:
        return {
            "success": False,
            "verified": False,
            "source": source_path,
            "error": str(e)
        }


def mac_screenshot(region: Optional[list] = None) -> Dict[str, Any]:
    """Capture a screenshot, optionally cropped to a region [x, y, w, h].

    A region decodes only that rectangle host-side (read one dialog instead of
    the full 1024x768 frame)."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            if not conn.connect():
                return {
                    "success": False,
                    "error": "MacintoshBridgeHost not available"
                }

        command = "SCREENSHOT"
        if region is not None:
            try:
                x, y, w, h = (int(v) for v in region)
                command = f"SCREENSHOT:{x}:{y}:{w}:{h}"
            except (ValueError, TypeError):
                return {"success": False,
                        "error": "region must be [x, y, width, height] integers"}
        # Pixmap transfer + host PNG decode. With a 0.8d46+ daemon the guest
        # crops to the region and PackBits-packs the rows before the transfer,
        # and a full-screen capture that follows another sends only the rows
        # that changed; the SHOTINFO field reports which of those happened.
        conn.last_shotinfo = None
        status, stdout, stderr = conn.send_command(command, timeout=30.0)

        if status == 0 and stdout:
            # stdout already contains base64-encoded PNG
            result = {
                "success": True,
                "image": stdout,  # Already base64 encoded
                "format": "png"
            }
            info = getattr(conn, "last_shotinfo", None)
            if info:
                for tok in info.split():
                    k, _, v = tok.partition("=")
                    if k and v.lstrip("-").isdigit():
                        result[k] = int(v)
                enc = result.get("enc")
                if enc is not None:
                    result["encoding"] = {0: "raw", 1: "packbits", 2: "delta", 3: "up+packbits"}.get(enc, str(enc))
            return result
        else:
            return {
                "success": False,
                "error": stderr or "Screenshot failed"
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def launch_app(path: str, document: Optional[str] = None) -> Dict[str, Any]:
    """Launch a GUI app on the Mac (foreground) via the daemon's LAUNCH verb.

    `document` (daemon 0.8d47+) is opened by the app at launch through an
    'odoc' Apple Event in the launch parameters — the way to get past an
    application whose cold start is a modal Standard File picker."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {
                "success": False,
                "path": path,
                "error": "Mac not connected. Make sure the AppleBridge daemon is running and connected."
            }
        # The :9001 control server routes a raw 'LAUNCH:<path>' verb to the daemon.
        verb = "LAUNCH:" + path + ("\t" + document if document else "")
        status, stdout, stderr = conn.send_command(verb, timeout=15.0)
        return {
            "success": status == 0,
            "status": status,
            "path": path,
            "message": stdout if stdout else None,
            "error": stderr if stderr else None,
        }
    except Exception as e:
        return {
            "success": False,
            "path": path,
            "error": str(e)
        }


def _inject(verb: str, label_fields: Dict[str, Any]) -> Dict[str, Any]:
    """Send a raw input-injection verb (KEY/TYPE/CLICK) via the control port."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "error": "Mac not connected", **label_fields}
        status, stdout, stderr = conn.send_command(verb, timeout=15.0)
        return {
            "success": status == 0,
            "status": status,
            "message": stdout if stdout else None,
            "error": stderr if stderr else None,
            **label_fields,
        }
    except Exception as e:
        return {"success": False, "error": str(e), **label_fields}


def mac_type(text: str) -> Dict[str, Any]:
    """Type text into the front app via the daemon's TYPE verb.

    Long bursts are split into small chunks with brief gaps: a freshly activated
    app flushes its event queue and can swallow the first keystrokes of a big
    burst, so chunking keeps a long string lossless. A bare CR (\\r) inside the
    text becomes a Return key (KEY:13); other characters ride a TYPE verb.
    """
    import time as _time
    payload = text.replace("\n", "\r")
    CHUNK = 12
    last = {"success": True, "text": text}
    i = 0
    while i < len(payload):
        ch = payload[i]
        if ch == "\r":                       # Return as a real key event
            last = _inject("KEY:13:36", {"text": text})
            i += 1
        else:
            j = i
            while j < len(payload) and payload[j] != "\r" and (j - i) < CHUNK:
                j += 1
            last = _inject("TYPE:" + payload[i:j], {"text": text})
            i = j
        if not last.get("success"):
            return last
        _time.sleep(0.12)
    return last


# Event Manager modifier bits (Inside Macintosh: Toolbox Essentials). The daemon
# takes the summed mask as the KEY verb's optional 3rd field.
_MODIFIER_BITS = {
    "cmd": 256, "command": 256, "apple": 256, "meta": 256,
    "shift": 512,
    "caps": 1024, "capslock": 1024, "alpha": 1024, "alphalock": 1024,
    "option": 2048, "opt": 2048, "alt": 2048,
    "control": 4096, "ctrl": 4096,
}


def _modifiers_mask(modifiers) -> int:
    """Turn a list of modifier names (or an int mask) into the Event Manager mask."""
    if modifiers is None:
        return 0
    if isinstance(modifiers, int):
        return int(modifiers)
    total = 0
    for m in modifiers:
        key = str(m).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
        if key not in _MODIFIER_BITS:
            raise ValueError(
                f"unknown modifier {m!r} (use cmd/command, shift, option, control, caps)")
        total |= _MODIFIER_BITS[key]
    return total


# Named special keys -> (charCode, virtual keyCode). Classic-Mac codes (Inside
# Macintosh: Toolbox Essentials / keyboard). Saves the caller memorising numeric
# codes for the non-typable keys that drive dialogs, forms and lists.
_NAMED_KEYS = {
    "return": (13, 36), "enter": (3, 76), "tab": (9, 48),
    "escape": (27, 53), "esc": (27, 53), "space": (32, 49),
    "delete": (8, 51), "backspace": (8, 51), "bksp": (8, 51),
    "forwarddelete": (127, 117), "fwddelete": (127, 117), "del": (127, 117),
    "left": (28, 123), "leftarrow": (28, 123),
    "right": (29, 124), "rightarrow": (29, 124),
    "up": (30, 126), "uparrow": (30, 126),
    "down": (31, 125), "downarrow": (31, 125),
    "home": (1, 115), "end": (4, 119),
    "pageup": (11, 116), "pgup": (11, 116),
    "pagedown": (12, 121), "pgdn": (12, 121), "help": (5, 114),
    "f1": (16, 122), "f2": (16, 120), "f3": (16, 99), "f4": (16, 118),
    "f5": (16, 96), "f6": (16, 97), "f7": (16, 98), "f8": (16, 100),
    "f9": (16, 101), "f10": (16, 109), "f11": (16, 103), "f12": (16, 111),
}


# Character -> virtual key code (US/ANSI key POSITIONS, Inside Macintosh:
# Toolbox Essentials "Key Codes"). A key-down message packs both halves: the low
# byte is the character the KCHR produced, the next byte the physical key. Apps
# split on which half they trust for Command shortcuts -- most call MenuKey with
# the character, but some (Photoshop 2.5) resolve the shortcut from the key code.
#
# Code 0 is NOT an "unset" value: it is the A key. Sending 0 for every character
# therefore made every shortcut look like Cmd-A to those apps. Verified live on
# 2026-07-26: in Photoshop 2.5, Cmd-O opened the Open dialog only with code 31,
# and Cmd-Q quit it only with code 12; with 0 both did nothing.
_CHAR_KEYCODES = {
    "a": 0,  "s": 1,  "d": 2,  "f": 3,  "h": 4,  "g": 5,  "z": 6,  "x": 7,
    "c": 8,  "v": 9,  "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16,
    "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24,
    "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32,
    "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41,
    "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50, " ": 49,
}

# Key codes name physical positions, so a QWERTZ keyboard swaps Y and Z against
# the table above. Set APPLEBRIDGE_KEY_LAYOUT=de when the guest runs a German
# KCHR *and* the target app resolves shortcuts by key code; everything else is
# position-identical for letters. Per-call `key_code` overrides either way.
if os.environ.get("APPLEBRIDGE_KEY_LAYOUT", "").strip().lower() in ("de", "german", "qwertz"):
    _CHAR_KEYCODES["y"], _CHAR_KEYCODES["z"] = _CHAR_KEYCODES["z"], _CHAR_KEYCODES["y"]


def _keycode_for_char(char_code: int) -> int:
    """Physical key code for a character code; 0 (the A key) when unmapped."""
    try:
        ch = chr(int(char_code)).lower()
    except (ValueError, TypeError):
        return 0
    return _CHAR_KEYCODES.get(ch, 0)


def mac_key(char_code: Optional[int] = None, key_code: Optional[int] = None,
            modifiers=None, key=None) -> Dict[str, Any]:
    """Press one key in the front app via the daemon's KEY verb.

    Give EITHER `key` (recommended) — a named special key (return, enter, tab,
    escape, space, delete, forwarddelete, left/right/up/down, home, end, pageup,
    pagedown, help, f1..f12) or a single character — OR the raw `char_code`
    (+ optional `key_code`) for full control. `modifiers` is a list of names
    (e.g. ["command"], ["command","shift"]) or a raw Event Manager mask, making
    Command-key shortcuts and Option/Shift-modified input reachable.

    The virtual key code is derived from the character (see _CHAR_KEYCODES) —
    apps that resolve Command shortcuts by key code need the real physical key,
    not 0. Pass `key_code` explicitly to override it.
    """
    mask = _modifiers_mask(modifiers)
    if key is not None:
        norm = str(key).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
        if norm in _NAMED_KEYS:
            cc, kc = _NAMED_KEYS[norm]
        elif len(str(key)) == 1:
            cc = ord(str(key))
            kc = _keycode_for_char(cc)
        else:
            return {"success": False, "key": key,
                    "error": f"unknown key {key!r}; use a single character or one of: "
                             + ", ".join(sorted(_NAMED_KEYS))}
    elif char_code is not None:
        cc = int(char_code)
        kc = _keycode_for_char(cc)
    else:
        return {"success": False, "error": "provide either `key` (name/char) or `char_code`"}
    if key_code is not None:
        kc = int(key_code)                 # explicit caller override wins
    verb = f"KEY:{cc}:{kc}:{mask}"
    return _inject(verb, {"char_code": cc, "key_code": kc, "key": key,
                          "modifiers": modifiers or []})


def _parse_menu_reply(stdout: str) -> Dict[str, Any]:
    """Parse the MENU verb's reply into ints, e.g.
    'menu found=1 menuID=130 item=1 nItems=3 titleX=34 itemY=28 selID=130
    selItem=1 poll=202' -> {'found':1,'menuID':130,...}."""
    out: Dict[str, Any] = {}
    for tok in (stdout or "").split():
        k, sep, v = tok.partition("=")
        if sep:
            try:
                out[k] = int(v)
            except ValueError:
                out[k] = v
    return out


def _parse_msread(stdout: str) -> Dict[str, Any]:
    """Parse the MSREAD verb's reply, e.g.
    'blk=00004140 calls=1 hits=1 armed=0 lastRes=03090003 head=NO' ->
    {'blk':'00004140','calls':1,'hits':1,'armed':0,'lastRes':0x03090003,'head':'NO'}.
    calls/hits/armed are decimal; blk/lastRes are hex; head is a string."""
    out: Dict[str, Any] = {}
    for tok in (stdout or "").split():
        k, sep, v = tok.partition("=")
        if not sep:
            continue
        if k in ("calls", "hits", "armed"):
            try:
                out[k] = int(v)
            except ValueError:
                pass
        elif k in ("blk", "lastRes"):
            try:
                out[k] = int(v, 16)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out


def mac_menu(key: str = None, modifiers=None, title: str = None,
             item: str = None) -> Dict[str, Any]:
    """Invoke a menu command. Two modes (see the tool schema for detail):

    - key: FRONT-app Command-key equivalent (Command+<key> via MenuKey).
    - title + item: journal-drive the DAEMON'S OWN menu bar by name (the only
      way to a shortcut-less item; own menus only -- MenuSelect uses the caller's
      menu list, so it can't reach a front app; JPROBE proved a background yield
      never pumps the journal).
    """
    # BY-NAME mode -> the daemon's MENU:<title>:<item> journaling verb.
    if title is not None and item is not None:
        t, it = str(title), str(item)
        for bad, lbl in ((t, "title"), (it, "item")):
            if any(c in bad for c in (":", "\r", "\n")):
                return {"success": False,
                        "error": f"{lbl} cannot contain ':' or newlines",
                        "title": title, "item": item}
        res = _inject(f"MENU:{t}:{it}",
                      {"title": t, "item": it, "target": "daemon_own_menu_bar"})
        fields = _parse_menu_reply(res.get("message") or "")
        res.update(fields)
        found = fields.get("found") == 1
        resolved = fields.get("item", 0) or 0
        sel = fields.get("selItem", 0) or 0
        if not found:
            res["success"] = False
            res["error"] = f"menu title {title!r} not found on the daemon's menu bar"
        elif resolved == 0:
            res["success"] = False
            res["error"] = f"item {item!r} not found in menu {title!r}"
        else:
            res["success"] = sel > 0
            if sel == 0:
                res["error"] = "menu resolved but MenuSelect returned no selection"
        return res
    if key is None:
        return {"success": False,
                "error": "provide either `key` (front-app Cmd-key) or `title`+`item` (by name)"}
    ch = str(key)
    if len(ch) != 1:
        return {"success": False, "error": "key must be a single character",
                "menu_key": key}
    try:
        cc = ord(ch.lower())
    except TypeError:
        return {"success": False, "error": "key must be a single character",
                "menu_key": key}
    if cc > 255:
        return {"success": False, "error": "key must be a MacRoman character",
                "menu_key": key}
    # Menu equivalents are Command-based; default to Command, always include it.
    names = list(modifiers) if modifiers else []
    mask = _modifiers_mask(names) | 256  # cmdKey
    return _inject(f"KEY:{cc}:{_keycode_for_char(cc)}:{mask}",
                   {"menu_key": key, "modifiers": names or ["command"]})


def mac_menu_front(menu_id: int, item: int, menu_x: int = 45) -> Dict[str, Any]:
    """Drive the FRONT app's menu via the Route B global MenuSelect trap patch.

    This is the one path that reaches a *foreign* front application's shortcut-less
    menu (mac_menu's by-name mode drives only the daemon's own bar). It orchestrates
    the proven daemon verbs and a host trigger:
      1. MSINSTALL  -- adopt the boot INIT's global patch (found by heap scan).
      2. MSDRIVE    -- arm the patch to return (menu_id, item) on the next call.
      3. host trigger -- activate BasiliskII and cliclick a menu-bar title so the
         front app calls MenuSelect; the patch returns the armed item WITHOUT the
         tracking loop, and the app dispatches the command.
      4. MSREAD     -- confirm the interception (hits advanced, lastRes matches).

    Requirements / limits:
      - The ABMenuInit boot extension must be installed and the guest rebooted
        (the patch is global only when installed at startup; an app-installed
        patch is process-local).
      - menu_id / item are NUMERIC (the target app's real menu id + 1-based item);
        resolving a foreign app's menu BY NAME needs reading its menu list and is
        not wired here.
      - The trigger uses host `cliclick` + `osascript` with BasiliskII visible
        locally -- LOCAL Basilisk only (no remote/SheepShaver trigger).
    """
    import subprocess
    import time

    try:
        mid, it = int(menu_id), int(item)
    except (TypeError, ValueError):
        return {"success": False, "error": "menu_id and item must be integers"}

    adopt = _inject("MSINSTALL", {"step": "adopt"})
    before = _parse_msread((_inject("MSREAD", {}).get("message")) or "")
    arm = _inject(f"MSDRIVE:{mid}:{it}", {"menu_id": mid, "item": it})
    if not arm.get("success"):
        return {"success": False, "error": "MSDRIVE failed (patch not installed? "
                "run the ABMenuInit boot INIT + reboot)",
                "menu_id": mid, "item": it, "arm": arm.get("message")}

    trigger_error = None
    try:
        subprocess.run(["osascript", "-e",
                        'tell application "BasiliskII" to activate'],
                       capture_output=True, text=True, timeout=10)
        time.sleep(0.6)
        geo = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to tell process "BasiliskII" '
             'to get {position, size} of window 1'],
            capture_output=True, text=True, timeout=10)
        nums = [int(n.strip()) for n in geo.stdout.split(",")]
        wx, wy = nums[0], nums[1]
        hx, hy = wx + int(menu_x), wy + 28 + 9   # 28px title bar + menu-bar row
        # press-HOLD a menu title (a quick click is missed); the armed patch
        # short-circuits tracking so no starvation. Retry once if it misses.
        for _ in range(2):
            subprocess.run(["cliclick", f"m:{hx},{hy}", "w:150",
                            f"dd:{hx},{hy}", "w:130", f"du:{hx},{hy}"],
                           capture_output=True, text=True, timeout=10)
            time.sleep(0.4)
            mid_read = _parse_msread((_inject("MSREAD", {}).get("message")) or "")
            if mid_read.get("hits", 0) > before.get("hits", 0):
                break
    except FileNotFoundError:
        trigger_error = ("cliclick/osascript not found on host -- Route B trigger "
                         "is local-Basilisk only")
    except Exception as e:  # geometry parse / subprocess failure
        trigger_error = str(e)

    after = _parse_msread((_inject("MSREAD", {}).get("message")) or "")
    fired = after.get("hits", 0) > before.get("hits", 0)
    want = ((mid & 0xFFFF) << 16) | (it & 0xFFFF)
    return {
        "success": bool(fired) and after.get("lastRes") == want,
        "menu_id": mid,
        "item": it,
        "fired": fired,
        "hits": after.get("hits"),
        "last_result_hex": "%08X" % (after.get("lastRes") or 0),
        "adopted": adopt.get("message"),
        "trigger_error": trigger_error,
        "note": ("Route B front-app menu drive via the boot-INIT MenuSelect patch. "
                 "If fired is false: confirm the ABMenuInit extension is installed "
                 "and the guest rebooted, that BasiliskII is visible locally, and "
                 "that menu_id is the app's real menu id."),
    }


def mac_click(x: int, y: int, count: int = 1, modifiers=None) -> Dict[str, Any]:
    """Click at (x, y) in the front app via the daemon's CLICK verb.

    `count` = 2 for a double-click (open a Finder item, select a word), 3 for a
    triple-click; the daemon posts the presses within the double-click interval at
    the same point so the app's own detection fires. `modifiers` (list of names or
    a mask) gives a shift-click (extend a selection) or command-click (multi-select).
    """
    n = max(1, min(3, int(count)))
    mask = _modifiers_mask(modifiers)
    if n == 1 and mask == 0:
        verb = f"CLICK:{int(x)}:{int(y)}"                 # legacy short form
    else:
        verb = f"CLICK:{int(x)}:{int(y)}:{n}:{mask}"
    return _inject(verb, {"x": x, "y": y, "count": n, "modifiers": modifiers or []})


def _ostype_hex(code: str) -> str:
    """4-char OSType -> 8 hex chars (space-padded, Mac convention)."""
    b = code.encode("mac_roman", errors="replace")[:4].ljust(4, b" ")
    return b.hex()


AE_WAIT_DEFAULT_SECONDS = 30.0     # interactive; the daemon's own default
AE_WAIT_MAX_SECONDS = 180.0        # the daemon clamps here too — see applebridge.h


def mac_send_apple_event(target_creator: str, event_class: str, event_id: str,
                         direct_object: Optional[str] = None,
                         expect_reply: bool = True,
                         wait_seconds: float = AE_WAIT_DEFAULT_SECONDS,
                         skip_pump_probe: bool = False) -> Dict[str, Any]:
    """Send an arbitrary Apple Event to a scriptable app and return its reply.

    The wait is bounded and stated, because on a cooperative scheduler blocking
    the daemon blocks the guest: `expect_reply=False` sends kAENoReply and
    cannot block at all, and `wait_seconds` (<= 180) caps the rest. Sending an
    event whose vocabulary declares reply 'null' and then waiting for one is how
    the 2026-07-27 KAHL/RUN took the emulator down.
    """
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "error": "Mac not connected"}
        for label, val in (("target_creator", target_creator),
                           ("event_class", event_class), ("event_id", event_id)):
            if not isinstance(val, str) or not (1 <= len(val) <= 4):
                return {"success": False, "error": f"{label} must be a 1-4 char code"}
        do_b64 = ""
        if direct_object:
            do_b64 = base64.b64encode(
                direct_object.encode("mac_roman", errors="replace")).decode("ascii")
        if expect_reply:
            secs = max(1.0, min(AE_WAIT_MAX_SECONDS, float(wait_seconds)))
        else:
            secs = 0.0
        ticks = int(secs * 60)

        # A no-reply send cannot learn anything: `status == 0` means the Apple
        # Event Manager accepted it for DELIVERY. So ask first whether the
        # target is reading at all — the one place where "delivered" gets
        # mistaken for "done". See host/pump_probe.py for why this is narrow.
        probe_info = None
        want, ptarget, why = pump_probe.should_probe(
            "mac_send_apple_event",
            {"expect_reply": expect_reply, "skip_pump_probe": skip_pump_probe,
             "target_creator": target_creator})
        if want:
            t0 = time.monotonic()
            pstatus, _pout, _perr = conn.send_command(
                pump_probe.probe_verb(_ostype_hex(ptarget)), timeout=8.0)
            probe_info = pump_probe.read_probe(pstatus, time.monotonic() - t0)
            # Blocked ONLY when the target is running and not reading. A
            # target that is not running at all fails loudly on the send
            # itself (-600), and swallowing that into "not read" would hide a
            # plain error behind a subtle one.
            if not probe_info["pumping"] and probe_info["target_running"]:
                return pump_probe.pending_result(
                    "mac_send_apple_event",
                    {"target_creator": target_creator}, probe_info)
        elif skip_pump_probe or not expect_reply:
            # A skip that leaves no trace is a check nobody can tell was not
            # made. Recorded so a later reader sees WHY nothing was probed.
            probe_info = {"skipped": True, "why": why}

        verb = (f"AESEND:{_ostype_hex(target_creator)}:{_ostype_hex(event_class)}:"
                f"{_ostype_hex(event_id)}:{do_b64}:{ticks}")
        # Our own read has to outlast the daemon's bound, or we would report a
        # timeout the daemon has not reached yet and leave the caller unsure
        # which side gave up.
        status, stdout, stderr = conn.send_command(verb, timeout=secs + 20.0)
        return {
            "success": status == 0,
            "status": status,
            "target": target_creator,
            "event": f"{event_class}/{event_id}",
            "waited_for_reply": expect_reply,
            "wait_seconds": secs if expect_reply else 0,
            "reply": stdout if stdout else None,
            "error": stderr if stderr else None,
            "probe": probe_info,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def mac_clipboard_get() -> Dict[str, Any]:
    """Read the guest TEXT scrap (clipboard)."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "error": "Mac not connected"}
        status, stdout, stderr = conn.send_command("CLIPGET", timeout=30.0)
        return {
            "success": status == 0,
            "text": stdout if stdout else "",
            "error": stderr if stderr else None,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def mac_clipboard_set(text: str) -> Dict[str, Any]:
    """Set the guest TEXT scrap (clipboard)."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "error": "Mac not connected"}
        b64 = base64.b64encode(
            (text or "").encode("mac_roman", errors="replace")).decode("ascii")
        status, stdout, stderr = conn.send_command("CLIPSET:" + b64, timeout=30.0)
        return {
            "success": status == 0,
            "bytes": len((text or "").encode("mac_roman", errors="replace")),
            "message": stdout if stdout else None,
            "error": stderr if stderr else None,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def mac_status() -> Dict[str, Any]:
    """Report bridge liveness. MACSTATUS is answered host-side, so it works even
    when the daemon is down — letting the caller see WHICH layer is broken."""
    try:
        conn = get_connection()
        status, stdout, stderr = conn.send_command("MACSTATUS", timeout=8.0)
    except Exception as e:
        # Control port unreachable => the host server itself is down.
        return {
            "success": False,
            "host_server_running": False,
            "daemon_connected": False,
            "error": str(e),
        }

    f = {}
    for part in (stdout or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            f[k.strip()] = v.strip()

    def _int(k):
        try:
            return int(f[k])
        except (KeyError, ValueError):
            return None

    def _bool(k):
        return (f[k] == "1") if k in f else None

    idle = None
    try:
        idle = float(f["idle_seconds"])
    except (KeyError, ValueError):
        pass

    return {
        "success": status == 0,
        "host_server_running": True,
        "daemon_connected": _bool("host_connected"),
        "daemon_responding": _bool("daemon_responding"),
        "toolserver_running": _bool("toolserver"),
        "idle_seconds": idle,
        "missed_heartbeats": _int("missed_heartbeats"),
        "rx_count": _int("rx"),
        "tx_count": _int("tx"),
        "err_count": _int("err"),              # error responses (STATUS != 0); None on pre-telemetry daemons
        "last_latency_ms": _int("lat"),        # last real command's RX->TX round-trip, ms
        "last_error": (f.get("lasterr") or None),   # short tag of the most recent error (auth/launch/cmd fail/...)
        "uptime_seconds": _int("uptime"),
        # WHICH link this is. uptime/rx/tx/err are cumulative for the daemon
        # PROCESS and simply continue across a redial, so they cannot answer
        # "did the connection I started my work on survive?". These can:
        # `link_id` changes on every accepted link, and changes even across a
        # host-server restart, where a bare counter would restart at 1 and
        # silently collide with an earlier link of the same number.
        "link_generation": _int("link_generation"),
        "link_epoch": f.get("link_epoch") or None,
        "link_id": (f"{f['link_epoch']}:{f['link_generation']}"
                    if f.get("link_epoch") and f.get("link_generation") else None),
        "home": f.get("home") or None,   # daemon install folder (for self-update staging)
        "raw": stdout,
    }


def mac_appletalk_browse(entity_type: str = "AFPServer", zone: str = "*",
                         name: str = "=") -> Dict[str, Any]:
    """List visible AppleTalk entities via the daemon's NBP lookup.

    The wire verb takes its fields positionally (type:zone:object), so the
    arguments are ordered here to match rather than named on the wire.
    """
    verb = "NBPLOOK:{}:{}:{}".format(entity_type or "AFPServer",
                                     zone or "*", name or "=")
    try:
        conn = get_connection()
        # Above the daemon's ~3 s NBP retry window plus transport slack.
        status, stdout, stderr = conn.send_command(verb, timeout=25.0)
    except Exception as e:
        return {"success": False, "error": str(e)}

    if status != 0:
        # AppleTalk off / driver error — distinct from "nothing answered".
        return {"success": False, "error": stderr or stdout or "NBP lookup failed",
                "entity_type": entity_type, "zone": zone}

    entities = []
    for line in (stdout or "").replace("\r", "\n").split("\n"):
        if not line.strip():
            continue
        f = line.split("\t")
        if len(f) >= 4:
            entities.append({"name": f[0], "type": f[1], "zone": f[2],
                             "address": f[3]})
    return {
        "success": True,
        "entity_type": entity_type,
        "zone": zone,
        "count": len(entities),
        "entities": entities,
        # A lookup that answers with nothing is a valid result, not an error —
        # say so, so an empty list doesn't read as a broken call.
        "note": (stderr or None) if stderr else
                (None if entities else "no entities answered this lookup"),
    }


def bridge_doctor_tool() -> Dict[str, Any]:
    """Cross-layer stack diagnosis.

    Deliberately runs the probes LOCALLY rather than over the control port: the
    single most useful moment for this tool is when the host server is the
    broken layer, and a control-port round-trip would fail exactly then. When
    the port IS reachable, mac_status is merged in for the daemon-side view.
    """
    try:
        report = bridge_doctor.collect()
    except Exception as e:
        return {"success": False, "error": f"probe failed: {e}"}

    result = {
        "success": True,
        "verdict": report["verdict"],          # ok | info | warn | error
        "ok": report["ok"],
        "findings": report["findings"],        # ranked; each may carry a `fix`
        "probes": report["probes"],
        "text": bridge_doctor.format_text(report),
    }
    # The daemon-side view is a bonus, never a precondition — a dead control
    # port is itself one of the diagnoses above.
    try:
        status = mac_status()
        result["mac_status"] = status
    except Exception as e:
        result["mac_status"] = {"success": False, "error": str(e)}
    return result

def _host_input_error(e) -> Dict[str, Any]:
    """A refusal from the input driver is a result, not an exception to hide.

    Every one of these means a gesture did NOT happen — which is the point: the
    alternative is a click landing in some other application.
    """
    return {"success": False, "error": str(e)}


def mac_host_click(x: int, y: int, count: int = 1,
                   modifiers: Optional[list] = None,
                   keep_front: bool = False) -> Dict[str, Any]:
    """Click the guest's REAL mouse at guest coordinates (local emulator only).

    Coordinates are read straight off a mac_screenshot image — that capture IS
    the guest framebuffer, so its pixels are guest coordinates 1:1.
    """
    hold = ",".join(modifiers) if modifiers else None
    try:
        with guest_input.Session(keep_front=bool(keep_front)) as s:
            pt = s.point(int(x), int(y))
            s.cliclick(guest_input.build_click(pt, count, hold))
        return {"success": True, "guest": [int(x), int(y)], "host": list(pt),
                "count": count, "modifiers": modifiers or [],
                "kept_front": bool(keep_front)}
    except guest_input.InputError as e:
        return _host_input_error(e)


def mac_host_menu(title_x: int, title_y: int,
                  item_x: int, item_y: int,
                  keep_front: bool = False) -> Dict[str, Any]:
    """Pull down a menu with the REAL mouse: press on the title, release on the item.

    Issued as ONE gesture on purpose. A menu left open blocks the guest's event
    loop, which starves the background daemon and drops the bridge for ~30 s —
    so there is deliberately no "open the menu and look" mode here.
    """
    try:
        with guest_input.Session(keep_front=bool(keep_front)) as s:
            g = s.geometry()
            guest_input.check_in_bounds(int(title_x), int(title_y), g["guest_size"])
            guest_input.check_in_bounds(int(item_x), int(item_y), g["guest_size"])
            title = guest_input.guest_to_host(g["origin"], g["title_h"],
                                              int(title_x), int(title_y))
            item = guest_input.guest_to_host(g["origin"], g["title_h"],
                                             int(item_x), int(item_y))
            s.cliclick(guest_input.build_menu_gesture(title, item))
        return {"success": True, "title": [int(title_x), int(title_y)],
                "item": [int(item_x), int(item_y)],
                "kept_front": bool(keep_front)}
    except guest_input.InputError as e:
        return _host_input_error(e)


def mac_host_screenshot(region: Optional[list] = None) -> Dict[str, Any]:
    """Capture the guest screen HOST-side (works while the daemon is blocked).

    mac_screenshot streams from the daemon, so it returns nothing exactly while
    a modal dialog or menu owns the machine. This grabs the emulator window
    instead. `region` is in guest coordinates, like every other argument here.
    """
    import tempfile
    rect = None
    if region is not None:
        try:
            rect = tuple(int(v) for v in region)
            if len(rect) != 4:
                raise ValueError
        except (TypeError, ValueError):
            return {"success": False,
                    "error": "region must be [x, y, width, height] integers"}
    path = os.path.join(tempfile.gettempdir(), "applebridge_host_shot.png")
    try:
        guest_input.capture(path, rect)
        with open(path, "rb") as fh:
            data = fh.read()
    except guest_input.InputError as e:
        return _host_input_error(e)
    except OSError as e:
        return {"success": False, "error": f"capture failed: {e}"}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if not data:
        return {"success": False, "error": "capture produced no image"}
    return {"success": True, "image": base64.b64encode(data).decode("ascii"),
            "format": "png", "region": list(rect) if rect else None}


# Long enough for SC/Link round-trips (host gives these LONG_TIMEOUT=240s daemon-side).
_BUILD_STEP_TIMEOUT = 250.0


def mac_build(project_dir: str, app_name: Optional[str] = None,
              sources: Optional[list] = None, libraries: Optional[list] = None,
              rez_file: Optional[str] = None, file_type: str = "APPL",
              creator: str = "????", model: str = "far",
              run: bool = False) -> Dict[str, Any]:
    """One-shot verified build: SC -> Link -> (Rez) -> SetFile -> verify-by-artifact."""
    conn = get_connection()
    if not conn.is_connected():
        return {"success": False, "stage": "connect", "error": "Mac not connected"}

    if not project_dir.endswith(":"):
        project_dir += ":"
    if not app_name:
        parts = project_dir.rstrip(":").split(":")
        app_name = parts[-1] if parts and parts[-1] else "App"

    def run_cmd(c, timeout=30.0):
        try:
            _status, out, _err = conn.send_command(c, timeout=timeout)
            return out or ""
        except Exception as e:
            return f"__SENDERR__:{e}"

    def exists(path):
        # ToolServer's `Exists` echoes the path to stdout when the file is there,
        # and "NoDir:-1701" (empty stdout) when it is not.
        r = run_cmd(f"Exists {path}", timeout=30.0)
        return bool(r.strip()) and "NoDir" not in r and "__SENDERR__" not in r

    # Diagnostics go through mpw.classify_diagnostics, the same classifier
    # mac_compile uses. This function carried its own — a line splitter plus a
    # case-SENSITIVE `"Error" in l`, which is exactly the defect that classifier
    # was written for on 2026-08-02: SC writes "Fatal error: unable to open
    # file", Asm writes "# Not a text file", and only one of the two contains a
    # capital "Error". The copy also dropped every line without a marker, so the
    # offending source line and the column marker never reached the caller
    # (measured 2026-08-06 on the mac_compile path; this path had it too).
    def diagnose(text):
        return mpw.classify_diagnostics(text)

    # 1. Discover sources
    if not sources:
        listing = run_cmd(f"Files {project_dir}", timeout=30.0)
        toks = listing.replace("\r", " ").replace("\n", " ").split()
        sources = [t for t in toks if t.endswith(".c")]
    if not sources:
        return {"success": False, "stage": "discover",
                "error": f"No .c sources found in {project_dir}", "project_dir": project_dir}

    err_file = f"{project_dir}build.err"

    # 2. Compile each source (≥ redirect for stderr; verify the .o by Exists)
    compile_results = []
    obj_files = []
    for c_file in sources:
        src = c_file if ":" in c_file else f"{project_dir}{c_file}"
        obj = (src[:-2] + ".o") if src.endswith(".c") else (src + ".o")
        run_cmd(f"Delete -i {obj}", timeout=30.0)   # clean slate: stale .o must not mask a failure
        run_cmd(f"SC {src} -o {obj} ≥ {err_file}", timeout=_BUILD_STEP_TIMEOUT)
        diag = diagnose(run_cmd(f"Catenate {err_file}", timeout=30.0))
        ok = exists(obj)
        compile_results.append({
            "file": c_file,
            "ok": ok,
            "warnings": diag["warnings"],
            "errors": diag["errors"],
            "remedies": diag["remedies"],
        })
        if ok:
            obj_files.append(obj)

    if any(not r["ok"] for r in compile_results):
        return {"success": False, "stage": "compile", "app_path": None,
                "sources": sources, "compile": compile_results}

    # 3. Link (verify the artifact, not the status — long links return -1712 yet succeed)
    app_path = f"{project_dir}{app_name}"
    libs = libraries or ['"{Libraries}Interface.o"', '"{Libraries}MacRuntime.o"',
                         '"{CLibraries}StdCLib.o"']
    link_cmd = (f"Link -model {model} {' '.join(obj_files)} {' '.join(libs)} "
                f"-o {app_path} ≥ {err_file}")
    run_cmd(f"Delete -i {app_path}", timeout=30.0)   # so Exists can't see a stale artifact
    run_cmd(link_cmd, timeout=_BUILD_STEP_TIMEOUT)
    # "Error 52: File was not needed for link" is a benign over-specified-lib
    # warning; the classifier already sorts it into warnings, so what comes back
    # as an error here is fatal (undefined entry, "Errors prevented...").
    link_diag = diagnose(run_cmd(f"Catenate {err_file}", timeout=30.0))
    link_errs = link_diag["errors"]
    # Verify by artifact (a long link returns -1712 yet succeeds, leaving the file
    # and an empty err) AND by the absence of fatal linker diagnostics (a failed
    # link can leave a partial file).
    if (not exists(app_path)) or link_errs:
        return {"success": False, "stage": "link", "app_path": None,
                "sources": sources, "compile": compile_results,
                "link": {"ok": False, "errors": link_errs}}

    result = {
        "success": True, "stage": "done", "app_path": app_path,
        "sources": sources, "compile": compile_results,
        "link": {"ok": True, "errors": link_errs},
    }

    # 4. Optional Rez (e.g. a SIZE resource for an Apple-Event-aware app)
    if rez_file:
        run_cmd(f"Rez -a {rez_file} -o {app_path} ≥ {err_file}", timeout=120.0)
        rez_errs = diagnose(run_cmd(f"Catenate {err_file}", timeout=30.0))["errors"]
        result["rez"] = {"ok": not rez_errs, "errors": rez_errs}
        if rez_errs:
            result["success"] = False
            result["stage"] = "rez"
            return result

    # 5. SetFile (type/creator) so the artifact is launchable
    run_cmd(f"SetFile -t {file_type} -c '{creator}' {app_path}", timeout=30.0)

    # 6. Optional run (foreground)
    if run:
        result["run"] = launch_app(app_path)

    return result


def mac_put_file(host_path: str, mac_path: str, type: Optional[str] = None,
                 creator: Optional[str] = None,
                 resource_path: Optional[str] = None) -> Dict[str, Any]:
    """Send a binary file (both forks) to the Mac via the WRITEFILE verb."""
    try:
        with open(host_path, "rb") as f:
            blob = f.read()

        rsrc = b""
        if macbinary.looks_like_macbinary(blob):
            mb = macbinary.decode(blob)
            data = mb["data"]
            rsrc = mb["rsrc"]
            type_b = _ostype(type) if type else mb["type"]
            creator_b = _ostype(creator) if creator else mb["creator"]
            source = "macbinary"
        else:
            data = blob
            if resource_path:
                with open(resource_path, "rb") as rf:
                    rsrc = rf.read()
            type_b = _ostype(type)
            creator_b = _ostype(creator)
            source = "flat"

        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "path": mac_path, "error": "Mac not connected"}

        cmd = "WRITEFILE:" + ":".join((
            base64.b64encode(mac_path.encode("mac_roman", errors="replace")).decode("ascii"),
            type_b.hex(),
            creator_b.hex(),
            base64.b64encode(data).decode("ascii"),
            base64.b64encode(rsrc).decode("ascii"),
        ))
        status, stdout, stderr = conn.send_command(cmd, timeout=240.0)
        return {
            "success": status == 0,
            "path": mac_path,
            "source": source,
            "data_bytes": len(data),
            "resource_bytes": len(rsrc),
            "type": type_b.decode("mac_roman", errors="replace"),
            "creator": creator_b.decode("mac_roman", errors="replace"),
            "error": stderr if status != 0 else None,
        }
    except Exception as e:
        return {"success": False, "path": mac_path, "error": str(e)}


def mac_get_file(mac_path: str, host_path: str, format: str = "auto") -> Dict[str, Any]:
    """Pull a file (both forks) from the Mac via the READFILE verb."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "path": mac_path, "error": "Mac not connected"}

        status, stdout, stderr = conn.send_command("READFILE:" + mac_path, timeout=240.0)
        if status != 0:
            return {"success": False, "path": mac_path,
                    "error": stderr or f"READFILE failed (status {status})"}

        blob = base64.b64decode(stdout)        # host returns a MacBinary, base64-framed
        mb = macbinary.decode(blob)
        wrote_macbinary = (format == "macbinary"
                           or (format == "auto" and len(mb["rsrc"]) > 0))
        if wrote_macbinary:
            with open(host_path, "wb") as f:
                f.write(blob)
        else:
            with open(host_path, "wb") as f:
                f.write(mb["data"])
        return {
            "success": True,
            "path": mac_path,
            "host_path": host_path,
            "format": "macbinary" if wrote_macbinary else "data",
            "data_bytes": len(mb["data"]),
            "resource_bytes": len(mb["rsrc"]),
            "type": mb["type"].decode("mac_roman", errors="replace"),
            "creator": mb["creator"].decode("mac_roman", errors="replace"),
        }
    except Exception as e:
        return {"success": False, "path": mac_path, "error": str(e)}


def mac_restart_toolserver(path: str = "MeinMac:MPW:ToolServer") -> Dict[str, Any]:
    """(Re)launch ToolServer via the LAUNCH verb, then verify with an Echo."""
    import time
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "error": "Mac not connected"}
        status, stdout, stderr = conn.send_command("LAUNCH:" + path, timeout=20.0)
        launched = (status == 0 and "Launched" in (stdout or ""))
        verified = False
        if launched:
            for _ in range(8):
                time.sleep(3)
                _, out, _e = conn.send_command("Echo TSCHECK", timeout=15.0)
                if "TSCHECK" in (out or "") and "no-ToolServer" not in (out or ""):
                    verified = True
                    break
        return {
            "success": launched and verified,
            "launched": launched,
            "verified": verified,
            "path": path,
            "error": (None if launched else (stderr or "LAUNCH failed")),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_applescript(script: str) -> Dict[str, Any]:
    """Run AppleScript on the host (macOS) via osascript (reads from stdin)."""
    import subprocess
    try:
        p = subprocess.run(["osascript", "-"], input=script,
                           capture_output=True, text=True, timeout=30)
        return {
            "success": p.returncode == 0,
            "stdout": p.stdout.strip(),
            "stderr": p.stderr.strip() or None,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def mac_verbose_log(max_bytes: int = 0) -> Dict[str, Any]:
    """Read the daemon's Verbose console ring over the bridge (LOG verb).

    Returns the on-screen monitor's rolling text buffer (the last ~60 lines of
    command lines + output + AE trace) as text, so the log can be read WITHOUT
    screenshotting or scrolling the fragile monitor window. `max_bytes` (>0)
    returns only the last N bytes of the buffer."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "error": "Mac not connected"}
        verb = f"LOG:{int(max_bytes)}" if max_bytes else "LOG"
        status, stdout, stderr = conn.send_command(verb, timeout=15.0)
        if status != 0:
            return {"success": False, "status": status,
                    "error": stderr or stdout or "LOG failed"}
        return {"success": True, "log": stdout, "bytes": len(stdout)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def mac_reboot() -> Dict[str, Any]:
    """Restart the emulated Mac via the daemon's REBOOT verb.

    A dropped connection IS the evidence here — the guest is going down, so the
    socket must die. What is NOT evidence is a REPLY with a non-zero status: the
    daemon answered, and it said no. That case used to read `status` and throw it
    away, so a refused reboot reported success.

    Found 2026-08-05 by an outside comment on the loop draft, which asked one
    searchable question of the whole surface — *where does a `success: true`
    arise from the absence of an exception rather than from the lower layer's
    answer?* Three instances of that class had turned up that day by accident;
    this is the fourth, and the first found by looking.
    """
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "error": "Mac not connected"}
        status, stdout, stderr = conn.send_command("REBOOT", timeout=15.0)
        if status != 0:
            return {"success": False, "status": status,
                    "error": stderr or f"the daemon refused REBOOT (status {status})"}
        return {
            "success": True,
            "message": stdout or "reboot triggered",
            "note": "Mac is restarting; poll mpw_execute until it answers again.",
        }
    except Exception as e:
        # The connection dropping mid-restart is expected, not a failure.
        return {"success": True, "note": f"reboot triggered (connection dropped: {e})"}


def mac_shutdown() -> Dict[str, Any]:
    """Cleanly power off the emulated Mac via the daemon's SHUTDOWN verb.

    The safe alternative to hard-killing the emulator process (which risks a
    corrupted guest disk image): the daemon calls the Shutdown Manager's
    ShutDwnPower, which flushes volumes and powers the machine off.
    """
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "error": "Mac not connected"}
        status, stdout, stderr = conn.send_command("SHUTDOWN", timeout=15.0)
        # Same rule as mac_reboot: a dead socket is evidence, a refusal is not.
        if status != 0:
            return {"success": False, "status": status,
                    "error": stderr or f"the daemon refused SHUTDOWN (status {status})"}
        return {
            "success": True,
            "message": stdout or "shutdown triggered",
            "note": "Mac is powering off; the emulator will quit and the bridge "
                    "connection will stay down until it is launched again.",
        }
    except Exception as e:
        # The connection dropping mid-shutdown is expected, not a failure.
        return {"success": True, "note": f"shutdown triggered (connection dropped: {e})"}


def mac_update_daemon(host_path: str, mac_dir: Optional[str] = None,
                      staged_name: str = "AppleBridge new") -> Dict[str, Any]:
    """Self-update the running daemon, entirely over the bridge.

    Stages `host_path` (a fork-aware MacBinary of the new daemon — e.g. one pulled
    with mac_get_file) into the daemon's install folder as `staged_name`, then
    sends the SWAPSELF verb: the daemon renames itself aside and renames the
    staged binary into its place (renaming an open file is allowed, unlike
    overwriting it). This does NOT reboot — call mac_reboot afterward so the
    watchdog launches the new binary, then verify the vers. `mac_dir` defaults to
    the daemon's reported install folder (mac_status home=)."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "error": "Mac not connected"}
        if not mac_dir:
            mac_dir = mac_status().get("home")
            if not mac_dir:
                return {"success": False,
                        "error": "daemon reported no install folder (home=); pass mac_dir"}
        if not mac_dir.endswith(":"):
            mac_dir += ":"
        staged = mac_dir + staged_name
        # 1. stage the new binary beside the running daemon (a fresh file: no lock)
        put = mac_put_file(host_path, staged, type="APPL", creator="ABrg")
        if not put.get("success"):
            return {"success": False, "stage": "stage", "staged": staged,
                    "error": put.get("error") or "staging failed"}
        # 2. swap it in (daemon renames itself aside, staged -> running name)
        status, stdout, stderr = conn.send_command("SWAPSELF", timeout=20.0)
        if status != 0:
            return {"success": False, "stage": "swapself", "staged": staged,
                    "status": status, "error": stderr or stdout or "swap failed",
                    "hint": "err -43 = staged binary missing; other = OS refused the rename"}
        return {"success": True, "staged": staged, "message": stdout or "Swapped",
                "next": "call mac_reboot to activate, then verify the daemon's vers"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# Tool dispatcher
TOOL_HANDLERS = {
    "mpw_execute": mpw_execute,
    "mac_write_file": mac_write_file,
    "mac_read_file": mac_read_file,
    "mac_list_files": mac_list_files,
    "mac_compile": mac_compile,
    "mac_screenshot": mac_screenshot,
    "launch_app": launch_app,
    "mac_type": mac_type,
    "mac_key": mac_key,
    "mac_menu": mac_menu,
    "mac_menu_front": mac_menu_front,
    "mac_click": mac_click,
    "mac_status": mac_status,
    "mac_appletalk_browse": mac_appletalk_browse,
    "mac_host_click": mac_host_click,
    "mac_host_menu": mac_host_menu,
    "mac_host_screenshot": mac_host_screenshot,
    "bridge_doctor": bridge_doctor_tool,
    "mac_build": mac_build,
    "mac_send_apple_event": mac_send_apple_event,
    "mac_clipboard_get": mac_clipboard_get,
    "mac_clipboard_set": mac_clipboard_set,
    "mac_put_file": mac_put_file,
    "mac_get_file": mac_get_file,
    "mac_restart_toolserver": mac_restart_toolserver,
    "run_applescript": run_applescript,
    "mac_verbose_log": mac_verbose_log,
    "mac_reboot": mac_reboot,
    "mac_shutdown": mac_shutdown,
    "mac_update_daemon": mac_update_daemon,
}


# One watch for every caller, because the repetition is a property of the
# TRAFFIC, not of one driver. Measured 2026-08-05 on the first run of a local
# model through this bridge: it called mac_compile three times with identical
# arguments before answering. Idempotent there, and `Delete -i` in the command
# list means each repeat really did delete and rebuild the object — so it was
# not even a no-op. On mac_key, mac_click, launch_app, SWAPSELF or REBOOT the
# same shape is a problem, and nothing would have shown it.
_REPEATS = loop_guard.RepeatWatch()

# Says, in the result, that the last write has not been compiled. The strategy's
# rule for what deserves training is "train only what a tool can DETECT but not
# ENFORCE" — detection was proved on 2026-08-06 (sixteen of eighty runs never
# compiled), and the enforcing half had never been tried. This is that attempt.
# It reports; whether a conductor turns it into a refusal is the conductor's
# decision, and it can, because the flag is in the result.
_UNCOMPILED = loop_guard.UncompiledWrite()


def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
    """Call a tool by name with arguments."""
    if name not in TOOL_HANDLERS:
        raise ValueError(f"Unknown tool: {name}")

    handler = TOOL_HANDLERS[name]
    repeat = _REPEATS.note(name, arguments)
    result = handler(**arguments)

    # Reported, never blocked. Refusing a repeated call would be a guard
    # deciding the caller did not mean what it asked for twice — which it
    # cannot know. The field is absent on an ordinary call, so nothing that
    # reads these results has to change.
    if repeat and isinstance(result, dict) and "repeated_call" not in result:
        result["repeated_call"] = repeat

    # Same route, same reason: the rule arrives at the moment its case is true,
    # in the place the caller is already reading. Fed AFTER the handler, so a
    # write that failed does not claim an uncompiled change.
    hint = _UNCOMPILED.note(name, arguments, result if isinstance(result, dict) else None)
    if hint and isinstance(result, dict) and "uncompiled_write" not in result:
        result["uncompiled_write"] = hint

    # One place, so every tool carries it and no tool has to remember to.
    # The host server appends a NOTES field to the control-port reply when the
    # session-to-session channel has something; `mac_connection` parks it on the
    # connection rather than in the result tuple, and it surfaces here as a
    # sibling key. It rides on traffic that was happening anyway, which is the
    # whole point: the alternative is a session that only learns of a message
    # when it thinks to look.
    #
    # It never overwrites a handler's own key, and it is only ever added to a
    # dict — a tool returning something else is left exactly as it was.
    try:
        pending = getattr(get_connection(), "last_notes", None)
        if pending and isinstance(result, dict) and "session_channel" not in result:
            result["session_channel"] = pending
    except Exception:                                  # noqa: BLE001
        pass
    return result
