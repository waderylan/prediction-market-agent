"""Bounded canonical projections and safe errors shared by separate market servers."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import quote

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from market_agent.domain import CanonicalMarket, MarketStatus, Platform
from market_agent.providers.exceptions import (
    MarketHTTPError,
    MarketMissingDataError,
    MarketTransportError,
    MarketValidationError,
)
from market_agent.providers.sports import DiscoveryCoverage, SportsEvent

Query = Annotated[str, Field(strict=True, min_length=1, max_length=200, pattern=r"\S")]
MarketId = Annotated[str, Field(strict=True, pattern=r"^[0-9]{1,20}$")]
Limit = Annotated[int, Field(strict=True, ge=1, le=10)]
ShortText = Annotated[str, Field(max_length=500)]
Price = Annotated[Decimal, Field(ge=0, le=1, max_digits=20)] | None


class OutcomeQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: ShortText
    side: Literal["yes", "no"] | None = None
    canonical_participant: ShortText | None = None
    price: Price = None
    price_kind: Literal["last_trade", "derived_complement", "provider_snapshot"]
    bid: Price = None
    ask: Price = None


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
    event_id: str | None = None
    raw_title: ShortText | None = None
    sports: SportsEvent | None = None
    outcome_quotes: Annotated[list[OutcomeQuote], Field(max_length=10)] = Field(
        default_factory=list
    )
    api_url: str | None = None
    market_url: str | None = None
    provider_updated_at: datetime | None = None
    price_observed_at: datetime | None = None
    last_trade_at: datetime | None = None
    provider_last_trade_price: Price = None
    provider_last_trade_outcome: ShortText | None = None
    expected_resolution_time: datetime | None = None

    @field_validator(
        "provider_updated_at", "price_observed_at", "last_trade_at", "expected_resolution_time"
    )
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("provider timestamp must include a timezone")
        return value


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
    discovery: DiscoveryCoverage | None = None
    clarification: str | None = None
    choices: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)


def project(market: CanonicalMarket, *, detail: bool = False) -> MarketSummary:
    fields = MarketSummary.model_fields
    data = {key: value for key, value in market.model_dump(mode="json").items() if key in fields}
    data.update({key: value for key, value in market.provider_data.items() if key in fields})
    data["api_url"] = str(market.source_url)
    # Official Gamma discovery docs specify /market/{slug}; never derive a slug.
    slug = market.provider_data.get("slug")
    if market.platform == Platform.POLYMARKET and isinstance(slug, str) and slug:
        data["market_url"] = f"https://polymarket.com/market/{quote(slug, safe='')}"
    sports = data.get("sports")
    if sports:
        mapping = dict(zip(sports["raw_participants"], sports["participants"], strict=True))
        data["outcome_quotes"] = [
            {
                **outcome,
                "canonical_participant": mapping.get(outcome["label"])
                if outcome.get("side") != "no"
                else None,
            }
            for outcome in data.get("outcome_quotes", [])
        ]
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
        async with asyncio.timeout(30):
            yield
    except TimeoutError:
        raise ToolError(
            f"{provider} exceeded the 30-second tool budget. Narrow the request or retry."
        ) from None
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
