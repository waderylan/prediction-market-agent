"""Bounded canonical projections and safe errors shared by separate market servers."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Self
from urllib.parse import quote
from zoneinfo import ZoneInfo

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from market_agent.domain import CanonicalMarket, MarketStatus, Platform
from market_agent.providers.exceptions import (
    MarketHTTPError,
    MarketMissingDataError,
    MarketRequestError,
    MarketTransportError,
    MarketValidationError,
)
from market_agent.providers.sports import DiscoveryCoverage, LiveStatus, SportsEvent

Query = Annotated[str, Field(strict=True, min_length=1, max_length=200, pattern=r"\S")]
Continuation = Annotated[str, Field(strict=True, min_length=1, max_length=2000, pattern=r"\S")]
MarketId = Annotated[str, Field(strict=True, pattern=r"^[0-9]{1,20}$")]
Limit = Annotated[int, Field(strict=True, ge=1, le=10)]
ShortText = Annotated[str, Field(max_length=500)]
Price = Annotated[Decimal, Field(ge=0, le=1, max_digits=20)] | None
QuoteFreshness = Literal["current", "stale", "timestamp_unavailable", "not_trading"]


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
    quote_as_of: datetime | None
    quote_freshness: QuoteFreshness
    quote_freshness_reason: str
    quote_is_stale: bool
    quote_stale_reason: str | None = None
    observation_id: str | None = None
    cache_hit: bool = False
    cache_age_ms: int = Field(default=0, ge=0)
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
    timing_warning: str | None = None
    settlement_value: Price = None
    winning_outcome: ShortText | None = None
    resolved_at: datetime | None = None

    @field_validator(
        "provider_updated_at",
        "price_observed_at",
        "last_trade_at",
        "expected_resolution_time",
        "quote_as_of",
        "resolved_at",
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


class SportsGame(BaseModel):
    """One game with all returned provider contracts for its outcomes."""

    model_config = ConfigDict(extra="forbid")
    event_id: str
    title: ShortText
    league: str
    participants: Annotated[list[ShortText], Field(min_length=2, max_length=2)]
    scheduled_start: datetime | None
    timezone: str
    local_date: str | None
    kickoff_local: str | None
    label: str
    live_status: LiveStatus
    live_status_reason: str
    market_url: str | None = None
    contracts: Annotated[list[MarketSummary], Field(min_length=1, max_length=4)]


class SearchResults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markets: Annotated[list[MarketSummary], Field(max_length=10)]
    games: Annotated[list[SportsGame], Field(max_length=10)] = Field(default_factory=list)
    coverage: Annotated[str, Field(max_length=200)] = (
        "Bounded first-page candidates; empty results do not prove no market exists."
    )
    discovery: DiscoveryCoverage | None = None
    clarification: str | None = None
    choices: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)
    result_kind: Literal["sports_games", "generic_markets", "clarification"] = "generic_markets"
    contracts_location: Literal["games[].contracts", "markets[]", "none"] = "markets[]"
    usage_note: str = Field(
        default=(
            "Sports results place contracts under games[].contracts; markets[] is reserved for "
            "generic topic search. Read result_kind and contracts_location before consuming."
        ),
        max_length=300,
    )

    @model_validator(mode="after")
    def identify_result_shape(self) -> Self:
        if self.clarification:
            self.result_kind = "clarification"
            self.contracts_location = "none"
        elif self.games:
            self.result_kind = "sports_games"
            self.contracts_location = "games[].contracts"
        else:
            self.result_kind = "generic_markets"
            self.contracts_location = "markets[]"
        return self


def project(market: CanonicalMarket, *, detail: bool = False) -> MarketSummary:
    fields = MarketSummary.model_fields
    data = {key: value for key, value in market.model_dump(mode="json").items() if key in fields}
    data.update({key: value for key, value in market.provider_data.items() if key in fields})
    data["api_url"] = str(market.source_url)
    # Official Gamma discovery docs specify /market/{slug}; never derive a slug.
    slug = market.provider_data.get("slug")
    if market.platform == Platform.POLYMARKET and isinstance(slug, str) and slug:
        data["market_url"] = f"https://polymarket.com/market/{quote(slug, safe='')}"
    series_ticker = market.provider_data.get("series_ticker")
    if (
        market.platform == Platform.KALSHI
        and isinstance(series_ticker, str)
        and series_ticker
        and market.event_id
    ):
        data["market_url"] = (
            f"https://kalshi.com/markets/{quote(series_ticker.lower(), safe='')}/x/"
            f"{quote(market.event_id.lower(), safe='')}"
        )
    # Neither provider documents its generic record-update clock as the timestamp of the
    # returned quote. Only expose an actual price-observation clock when one exists.
    data["quote_as_of"] = data.get("price_observed_at")
    quote_as_of = data["quote_as_of"]
    actively_trading = market.status in {
        MarketStatus.OPEN,
        MarketStatus.PAUSED,
        MarketStatus.UNOPENED,
    }
    if not actively_trading:
        freshness: QuoteFreshness = "not_trading"
        freshness_reason = "The contract is not actively trading; displayed prices are historical."
    elif quote_as_of is None:
        freshness = "timestamp_unavailable"
        freshness_reason = (
            "Provider supplies no authoritative timestamp for this quote observation. "
            "This is unknown freshness, not evidence that the quote is stale."
        )
    else:
        observed = (
            datetime.fromisoformat(quote_as_of.replace("Z", "+00:00"))
            if isinstance(quote_as_of, str)
            else quote_as_of
        )
        age = market.retrieved_at - observed.astimezone(UTC)
        if age > timedelta(minutes=15):
            freshness = "stale"
            freshness_reason = "The authoritative quote timestamp is more than 15 minutes old."
        else:
            freshness = "current"
            freshness_reason = (
                "The authoritative quote timestamp is within 15 minutes of retrieval."
            )
    data["quote_freshness"] = freshness
    data["quote_freshness_reason"] = freshness_reason
    data["quote_is_stale"] = freshness == "stale"
    data["quote_stale_reason"] = freshness_reason if freshness == "stale" else None
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
        scheduled = sports.get("scheduled_start")
        expected = data.get("expected_resolution_time")
        if scheduled and expected:
            scheduled_at = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
            expected_at = datetime.fromisoformat(expected.replace("Z", "+00:00"))
            if expected_at < scheduled_at:
                data["expected_resolution_time"] = None
                data["timing_warning"] = (
                    "Provider expected resolution preceded the scheduled start and is omitted."
                )
        close = data.get("close_time")
        if scheduled and close:
            scheduled_at = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
            close_at = datetime.fromisoformat(close.replace("Z", "+00:00"))
            if close_at - scheduled_at > timedelta(hours=24):
                close_warning = (
                    "Provider trading close is more than 24 hours after scheduled start; it is "
                    "not kickoff and may reflect administrative or postponement handling. Verify "
                    "the contract rules before interpreting the timing."
                )
                existing = data.get("timing_warning")
                data["timing_warning"] = (
                    f"{existing} {close_warning}" if existing else close_warning
                )
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


def _live_status(
    sports: SportsEvent, contracts: list[MarketSummary], observed_at: datetime
) -> tuple[LiveStatus, str]:
    if contracts and all(contract.status == MarketStatus.RESOLVED for contract in contracts):
        return "settled", "All returned outcome contracts are resolved."
    if sports.scheduled_start is None:
        return "pregame", "The provider supplies no scheduled start; live phase is unknown."
    if observed_at < sports.scheduled_start:
        return "pregame", "The scheduled start is in the future."
    duration = timedelta(hours=5 if sports.league == "mlb" else 4, minutes=30)
    if observed_at <= sports.scheduled_start + duration and any(
        contract.status in {MarketStatus.OPEN, MarketStatus.PAUSED} for contract in contracts
    ):
        return "live", "The game is inside the league-specific expected live window."
    return (
        "awaiting_resolution",
        "The expected live window has ended and settlement is not confirmed.",
    )


def group_games(markets: list[CanonicalMarket], *, timezone: str) -> list[SportsGame]:
    zone = ZoneInfo(timezone)
    grouped: dict[str, list[CanonicalMarket]] = {}
    for market in markets:
        if market.event_id and market.provider_data.get("sports"):
            grouped.setdefault(market.event_id, []).append(market)
    games: list[SportsGame] = []
    for event_id, values in grouped.items():
        sports = SportsEvent.model_validate(values[0].provider_data["sports"])
        if any(
            SportsEvent.model_validate(value.provider_data["sports"]) != sports
            for value in values[1:]
        ):
            raise MarketValidationError(values[0].platform.value, "game contracts disagree")
        contracts = [project(value) for value in values]
        observed_at = max(contract.retrieved_at for contract in contracts)
        status, reason = _live_status(sports, contracts, observed_at)
        local = sports.scheduled_start.astimezone(zone) if sports.scheduled_start else None
        market_urls = {contract.market_url for contract in contracts if contract.market_url}
        label = sports.raw_title
        if local:
            label = f"{sports.raw_title} — {local.strftime('%Y-%m-%d %I:%M %p %Z')}"
        games.append(
            SportsGame(
                event_id=event_id,
                title=sports.raw_title,
                league=sports.league,
                participants=sports.participants,
                scheduled_start=sports.scheduled_start,
                timezone=timezone,
                local_date=local.date().isoformat() if local else None,
                kickoff_local=local.isoformat() if local else None,
                label=label,
                live_status=status,
                live_status_reason=reason,
                market_url=next(iter(market_urls)) if len(market_urls) == 1 else None,
                contracts=contracts,
            )
        )
    return sorted(
        games,
        key=lambda game: (game.scheduled_start or datetime.max.replace(tzinfo=UTC), game.event_id),
    )


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
    except MarketRequestError as error:
        raise ToolError(
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
                default=str,
            )
        ) from None
    except ValueError:
        raise ToolError(
            "Invalid market request. Check query, identifier, status, and limit."
        ) from None
