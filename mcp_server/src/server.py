"""
RespectASO MCP Server — entry point.

Starts the FastMCP server with stdio transport.
Each tool module registers its tools by decorating functions with @mcp.tool()
on import — this file just ensures all modules are imported before run().

Transport: stdio — compatible with Claude Desktop and most MCP clients.

Usage:
    # from the repo root:
    python -m mcp_server.src.server

    # or from mcp_server/:
    python src/server.py
"""

import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow `from aso.services import ...` from tool modules
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import shared mcp instance + all tool modules
# Importing a module that calls @mcp.tool() registers its tools automatically.
# ---------------------------------------------------------------------------

from mcp_server.src.app import mcp  # noqa: E402

import mcp_server.src.tools.health          # noqa: F401, E402
import mcp_server.src.tools.keyword_search  # noqa: F401, E402
import mcp_server.src.tools.opportunity     # noqa: F401, E402

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting RespectASO MCP server (stdio transport)...")
    mcp.run(transport="stdio")
