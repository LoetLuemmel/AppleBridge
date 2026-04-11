#!/usr/bin/env python3
"""
AppleBridge MCP Server
Provides Claude Code access to classic Macintosh development environment.

Usage:
    python -m mcp.server

Or configure in Claude Code's MCP settings.
"""

import json
import sys
from typing import Any, Dict, Optional

from .tools import TOOLS, call_tool
from .mac_connection import get_connection, MacConnection


class MCPServer:
    """MCP Server for AppleBridge."""

    def __init__(self):
        # Connection will be established on-demand when tools are called
        print("AppleBridge MCP Server initialized", file=sys.stderr)
        print("MacintoshBridgeHost should be running on localhost:9001", file=sys.stderr)
        print("Mac daemon should connect to MacintoshBridgeHost on port 9000", file=sys.stderr)

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a single MCP request."""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        try:
            if method == "initialize":
                return self._handle_initialize(req_id, params)
            elif method == "tools/list":
                return self._handle_list_tools(req_id)
            elif method == "tools/call":
                return self._handle_call_tool(req_id, params)
            elif method == "ping":
                return self._make_response(req_id, {})
            else:
                return self._make_error(req_id, -32601, f"Unknown method: {method}")
        except Exception as e:
            return self._make_error(req_id, -32603, str(e))

    def _handle_initialize(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle initialize request."""
        return self._make_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "applebridge",
                "version": "0.1.0"
            }
        })

    def _handle_list_tools(self, req_id: Any) -> Dict[str, Any]:
        """Handle tools/list request."""
        return self._make_response(req_id, {
            "tools": TOOLS
        })

    def _handle_call_tool(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/call request."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if not tool_name:
            return self._make_error(req_id, -32602, "Missing tool name")

        try:
            result = call_tool(tool_name, arguments)

            # Format result as content
            if isinstance(result, dict):
                content_text = json.dumps(result, indent=2)
            else:
                content_text = str(result)

            return self._make_response(req_id, {
                "content": [
                    {
                        "type": "text",
                        "text": content_text
                    }
                ]
            })
        except Exception as e:
            return self._make_response(req_id, {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": str(e)})
                    }
                ],
                "isError": True
            })

    def _make_response(self, req_id: Any, result: Any) -> Dict[str, Any]:
        """Create a JSON-RPC response."""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result
        }

    def _make_error(self, req_id: Any, code: int, message: str) -> Dict[str, Any]:
        """Create a JSON-RPC error response."""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message
            }
        }

    def run(self):
        """Run the MCP server (stdio mode)."""
        # Log startup to stderr (stdout is for JSON-RPC)
        print("AppleBridge MCP Server starting...", file=sys.stderr)

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
                response = self.handle_request(request)
                print(json.dumps(response), flush=True)
            except json.JSONDecodeError as e:
                error_response = self._make_error(None, -32700, f"Parse error: {e}")
                print(json.dumps(error_response), flush=True)
            except Exception as e:
                error_response = self._make_error(None, -32603, f"Internal error: {e}")
                print(json.dumps(error_response), flush=True)


def main():
    """Main entry point."""
    server = MCPServer()
    server.run()


if __name__ == "__main__":
    main()
