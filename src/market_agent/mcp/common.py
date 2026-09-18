"""Bounded canonical projections and safe errors shared by separate market servers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_agent.domain import CanonicalMarket, MarketStatus, Platform
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
    platform: Platform
    market_id: Annotated[str, Field(min_length=1, max_length=100)]
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
    model_config = ConfigDict(extra="forbid")
    markets: Annotated[list[MarketSummary], Field(max_length=10)]
    coverage: Annotated[str, Field(max_length=200)] = (
        "Bounded first-page candidates; empty results do not prove no market exists."
    )


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
async def controlled_errors(provider: str) -> AsyncIterator[None]:
    """Never expose provider response bodies or validation payloads in tool errors."""
    try:
        yield
    except MarketTransportError:
        raise ToolError(f"{provider} is unreachable or timed out. Try again later.") from None
    except MarketHTTPError as error:
        raise ToolError(
            f"{provider} returned HTTP {error.status_code}. "
            + ("Try again later." if error.retryable else "Check the market ID or request.")
        ) from None
    except MarketMissingDataError:
        raise ToolError(
            f"{provider} returned incomplete market data; cannot identify the contract."
        ) from None
    except (MarketValidationError, ValidationError):
        raise ToolError(
            f"{provider} returned malformed or oversized market data; cannot use it."
        ) from None
    except ValueError:
        raise ToolError(
            "Invalid market request. Check query, identifier, status, and limit."
        ) from None
