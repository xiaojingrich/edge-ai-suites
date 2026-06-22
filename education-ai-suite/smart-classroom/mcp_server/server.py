"""
Smart Classroom MCP Server

Exposes classroom session data and tools via the MCP protocol.
This is the unified interface layer for all smart-classroom capabilities
that external agents need to access.

Started from main.py alongside the main application.
"""

import os

from mcp.server.fastmcp import FastMCP
from mcp_server.tools import register_all_tools


mcp = FastMCP(
    "smart-classroom",
    description="Smart Classroom data and tools for classroom evaluation and analysis",
    host="0.0.0.0",
    port=int(os.environ.get("MCP_SERVER_PORT", "8100")),
)

register_all_tools(mcp)
