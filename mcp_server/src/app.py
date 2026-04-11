"""
Shared FastMCP application instance.

All tool modules import `mcp` from here and register with @mcp.tool().
server.py then imports each tool module (triggering decoration) before
calling mcp.run().
"""

from fastmcp import FastMCP

mcp = FastMCP(
    name="RespectASO",
    instructions=(
        "You are connected to RespectASO, a free open-source App Store "
        "Optimization (ASO) research tool. Use the available tools to research "
        "iOS App Store keywords: check popularity, difficulty, opportunity scores, "
        "competitor landscapes, and download estimates. All data comes directly "
        "from Apple's public iTunes Search API — no API key required."
    ),
)
