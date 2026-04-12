"""
Health tool — server liveness check.
"""

from mcp_server.src import mcp
from mcp_server.src.django_bootstrap import bootstrap_django



@mcp.tool()
def ping() -> dict:
    """
    Check that the RespectASO MCP server is reachable and ready.

    Returns:
        {"status": "ok"} when core dependencies are available.
        {"status": "degraded"} when server is reachable but not fully ready.
    """
    try:
        # Readiness check: Django can bootstrap and DB connection responds.
        bootstrap_django()

        from django.db import connection

        # Check if the database is reachable
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

        return {"status": "ok"}
    except Exception:
        return {"status": "degraded"}
