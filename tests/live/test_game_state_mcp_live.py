"""Bounded public sports-state checks through the independent stdio MCP process."""

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


async def test_game_state_stdio_live_all_supported_leagues():
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "market_agent.mcp.sports_state"]
    )
    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as session,
    ):
        await session.initialize()
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert set(tools) == {"sports_state_find_games", "sports_state_get_game_state"}
        assert tools["sports_state_find_games"].inputSchema["additionalProperties"] is False
        assert (
            await session.call_tool(
                "sports_state_find_games",
                {"query": "Yankees", "league": "mlb", "timezone": "UTC", "limit": 20},
            )
        ).isError

        for query, league in [
            ("Yankees", "mlb"),
            ("Chicago Bears", "nfl"),
            ("USC Trojans", "ncaa_football"),
        ]:
            result = await session.call_tool(
                "sports_state_find_games",
                {"query": query, "league": league, "timezone": "America/Los_Angeles", "limit": 2},
            )
            assert not result.isError
            payload = result.structuredContent
            validate(payload, tools["sports_state_find_games"].outputSchema)
            assert payload["coverage"]["scoreboard_requests"] <= 3
            assert len(payload["games"]) <= 2
            print(
                league,
                "returned",
                len(payload["games"]),
                "requests",
                payload["coverage"]["scoreboard_requests"],
                "local_date",
                payload["local_date"],
            )
            if not payload["games"]:
                continue
            game = payload["games"][0]
            detail = await session.call_tool(
                "sports_state_get_game_state", {"game_ref": game["game_ref"]}
            )
            assert not detail.isError
            validate(detail.structuredContent, tools["sports_state_get_game_state"].outputSchema)
            state = detail.structuredContent
            assert state["league"] == league
            assert state["game_ref"] == game["game_ref"]
            assert state["source"] in {"espn", "mlb_statsapi"}
            assert state["retrieved_at"]
            assert state["situation"]["sport"] == ("baseball" if league == "mlb" else "football")
            if state["lifecycle"] in {"live", "halftime"} and league == "mlb":
                assert {"inning", "balls", "strikes", "outs", "on_first"} <= set(state["situation"])
            if state["lifecycle"] in {"live", "halftime"} and league != "mlb":
                assert {"possession_team", "down", "distance", "field_position"} <= set(
                    state["situation"]
                )
