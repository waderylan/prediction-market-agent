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


async def test_kalshi_stdio_live():
    params = StdioServerParameters(command=sys.executable, args=["-m", "market_agent.mcp.kalshi"])
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as session,
    ):
        await session.initialize()
        assert len((await session.list_tools()).tools) == 2
        result = await session.call_tool("kalshi_get_market", {"market_id": "KXPRESPERSON-28-JVAN"})
        assert not result.isError
        assert result.structuredContent["platform"] == "kalshi"
        result = await session.call_tool(
            "kalshi_search_markets", {"query": "presidential election", "limit": 2}
        )
        assert not result.isError
        assert len(result.structuredContent["markets"]) <= 2
