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
        assert set(tools) == {
            "sports_state_find_games",
            "sports_state_get_game_state",
            "sports_state_get_box_score",
            "sports_state_list_players",
            "sports_state_get_player_stats",
        }
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
            ("all", "ncaa_football"),
        ]:
            result = await session.call_tool(
                "sports_state_find_games",
                {
                    "query": query,
                    "league": league,
                    "timezone": "America/Los_Angeles",
                    "limit": 2,
                    "compact": query == "all",
                },
            )
            assert not result.isError
            payload = result.structuredContent
            validate(payload, tools["sports_state_find_games"].outputSchema)
            assert payload["coverage"]["scoreboard_requests"] <= 3
            assert len(payload["games"]) <= 2
            assert payload["discovery_mode"] == ("schedule" if query == "all" else "team")
            assert payload["compact"] is (query == "all")
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
            if state["lifecycle"] in {"scheduled", "pregame"}:
                assert state["home_score"] is None and state["away_score"] is None
                assert state["period"] is None and state["clock"] is None
                assert state["last_play"] is None
                if league == "mlb":
                    assert state["situation"]["phase"] == "not_started"
                    assert state["situation"]["half"] == "unknown"
            if state["lifecycle"] in {"live", "halftime"} and league == "mlb":
                assert {"inning", "balls", "strikes", "outs", "on_first"} <= set(state["situation"])
                assert state["situation"]["phase"] in {"active", "transition"}
            if state["lifecycle"] in {"live", "halftime"} and league != "mlb":
                assert {"possession_team", "down", "distance", "field_position"} <= set(
                    state["situation"]
                )
            box = await session.call_tool(
                "sports_state_get_box_score", {"game_ref": game["game_ref"]}
            )
            assert not box.isError
            validate(box.structuredContent, tools["sports_state_get_box_score"].outputSchema)
            box_score = box.structuredContent
            assert box_score["league"] == league
            assert box_score["game_ref"] == game["game_ref"]
            assert box_score["sport"] == ("baseball" if league == "mlb" else "football")
            assert box_score["source"] in {"espn", "mlb_statsapi"}
            assert box_score["observation_id"].startswith(f"{box_score['source']}:")
            assert box_score["completeness"]
            if league == "mlb":
                assert {"line_score", "batting", "pitching"} <= set(box_score)
                for team in ("away", "home"):
                    for player in box_score["batting"][team]:
                        assert player["player_id"] and player["name"]
                    for player in box_score["pitching"][team]:
                        assert player["player_id"] and player["name"]
                        if "outs_recorded" in player:
                            assert player["outs_recorded"] >= 0
            else:
                assert {"line_score", "team_stats", "player_stats"} <= set(box_score)
            players = await session.call_tool(
                "sports_state_list_players", {"game_ref": game["game_ref"]}
            )
            assert not players.isError
            validate(players.structuredContent, tools["sports_state_list_players"].outputSchema)
            directory = players.structuredContent
            assert directory["game_ref"] == game["game_ref"]
            assert directory["observation_id"] == box_score["observation_id"]
            if directory["players"]:
                selected = directory["players"][0]
                player = await session.call_tool(
                    "sports_state_get_player_stats",
                    {
                        "game_ref": game["game_ref"],
                        "player_id": selected["player_id"],
                    },
                )
                assert not player.isError
                validate(
                    player.structuredContent,
                    tools["sports_state_get_player_stats"].outputSchema,
                )
                assert player.structuredContent["player_id"] == selected["player_id"]
                assert player.structuredContent["team"] == selected["team"]
                assert player.structuredContent["observation_id"] == box_score["observation_id"]
