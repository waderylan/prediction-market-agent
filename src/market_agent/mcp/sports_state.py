"""Independent read-only sports game-state stdio MCP server."""

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
    FindGamesResult,
    GameState,
    SportsStateClient,
    SportsStateError,
)
from market_agent.providers.sports import League

Query = Annotated[str, Field(strict=True, min_length=1, max_length=200, pattern=r"\S")]
Timezone = Annotated[str, Field(strict=True, min_length=1, max_length=100, pattern=r"\S")]
Limit = Annotated[int, Field(strict=True, ge=1, le=10)]
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

    server = FastMCP("Sports game state", lifespan=lifespan, log_level="CRITICAL")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

    @server.tool(annotations=annotations)
    async def sports_state_find_games(
        query: Query,
        league: League,
        timezone: Timezone,
        local_date: date | None = None,
        limit: Limit = 5,
    ) -> FindGamesResult:
        """Find a current-day MLB, NFL, or NCAA Division I football game by team or matchup.
        league and IANA timezone are required. local_date is one exact local calendar day; when
        omitted, the server uses and discloses today's date in that timezone. Returns at most ten
        provider-backed game choices. Copy one returned game_ref unchanged into
        sports_state_get_game_state. Ambiguous teams return choices without provider I/O. This
        tool does not return odds, forecasts, contract rules, or market settlement.
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
        """Read one current normalized game snapshot from an unchanged discovery game_ref.
        The opaque reference is checksummed and restart-safe; never construct it from an ESPN ID,
        MLB gamePk, team, date, market ID, or prior knowledge. Returns common score/lifecycle data
        and exactly one league-specific situation object. Null situation fields are unavailable,
        not inferred. ESPN is primary; exact-identity MLB StatsAPI fallback is MLB-only. A final
        sporting result does not establish prediction-market settlement or contract equivalence.
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

    # The bundled FastMCP generator validates unexpected kwargs at call time but omits the
    # equivalent JSON Schema keyword. Publish that constraint so agents can see it at discovery.
    for tool_name in ("sports_state_find_games", "sports_state_get_game_state"):
        registered = server._tool_manager.get_tool(tool_name)  # noqa: SLF001
        assert registered is not None
        registered.parameters["additionalProperties"] = False

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
