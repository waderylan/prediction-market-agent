"""Canonical market types used after provider-specific parsing."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class Platform(StrEnum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


class MarketStatus(StrEnum):
    UNOPENED = "unopened"
    OPEN = "open"
    PAUSED = "paused"
    CLOSED = "closed"
    RESOLVED = "resolved"
    ARCHIVED = "archived"
    UNKNOWN = "unknown"


Probability = Decimal | None


class CanonicalMarket(BaseModel):
    """Provider-neutral representation with nullable unavailable fields."""

    model_config = ConfigDict(frozen=True)

    platform: Platform
    market_id: str = Field(min_length=1)
    event_id: str | None = None
    title: str = Field(min_length=1)
    description: str | None = None
    outcomes: tuple[str, ...] = Field(min_length=2)
    yes_price: Probability = None
    no_price: Probability = None
    yes_bid: Probability = None
    yes_ask: Probability = None
    open_time: datetime | None = None
    close_time: datetime | None = None
    resolution_deadline: datetime | None = None
    resolution_source: str | None = None
    rules: str | None = None
    status: MarketStatus
    liquidity: Decimal | None = Field(default=None, ge=0)
    volume: Decimal | None = Field(default=None, ge=0)
    source_url: HttpUrl
    retrieved_at: datetime
    provider_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("yes_price", "no_price", "yes_bid", "yes_ask")
    @classmethod
    def probability_in_unit_interval(cls, value: Probability) -> Probability:
        if value is not None and not Decimal("0") <= value <= Decimal("1"):
            raise ValueError("probability must be between 0 and 1")
        return value

    @field_validator("open_time", "close_time", "resolution_deadline", "retrieved_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)
