"""Independent read-only Kalshi stdio server over the shared API client."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

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

Ticker = Annotated[str, Field(strict=True, pattern=r"^[A-Z0-9][A-Z0-9._-]{0,99}$")]
SearchStatus = Literal["open", "closed", "resolved"] | None


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
    async def kalshi_search_markets(
        query: Query,
        status: SearchStatus = "open",
        limit: Limit = 5,
    ) -> SearchResults:
        """Find Kalshi contracts by short event topic or known ticker fragment. Scans only
        three public event pages and ranks locally, so discovery is incomplete. Empty results
        do not imply absence. Defaults to open; resolved means settled; null includes all states.
        Returns up to ten candidates. Use kalshi_get_market with a known MARKET ticker for a
        direct lookup or full settlement rules. Never substitute Polymarket for a Kalshi request.
        """
        async with controlled_errors("Kalshi"):
            assert active_client is not None
            markets = await active_client.search_markets(
                query,
                status=MarketStatus(status) if status else None,
                limit=limit,
            )
            return SearchResults(
                markets=[project(market) for market in markets[:limit]],
                coverage=(
                    "Bounded three-page Kalshi catalog scan; empty results do not prove absence."
                ),
            )

    @server.tool(annotations=annotations)
    async def kalshi_get_market(market_id: Ticker) -> MarketDetail:
        """Read a Kalshi contract by full uppercase MARKET ticker (not event/series ticker).
        Get settlement rules, authority, timestamps, decimal last-trade price, and current bid/ask.
        Null means unavailable. Last-trade prices can be stale; bids/asks are separate fields.
        Use remembered tickers directly for fresh quotes. Read-only public data; no account needed.
        """
        async with controlled_errors("Kalshi"):
            assert active_client is not None
            result = project(await active_client.get_market(market_id), detail=True)
            assert isinstance(result, MarketDetail)
            return result

    return server


if __name__ == "__main__":
    create_server().run(transport="stdio")
