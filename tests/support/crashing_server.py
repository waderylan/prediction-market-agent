"""Stdio fixture that exits during a tool call to exercise transport recovery."""

import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("crashing-kalshi", log_level="CRITICAL")


@server.tool()
def kalshi_crash() -> dict[str, bool]:
    os._exit(1)


@server.tool()
def kalshi_ping() -> dict[str, bool]:
    return {"alive": True}


if __name__ == "__main__":
    server.run(transport="stdio")
