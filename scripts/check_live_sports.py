"""Check exact games through the repository's stdio sports-state MCP server.

Example: uv run python scripts/check_live_sports.py --game nfl:Steelers:2026-10-01
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check_games(specifications: list[str], timezone: str) -> None:
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "market_agent.mcp.sports_state"]
    )
    async with (
        stdio_client(parameters) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as session,
    ):
        await session.initialize()
        for specification in specifications:
            try:
                league, query, local_date = specification.split(":", 2)
            except ValueError as error:
                raise ValueError("--game must be LEAGUE:QUERY:YYYY-MM-DD") from error
            found = await session.call_tool(
                "sports_state_find_games",
                {
                    "league": league,
                    "query": query,
                    "local_date": local_date,
                    "timezone": timezone,
                    "limit": 1,
                },
            )
            if found.isError:
                raise RuntimeError(f"{league} discovery failed: {found.content}")
            games = found.structuredContent["games"]
            if not games:
                raise RuntimeError(f"No {league} game found for {query} on {local_date}")
            game = games[0]
            game_ref = game["game_ref"]
            state = await session.call_tool("sports_state_get_game_state", {"game_ref": game_ref})
            if state.isError:
                raise RuntimeError(f"{league} state failed: {state.content}")
            views = (
                ("summary", "full", "line_score", "batting", "pitching")
                if league == "mlb"
                else ("summary", "full", "line_score", "team_stats", "player_stats")
            )
            for view in views:
                box = await session.call_tool(
                    "sports_state_get_box_score", {"game_ref": game_ref, "view": view}
                )
                if box.isError:
                    raise RuntimeError(f"{league} {view} failed: {box.content}")
                if box.structuredContent["view"] != view:
                    raise RuntimeError(f"{league} {view} returned a different view")
            directory = await session.call_tool("sports_state_list_players", {"game_ref": game_ref})
            if directory.isError:
                raise RuntimeError(f"{league} player directory failed: {directory.content}")
            player_count = len(directory.structuredContent["players"])
            if player_count:
                player_id = directory.structuredContent["players"][0]["player_id"]
                player = await session.call_tool(
                    "sports_state_get_player_stats",
                    {"game_ref": game_ref, "player_id": player_id},
                )
                if player.isError:
                    raise RuntimeError(f"{league} player stats failed: {player.content}")
            plays = await session.call_tool(
                "sports_state_get_play_by_play", {"game_ref": game_ref, "limit": 5}
            )
            if plays.isError:
                raise RuntimeError(f"{league} play-by-play failed: {plays.content}")
            print(
                f"{league}: {game['away_team']} at {game['home_team']} "
                f"({game['provider_game_id']}); lifecycle={state.structuredContent['lifecycle']}; "
                f"views={','.join(views)}; players={player_count}; "
                f"plays={len(plays.structuredContent['plays'])}; PASS"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", action="append", required=True, metavar="LEAGUE:QUERY:DATE")
    parser.add_argument("--timezone", default="America/Los_Angeles")
    args = parser.parse_args()
    asyncio.run(check_games(args.game, args.timezone))


if __name__ == "__main__":
    main()
