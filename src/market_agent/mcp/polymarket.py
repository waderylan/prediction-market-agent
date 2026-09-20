"""Read-only stdio MCP surface over the ordinary Polymarket client."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from market_agent.domain import MarketStatus
from market_agent.mcp.common import (
    Limit,
    MarketDetail,
    MarketId,
    Query,
    SearchResults,
    controlled_errors,
    project,
)
from market_agent.providers import PolymarketClient


def create_server(client: PolymarketClient | None = None) -> FastMCP[Any]:
    active_client = client

    @asynccontextmanager
    async def lifespan(server: FastMCP[Any]) -> AsyncIterator[None]:
        nonlocal active_client
        if client is not None:
            yield
        else:
            async with PolymarketClient() as owned:
                active_client = owned
                yield
            active_client = None

    server = FastMCP("Polymarket", lifespan=lifespan, log_level="CRITICAL")
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

    @server.tool(annotations=annotations)
    async def polymarket_search_markets(
        query: Query,
        status: MarketStatus | None = MarketStatus.OPEN,
        limit: Limit = 5,
    ) -> SearchResults:
        """Find Polymarket contracts by short topic/name, not a full question. Defaults to open
        markets; use null status to include historical markets. Returns up to 10 unique
        candidates with decimal prices and IDs. Search is not exhaustive; try a shorter topic
        if empty. Fetch a candidate by ID for resolution rules before interpreting its odds.
        Scans up to three pages, stopping when enough candidates are found. Resolved requires
        explicit provider resolution metadata; a zero or one price does not prove settlement.
        """
        async with controlled_errors("Polymarket"):
            assert active_client is not None
            markets = await active_client.search_markets(query, status=status, limit=limit)
            return SearchResults(
                markets=[project(market) for market in markets[:limit]],
                coverage=f"Bounded search of up to {active_client.max_search_pages} pages; "
                "stops when enough unique candidates are found. "
                "Empty results do not prove absence.",
            )

    @server.tool(annotations=annotations)
    async def polymarket_get_market(market_id: MarketId) -> MarketDetail:
        """Read a Polymarket contract by its numeric Gamma market ID from search (not slug,
        event ID, or token ID). Returns current snapshot prices, rules, source and retrieval
        time. Null means unavailable; truncated rules are incomplete. Market price is not
        an independent probability forecast. No trading or account access.
        YES bid/ask are available only for standard Yes/No outcome ordering. Team-named or
        reversed outcomes have null YES bid/ask; do not infer a YES mapping for them.
        """
        async with controlled_errors("Polymarket"):
            assert active_client is not None
            result = project(await active_client.get_market(market_id), detail=True)
            assert isinstance(result, MarketDetail)
            return result

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
