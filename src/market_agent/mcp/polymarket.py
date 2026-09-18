"""Read-only stdio MCP surface over the ordinary Polymarket client."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_agent.domain import CanonicalMarket, MarketStatus
from market_agent.providers import PolymarketClient
from market_agent.providers.exceptions import (
    MarketHTTPError,
    MarketMissingDataError,
    MarketTransportError,
    MarketValidationError,
)

Query = Annotated[str, Field(strict=True, min_length=1, max_length=200, pattern=r"\S")]
MarketId = Annotated[str, Field(strict=True, pattern=r"^[0-9]{1,20}$")]
Limit = Annotated[int, Field(strict=True, ge=1, le=10)]
ShortText = Annotated[str, Field(max_length=500)]
Price = Annotated[Decimal, Field(ge=0, le=1, max_digits=20)] | None


class MarketSummary(BaseModel):
    """Small canonical projection; prices are decimal strings, never forecasts."""

    model_config = ConfigDict(extra="forbid")
    market_id: MarketId
    title: ShortText
    status: MarketStatus
    yes_price: Price
    no_price: Price
    yes_bid: Price
    yes_ask: Price
    close_time: datetime | None
    source_url: Annotated[str, Field(max_length=2048)]
    retrieved_at: datetime


class MarketDetail(MarketSummary):
    outcomes: Annotated[list[ShortText], Field(min_length=2, max_length=10)]
    rules: Annotated[str, Field(max_length=12000)] | None
    rules_truncated: bool
    resolution_source: Annotated[str, Field(max_length=2000)] | None
    resolution_deadline: datetime | None


class SearchResults(BaseModel):
    markets: Annotated[list[MarketSummary], Field(max_length=10)]
    coverage: str = "Bounded first-page candidates; empty results do not prove no market exists."


def project(market: CanonicalMarket, *, detail: bool = False) -> MarketSummary:
    fields = MarketSummary.model_fields
    data = {key: value for key, value in market.model_dump(mode="json").items() if key in fields}
    if detail:
        data.update(
            outcomes=list(market.outcomes),
            rules=market.rules[:12000] if market.rules else None,
            rules_truncated=bool(market.rules and len(market.rules) > 12000),
            resolution_source=market.resolution_source,
            resolution_deadline=market.resolution_deadline,
        )
        return MarketDetail.model_validate(data)
    return MarketSummary.model_validate(data)


@asynccontextmanager
async def controlled_errors() -> AsyncIterator[None]:
    """Never expose provider response bodies or validation payloads in tool errors."""
    try:
        yield
    except MarketTransportError:
        raise ToolError("Polymarket is unreachable or timed out. Try again later.") from None
    except MarketHTTPError as error:
        raise ToolError(
            f"Polymarket returned HTTP {error.status_code}. "
            + ("Try again later." if error.retryable else "Check the market ID or request.")
        ) from None
    except MarketMissingDataError:
        raise ToolError(
            "Polymarket returned incomplete market data; cannot identify the contract."
        ) from None
    except (MarketValidationError, ValidationError):
        raise ToolError(
            "Polymarket returned malformed or oversized market data; cannot use it."
        ) from None
    except ValueError:
        raise ToolError(
            "Invalid market request. Check query, identifier, status, and limit."
        ) from None


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
        markets; use null status to include historical markets. Returns up to 10 first-page
        candidates with decimal prices and IDs. Search is not exhaustive; try a shorter topic
        if empty. Fetch a candidate by ID for resolution rules before interpreting its odds.
        """
        async with controlled_errors():
            assert active_client is not None
            markets = await active_client.search_markets(query, status=status, limit=limit)
            return SearchResults(markets=[project(market) for market in markets[:limit]])

    @server.tool(annotations=annotations)
    async def polymarket_get_market(market_id: MarketId) -> MarketDetail:
        """Read a Polymarket contract by its numeric Gamma market ID from search (not slug,
        event ID, or token ID). Returns current snapshot prices, rules, source and retrieval
        time. Null means unavailable; truncated rules are incomplete. Market price is not
        an independent probability forecast. No trading or account access.
        """
        async with controlled_errors():
            assert active_client is not None
            result = project(await active_client.get_market(market_id), detail=True)
            assert isinstance(result, MarketDetail)
            return result

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
