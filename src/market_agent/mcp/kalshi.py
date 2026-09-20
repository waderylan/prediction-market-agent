"""Independent read-only Kalshi stdio server over the shared API client."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from market_agent.domain import MarketStatus
from market_agent.mcp.common import (
    Limit,
    MarketDetail,
    Query,
    SearchResults,
    controlled_errors,
    project,
)
from market_agent.providers import KalshiClient
from market_agent.providers.kalshi import normalize_query
from market_agent.providers.sports import League, resolve_query
from market_agent.providers.sports_search import KALSHI_SERIES, search_kalshi

Ticker = Annotated[str, Field(strict=True, pattern=r"^[A-Z0-9][A-Z0-9._-]{0,99}$")]
SearchStatus = Literal["open", "closed", "resolved"] | None


class SeriesSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticker: Ticker
    title: Annotated[str, Field(min_length=1, max_length=500)]
    category: Annotated[str, Field(max_length=200)] | None


class SeriesResults(BaseModel):
    query: str
    series: Annotated[list[SeriesSummary], Field(max_length=10)]
    coverage: str = "Locally ranked series metadata; a series does not imply an open market."


class KalshiSearchResults(SearchResults):
    query: str
    normalized_query: str
    series_ticker: Ticker | None


def create_server(client: KalshiClient | None = None) -> FastMCP[Any]:
    active_client = client

    @asynccontextmanager
    async def lifespan(server: FastMCP[Any]) -> AsyncIterator[None]:
        nonlocal active_client
        if client is not None:
            yield
        else:
            async with KalshiClient(max_search_pages=3) as owned:
                active_client = owned
                yield
            active_client = None

    server = FastMCP("Kalshi", lifespan=lifespan, log_level="CRITICAL")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

    @server.tool(annotations=annotations)
    async def kalshi_search_series(
        query: Query,
        category: Query | None = None,
        tags: Query | None = None,
        limit: Limit = 5,
    ) -> SeriesResults:
        """Discover recurring event series by topic, e.g. 'professional baseball game'.
        Optional precision control for generic topics. Sports team searches resolve their series
        automatically in kalshi_search_markets. Pass a returned ticker as series_ticker to
        narrow a search. Category is exact and case-sensitive
        (e.g. Sports); tags is a provider filter string. Results are metadata, not quotes.
        Never use a series ticker as a market ticker. Empty results: try a shorter topic.
        """
        async with controlled_errors("Kalshi"):
            assert active_client is not None
            items = await active_client.search_series(
                query, category=category, tags=tags, limit=limit
            )
            return SeriesResults(query=query, series=[SeriesSummary(**item) for item in items])

    @server.tool(annotations=annotations)
    async def kalshi_search_markets(
        query: Query,
        status: SearchStatus = "open",
        limit: Limit = 5,
        series_ticker: Ticker | None = None,
        league: League | None = None,
        event_date: date | None = None,
    ) -> KalshiSearchResults:
        """Find sports game winners by team or matchup, e.g. Yankees, Padres, Chiefs vs Bills.
        Sports automatically resolve verified league/series metadata; no series call needed.
        Optional league: mlb, nfl, ncaa_football. NCAA covers Division I FBS/FCS.
        Ambiguous cities/abbreviations return clarification choices; ask the user, never guess.
        event_date is scheduled UTC date, not trading close. Doubleheaders remain separate:
        ask for game time/event ID when multiple games match. Only full-game winners supported;
        no spreads, totals, props or futures. series_ticker remains an optional precision filter.
        Generic non-sports topics retain bounded catalog search and optional series discovery.
        Empty results do not prove absence. Defaults open; resolved means settled; null all states.
        Only use exact returned MARKET tickers for details; never construct or guess tickers.
        Never substitute Polymarket for a Kalshi request.
        """
        async with controlled_errors("Kalshi"):
            assert active_client is not None
            sports_query = resolve_query(
                query,
                league
                or (KALSHI_SERIES[series_ticker][0] if series_ticker in KALSHI_SERIES else None),
            )
            if sports_query.clarification:
                return KalshiSearchResults(
                    query=query,
                    normalized_query=normalize_query(query),
                    series_ticker=series_ticker,
                    markets=[],
                    clarification=sports_query.clarification,
                    choices=sports_query.choices,
                    coverage="Clarification required; no upstream market search performed.",
                )
            if sports_query.league:
                sports_markets, discovery = await search_kalshi(
                    active_client,
                    sports_query,
                    status=MarketStatus(status) if status else None,
                    limit=limit,
                    series_ticker=series_ticker,
                    event_date=event_date,
                )
                return KalshiSearchResults(
                    query=query,
                    normalized_query=" vs ".join(t.name for t in sports_query.teams),
                    series_ticker=series_ticker,
                    markets=[project(m) for m in sports_markets],
                    discovery=discovery,
                    coverage="Bounded sports discovery; empty results do not prove absence. "
                    "Multiple event IDs require date/time clarification before selecting a game.",
                )
            if event_date is not None:
                raise ValueError("event_date requires a supported sports query")
            markets = await active_client.search_markets(
                query,
                status=MarketStatus(status) if status else None,
                limit=limit,
                series_ticker=series_ticker,
            )
            return KalshiSearchResults(
                query=query,
                normalized_query=normalize_query(query),
                series_ticker=series_ticker,
                markets=[project(market) for market in markets[:limit]],
                coverage=(
                    f"Bounded {active_client.max_search_pages}-page "
                    f"{'series-filtered' if series_ticker else 'unfiltered'} event scan; "
                    "at most ten candidate events expanded. Empty results do not prove absence; "
                    "older historical markets may be omitted."
                ),
            )

    @server.tool(annotations=annotations)
    async def kalshi_get_market(market_id: Ticker) -> MarketDetail:
        """Read a Kalshi contract by full uppercase MARKET ticker (not event/series ticker).
        Get settlement rules, authority, timestamps, decimal last-trade price, and current bid/ask.
        Null means unavailable. Last-trade prices can be stale; bids/asks are separate fields.
        Use remembered tickers directly for fresh quotes. Read-only public data; no account needed.
        Only copy exact tickers supplied by the user or returned by discovery (including memory).
        Never construct or guess tickers, dates, team codes, or game-time segments.
        """
        async with controlled_errors("Kalshi"):
            assert active_client is not None
            result = project(await active_client.get_market(market_id), detail=True)
            assert isinstance(result, MarketDetail)
            return result

    return server


if __name__ == "__main__":
    create_server().run(transport="stdio")
