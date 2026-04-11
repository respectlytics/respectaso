"""
Health tool — server liveness check.
"""

from mcp_server.src.app import mcp


@mcp.tool()
def ping() -> dict:
    """
    Check that the RespectASO MCP server is running and reachable.

    Returns a simple status dict. Call this to verify the server is
    connected before making analysis calls.
    """
    return {"status": "ok", "server": "RespectASO MCP"}
