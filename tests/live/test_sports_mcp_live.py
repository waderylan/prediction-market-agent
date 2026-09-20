"""Bounded public searches over actual stdio MCP; no dependency on a specific open game."""

import os
import sys
from datetime import timedelta

import pytest
from jsonschema import validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = [
    pytest.mark.live_smoke,
    pytest.mark.skipif(os.getenv("RUN_LIVE_SMOKE") != "1", reason="set RUN_LIVE_SMOKE=1"),
]


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
async def test_sports_stdio_live(provider):
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "market_agent.mcp." + provider]
    )
    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as session,
    ):
        await session.initialize()
        tools = {t.name: t for t in (await session.list_tools()).tools}
        search_name = provider + "_search_markets"
        assert tools[search_name].inputSchema["properties"]["limit"]["maximum"] == 10
        assert (await session.call_tool(search_name, {"query": "Yankees", "limit": 20})).isError
        for query, league in [
            ("Yankees", "mlb"),
            ("Chiefs", "nfl"),
            ("NCAA football Notre Dame", "ncaa_football"),
        ]:
            result = await session.call_tool(search_name, {"query": query, "limit": 2})
            assert not result.isError
            payload = result.structuredContent
            validate(payload, tools[search_name].outputSchema)
            assert payload["discovery"]["pages_scanned"] <= 3
            assert payload["markets"] == []
            assert len(payload["games"]) <= 2
            print(
                provider,
                league,
                "returned",
                len(payload["games"]),
                "pages",
                payload["discovery"]["pages_scanned"],
                "stop",
                payload["discovery"]["stop_reason"],
            )
            for game in payload["games"][:1]:
                assert game["league"] == league
                assert game["local_date"]
                assert game["live_status"] in {
                    "pregame",
                    "live",
                    "awaiting_resolution",
                    "settled",
                }
                market = game["contracts"][0]
                assert market["sports"]["league"] == league
                assert market["sports"]["market_type"] == "game_winner"
                assert market["outcome_quotes"]
                detail_name = provider + "_get_market"
                detail = await session.call_tool(detail_name, {"market_id": market["market_id"]})
                assert not detail.isError
                validate(detail.structuredContent, tools[detail_name].outputSchema)
                assert detail.structuredContent["market_id"] == market["market_id"]
