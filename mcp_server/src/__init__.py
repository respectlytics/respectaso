"""
Shared MCP application instance for decorator-based tool registration.

Tool modules should import `mcp` from this package and decorate functions
with `@mcp.tool(...)`.
"""

from fastmcp import FastMCP

mcp = FastMCP(
    name="RespectASO",
    instructions=(
        "You are connected to RespectASO for App Store keyword research. "
        "Tool guide: search_keyword = fresh analysis for one keyword+country "
        "(scores + competitors); opportunity_search = compare one keyword across 30"
        "countries and rank opportunity; get_app_id_from_name = resolve app "
        "title to app_id for app-filtered queries; ping = server health check; "
        "list_resources = discover available resources and URIs; read_resource = "
        "read a resource by URI. For saved keyword history, prefer read_resource "
        "with respectaso://keywords/saved{?app_id,country} (use "
        "get_app_id_from_name first when only an app title is given), not "
        "search_keyword."
    ),
)

__all__ = ["mcp"]
