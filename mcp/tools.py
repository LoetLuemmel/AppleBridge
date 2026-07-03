"""
AppleBridge MCP Tools
Tool implementations for classic Mac development.
"""

import base64
import os
import sys
from typing import Any, Dict, List, Optional

from .mac_connection import get_connection

# host/ holds the stdlib-only macbinary helper; make it importable regardless of
# how the MCP server is launched (mirrors how host_server.py imports it flat).
_HOST_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host")
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)
import macbinary  # noqa: E402


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

Use MPW syntax:
- Paths use : separator (e.g., "MeinMac:Folder:File.c")
- Common commands: Directory, Files, Echo, SC (compile), ILink (link)
- ToolServer returns stdout; use for commands that produce output

Examples:
- Directory - show current directory
- Files "MeinMac:Temp:" - list files
- Echo "hello" > "MeinMac:Temp:test.txt" - write file
- SC "MeinMac:Temp:test.c" - compile C file""",
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
Content will be converted to MacRoman encoding with CR line endings.""",
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
        "description": """Compile a C source file using MPW's SC compiler.

Compiles the specified source file. Output is source.c.o by default.
Returns success status and any compiler messages.""",
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
decode only that rectangle — e.g. read a single dialog instead of the whole
frame, for a smaller, faster image.""",
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

Injects a single keyDown/keyUp into the OS event queue. Use for keys mac_type
can't express as plain text — Return (char 13), Enter (3), Tab (9), Escape (27),
Backspace (8), or the arrows (give their key_code with char_code 28-31).

char_code is the ASCII/MacRoman byte; key_code is the virtual key code (0 is
fine for ordinary characters). Pass `modifiers` to hold Command/Shift/Option/
Control — e.g. ["command"] for Cmd-key shortcuts, ["command","shift"]. For a
menu command it's usually clearer to call mac_menu.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "char_code": {
                    "type": "integer",
                    "description": "ASCII/MacRoman character code (e.g. 13 = Return, 27 = Escape)"
                },
                "key_code": {
                    "type": "integer",
                    "description": "Virtual key code (optional; 0 for ordinary characters)"
                },
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Modifier keys to hold: any of command/cmd, shift, option/alt, control/ctrl, caps (e.g. [\"command\"])"
                }
            },
            "required": ["char_code"]
        }
    },
    {
        "name": "mac_menu",
        "description": """Invoke a menu command by its Command-key equivalent in the FRONT app.

Selecting a menu item is a modal mouse-tracking loop that runs INSIDE the front
app, so a synthetic click can't drive it — the reachable path is the menu's
KEYBOARD equivalent. This injects Command+<key> (add Shift/Option via
`modifiers`), which the front app dispatches through MenuKey.

`key` is the single character shown next to the menu item (read it off a
screenshot): e.g. "Q" to Quit, "W" to close, "N" for New, "S" to Save. Items
with NO Command-key equivalent can't be reached this way — use the app's own
keyboard interface (e.g. a command window) for those.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Single character of the menu item's Command-key equivalent (e.g. Q, W, N, S)"
                },
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extra modifiers beyond Command (e.g. [\"shift\"] for a Cmd-Shift item). Command is always included."
                }
            },
            "required": ["key"]
        }
    },
    {
        "name": "mac_click",
        "description": """Click at a point in the FRONT application on the classic Mac.

Moves the emulated mouse to (x, y) in global screen coordinates and posts a
mouse-down/up there, poking the low-memory button state so tracked controls
(buttons, menus) register a real press. Pair with mac_screenshot to read a
dialog, then click its button. Coordinates are screen pixels (origin top-left;
the screen is 1024×768).""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Horizontal screen coordinate (pixels)"},
                "y": {"type": "integer", "description": "Vertical screen coordinate (pixels)"}
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
  - rx_count / tx_count / uptime_seconds — daemon counters

Diagnostic shortcut: daemon_connected but not toolserver_running => commands
will come back empty; daemon not connected => the bridge/emulator is down.""",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
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
target app must be running. Returns the reply in `reply`.""",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_creator": {"type": "string", "description": "4-char creator of the target app (e.g. MPSX, ttxt)"},
                "event_class": {"type": "string", "description": "4-char event class (e.g. misc, aevt, core)"},
                "event_id": {"type": "string", "description": "4-char event id (e.g. dosc, quit, getd)"},
                "direct_object": {"type": "string", "description": "Optional text direct object (e.g. a script or property spec)"}
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

        return {
            "success": status == 0,
            "status": status,
            "output": stdout if stdout else "(no output)",
            "error": stderr if stderr else None
        }
    except Exception as e:
        return {
            "success": False,
            "status": -1,
            "output": None,
            "error": str(e)
        }


def mac_write_file(path: str, content: str) -> Dict[str, Any]:
    """Write file to Mac filesystem using Echo command."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "path": path, "error": "Mac not connected"}

        # Escape single quotes in content
        escaped_content = content.replace("'", "'\"'\"'")

        # Use Echo to write file (handles encoding automatically)
        command = f"Echo '{escaped_content}' > '{path}'"

        status, stdout, stderr = conn.send_command(command, timeout=30.0)

        return {
            "success": status == 0,
            "path": path,
            "bytes_written": len(content),
            "error": stderr if status != 0 else None
        }
    except Exception as e:
        return {
            "success": False,
            "path": path,
            "error": str(e)
        }


def mac_read_file(path: str) -> Dict[str, Any]:
    """Read file from Mac filesystem using Catenate command."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "path": path, "error": "Mac not connected"}

        command = f"Catenate '{path}'"
        status, stdout, stderr = conn.send_command(command, timeout=30.0)

        if status == 0:
            return {
                "success": True,
                "path": path,
                "content": stdout
            }
        else:
            return {
                "success": False,
                "path": path,
                "content": None,
                "error": stderr or f"Failed to read file (status {status})"
            }
    except Exception as e:
        return {
            "success": False,
            "path": path,
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
        command = f"Files -l '{path}'"
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
                options: Optional[str] = None) -> Dict[str, Any]:
    """Compile C source file with SC."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "source": source_path, "error": "Mac not connected"}

        # Build compile command
        command = f"SC '{source_path}'"
        if output_path:
            command += f" -o '{output_path}'"
        if options:
            command += f" {options}"

        status, stdout, stderr = conn.send_command(command, timeout=120.0)

        # Check if object file was created
        obj_path = output_path or (source_path + ".o")

        return {
            "success": status == 0,
            "source": source_path,
            "object": obj_path,
            "output": stdout if stdout else None,
            "error": stderr if stderr else None
        }
    except Exception as e:
        return {
            "success": False,
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
        # Full-screen (or cropped) pixmap transfer + host PNG decode.
        status, stdout, stderr = conn.send_command(command, timeout=30.0)

        if status == 0 and stdout:
            # stdout already contains base64-encoded PNG
            return {
                "success": True,
                "image": stdout,  # Already base64 encoded
                "format": "png"
            }
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


def launch_app(path: str) -> Dict[str, Any]:
    """Launch a GUI app on the Mac (foreground) via the daemon's LAUNCH verb."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {
                "success": False,
                "path": path,
                "error": "Mac not connected. Make sure the AppleBridge daemon is running and connected."
            }
        # The :9001 control server routes a raw 'LAUNCH:<path>' verb to the daemon.
        status, stdout, stderr = conn.send_command("LAUNCH:" + path, timeout=15.0)
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


def mac_key(char_code: int, key_code: int = 0, modifiers=None) -> Dict[str, Any]:
    """Press one key in the front app via the daemon's KEY verb.

    modifiers is an optional list of names (e.g. ["command"], ["command","shift"])
    or a raw Event Manager mask; it makes Command-key menu shortcuts and
    Option/Shift-modified input reachable.
    """
    mask = _modifiers_mask(modifiers)
    verb = f"KEY:{int(char_code)}:{int(key_code)}:{mask}"
    return _inject(verb, {"char_code": char_code, "key_code": key_code,
                          "modifiers": modifiers or []})


def mac_menu(key: str, modifiers=None) -> Dict[str, Any]:
    """Invoke a menu command by its Command-key equivalent in the front app.

    Selecting a menu item is a modal mouse-tracking loop that runs inside the
    front app; a background daemon is not scheduled during it, so a synthetic
    click can't drive it. The reachable path is the menu's KEYBOARD equivalent:
    this injects Command+<key> (add Shift/Option via `modifiers`), which the front
    app dispatches through MenuKey. `key` is the single character printed next to
    the menu item (e.g. "Q" to Quit, "W" to close, "N" for New). Items with no
    Command-key equivalent cannot be reached this way -- use the app's own
    keyboard interface for those.
    """
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
    return _inject(f"KEY:{cc}:0:{mask}",
                   {"menu_key": key, "modifiers": names or ["command"]})


def mac_click(x: int, y: int) -> Dict[str, Any]:
    """Click at (x, y) in the front app via the daemon's CLICK verb."""
    return _inject(f"CLICK:{int(x)}:{int(y)}", {"x": x, "y": y})


def _ostype_hex(code: str) -> str:
    """4-char OSType -> 8 hex chars (space-padded, Mac convention)."""
    b = code.encode("mac_roman", errors="replace")[:4].ljust(4, b" ")
    return b.hex()


def mac_send_apple_event(target_creator: str, event_class: str, event_id: str,
                         direct_object: Optional[str] = None) -> Dict[str, Any]:
    """Send an arbitrary Apple Event to a scriptable app and return its reply."""
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
        verb = (f"AESEND:{_ostype_hex(target_creator)}:{_ostype_hex(event_class)}:"
                f"{_ostype_hex(event_id)}:{do_b64}")
        status, stdout, stderr = conn.send_command(verb, timeout=300.0)
        return {
            "success": status == 0,
            "status": status,
            "target": target_creator,
            "event": f"{event_class}/{event_id}",
            "reply": stdout if stdout else None,
            "error": stderr if stderr else None,
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
        "uptime_seconds": _int("uptime"),
        "home": f.get("home") or None,   # daemon install folder (for self-update staging)
        "raw": stdout,
    }


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

    def diag_lines(text):
        return [l.strip() for l in text.replace("\r", "\n").split("\n") if l.strip()]

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
        diag = diag_lines(run_cmd(f"Catenate {err_file}", timeout=30.0))
        ok = exists(obj)
        compile_results.append({
            "file": c_file,
            "ok": ok,
            "warnings": [l for l in diag if "Warning" in l],
            "errors": [l for l in diag if "Error" in l],
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
    link_diag = diag_lines(run_cmd(f"Catenate {err_file}", timeout=30.0))
    # "Error 52: File was not needed for link" is a benign over-specified-lib warning;
    # everything else the linker writes (undefined entry, "Errors prevented...") is fatal.
    link_errs = [l for l in link_diag if "Error" in l and "Error 52" not in l]
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
        rez_errs = [l for l in diag_lines(run_cmd(f"Catenate {err_file}", timeout=30.0))
                    if "Error" in l]
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


def mac_reboot() -> Dict[str, Any]:
    """Restart the emulated Mac via the daemon's REBOOT verb."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "error": "Mac not connected"}
        status, stdout, stderr = conn.send_command("REBOOT", timeout=15.0)
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
    "mac_click": mac_click,
    "mac_status": mac_status,
    "mac_build": mac_build,
    "mac_send_apple_event": mac_send_apple_event,
    "mac_clipboard_get": mac_clipboard_get,
    "mac_clipboard_set": mac_clipboard_set,
    "mac_put_file": mac_put_file,
    "mac_get_file": mac_get_file,
    "mac_restart_toolserver": mac_restart_toolserver,
    "run_applescript": run_applescript,
    "mac_reboot": mac_reboot,
    "mac_shutdown": mac_shutdown,
    "mac_update_daemon": mac_update_daemon,
}


def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
    """Call a tool by name with arguments."""
    if name not in TOOL_HANDLERS:
        raise ValueError(f"Unknown tool: {name}")

    handler = TOOL_HANDLERS[name]
    return handler(**arguments)
