"""Versioned executable watch contracts; free-form prose never reaches evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
Probability = Annotated[Decimal, Field(ge=0, le=1, max_digits=8, decimal_places=6)]
Threshold = Annotated[Decimal, Field(gt=0, le=1, max_digits=8, decimal_places=6)]
Identifier = Annotated[str, Field(min_length=1, max_length=2048)]


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class WatchStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    TERMINAL = "terminal"


class DeliveryChannel(StrEnum):
    INBOX = "inbox"
    TELEGRAM = "telegram"


class SourceStatus(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    STALE = "stale"
    MALFORMED = "malformed"


class GameIdentity(StrictModel):
    league: Literal["mlb", "nfl", "ncaa_football"]
    game_ref: Identifier
    home_team: Annotated[str, Field(min_length=1, max_length=200)]
    away_team: Annotated[str, Field(min_length=1, max_length=200)]
    scheduled_start: datetime

    @field_validator("scheduled_start")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_start must include a timezone")
        return value

    @property
    def label(self) -> str:
        return f"{self.away_team} at {self.home_team}"


class MarketIdentity(StrictModel):
    platform: Literal["kalshi", "polymarket"]
    market_id: Annotated[str, Field(min_length=1, max_length=100)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    outcome: Annotated[str, Field(min_length=1, max_length=500)]
    market_type: Literal["game_winner"] = "game_winner"


class DeliveryPolicy(StrictModel):
    channels: frozenset[DeliveryChannel] = Field(
        default_factory=lambda: frozenset({DeliveryChannel.INBOX})
    )

    @model_validator(mode="after")
    def inbox_is_authoritative(self) -> Self:
        if DeliveryChannel.INBOX not in self.channels:
            raise ValueError("inbox delivery is required")
        return self


class PriceMoveCondition(StrictModel):
    kind: Literal["price_move"] = "price_move"
    condition_id: Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,64}$")]
    threshold: Threshold
    window_seconds: int = Field(ge=30, le=3600)
    event_relationship: Literal["scoring_event", "no_tracked_scoring_event", "any"]
    correlation_window_seconds: int = Field(default=120, ge=30, le=3600)
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    rearm_below: Probability | None = None

    @model_validator(mode="after")
    def rearm_is_below_threshold(self) -> Self:
        if self.rearm_below is not None and self.rearm_below >= self.threshold:
            raise ValueError("rearm_below must be less than threshold")
        return self


class DivergenceCondition(StrictModel):
    kind: Literal["cross_platform_divergence"] = "cross_platform_divergence"
    condition_id: Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,64}$")]
    threshold: Threshold
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    rearm_below: Probability | None = None


class LifecycleCondition(StrictModel):
    kind: Literal["lifecycle_change"] = "lifecycle_change"
    condition_id: Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,64}$")]
    to_states: frozenset[Literal["live", "halftime", "delayed", "final", "cancelled"]]
    cooldown_seconds: int = Field(default=0, ge=0, le=86400)


WatchCondition = Annotated[
    PriceMoveCondition | DivergenceCondition | LifecycleCondition,
    Field(discriminator="kind"),
]


class WatchRule(StrictModel):
    schema_version: Literal[1] = 1
    watch_id: Annotated[str, Field(pattern=r"^watch_[a-f0-9]{32}$")]
    session_id: Annotated[str, Field(min_length=1, max_length=128)]
    status: WatchStatus = WatchStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
    confirmed_at: datetime
    creation_source: Literal["langgraph", "codex", "cli"]
    game: GameIdentity
    markets: list[MarketIdentity] = Field(min_length=1, max_length=4)
    conditions: list[WatchCondition] = Field(min_length=1, max_length=8)
    delivery: DeliveryPolicy = Field(default_factory=DeliveryPolicy)
    automatic_explanations: Literal[False] = False
    start_before_seconds: Literal[900] = 900
    stop_after_terminal_seconds: Literal[900] = 900

    @model_validator(mode="after")
    def semantic_contract(self) -> Self:
        identities = {(m.platform, m.market_id, m.outcome.casefold()) for m in self.markets}
        if len(identities) != len(self.markets):
            raise ValueError("market references must be unique")
        condition_ids = {condition.condition_id for condition in self.conditions}
        if len(condition_ids) != len(self.conditions):
            raise ValueError("condition IDs must be unique")
        if (
            any(isinstance(c, DivergenceCondition) for c in self.conditions)
            and len({m.platform for m in self.markets}) < 2
        ):
            raise ValueError("cross-platform divergence requires two platforms")
        if (
            any(isinstance(c, DivergenceCondition) for c in self.conditions)
            and len({m.outcome.casefold() for m in self.markets}) != 1
        ):
            raise ValueError("cross-platform divergence requires one canonical outcome")
        return self

    @property
    def monitoring_starts_at(self) -> datetime:
        return self.game.scheduled_start - timedelta(seconds=self.start_before_seconds)


class QuoteObservation(StrictModel):
    platform: Literal["kalshi", "polymarket"]
    market_id: str
    outcome: str
    price: Probability | None
    quote_time: datetime | None
    retrieved_at: datetime
    status: SourceStatus
    provider_observation_id: str | None = Field(default=None, max_length=200)
    cache_hit: bool = False
    warning: str | None = Field(default=None, max_length=500)
    contract_terminal: bool = False


class ScoringPlay(StrictModel):
    play_id: Annotated[str, Field(min_length=1, max_length=200)]
    play_time: datetime
    description: Annotated[str, Field(min_length=1, max_length=500)]
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)
    period_label: str | None = Field(default=None, max_length=100)
    scoring: bool = True


class WatchObservation(StrictModel):
    schema_version: Literal[1] = 1
    observation_id: Annotated[str, Field(pattern=r"^obs_[a-f0-9]{32}$")]
    game_ref: str
    sports_observed_at: datetime
    retrieved_at: datetime
    lifecycle: Literal["scheduled", "pregame", "live", "halftime", "delayed", "final", "cancelled"]
    quotes: list[QuoteObservation] = Field(max_length=4)
    new_scoring_plays: list[ScoringPlay] = Field(default_factory=list, max_length=20)
    recent_plays: list[ScoringPlay] = Field(default_factory=list, max_length=20)
    sports_status: SourceStatus
    sports_warning: str | None = Field(default=None, max_length=500)


class EvidenceDelta(StrictModel):
    platform: str
    market_id: str
    outcome: str
    before_price: Probability | None
    after_price: Probability | None
    window_seconds: int | None = None


class WatchTrigger(StrictModel):
    schema_version: Literal[1] = 1
    trigger_id: Annotated[str, Field(pattern=r"^trigger_[a-f0-9]{32}$")]
    watch_id: str
    session_id: str
    condition_id: str
    fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    triggered_at: datetime
    game: GameIdentity
    deltas: list[EvidenceDelta] = Field(min_length=1, max_length=8)
    correlated_events: list[ScoringPlay] = Field(default_factory=list, max_length=10)
    recent_plays: list[ScoringPlay] = Field(default_factory=list, max_length=10)
    lifecycle_before: str | None = None
    lifecycle_after: str | None = None
    source_warnings: list[str] = Field(default_factory=list, max_length=10)
    observation_ids: list[str] = Field(min_length=1, max_length=20)
    message: Annotated[str, Field(min_length=1, max_length=1500)]


class OutboxStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RETRY = "retry_scheduled"
    SENT = "sent"
    FAILED = "failed_terminal"


class OutboxItem(StrictModel):
    outbox_id: str
    trigger_id: str
    watch_id: str
    channel: Literal["telegram"] = "telegram"
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = Field(default=0, ge=0, le=20)
    available_at: datetime
    lease_owner: str | None = None
    lease_until: datetime | None = None
    provider_message_id: str | None = None
    error_class: str | None = None


class PollResult(StrictModel):
    claimed_watches: int = 0
    observation_groups: int = 0
    created_triggers: int = 0
    duplicate_triggers: int = 0
    source_warnings: int = 0
    delivery_attempts: int = 0
    model_calls: Literal[0] = 0
    tavily_calls: Literal[0] = 0
