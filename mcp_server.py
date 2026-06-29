"""Run the StockFlow MCP server over stdio.

Example:
    python -m mcp_server
"""

from integrations.mcp_server import run_stdio


if __name__ == "__main__":
    run_stdio()
