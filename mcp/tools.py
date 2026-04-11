"""
AppleBridge MCP Tools
Tool implementations for classic Mac development.
"""

import base64
import os
from typing import Any, Dict, List, Optional

from .mac_connection import get_connection

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
        "description": """Capture a screenshot of the Basilisk II emulator window.

Returns the screenshot as a base64-encoded PNG image.
Useful for seeing the current state of the Mac desktop.""",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
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


def mac_list_files(path: str) -> Dict[str, Any]:
    """List files in Mac directory."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            return {"success": False, "path": path, "error": "Mac not connected"}

        command = f"Files -l '{path}'"
        status, stdout, stderr = conn.send_command(command, timeout=30.0)

        if status == 0 and stdout:
            # Parse the file listing
            lines = stdout.strip().split('\n')
            files = []

            # Skip header lines (first 2 lines are headers)
            for line in lines[2:]:
                if line.strip():
                    # Parse: Name Type Crtr Size Flags Date Date
                    parts = line.split()
                    if len(parts) >= 4:
                        files.append({
                            "name": parts[0],
                            "type": parts[1] if len(parts) > 1 else "",
                            "creator": parts[2] if len(parts) > 2 else "",
                            "size": parts[3] if len(parts) > 3 else ""
                        })

            return {
                "success": True,
                "path": path,
                "files": files,
                "raw": stdout
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


def mac_screenshot() -> Dict[str, Any]:
    """Capture screenshot via MacintoshBridgeHost."""
    try:
        conn = get_connection()
        if not conn.is_connected():
            if not conn.connect():
                return {
                    "success": False,
                    "error": "MacintoshBridgeHost not available"
                }

        # Send SCREENSHOT command
        status, stdout, stderr = conn.send_command("SCREENSHOT", timeout=10.0)

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


# Tool dispatcher
TOOL_HANDLERS = {
    "mpw_execute": mpw_execute,
    "mac_write_file": mac_write_file,
    "mac_read_file": mac_read_file,
    "mac_list_files": mac_list_files,
    "mac_compile": mac_compile,
    "mac_screenshot": mac_screenshot
}


def call_tool(name: str, arguments: Dict[str, Any]) -> Any:
    """Call a tool by name with arguments."""
    if name not in TOOL_HANDLERS:
        raise ValueError(f"Unknown tool: {name}")

    handler = TOOL_HANDLERS[name]
    return handler(**arguments)
