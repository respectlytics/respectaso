"""RespectASO MCP Server — CLI entry point for bundled macOS app.

This is the entry point for the standalone MCP binary that MCP clients
(Claude Desktop, Cursor, VS Code) launch via stdio. It runs without
any GUI window.

Usage (development):
    python desktop/mcp_entry.py

Usage (bundled):
    /Applications/RespectASO.app/Contents/MacOS/respectaso-mcp

MCP client config example (Claude Desktop):
    {
        "mcpServers": {
            "respectaso": {
                "command": "/Applications/RespectASO.app/Contents/MacOS/respectaso-mcp"
            }
        }
    }
"""

import os
import sys
from pathlib import Path


def main() -> None:
    # Determine project root
    if getattr(sys, "frozen", False):
        base_dir = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        # This file is at desktop/mcp_entry.py — parent is the repo root
        base_dir = Path(__file__).resolve().parent.parent

    # Ensure project root is on sys.path
    base_dir_str = str(base_dir)
    if base_dir_str not in sys.path:
        sys.path.insert(0, base_dir_str)

    # Delegate to the main server module
    from aso_pro.mcp.server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
