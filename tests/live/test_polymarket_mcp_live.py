import os
import sys
from datetime import timedelta

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = [
    pytest.mark.live_smoke,
    pytest.mark.skipif(os.getenv("RUN_LIVE_SMOKE") != "1", reason="set RUN_LIVE_SMOKE=1"),
]


async def test_public_mcp_stdio():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "market_agent.mcp.polymarket"]
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as session,
    ):
        await session.initialize()
        assert len((await session.list_tools()).tools) == 2
        search = await session.call_tool(
            "polymarket_search_markets", {"query": "Vance", "limit": 2}
        )
        assert not search.isError
        detail = await session.call_tool("polymarket_get_market", {"market_id": "561229"})
        assert not detail.isError
        assert detail.structuredContent["market_id"] == "561229"
