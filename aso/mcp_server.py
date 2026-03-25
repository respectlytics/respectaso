from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from .forms import COUNTRY_CHOICES  # module-level — required for test patching

mcp = FastMCP("RespectASO")
