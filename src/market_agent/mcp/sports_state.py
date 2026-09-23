"""Independent read-only sports game-detail stdio MCP server."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from market_agent.providers.game_state import (
    BoxScoreView,
    BoxScoreViewName,
    FindGamesResult,
    GameState,
    PlayByPlay,
    PlayerDirectory,
    PlayerStats,
    SportsStateClient,
    SportsStateError,
    TeamSide,
    project_box_score,
)
from market_agent.providers.sports import League

Query = Annotated[str, Field(strict=True, min_length=1, max_length=200, pattern=r"\S")]
Timezone = Annotated[str, Field(strict=True, min_length=1, max_length=100, pattern=r"\S")]
Limit = Annotated[int, Field(strict=True, ge=1, le=10)]
Compact = Annotated[bool, Field(strict=True)]
GameRef = Annotated[str, Field(strict=True, min_length=1, max_length=2048, pattern=r"\S")]
PlayerId = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")]
PlayId = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")]
PlayLimit = Annotated[int, Field(strict=True, ge=1, le=50)]
Period = Annotated[int, Field(strict=True, ge=1, le=30)]


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

    server = FastMCP("Sports game detail", lifespan=lifespan, log_level="CRITICAL")
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
        Copy one returned game_ref unchanged into the exact-game tools for current state, box
        scores, player lookup and detail, or bounded play-by-play.
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
        down, distance, field position, and timeouts. Use sports_state_get_box_score for period
        scoring and team totals, or the player tools for one player's statistics. This detail
        response, not discovery,
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
    async def sports_state_get_box_score(
        game_ref: GameRef,
        view: BoxScoreViewName = "summary",
        team_side: TeamSide = "both",
    ) -> BoxScoreView:
        """Read one exact MLB, NFL, or NCAA football box score from a discovery game_ref.
        Copy game_ref unchanged from sports_state_find_games; never provide or construct a provider
        identifier. The default view="summary" returns the line score plus compact player leaders;
        present that view and tell the user they can request view="full" or one available section.
        Section views are line_score, batting, pitching, team_stats, and player_stats as applicable
        to the sport. team_side can narrow player/team sections to away or home. Do not reconstruct
        omitted sections. Inning participation distinguishes played, not-yet-reached, and
        unnecessary home half-innings. Completeness identifies exact missing required and optional
        fields. Season statistics and play-by-play are excluded. ESPN is primary; MLB StatsAPI is
        an exact-identity MLB-only fallback.
        """
        assert active_client is not None
        try:
            async with asyncio.timeout(30):
                full_box_score = await active_client.get_box_score(game_ref)
                return project_box_score(full_box_score, view=view, team_side=team_side)
        except TimeoutError:
            raise _tool_error(
                SportsStateError(
                    "tool_timeout", "sports_state_get_box_score exceeded its 30-second budget"
                )
            ) from None
        except SportsStateError as error:
            raise _tool_error(error) from None

    @server.tool(annotations=annotations)
    async def sports_state_list_players(game_ref: GameRef) -> PlayerDirectory:
        """List players with available game-stat lines for one discovery game_ref.
        The result is a compact lookup directory: stable provider player_id, player name, team,
        side, and available stat groups. It is not a full active or season roster. Copy one
        returned player_id unchanged with the same game_ref into
        sports_state_get_player_stats. The directory reuses the exact normalized box-score
        observation, cache, identity checks, and MLB fallback without exposing the full box score.
        """
        assert active_client is not None
        try:
            async with asyncio.timeout(30):
                return await active_client.list_players(game_ref)
        except TimeoutError:
            raise _tool_error(
                SportsStateError(
                    "tool_timeout", "sports_state_list_players exceeded its 30-second budget"
                )
            ) from None
        except SportsStateError as error:
            raise _tool_error(error) from None

    @server.tool(annotations=annotations)
    async def sports_state_get_player_stats(game_ref: GameRef, player_id: PlayerId) -> PlayerStats:
        """Read one player's game-only statistics from an exact discovery game_ref.
        First call sports_state_list_players and copy its player_id unchanged. MLB returns that
        player's batting and/or pitching line. NFL and NCAA football return only that player's
        available categorized stat groups. The result omits every unrelated player, season
        statistics, and play-by-play. It reuses the same provider observation and safety bounds
        as sports_state_get_box_score; ESPN is primary and MLB fallback remains exact-identity.
        """
        assert active_client is not None
        try:
            async with asyncio.timeout(30):
                return await active_client.get_player_stats(game_ref, player_id)
        except TimeoutError:
            raise _tool_error(
                SportsStateError(
                    "tool_timeout", "sports_state_get_player_stats exceeded its 30-second budget"
                )
            ) from None
        except SportsStateError as error:
            raise _tool_error(error) from None

    @server.tool(annotations=annotations)
    async def sports_state_get_play_by_play(
        game_ref: GameRef,
        limit: PlayLimit = 20,
        before_play_id: PlayId | None = None,
        after_play_id: PlayId | None = None,
        play_filter: Literal["all", "scoring"] = "all",
        period: Period | None = None,
        team: Literal["away", "home"] | None = None,
    ) -> PlayByPlay:
        """Read a bounded chronological play window for one exact discovery game_ref.
        With no anchor, returns the latest limit plays. Copy first_play_id into before_play_id to
        page backward, or resume_after_play_id into after_play_id to request only later unseen
        plays. before_play_id and after_play_id are mutually exclusive. limit is 1-50. Optional
        play_filter="scoring", period, and home/away team filters support focused inspection.
        Every play carries a stable provider-backed play_id, sequence, text, score, team when
        supplied, event_kind, and sport-specific context. Baseball context uses outs_before and
        outs_after; never describe outs_after as pre-play state. Substitutions are labeled and
        structured separately from pitches and plate appearances. ESPN provides pitch/action
        granularity for MLB and play granularity for football; exact-identity MLB fallback provides
        at-bat granularity.
        Results exclude odds, win probability, season statistics, and raw provider payloads.
        """
        assert active_client is not None
        try:
            async with asyncio.timeout(30):
                return await active_client.get_play_by_play(
                    game_ref,
                    limit=limit,
                    before_play_id=before_play_id,
                    after_play_id=after_play_id,
                    play_filter=play_filter,
                    period=period,
                    team=team,
                )
        except TimeoutError:
            raise _tool_error(
                SportsStateError(
                    "tool_timeout", "sports_state_get_play_by_play exceeded its 30-second budget"
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
        "sports_state_list_players",
        "sports_state_get_player_stats",
        "sports_state_get_play_by_play",
    ):
        registered = server._tool_manager.get_tool(tool_name)  # noqa: SLF001
        assert registered is not None
        registered.parameters["additionalProperties"] = False

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
