"""
Shared MCP application instance for decorator-based tool registration.

Tool modules should import `mcp` from this package and decorate functions
with `@mcp.tool(...)`.
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

__all__ = ["mcp"]
