"""Independent read-only sports state and box-score stdio MCP server."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from market_agent.providers.game_state import (
    BoxScore,
    FindGamesResult,
    GameState,
    SportsStateClient,
    SportsStateError,
)
from market_agent.providers.sports import League

Query = Annotated[str, Field(strict=True, min_length=1, max_length=200, pattern=r"\S")]
Timezone = Annotated[str, Field(strict=True, min_length=1, max_length=100, pattern=r"\S")]
Limit = Annotated[int, Field(strict=True, ge=1, le=10)]
Compact = Annotated[bool, Field(strict=True)]
GameRef = Annotated[str, Field(strict=True, min_length=1, max_length=2048, pattern=r"\S")]


def _tool_error(error: SportsStateError) -> ToolError:
    return ToolError(
        json.dumps(
            {
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "fields": error.fields,
                }
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def create_server(client: SportsStateClient | None = None) -> FastMCP[Any]:
    active_client = client

    @asynccontextmanager
    async def lifespan(server: FastMCP[Any]) -> AsyncIterator[None]:
        nonlocal active_client
        if client is not None:
            yield
        else:
            async with SportsStateClient() as owned:
                active_client = owned
                yield
            active_client = None

    server = FastMCP("Sports state and box scores", lifespan=lifespan, log_level="CRITICAL")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

    @server.tool(annotations=annotations)
    async def sports_state_find_games(
        query: Query,
        league: League,
        timezone: Timezone,
        local_date: date | None = None,
        limit: Limit = 5,
        compact: Compact = False,
    ) -> FindGamesResult:
        """Find a current-day MLB, NFL, or NCAA Division I football game by team or matchup.
        league and IANA timezone are required. local_date is one exact local calendar day; when
        omitted, the server uses and discloses today's date in that timezone. Returns at most ten
        provider-backed game choices. Use query="all" for a bounded same-day league slate (at
        most ten games); it is not a season scan or exhaustive pagination surface. Set compact=true
        to return only selection identity, timing, lifecycle, and game_ref for each candidate.
        Copy one returned game_ref unchanged into sports_state_get_game_state for the current
        situation or sports_state_get_box_score for line scoring and team/player game statistics.
        Ambiguous teams return choices and exact retry guidance without provider I/O. References
        are scoped to the requested timezone. This tool does not return odds, forecasts, contract
        rules, or market settlement.
        """
        assert active_client is not None
        try:
            async with asyncio.timeout(30):
                return await active_client.find_games(
                    query,
                    league=league,
                    timezone=timezone,
                    local_date=local_date,
                    limit=limit,
                    compact=compact,
                )
        except TimeoutError:
            raise _tool_error(
                SportsStateError(
                    "tool_timeout", "sports_state_find_games exceeded its 30-second budget"
                )
            ) from None
        except SportsStateError as error:
            raise _tool_error(error) from None

    @server.tool(annotations=annotations)
    async def sports_state_get_game_state(game_ref: GameRef) -> GameState:
        """Read the live score and situation from an unchanged discovery game_ref.
        The opaque reference is checksummed and restart-safe; never construct it from an ESPN ID,
        MLB gamePk, team, date, market ID, or prior knowledge. Returns common score/lifecycle data
        plus the current baseball inning/count/runners/batter/pitcher or football possession,
        down, distance, field position, and timeouts. Use sports_state_get_box_score instead for
        period scoring, team totals, and player statistics. This detail response, not discovery,
        is authoritative for normalized state fields. Scheduled/pregame placeholders and
        unavailable fields are null, not inferred. ESPN is primary; exact-identity MLB StatsAPI
        fallback is MLB-only. A final sporting result does not establish prediction-market
        settlement or contract equivalence.
        """
        assert active_client is not None
        try:
            async with asyncio.timeout(30):
                return await active_client.get_game_state(game_ref)
        except TimeoutError:
            raise _tool_error(
                SportsStateError(
                    "tool_timeout", "sports_state_get_game_state exceeded its 30-second budget"
                )
            ) from None
        except SportsStateError as error:
            raise _tool_error(error) from None

    @server.tool(annotations=annotations)
    async def sports_state_get_box_score(game_ref: GameRef) -> BoxScore:
        """Read one exact MLB, NFL, or NCAA football box score from a discovery game_ref.
        Copy game_ref unchanged from sports_state_find_games; never provide or construct a provider
        identifier. Baseball returns inning-by-inning scoring, runs/hits/errors/left-on-base totals,
        and game-only batting and pitching lines. Football returns period scoring, team statistics,
        and provider-categorized player lines. Stable player IDs, source/retrieval provenance,
        lifecycle-aware cache metadata, completeness, and warnings are included. Provider-omitted
        optional statistics are omitted rather than filled from season totals. This tool excludes
        play-by-play and does not use Tavily. ESPN is primary; MLB StatsAPI is an exact-identity
        MLB-only fallback.
        """
        assert active_client is not None
        try:
            async with asyncio.timeout(30):
                return await active_client.get_box_score(game_ref)
        except TimeoutError:
            raise _tool_error(
                SportsStateError(
                    "tool_timeout", "sports_state_get_box_score exceeded its 30-second budget"
                )
            ) from None
        except SportsStateError as error:
            raise _tool_error(error) from None

    # The bundled FastMCP generator validates unexpected kwargs at call time but omits the
    # equivalent JSON Schema keyword. Publish that constraint so agents can see it at discovery.
    for tool_name in (
        "sports_state_find_games",
        "sports_state_get_game_state",
        "sports_state_get_box_score",
    ):
        registered = server._tool_manager.get_tool(tool_name)  # noqa: SLF001
        assert registered is not None
        registered.parameters["additionalProperties"] = False

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
