"""
Allow running the MCP server as a module:
    python -m mcp
"""

from .server import main

if __name__ == "__main__":
    main()
