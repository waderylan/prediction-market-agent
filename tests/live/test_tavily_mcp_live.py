"""Bounded public Tavily search through the game-scoped stdio MCP process."""

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


async def test_tavily_game_evidence_stdio_live():
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "market_agent.mcp.tavily"]
    )
    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as session,
    ):
        await session.initialize()
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert set(tools) == {"tavily_search_game_evidence"}
        tool = tools["tavily_search_game_evidence"]
        assert tool.inputSchema["additionalProperties"] is False

        result = await session.call_tool(
            "tavily_search_game_evidence",
            {
                "league": "mlb",
                "team_a": "Miami Marlins",
                "team_b": "San Diego Padres",
                "game_date": "2026-09-20",
                "scheduled_start": "2026-09-20T20:10:00Z",
                "focus": "injuries",
            },
        )

        assert not result.isError
        payload = result.structuredContent
        validate(payload, tool.outputSchema)
        assert len(payload["sources"]) <= 5
        assert payload["rejected_result_count"] <= 5
        assert all(source["relationship"] == "same_matchup_date" for source in payload["sources"])
        assert all(source["url"].startswith("https://") for source in payload["sources"])
        print(
            "tavily returned",
            len(payload["sources"]),
            "same-matchup/date sources and rejected",
            payload["rejected_result_count"],
        )
