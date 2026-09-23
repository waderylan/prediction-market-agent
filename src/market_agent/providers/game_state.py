"""Bounded exact-game state, statistics, and plays with an MLB-only fallback."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from typing import Any, Literal, Self
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from market_agent.logging import log_event
from market_agent.providers.sports import League, Team, participant, resolve_query, teams

logger = logging.getLogger(__name__)

Lifecycle = Literal[
    "scheduled",
    "pregame",
    "live",
    "halftime",
    "delayed",
    "suspended",
    "postponed",
    "cancelled",
    "final",
    "unknown",
]
Source = Literal["espn", "mlb_statsapi"]
HalfInning = Literal["top", "bottom", "unknown"]
BaseballPhase = Literal["not_started", "active", "transition", "complete", "unavailable"]
CompletenessStatus = Literal["complete", "partial", "unavailable"]
InningParticipation = Literal["played", "not_played", "not_reached", "unknown"]
BoxScoreViewName = Literal[
    "summary",
    "full",
    "line_score",
    "batting",
    "pitching",
    "team_stats",
    "player_stats",
]
TeamSide = Literal["away", "home", "both"]
PlayEventKind = Literal[
    "pitch",
    "plate_appearance",
    "substitution",
    "game_action",
    "football_play",
]
NOT_STARTED_LIFECYCLES = {"scheduled", "pregame"}

DISCOVERY_USAGE = (
    "Discovery is a lightweight scoreboard snapshot for choosing a game. Copy game_ref unchanged "
    "into sports_state_get_game_state for current situation fields or "
    "sports_state_get_box_score for line scoring and team/player game statistics. Scheduled and "
    "pregame state placeholders are returned as null. Live discovery and detail are separate "
    "observations and may drift. game_ref is scoped to the requested timezone."
)
DETAIL_USAGE = (
    "Detail is the authoritative normalized sporting-state snapshot for this game reference. "
    "Null fields are unavailable or not meaningful for the lifecycle; sporting state does not "
    "establish prediction-market settlement."
)
BOX_SCORE_USAGE = (
    "The default summary contains scoring and compact leaders. Use view=full for every available "
    "box-score section, or request line_score, batting, pitching, team_stats, or player_stats. "
    "Unavailable fields are omitted; inning participation explains every null run value. Season "
    "statistics and play-by-play are not included."
)
PLAYER_DIRECTORY_USAGE = (
    "Available players are the provider-backed participants with game-stat lines in this exact "
    "box-score observation. Copy one player_id unchanged into "
    "sports_state_get_player_stats; this is not a season roster."
)
PLAYER_STATS_USAGE = (
    "Player statistics are a narrow projection of one exact-game box-score observation. "
    "They contain game-only provider fields, not season statistics or play-by-play."
)
PLAY_BY_PLAY_USAGE = (
    "Plays are chronological and carry stable provider-backed play_id values. Use the first "
    "returned ID as before_play_id to page backward or resume_after_play_id as after_play_id to "
    "request only later unseen plays. Baseball outs_before and outs_after describe state around "
    "the event; substitution events are labeled separately from pitches and plate appearances. "
    "This is game history, not market settlement evidence."
)

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_SCOREBOARD_EVENTS = 200
MAX_PLAYS = 1000
MAX_CACHE_ENTRIES = 256
MAX_TEXT = 1000
REF_DOMAIN = b"sports-state-game-ref-v1\x00"

ESPN_ROUTES: dict[League, tuple[str, str]] = {
    "mlb": ("baseball", "mlb"),
    "nfl": ("football", "nfl"),
    "ncaa_football": ("football", "college-football"),
}
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
MLB_STATS_BASE = "https://statsapi.mlb.com/api"


class SportsStateError(RuntimeError):
    """Safe error surfaced by the narrow sports-state MCP."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        fields: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.fields = fields or {}
        super().__init__(message)


class ProviderUnavailableError(SportsStateError):
    """Provider transport or HTTP failure."""


class StateValidationError(SportsStateError):
    """Structurally unsafe or semantically impossible provider data."""


class IdentityMismatchError(StateValidationError):
    """A detail response conflicts with the discovered game identity."""


class StateWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    section: str | None = Field(default=None, min_length=1, max_length=100)
    fields: list[str] = Field(default_factory=list, max_length=30)


class FootballSituation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sport: Literal["football"] = "football"
    possession_team: str | None = Field(default=None, max_length=200)
    down: int | None = Field(default=None, ge=1, le=4)
    distance: int | None = Field(default=None, ge=0)
    field_position: str | None = Field(default=None, max_length=100)
    red_zone: bool | None = None
    home_timeouts: int | None = Field(default=None, ge=0, le=3)
    away_timeouts: int | None = Field(default=None, ge=0, le=3)
    down_distance_label: str | None = Field(default=None, max_length=200)


class BaseballSituation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sport: Literal["baseball"] = "baseball"
    phase: BaseballPhase = "unavailable"
    inning: int | None = Field(default=None, ge=1, le=30)
    half: HalfInning = "unknown"
    balls: int | None = Field(default=None, ge=0, le=4)
    strikes: int | None = Field(default=None, ge=0, le=3)
    outs: int | None = Field(default=None, ge=0, le=3)
    on_first: bool | None = None
    on_second: bool | None = None
    on_third: bool | None = None
    batter: str | None = Field(default=None, max_length=200)
    pitcher: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def valid_phase(self) -> Self:
        state_values = (
            self.inning,
            self.balls,
            self.strikes,
            self.outs,
            self.on_first,
            self.on_second,
            self.on_third,
            self.batter,
            self.pitcher,
        )
        if self.phase == "not_started" and (
            self.half != "unknown" or any(value is not None for value in state_values)
        ):
            raise ValueError("not-started baseball state must not contain game situation values")
        if self.phase == "active":
            if self.inning is None or self.half == "unknown":
                raise ValueError("active baseball state requires an inning and half")
            if any(
                value is not None and value > maximum
                for value, maximum in ((self.balls, 3), (self.strikes, 2), (self.outs, 2))
            ):
                raise ValueError("active plate-appearance counts exceed baseball bounds")
        return self


class GameSummary(BaseModel):
    """Provider-backed game identity and common state without a situation payload."""

    model_config = ConfigDict(extra="forbid")

    league: League
    game_ref: str = Field(min_length=1, max_length=2048)
    source: Source
    provider_game_id: str = Field(min_length=1, max_length=100)
    home_team: str = Field(min_length=1, max_length=200)
    away_team: str = Field(min_length=1, max_length=200)
    raw_home_team: str = Field(min_length=1, max_length=200)
    raw_away_team: str = Field(min_length=1, max_length=200)
    scheduled_start: datetime
    timezone: str = Field(min_length=1, max_length=100)
    local_date: date
    scheduled_start_local: datetime
    home_score: int | None = Field(default=None, ge=0)
    away_score: int | None = Field(default=None, ge=0)
    lifecycle: Lifecycle
    period: int | None = Field(default=None, ge=1, le=30)
    period_label: str | None = Field(default=None, max_length=100)
    clock: str | None = Field(default=None, max_length=100)
    last_play: str | None = Field(default=None, max_length=MAX_TEXT)
    retrieved_at: datetime
    provider_updated_at: datetime | None = None
    observation_id: str = Field(min_length=1, max_length=100)
    cache_hit: bool = False
    cache_age_ms: int = Field(default=0, ge=0)
    source_url: str = Field(min_length=1, max_length=2048)
    warnings: list[StateWarning] = Field(default_factory=list, max_length=20)

    @field_validator(
        "scheduled_start", "scheduled_start_local", "retrieved_at", "provider_updated_at"
    )
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must include a timezone")
        return value


class GameState(GameSummary):
    usage_note: str = Field(default=DETAIL_USAGE, max_length=500)
    situation: FootballSituation | BaseballSituation


class BoxScoreTeam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    score: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)


class InningLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inning: int = Field(ge=1, le=30)
    away_runs: int | None = Field(default=None, ge=0)
    home_runs: int | None = Field(default=None, ge=0)
    away_participation: InningParticipation = "unknown"
    home_participation: InningParticipation = "unknown"

    @model_validator(mode="after")
    def participation_matches_runs(self) -> Self:
        for role in ("away", "home"):
            runs = getattr(self, f"{role}_runs")
            participation = getattr(self, f"{role}_participation")
            if participation == "played" and runs is None:
                raise ValueError("played inning half requires a run value")
            if participation != "played" and runs is not None:
                raise ValueError("unplayed inning half cannot contain a run value")
        return self


class TeamTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    hits: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    errors: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    left_on_base: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)


class BaseballLineScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    innings: list[InningLine] = Field(max_length=30)
    away_totals: TeamTotals
    home_totals: TeamTotals


class BatterLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    lineup_slot: int | None = Field(
        default=None, ge=1, le=99, exclude_if=lambda value: value is None
    )
    positions: list[str] = Field(
        default_factory=list, max_length=10, exclude_if=lambda value: not value
    )
    starter: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    at_bats: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    runs: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    hits: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    doubles: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    triples: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    home_runs: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    rbi: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    walks: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    strikeouts: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    stolen_bases: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)


class PitcherLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    starter: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    appearance_order: int = Field(ge=1, le=30)
    outs_recorded: int | None = Field(
        default=None, ge=0, le=90, exclude_if=lambda value: value is None
    )
    innings_pitched_display: str | None = Field(
        default=None, pattern=r"^\d+\.[0-2]$", exclude_if=lambda value: value is None
    )
    hits: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    runs: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    earned_runs: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    walks: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    strikeouts: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    home_runs: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    pitches: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    strikes: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)


class TeamBatting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    away: list[BatterLine] = Field(max_length=30)
    home: list[BatterLine] = Field(max_length=30)


class TeamPitching(BaseModel):
    model_config = ConfigDict(extra="forbid")

    away: list[PitcherLine] = Field(max_length=30)
    home: list[PitcherLine] = Field(max_length=30)


class BaseballBoxScoreCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_score: CompletenessStatus
    team_totals: CompletenessStatus
    batting: CompletenessStatus
    pitching: CompletenessStatus
    missing_required_fields: dict[str, list[str]] = Field(default_factory=dict)
    missing_optional_fields: dict[str, list[str]] = Field(default_factory=dict)


class FootballPeriodLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: int = Field(ge=1, le=20)
    away_points: int | None = Field(default=None, ge=0)
    home_points: int | None = Field(default=None, ge=0)


class FootballLineScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    periods: list[FootballPeriodLine] = Field(max_length=20)


class FootballStatistic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=100)


class FootballPlayerLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    statistics: list[FootballStatistic] = Field(max_length=30)


class FootballPlayerGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=100)
    players: list[FootballPlayerLine] = Field(max_length=100)


class TeamFootballStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    away: list[FootballStatistic] = Field(max_length=100)
    home: list[FootballStatistic] = Field(max_length=100)


class TeamFootballPlayers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    away: list[FootballPlayerGroup] = Field(max_length=20)
    home: list[FootballPlayerGroup] = Field(max_length=20)


class FootballBoxScoreCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_score: CompletenessStatus
    team_stats: CompletenessStatus
    player_stats: CompletenessStatus
    missing_required_fields: dict[str, list[str]] = Field(default_factory=dict)
    missing_optional_fields: dict[str, list[str]] = Field(default_factory=dict)


class _BoxScoreBase(BaseModel):
    """Shared identity and provenance for an exact-game box score."""

    model_config = ConfigDict(extra="forbid")

    league: League
    game_ref: str = Field(min_length=1, max_length=2048)
    provider_game_id: str = Field(min_length=1, max_length=100)
    source: Source
    source_url: str = Field(min_length=1, max_length=2048)
    scheduled_start: datetime
    timezone: str = Field(min_length=1, max_length=100)
    local_date: date
    lifecycle: Lifecycle
    period: int | None = Field(default=None, ge=1, le=30, exclude_if=lambda value: value is None)
    period_label: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    away_team: BoxScoreTeam
    home_team: BoxScoreTeam
    retrieved_at: datetime
    provider_updated_at: datetime | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    observation_id: str = Field(min_length=1, max_length=100)
    cache_hit: bool = False
    cache_age_ms: int = Field(default=0, ge=0)
    is_partial: bool
    warnings: list[StateWarning] = Field(default_factory=list, max_length=20)
    usage_note: str = Field(default=BOX_SCORE_USAGE, max_length=500)

    @field_validator("scheduled_start", "retrieved_at", "provider_updated_at")
    @classmethod
    def aware_box_score_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamps must include a timezone")
        return value


class BoxScore(_BoxScoreBase):
    """Sport-discriminated box score without irrelevant null sections."""

    sport: Literal["baseball", "football"]
    line_score: BaseballLineScore | FootballLineScore
    batting: TeamBatting | None = Field(default=None, exclude_if=lambda value: value is None)
    pitching: TeamPitching | None = Field(default=None, exclude_if=lambda value: value is None)
    team_stats: TeamFootballStatistics | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    player_stats: TeamFootballPlayers | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    completeness: BaseballBoxScoreCompleteness | FootballBoxScoreCompleteness

    @model_validator(mode="after")
    def valid_sport_sections(self) -> Self:
        if self.sport == "baseball":
            valid = (
                self.league == "mlb"
                and isinstance(self.line_score, BaseballLineScore)
                and self.batting is not None
                and self.pitching is not None
                and self.team_stats is None
                and self.player_stats is None
                and isinstance(self.completeness, BaseballBoxScoreCompleteness)
            )
        else:
            valid = (
                self.league in {"nfl", "ncaa_football"}
                and isinstance(self.line_score, FootballLineScore)
                and self.batting is None
                and self.pitching is None
                and self.team_stats is not None
                and self.player_stats is not None
                and isinstance(self.completeness, FootballBoxScoreCompleteness)
            )
        if not valid:
            raise ValueError("box-score sections do not match the sport")
        return self


class BoxScoreHighlightStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=100)


class BoxScoreHighlight(BaseModel):
    """One compact provider-backed player line selected for the summary view."""

    model_config = ConfigDict(extra="forbid")

    player_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    team: str = Field(min_length=1, max_length=200)
    team_side: Literal["away", "home"]
    stat_group: str = Field(min_length=1, max_length=100)
    statistics: list[BoxScoreHighlightStat] = Field(min_length=1, max_length=12)


class BoxScoreView(_BoxScoreBase):
    """Presentation-bounded projection of one normalized full box score."""

    sport: Literal["baseball", "football"]
    view: BoxScoreViewName
    team_side: TeamSide
    available_views: list[BoxScoreViewName] = Field(min_length=3, max_length=7)
    follow_up_tip: str = Field(min_length=1, max_length=500)
    line_score: BaseballLineScore | FootballLineScore | None = None
    batting: TeamBatting | None = Field(default=None, exclude_if=lambda value: value is None)
    pitching: TeamPitching | None = Field(default=None, exclude_if=lambda value: value is None)
    team_stats: TeamFootballStatistics | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    player_stats: TeamFootballPlayers | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    highlights: list[BoxScoreHighlight] = Field(default_factory=list, max_length=8)
    completeness: BaseballBoxScoreCompleteness | FootballBoxScoreCompleteness

    @model_validator(mode="after")
    def valid_view(self) -> Self:
        expected_sport = "baseball" if self.league == "mlb" else "football"
        if self.sport != expected_sport:
            raise ValueError("box-score view sport does not match league")
        if self.view not in self.available_views:
            raise ValueError("requested box-score view is unavailable for this sport")
        present = {
            name
            for name in ("line_score", "batting", "pitching", "team_stats", "player_stats")
            if getattr(self, name) is not None
        }
        expected: dict[BoxScoreViewName, set[str]] = {
            "summary": {"line_score"},
            "full": (
                {"line_score", "batting", "pitching"}
                if self.sport == "baseball"
                else {"line_score", "team_stats", "player_stats"}
            ),
            "line_score": {"line_score"},
            "batting": {"batting"},
            "pitching": {"pitching"},
            "team_stats": {"team_stats"},
            "player_stats": {"player_stats"},
        }
        if present != expected[self.view]:
            raise ValueError("box-score sections do not match the requested view")
        if self.view == "summary" and not self.highlights and not self.is_partial:
            # A final provider response can legitimately have no player sections, but that must
            # already be disclosed as partial/unavailable rather than appearing complete.
            raise ValueError("complete summary view requires at least one player highlight")
        return self


class AvailablePlayer(BaseModel):
    """Compact selector for one player with provider-backed game statistics."""

    model_config = ConfigDict(extra="forbid")

    player_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    team: str = Field(min_length=1, max_length=200)
    team_side: Literal["away", "home"]
    stat_groups: list[str] = Field(min_length=1, max_length=20)


class PlayerDirectory(_BoxScoreBase):
    """Players selectable from one exact normalized box-score observation."""

    sport: Literal["baseball", "football"]
    players: list[AvailablePlayer] = Field(max_length=200)
    player_stats_completeness: CompletenessStatus
    usage_note: str = Field(default=PLAYER_DIRECTORY_USAGE, max_length=500)


class FootballPlayerStatGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=100)
    statistics: list[FootballStatistic] = Field(max_length=30)


class PlayerStats(_BoxScoreBase):
    """One player's game-only statistics from an exact box-score observation."""

    sport: Literal["baseball", "football"]
    player_id: str = Field(min_length=1, max_length=100)
    player_name: str = Field(min_length=1, max_length=200)
    team: str = Field(min_length=1, max_length=200)
    team_side: Literal["away", "home"]
    batting: BatterLine | None = Field(default=None, exclude_if=lambda value: value is None)
    pitching: PitcherLine | None = Field(default=None, exclude_if=lambda value: value is None)
    stat_groups: list[FootballPlayerStatGroup] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    usage_note: str = Field(default=PLAYER_STATS_USAGE, max_length=500)

    @model_validator(mode="after")
    def valid_sport_sections(self) -> Self:
        if self.sport == "baseball":
            valid = (
                self.league == "mlb"
                and (self.batting is not None or self.pitching is not None)
                and self.stat_groups is None
            )
        else:
            valid = (
                self.league in {"nfl", "ncaa_football"}
                and self.batting is None
                and self.pitching is None
                and bool(self.stat_groups)
            )
        if not valid:
            raise ValueError("player-stat sections do not match the sport")
        return self


class BaseballPlayContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sport: Literal["baseball"] = "baseball"
    half: HalfInning
    balls: int | None = Field(default=None, ge=0, le=4, exclude_if=lambda value: value is None)
    strikes: int | None = Field(default=None, ge=0, le=3, exclude_if=lambda value: value is None)
    outs_before: int | None = Field(
        default=None, ge=0, le=3, exclude_if=lambda value: value is None
    )
    outs_after: int | None = Field(default=None, ge=0, le=3, exclude_if=lambda value: value is None)
    at_bat_id: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )


class SubstitutionParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=100)
    player_id: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=200)


class BaseballSubstitution(BaseModel):
    """Provider-labeled roster action kept separate from plate-appearance state."""

    model_config = ConfigDict(extra="forbid")

    substitution_type: str = Field(min_length=1, max_length=100)
    participants: list[SubstitutionParticipant] = Field(default_factory=list, max_length=10)


class FootballPlayContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sport: Literal["football"] = "football"
    down: int | None = Field(default=None, ge=1, le=4, exclude_if=lambda value: value is None)
    distance: int | None = Field(default=None, ge=0, le=100, exclude_if=lambda value: value is None)
    field_position: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    yards: int | None = Field(default=None, ge=-200, le=200, exclude_if=lambda value: value is None)
    turnover: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    penalty: bool | None = Field(default=None, exclude_if=lambda value: value is None)


class GamePlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    play_id: str = Field(min_length=1, max_length=100)
    sequence: int = Field(ge=1, le=MAX_PLAYS)
    provider_sequence: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    period: int | None = Field(default=None, ge=1, le=30, exclude_if=lambda value: value is None)
    period_label: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    clock: str | None = Field(default=None, max_length=100, exclude_if=lambda value: value is None)
    play_type: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    event_kind: PlayEventKind
    substitution: BaseballSubstitution | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    team: str | None = Field(default=None, max_length=200, exclude_if=lambda value: value is None)
    team_side: Literal["away", "home"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    scoring_play: bool
    away_score: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    home_score: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    wallclock: datetime | None = Field(default=None, exclude_if=lambda value: value is None)
    context: BaseballPlayContext | FootballPlayContext

    @field_validator("wallclock")
    @classmethod
    def aware_wallclock(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("play wallclock must include a timezone")
        return value

    @model_validator(mode="after")
    def structured_event_matches_kind(self) -> Self:
        if (self.event_kind == "substitution") != (self.substitution is not None):
            raise ValueError("substitution payload does not match event kind")
        if self.context.sport == "football" and self.event_kind != "football_play":
            raise ValueError("football context requires football_play event kind")
        if self.context.sport == "baseball" and self.event_kind == "football_play":
            raise ValueError("baseball context cannot use football_play event kind")
        return self


class _PlayFeed(_BoxScoreBase):
    sport: Literal["baseball", "football"]
    granularity: Literal["pitch_or_action", "play", "at_bat"]
    plays: list[GamePlay] = Field(max_length=MAX_PLAYS)
    usage_note: str = Field(default=PLAY_BY_PLAY_USAGE, max_length=500)


class PlayByPlay(_BoxScoreBase):
    """Bounded play window with stable anchors for paging and unseen-play retrieval."""

    sport: Literal["baseball", "football"]
    granularity: Literal["pitch_or_action", "play", "at_bat"]
    anchor_mode: Literal["latest", "before", "after"]
    anchor_play_id: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    requested_limit: int = Field(ge=1, le=50)
    play_filter: Literal["all", "scoring"]
    period_filter: int | None = Field(
        default=None, ge=1, le=30, exclude_if=lambda value: value is None
    )
    team_filter: Literal["away", "home"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    total_plays: int = Field(ge=0, le=MAX_PLAYS)
    matching_plays: int = Field(ge=0, le=MAX_PLAYS)
    plays: list[GamePlay] = Field(max_length=50)
    first_play_id: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    last_play_id: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    has_earlier: bool
    has_later: bool
    next_before_play_id: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    resume_after_play_id: str | None = Field(
        default=None, max_length=100, exclude_if=lambda value: value is None
    )
    usage_note: str = Field(default=PLAY_BY_PLAY_USAGE, max_length=500)

    @model_validator(mode="after")
    def consistent_window(self) -> Self:
        if len(self.plays) > self.requested_limit:
            raise ValueError("play window exceeds requested limit")
        ids = [play.play_id for play in self.plays]
        if len(ids) != len(set(ids)):
            raise ValueError("play window contains duplicate IDs")
        if any(
            left.sequence >= right.sequence
            for left, right in zip(self.plays, self.plays[1:], strict=False)
        ):
            raise ValueError("play window is not chronological")
        if any(play.context.sport != self.sport for play in self.plays):
            raise ValueError("play context does not match feed sport")
        if self.play_filter == "scoring" and any(not play.scoring_play for play in self.plays):
            raise ValueError("play window violates scoring filter")
        if self.period_filter is not None and any(
            play.period != self.period_filter for play in self.plays
        ):
            raise ValueError("play window violates period filter")
        if self.team_filter is not None and any(
            play.team_side != self.team_filter for play in self.plays
        ):
            raise ValueError("play window violates team filter")
        if self.plays:
            if self.first_play_id != ids[0] or self.last_play_id != ids[-1]:
                raise ValueError("play window boundary IDs are inconsistent")
        elif self.first_play_id is not None or self.last_play_id is not None:
            raise ValueError("empty play window has boundary IDs")
        if self.anchor_mode == "latest":
            if self.anchor_play_id is not None:
                raise ValueError("latest play window cannot have an anchor")
        elif self.anchor_play_id is None:
            raise ValueError("anchored play window is missing its anchor")
        expected_resume = (
            self.anchor_play_id
            if self.anchor_mode == "before" or not self.plays
            else self.last_play_id
        )
        if self.resume_after_play_id != expected_resume:
            raise ValueError("play resume ID is inconsistent")
        expected_before = self.first_play_id if self.plays and self.has_earlier else None
        if self.next_before_play_id != expected_before:
            raise ValueError("play backward-page ID is inconsistent")
        return self


class CompactGameSummary(BaseModel):
    """Minimum selection fields for callers that will retrieve exact detail next."""

    model_config = ConfigDict(extra="forbid")

    game_ref: str = Field(min_length=1, max_length=2048)
    home_team: str = Field(min_length=1, max_length=200)
    away_team: str = Field(min_length=1, max_length=200)
    scheduled_start: datetime
    scheduled_start_local: datetime
    lifecycle: Lifecycle

    @field_validator("scheduled_start", "scheduled_start_local")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
        return value


class DiscoveryCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_local_date: date
    derived_local_date: bool
    scoreboard_requests: int = Field(ge=0, le=3)
    provider_dates_requested: list[date] = Field(max_length=3)
    events_scanned: int = Field(ge=0, le=600)
    matching_games: int = Field(ge=0, le=10)
    discarded_event_count: int = Field(ge=0, le=600)
    warnings: list[StateWarning] = Field(default_factory=list, max_length=20)
    utc_boundary_check: bool = False
    utc_boundary_note: str = (
        "One local calendar day can overlap two ESPN UTC date pages; additional listed reads only "
        "complete that local day and do not widen the requested date."
    )
    scope: str = "Requested local day only; bounded UTC-boundary checks are not a season scan."


class FindGamesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    league: League
    timezone: str
    local_date: date
    discovery_mode: Literal["team", "schedule", "clarification"] = "team"
    compact: bool = False
    games: list[GameSummary | CompactGameSummary] = Field(max_length=10)
    coverage: DiscoveryCoverage
    clarification: str | None = Field(default=None, max_length=500)
    choices: list[str] = Field(default_factory=list, max_length=20)
    suggested_queries: list[str] = Field(default_factory=list, max_length=20)
    usage_note: str = Field(default=DISCOVERY_USAGE, max_length=500)


class _GameRefPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    source: Literal["espn"]
    league: League
    event_id: str = Field(pattern=r"^[0-9]{1,40}$")
    scheduled_start: datetime
    timezone: str = Field(min_length=1, max_length=100)
    home_team: str = Field(min_length=1, max_length=200)
    away_team: str = Field(min_length=1, max_length=200)

    @field_validator("scheduled_start")
    @classmethod
    def aware_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_start must include a timezone")
        return value.astimezone(UTC)


class _CacheEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    cached_at: datetime
    expires_at: datetime
    state: GameState


class _BoxScoreCacheEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    cached_at: datetime
    expires_at: datetime
    box_score: BoxScore


class _PlayFeedCacheEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    cached_at: datetime
    expires_at: datetime
    feed: _PlayFeed


def _text(value: Any, *, limit: int = MAX_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StateValidationError("malformed_response", f"{label} must be an object")
    return value


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise StateValidationError("malformed_response", f"{label} must be a list")
    return [_object(item, f"{label} item") for item in value]


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise StateValidationError("malformed_response", f"{label} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise StateValidationError("malformed_response", f"{label} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StateValidationError("malformed_response", f"{label} lacks a timezone")
    return parsed.astimezone(UTC)


def _integer(value: Any, label: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise StateValidationError("malformed_response", f"{label} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise StateValidationError("malformed_response", f"{label} must be an integer") from error
    if str(number) != str(value).strip() and not isinstance(value, int):
        raise StateValidationError("malformed_response", f"{label} must be an integer")
    if number < minimum or (maximum is not None and number > maximum):
        raise StateValidationError("impossible_state", f"{label} is outside valid bounds")
    return number


def _optional_integer(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
    zero_is_null: bool = False,
) -> int | None:
    if value is None or value == "":
        return None
    number = _integer(value, label, minimum=0 if zero_is_null else minimum, maximum=maximum)
    if zero_is_null and number == 0:
        return None
    if number < minimum:
        raise StateValidationError("impossible_state", f"{label} is outside valid bounds")
    return number


def _boolean(value: Any, label: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise StateValidationError("malformed_response", f"{label} must be a boolean")
    return value


def _canonical_team(provider_team: dict[str, Any], league: League) -> tuple[Team, str]:
    raw = _text(provider_team.get("displayName"), limit=200)
    if raw is None:
        raise StateValidationError("malformed_response", "team displayName is missing")
    labels = [
        raw,
        _text(provider_team.get("shortDisplayName"), limit=200),
        _text(provider_team.get("name"), limit=200),
        _text(provider_team.get("abbreviation"), limit=50),
    ]
    location = _text(provider_team.get("location"), limit=100)
    name = _text(provider_team.get("name"), limit=100)
    if location and name:
        labels.append(f"{location} {name}")
    resolved = {
        found.kalshi_id: found
        for label in labels
        if label and (found := participant(label, league)) is not None
    }
    if len(resolved) != 1:
        raise StateValidationError(
            "unknown_provider_team", "provider team does not match one reviewed identity"
        )
    return next(iter(resolved.values())), raw


def _status(status: dict[str, Any]) -> tuple[Lifecycle, int | None, str | None, str | None]:
    type_data = _object(status.get("type"), "status.type")
    name = (_text(type_data.get("name"), limit=100) or "").upper()
    state = (_text(type_data.get("state"), limit=20) or "").lower()
    detail = _text(type_data.get("detail") or type_data.get("shortDetail"), limit=100)
    description = (_text(type_data.get("description"), limit=100) or "").lower()
    combined = " ".join((name.lower(), description, (detail or "").lower()))
    if "postpon" in combined:
        lifecycle: Lifecycle = "postponed"
    elif "cancel" in combined:
        lifecycle = "cancelled"
    elif "suspend" in combined:
        lifecycle = "suspended"
    elif "delay" in combined:
        lifecycle = "delayed"
    elif "halftime" in combined or "half time" in combined:
        lifecycle = "halftime"
    elif bool(type_data.get("completed")) or name in {"STATUS_FINAL", "STATUS_FULL_TIME"}:
        lifecycle = "final"
    elif state == "in" or name == "STATUS_IN_PROGRESS":
        lifecycle = "live"
    elif state == "pre" or name in {"STATUS_SCHEDULED", "STATUS_PRE_GAME"}:
        lifecycle = (
            "pregame"
            if "pregame" in combined or "pre-game" in combined or "warmup" in combined
            else "scheduled"
        )
    else:
        lifecycle = "unknown"
    period = _optional_integer(status.get("period"), "status.period", maximum=30, zero_is_null=True)
    clock = _text(status.get("displayClock"), limit=100)
    return lifecycle, period, detail, clock


def _score_and_teams(
    competition: dict[str, Any], league: League
) -> tuple[Team, str, int | None, str, Team, str, int | None, str, dict[str, str]]:
    competitors = _objects(competition.get("competitors"), "competition.competitors")
    if len(competitors) != 2:
        raise StateValidationError("malformed_response", "game must have exactly two competitors")
    by_role: dict[str, tuple[Team, str, int | None, str]] = {}
    ids: dict[str, str] = {}
    for competitor in competitors:
        role = competitor.get("homeAway")
        if role not in {"home", "away"} or role in by_role:
            raise StateValidationError("malformed_response", "home/away roles are invalid")
        team_data = _object(competitor.get("team"), "competitor.team")
        team, raw = _canonical_team(team_data, league)
        score = (
            None
            if competitor.get("score") in (None, "")
            else _integer(competitor.get("score"), f"{role} score")
        )
        provider_id = _text(team_data.get("id"), limit=100)
        if provider_id is None:
            raise StateValidationError("malformed_response", "provider team ID is missing")
        by_role[role] = (team, raw, score, provider_id)
        ids[provider_id] = team.name
    home, away = by_role["home"], by_role["away"]
    if home[0].kalshi_id == away[0].kalshi_id:
        raise StateValidationError("impossible_state", "game has duplicate participants")
    return (*home, *away, ids)


def _source_url(league: League, operation: Literal["scoreboard", "summary"], value: str) -> str:
    sport, provider_league = ESPN_ROUTES[league]
    base = f"{ESPN_BASE}/{sport}/{provider_league}/{operation}"
    return f"{base}?{'dates' if operation == 'scoreboard' else 'event'}={quote(value, safe='')}"


def _ref_json(payload: _GameRefPayload) -> bytes:
    return json.dumps(
        payload.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
    ).encode()


def encode_game_ref(payload: _GameRefPayload) -> str:
    serialized = _ref_json(payload)
    envelope = {
        "payload": json.loads(serialized),
        "checksum": hashlib.sha256(REF_DOMAIN + serialized).hexdigest(),
    }
    raw = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_game_ref(value: str) -> _GameRefPayload:
    if not value or len(value) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise SportsStateError(
            "invalid_game_ref",
            "game_ref is not a valid sports-state reference",
            fields={"game_ref": "invalid"},
        )
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "checksum"}:
            raise ValueError
        payload = _GameRefPayload.model_validate(envelope["payload"])
        checksum = envelope["checksum"]
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError
        expected = hashlib.sha256(REF_DOMAIN + _ref_json(payload)).hexdigest()
        if not hmac.compare_digest(checksum, expected):
            raise ValueError
        ZoneInfo(payload.timezone)
        catalog = {team.name: team for team in teams() if team.league == payload.league}
        if (
            payload.home_team not in catalog
            or payload.away_team not in catalog
            or payload.home_team == payload.away_team
        ):
            raise ValueError
        return payload
    except (
        ValueError,
        TypeError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
        ValidationError,
        ZoneInfoNotFoundError,
    ) as error:
        raise SportsStateError(
            "invalid_game_ref",
            "game_ref failed schema or checksum validation",
            fields={"game_ref": "invalid"},
        ) from error


def _observation_id(source: Source) -> str:
    return f"{source}:{uuid4()}"


def _event_summary(
    event: dict[str, Any], league: League, timezone: ZoneInfo, retrieved_at: datetime
) -> GameSummary:
    event_id = _text(event.get("id"), limit=100)
    if event_id is None or not event_id.isdigit():
        raise StateValidationError("malformed_response", "event ID is missing or invalid")
    competitions = _objects(event.get("competitions"), "event.competitions")
    if len(competitions) != 1:
        raise StateValidationError("malformed_response", "event must have one competition")
    competition = competitions[0]
    competition_id = _text(competition.get("id"), limit=100)
    if competition_id is not None and competition_id != event_id:
        raise StateValidationError(
            "response_identity_mismatch", "competition ID conflicts with event"
        )
    home, raw_home, home_score, _, away, raw_away, away_score, _, _ = _score_and_teams(
        competition, league
    )
    scheduled = _timestamp(event.get("date") or competition.get("date"), "event.date")
    local = scheduled.astimezone(timezone)
    status_data = _object(event.get("status") or competition.get("status"), "event.status")
    lifecycle, period, label, clock = _status(status_data)
    situation = competition.get("situation")
    last_play = None
    if isinstance(situation, dict) and isinstance(situation.get("lastPlay"), dict):
        last_play = _text(situation["lastPlay"].get("text"))
    if lifecycle in NOT_STARTED_LIFECYCLES:
        home_score = away_score = period = clock = last_play = None
        label = None
    ref = encode_game_ref(
        _GameRefPayload(
            version=1,
            source="espn",
            league=league,
            event_id=event_id,
            scheduled_start=scheduled,
            timezone=timezone.key,
            home_team=home.name,
            away_team=away.name,
        )
    )
    return GameSummary(
        league=league,
        game_ref=ref,
        source="espn",
        provider_game_id=event_id,
        home_team=home.name,
        away_team=away.name,
        raw_home_team=raw_home,
        raw_away_team=raw_away,
        scheduled_start=scheduled,
        timezone=timezone.key,
        local_date=local.date(),
        scheduled_start_local=local,
        home_score=home_score,
        away_score=away_score,
        lifecycle=lifecycle,
        period=period,
        period_label=label,
        clock=clock,
        last_play=last_play,
        retrieved_at=retrieved_at,
        observation_id=_observation_id("espn"),
        source_url=_source_url(league, "scoreboard", local.date().strftime("%Y%m%d")),
    )


def _validate_reference(summary: GameSummary, reference: _GameRefPayload) -> None:
    fields = {
        "league": summary.league == reference.league,
        "event_id": summary.provider_game_id == reference.event_id,
        "home_team": summary.home_team == reference.home_team,
        "away_team": summary.away_team == reference.away_team,
        "local_date": summary.scheduled_start.astimezone(ZoneInfo(reference.timezone)).date()
        == reference.scheduled_start.astimezone(ZoneInfo(reference.timezone)).date(),
        "scheduled_start": abs(
            (summary.scheduled_start - reference.scheduled_start).total_seconds()
        )
        <= 30 * 60,
    }
    conflicts = [field for field, matches in fields.items() if not matches]
    if conflicts:
        raise IdentityMismatchError(
            "response_identity_mismatch",
            "provider detail conflicts with the discovered game reference",
            fields={"conflicts": ",".join(conflicts)},
        )


def _athlete_index(root: dict[str, Any]) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}

    def remember(value: Any) -> None:
        if not isinstance(value, dict):
            return
        athlete_id = _text(value.get("id"), limit=100)
        name = _text(value.get("displayName") or value.get("fullName"), limit=200)
        if athlete_id and name:
            candidates.setdefault(athlete_id, set()).add(name)

    boxscore = root.get("boxscore")
    players = boxscore.get("players") if isinstance(boxscore, dict) else None
    if isinstance(players, list):
        for team in players[:4]:
            statistics = team.get("statistics") if isinstance(team, dict) else None
            if not isinstance(statistics, list):
                continue
            for statistic in statistics[:20]:
                athletes = statistic.get("athletes") if isinstance(statistic, dict) else None
                if not isinstance(athletes, list):
                    continue
                for entry in athletes[:100]:
                    if isinstance(entry, dict):
                        remember(entry.get("athlete"))
    rosters = root.get("rosters")
    if isinstance(rosters, list):
        for team in rosters[:4]:
            roster = team.get("roster") if isinstance(team, dict) else None
            if not isinstance(roster, list):
                continue
            for entry in roster[:100]:
                if isinstance(entry, dict):
                    remember(entry.get("athlete"))
    return {
        athlete_id: next(iter(names)) for athlete_id, names in candidates.items() if len(names) == 1
    }


def _athlete_name(value: Any, athlete_index: Mapping[str, str] | None = None) -> str | None:
    if not isinstance(value, dict):
        return None
    athlete = value.get("athlete")
    if isinstance(athlete, dict):
        direct = _text(athlete.get("displayName") or athlete.get("fullName"), limit=200)
        athlete_id = athlete.get("id")
    else:
        direct = _text(value.get("fullName") or value.get("displayName"), limit=200)
        athlete_id = value.get("playerId") or value.get("id")
    if direct:
        return direct
    return athlete_index.get(str(athlete_id)) if athlete_index and athlete_id is not None else None


def _football_situation(
    root: dict[str, Any], competition: dict[str, Any], team_ids: dict[str, str]
) -> tuple[FootballSituation, str | None, list[StateWarning]]:
    situation = (
        competition.get("situation") if isinstance(competition.get("situation"), dict) else None
    )
    current = None
    drives = root.get("drives")
    if isinstance(drives, dict) and isinstance(drives.get("current"), dict):
        current = drives["current"]
    state: Mapping[str, Any] = situation or {}
    last_play_data: Mapping[str, Any] | None = None
    possession_id = state.get("possession")
    if current:
        current_team = current.get("team")
        if possession_id is None and isinstance(current_team, dict):
            possession_id = current_team.get("id")
        plays = current.get("plays")
        if isinstance(plays, list) and plays and isinstance(plays[-1], dict):
            last_play_data = plays[-1]
            end = last_play_data.get("end")
            if not state and isinstance(end, dict):
                state = end
    if last_play_data is None and isinstance(situation, dict):
        possible = situation.get("lastPlay")
        if isinstance(possible, dict):
            last_play_data = possible
    raw_down = state.get("down")
    warnings: list[StateWarning] = []
    if raw_down in {-1, "-1", 0, "0", None, ""}:
        down = None
        if raw_down in {-1, "-1"}:
            warnings.append(
                StateWarning(
                    code="provider_transition_sentinel",
                    message="Provider supplied a transition sentinel for down; down is null.",
                )
            )
    else:
        down = _optional_integer(raw_down, "football down", minimum=1, maximum=4)
    distance = _optional_integer(state.get("distance"), "football distance", maximum=100)
    possession = team_ids.get(str(possession_id)) if possession_id is not None else None
    if possession_id is not None and possession is None:
        raise StateValidationError("impossible_state", "possession team is not a participant")
    home_timeouts = _optional_integer(state.get("homeTimeouts"), "home timeouts", maximum=3)
    away_timeouts = _optional_integer(state.get("awayTimeouts"), "away timeouts", maximum=3)
    return (
        FootballSituation(
            possession_team=possession,
            down=down,
            distance=distance,
            field_position=_text(state.get("possessionText"), limit=100),
            red_zone=_boolean(state.get("isRedZone"), "red zone"),
            home_timeouts=home_timeouts,
            away_timeouts=away_timeouts,
            down_distance_label=_text(
                state.get("downDistanceText") or state.get("shortDownDistanceText"), limit=200
            ),
        ),
        _text(last_play_data.get("text")) if last_play_data else None,
        warnings,
    )


def _baseball_situation(
    root: dict[str, Any], status: dict[str, Any], period: int | None, lifecycle: Lifecycle
) -> tuple[BaseballSituation, str | None]:
    if lifecycle in NOT_STARTED_LIFECYCLES:
        return BaseballSituation(phase="not_started"), None
    situation = root.get("situation")
    state = _object(situation, "summary.situation") if situation is not None else {}
    type_data = _object(status.get("type"), "status.type")
    prefix = (_text(status.get("periodPrefix"), limit=20) or "").lower()
    detail = (
        _text(type_data.get("detail") or type_data.get("shortDetail"), limit=100) or ""
    ).lower()
    half: HalfInning = (
        "top"
        if prefix == "top" or detail.startswith("top")
        else "bottom"
        if prefix == "bottom" or detail.startswith("bot")
        else "unknown"
    )
    last_play = state.get("lastPlay")
    athlete_index = _athlete_index(root)
    base_keys = ("onFirst", "onSecond", "onThird")
    has_base_state = any(key in state for key in base_keys)

    def occupied(key: str) -> bool | None:
        if key not in state:
            return False if has_base_state else None
        value = state.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, dict):
            return True
        return False if value is None else None

    balls = _optional_integer(state.get("balls"), "balls", maximum=4)
    strikes = _optional_integer(state.get("strikes"), "strikes", maximum=3)
    outs = _optional_integer(state.get("outs"), "outs", maximum=3)
    active = (
        lifecycle == "live"
        and period is not None
        and half != "unknown"
        and (balls is None or balls <= 3)
        and (strikes is None or strikes <= 2)
        and (outs is None or outs <= 2)
    )
    phase: BaseballPhase = (
        "complete"
        if lifecycle == "final"
        else "active"
        if active
        else "transition"
        if lifecycle in {"live", "delayed", "suspended"}
        else "unavailable"
    )
    return (
        BaseballSituation(
            phase=phase,
            inning=period,
            half=half,
            balls=balls,
            strikes=strikes,
            outs=outs,
            on_first=occupied("onFirst"),
            on_second=occupied("onSecond"),
            on_third=occupied("onThird"),
            batter=_athlete_name(state.get("batter"), athlete_index),
            pitcher=_athlete_name(state.get("pitcher"), athlete_index),
        ),
        _text(last_play.get("text")) if isinstance(last_play, dict) else None,
    )


def _espn_detail(
    root: Any, reference: _GameRefPayload, retrieved_at: datetime, source_url: str
) -> GameState:
    payload = _object(root, "summary response")
    header = _object(payload.get("header"), "summary.header")
    header_id = _text(header.get("id"), limit=100)
    if header_id != reference.event_id:
        raise IdentityMismatchError(
            "response_identity_mismatch", "summary event ID does not match game_ref"
        )
    competitions = _objects(header.get("competitions"), "summary.header.competitions")
    if len(competitions) != 1:
        raise StateValidationError("malformed_response", "summary must have one competition")
    competition = competitions[0]
    competition_id = _text(competition.get("id"), limit=100)
    if competition_id is not None and competition_id != reference.event_id:
        raise IdentityMismatchError(
            "response_identity_mismatch", "summary competition ID does not match game_ref"
        )
    home, raw_home, home_score, _, away, raw_away, away_score, _, team_ids = _score_and_teams(
        competition, reference.league
    )
    scheduled = _timestamp(competition.get("date"), "summary competition date")
    status_data = _object(competition.get("status"), "summary competition status")
    lifecycle, period, label, clock = _status(status_data)
    if lifecycle in NOT_STARTED_LIFECYCLES:
        home_score = away_score = period = clock = None
        label = None
    zone = ZoneInfo(reference.timezone)
    local = scheduled.astimezone(zone)
    summary = GameSummary(
        league=reference.league,
        game_ref=encode_game_ref(reference),
        source="espn",
        provider_game_id=reference.event_id,
        home_team=home.name,
        away_team=away.name,
        raw_home_team=raw_home,
        raw_away_team=raw_away,
        scheduled_start=scheduled,
        timezone=reference.timezone,
        local_date=local.date(),
        scheduled_start_local=local,
        home_score=home_score,
        away_score=away_score,
        lifecycle=lifecycle,
        period=period,
        period_label=label,
        clock=clock,
        retrieved_at=retrieved_at,
        observation_id=_observation_id("espn"),
        source_url=source_url,
    )
    _validate_reference(summary, reference)
    situation: FootballSituation | BaseballSituation
    warnings: list[StateWarning] = []
    if lifecycle in NOT_STARTED_LIFECYCLES:
        situation = (
            BaseballSituation(phase="not_started")
            if reference.league == "mlb"
            else FootballSituation()
        )
        last_play = None
    elif reference.league == "mlb":
        situation, last_play = _baseball_situation(payload, status_data, period, lifecycle)
    else:
        situation, last_play, warnings = _football_situation(payload, competition, team_ids)
    state_data = summary.model_dump()
    state_data["last_play"] = last_play
    state_data["warnings"] = warnings
    return GameState(**state_data, situation=situation)


def _box_score_observation_id(box_score: BoxScore) -> str:
    snapshot = box_score.model_dump(mode="json")
    for field in ("retrieved_at", "observation_id", "cache_hit", "cache_age_ms"):
        snapshot.pop(field, None)
    serialized = json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode()
    return f"{box_score.source}:{hashlib.sha256(serialized).hexdigest()}"


def _finalize_box_score(box_score: BoxScore) -> BoxScore:
    return box_score.model_copy(update={"observation_id": _box_score_observation_id(box_score)})


def _display_stat(label: str, value: int | str | None) -> BoxScoreHighlightStat | None:
    if value is None:
        return None
    return BoxScoreHighlightStat(label=label, value=str(value))


def _baseball_highlights(box_score: BoxScore, team_side: TeamSide) -> list[BoxScoreHighlight]:
    assert box_score.batting is not None and box_score.pitching is not None
    highlights: list[BoxScoreHighlight] = []
    sides: tuple[Literal["away", "home"], ...] = (
        ("away", "home") if team_side == "both" else (team_side,)
    )
    for side in sides:
        team = box_score.away_team.name if side == "away" else box_score.home_team.name
        batters = getattr(box_score.batting, side)
        if batters:
            batter = max(
                batters,
                key=lambda line: (
                    line.rbi or 0,
                    line.home_runs or 0,
                    line.hits or 0,
                    line.runs or 0,
                    line.walks or 0,
                ),
            )
            stats = [
                _display_stat("AB", batter.at_bats),
                _display_stat("R", batter.runs),
                _display_stat("H", batter.hits),
                _display_stat("RBI", batter.rbi),
                _display_stat("BB", batter.walks),
                _display_stat("SO", batter.strikeouts),
                _display_stat("HR", batter.home_runs),
            ]
            highlights.append(
                BoxScoreHighlight(
                    player_id=batter.player_id,
                    name=batter.name,
                    team=team,
                    team_side=side,
                    stat_group="batting",
                    statistics=[stat for stat in stats if stat is not None],
                )
            )
        pitchers = getattr(box_score.pitching, side)
        if pitchers:
            pitcher = max(
                pitchers,
                key=lambda line: (
                    line.outs_recorded or 0,
                    line.strikeouts or 0,
                    -(line.earned_runs or 0),
                ),
            )
            stats = [
                _display_stat("IP", pitcher.innings_pitched_display),
                _display_stat("H", pitcher.hits),
                _display_stat("R", pitcher.runs),
                _display_stat("ER", pitcher.earned_runs),
                _display_stat("BB", pitcher.walks),
                _display_stat("SO", pitcher.strikeouts),
            ]
            highlights.append(
                BoxScoreHighlight(
                    player_id=pitcher.player_id,
                    name=pitcher.name,
                    team=team,
                    team_side=side,
                    stat_group="pitching",
                    statistics=[stat for stat in stats if stat is not None],
                )
            )
    return highlights


def _football_highlights(box_score: BoxScore, team_side: TeamSide) -> list[BoxScoreHighlight]:
    assert box_score.player_stats is not None
    highlights: list[BoxScoreHighlight] = []
    sides: tuple[Literal["away", "home"], ...] = (
        ("away", "home") if team_side == "both" else (team_side,)
    )
    for side in sides:
        team = box_score.away_team.name if side == "away" else box_score.home_team.name
        for group in getattr(box_score.player_stats, side):
            if not group.players:
                continue
            player = group.players[0]
            highlights.append(
                BoxScoreHighlight(
                    player_id=player.player_id,
                    name=player.name,
                    team=team,
                    team_side=side,
                    stat_group=group.category,
                    statistics=[
                        BoxScoreHighlightStat(label=stat.label, value=stat.value)
                        for stat in player.statistics[:12]
                    ],
                )
            )
            if len([item for item in highlights if item.team_side == side]) >= 4:
                break
    return highlights


def _filter_team_section(section: BaseModel, team_side: TeamSide) -> BaseModel:
    if team_side == "both":
        return section
    other = "home" if team_side == "away" else "away"
    return section.model_copy(update={other: []})


def project_box_score(
    box_score: BoxScore,
    *,
    view: BoxScoreViewName = "summary",
    team_side: TeamSide = "both",
) -> BoxScoreView:
    """Project a full cached box score into one agent-readable presentation view."""

    available_views: list[BoxScoreViewName] = (
        ["summary", "full", "line_score", "batting", "pitching"]
        if box_score.sport == "baseball"
        else ["summary", "full", "line_score", "team_stats", "player_stats"]
    )
    if view not in available_views:
        raise SportsStateError(
            "unsupported_box_score_view",
            f"{view} is not available for {box_score.sport} box scores",
            fields={"view": view, "sport": box_score.sport},
        )
    common = box_score.model_dump(include=set(_BoxScoreBase.model_fields) - {"usage_note"})
    sections: dict[str, Any] = {}
    if view in {"summary", "full", "line_score"}:
        sections["line_score"] = box_score.line_score
    if view in {"full", "batting"} and box_score.batting is not None:
        sections["batting"] = _filter_team_section(box_score.batting, team_side)
    if view in {"full", "pitching"} and box_score.pitching is not None:
        sections["pitching"] = _filter_team_section(box_score.pitching, team_side)
    if view in {"full", "team_stats"} and box_score.team_stats is not None:
        sections["team_stats"] = _filter_team_section(box_score.team_stats, team_side)
    if view in {"full", "player_stats"} and box_score.player_stats is not None:
        sections["player_stats"] = _filter_team_section(box_score.player_stats, team_side)
    highlights = (
        _baseball_highlights(box_score, team_side)
        if box_score.sport == "baseball"
        else _football_highlights(box_score, team_side)
    )
    return BoxScoreView(
        **common,
        sport=box_score.sport,
        view=view,
        team_side=team_side,
        available_views=available_views,
        follow_up_tip=(
            "Present this requested view only. Tell the user they can ask for the full box score "
            "or for any available section: " + ", ".join(available_views[2:]) + "."
        ),
        completeness=box_score.completeness,
        highlights=highlights if view == "summary" else [],
        usage_note=BOX_SCORE_USAGE,
        **sections,
    )


def _player_projection_data(box_score: BoxScore) -> dict[str, Any]:
    fields = set(_BoxScoreBase.model_fields) - {"usage_note"}
    return box_score.model_dump(include=fields)


def _player_completeness(box_score: BoxScore) -> CompletenessStatus:
    if isinstance(box_score.completeness, FootballBoxScoreCompleteness):
        return box_score.completeness.player_stats
    values = {box_score.completeness.batting, box_score.completeness.pitching}
    if values == {"unavailable"}:
        return "unavailable"
    if values == {"complete"}:
        return "complete"
    return "partial"


def list_box_score_players(box_score: BoxScore) -> PlayerDirectory:
    """Build a compact, de-duplicated player selector from one box-score snapshot."""

    indexed: dict[str, AvailablePlayer] = {}

    def remember(
        *, player_id: str, name: str, team: str, team_side: Literal["away", "home"], role: str
    ) -> None:
        current = indexed.get(player_id)
        if current is None:
            indexed[player_id] = AvailablePlayer(
                player_id=player_id,
                name=name,
                team=team,
                team_side=team_side,
                stat_groups=[role],
            )
            return
        if (current.name, current.team, current.team_side) != (name, team, team_side):
            raise StateValidationError(
                "malformed_response", "one provider player ID has conflicting game identities"
            )
        if role not in current.stat_groups:
            indexed[player_id] = current.model_copy(
                update={"stat_groups": [*current.stat_groups, role]}
            )

    if box_score.sport == "baseball":
        assert box_score.batting is not None and box_score.pitching is not None
        for batting_line in box_score.batting.away:
            remember(
                player_id=batting_line.player_id,
                name=batting_line.name,
                team=box_score.away_team.name,
                team_side="away",
                role="batting",
            )
        for batting_line in box_score.batting.home:
            remember(
                player_id=batting_line.player_id,
                name=batting_line.name,
                team=box_score.home_team.name,
                team_side="home",
                role="batting",
            )
        for pitcher_line in box_score.pitching.away:
            remember(
                player_id=pitcher_line.player_id,
                name=pitcher_line.name,
                team=box_score.away_team.name,
                team_side="away",
                role="pitching",
            )
        for pitcher_line in box_score.pitching.home:
            remember(
                player_id=pitcher_line.player_id,
                name=pitcher_line.name,
                team=box_score.home_team.name,
                team_side="home",
                role="pitching",
            )
    else:
        assert box_score.player_stats is not None

        def remember_groups(
            side: Literal["away", "home"], team: str, groups: list[FootballPlayerGroup]
        ) -> None:
            for group in groups:
                for player in group.players:
                    remember(
                        player_id=player.player_id,
                        name=player.name,
                        team=team,
                        team_side=side,
                        role=group.category,
                    )

        remember_groups("away", box_score.away_team.name, box_score.player_stats.away)
        remember_groups("home", box_score.home_team.name, box_score.player_stats.home)
    players = sorted(
        indexed.values(),
        key=lambda player: (
            0 if player.team_side == "away" else 1,
            player.name.casefold(),
            player.player_id,
        ),
    )
    if len(players) > 200:
        raise StateValidationError(
            "response_too_large", "box score contains more than 200 distinct players"
        )
    return PlayerDirectory(
        **_player_projection_data(box_score),
        sport=box_score.sport,
        players=players,
        player_stats_completeness=_player_completeness(box_score),
    )


def project_player_stats(box_score: BoxScore, player_id: str) -> PlayerStats:
    """Select one player without exposing unrelated box-score rows to the caller."""

    if box_score.sport == "baseball":
        assert box_score.batting is not None and box_score.pitching is not None
        matches: list[tuple[str, Literal["away", "home"], BatterLine | PitcherLine, str]] = []
        matches.extend(
            (box_score.away_team.name, "away", line, "batting")
            for line in box_score.batting.away
            if line.player_id == player_id
        )
        matches.extend(
            (box_score.home_team.name, "home", line, "batting")
            for line in box_score.batting.home
            if line.player_id == player_id
        )
        matches.extend(
            (box_score.away_team.name, "away", line, "pitching")
            for line in box_score.pitching.away
            if line.player_id == player_id
        )
        matches.extend(
            (box_score.home_team.name, "home", line, "pitching")
            for line in box_score.pitching.home
            if line.player_id == player_id
        )
        if not matches:
            raise SportsStateError(
                "player_not_found",
                "player_id is not present in this game's available statistic lines",
                fields={"player_id": player_id},
            )
        identities = {(team, side, line.name) for team, side, line, _ in matches}
        if len(identities) != 1:
            raise StateValidationError(
                "malformed_response", "one provider player ID has conflicting game identities"
            )
        team, side, name = next(iter(identities))
        batting = next((line for _, _, line, role in matches if role == "batting"), None)
        pitching = next((line for _, _, line, role in matches if role == "pitching"), None)
        return PlayerStats(
            **_player_projection_data(box_score),
            sport="baseball",
            player_id=player_id,
            player_name=name,
            team=team,
            team_side=side,
            batting=batting if isinstance(batting, BatterLine) else None,
            pitching=pitching if isinstance(pitching, PitcherLine) else None,
        )

    assert box_score.player_stats is not None
    found_name: str | None = None
    found_team: str | None = None
    found_side: Literal["away", "home"] | None = None
    stat_groups: list[FootballPlayerStatGroup] = []

    def collect(
        side: Literal["away", "home"], team: str, groups: list[FootballPlayerGroup]
    ) -> None:
        nonlocal found_name, found_team, found_side
        for group in groups:
            for player in group.players:
                if player.player_id != player_id:
                    continue
                identity = (player.name, team, side)
                if found_name is not None and identity != (found_name, found_team, found_side):
                    raise StateValidationError(
                        "malformed_response",
                        "one provider player ID has conflicting game identities",
                    )
                found_name, found_team, found_side = identity
                stat_groups.append(
                    FootballPlayerStatGroup(category=group.category, statistics=player.statistics)
                )

    collect("away", box_score.away_team.name, box_score.player_stats.away)
    collect("home", box_score.home_team.name, box_score.player_stats.home)
    if found_name is None or found_team is None or found_side is None:
        raise SportsStateError(
            "player_not_found",
            "player_id is not present in this game's available statistic lines",
            fields={"player_id": player_id},
        )
    return PlayerStats(
        **_player_projection_data(box_score),
        sport="football",
        player_id=player_id,
        player_name=found_name,
        team=found_team,
        team_side=found_side,
        stat_groups=stat_groups,
    )


def _optional_timestamp(value: Any, label: str) -> datetime | None:
    if value in (None, ""):
        return None
    return _timestamp(value, label)


def _espn_play_team_index(
    payload: dict[str, Any], state: GameState
) -> dict[str, tuple[str, Literal["away", "home"]]]:
    header = _object(payload.get("header"), "summary.header")
    competition = _objects(header.get("competitions"), "summary.header.competitions")[0]
    result: dict[str, tuple[str, Literal["away", "home"]]] = {}
    for competitor in _objects(competition.get("competitors"), "competition.competitors"):
        side = competitor.get("homeAway")
        if side not in {"away", "home"}:
            raise StateValidationError("malformed_response", "play team side is invalid")
        team = _object(competitor.get("team"), "play competitor team")
        provider_id = _text(team.get("id"), limit=100)
        if provider_id is None or provider_id in result:
            raise StateValidationError("malformed_response", "play team identity is invalid")
        result[provider_id] = (
            state.away_team if side == "away" else state.home_team,
            side,
        )
    if len(result) != 2:
        raise StateValidationError("malformed_response", "play feed must identify two teams")
    return result


def _baseball_event_kind(
    raw: dict[str, Any], type_data: dict[str, Any], text: str
) -> PlayEventKind:
    type_text = " ".join(
        value
        for value in (
            _text(type_data.get("type"), limit=100),
            _text(type_data.get("text"), limit=100),
            _text(type_data.get("abbreviation"), limit=100),
        )
        if value
    ).casefold()
    combined = f"{type_text} {text.casefold()}"
    substitution_markers = (
        "substitution",
        "pitching change",
        "defensive change",
        "pinch hit",
        "pinch-hit",
        "pinch ran",
        "pinch run",
        "replaced ",
        " hit for ",
    )
    if any(marker in combined for marker in substitution_markers):
        return "substitution"
    if "pitch" in type_text:
        return "pitch"
    if raw.get("atBatId") not in (None, ""):
        return "plate_appearance"
    return "game_action"


def _substitution_participants(raw: dict[str, Any]) -> list[SubstitutionParticipant]:
    values = raw.get("participants")
    if not isinstance(values, list):
        return []
    participants: list[SubstitutionParticipant] = []
    for value in values[:10]:
        if not isinstance(value, dict):
            continue
        athlete_value = value.get("athlete")
        athlete = athlete_value if isinstance(athlete_value, dict) else value
        role = _text(value.get("type") or value.get("role"), limit=100) or "participant"
        player_id = _text(athlete.get("id"), limit=100)
        name = _text(athlete.get("displayName") or athlete.get("fullName"), limit=200)
        if player_id is None and name is None:
            continue
        participants.append(SubstitutionParticipant(role=role, player_id=player_id, name=name))
    return participants


def _espn_play(
    raw: dict[str, Any],
    *,
    sequence: int,
    sport: Literal["baseball", "football"],
    teams_by_id: dict[str, tuple[str, Literal["away", "home"]]],
    outs_before: int | None = None,
    outs_after: int | None = None,
) -> GamePlay:
    play_id = _text(raw.get("id"), limit=100)
    text = _text(raw.get("text"))
    if play_id is None or text is None:
        raise StateValidationError("malformed_response", "play identity or text is missing")
    period_data = _object(raw.get("period", {}), "play period")
    period = _optional_integer(period_data.get("number"), "play period", maximum=30)
    team_id = None
    team_data = raw.get("team")
    if isinstance(team_data, dict):
        team_id = _text(team_data.get("id"), limit=100)
    if team_id is None:
        participants = raw.get("teamParticipants")
        if isinstance(participants, list):
            offense = next(
                (
                    item
                    for item in participants
                    if isinstance(item, dict) and item.get("type") == "offense"
                ),
                None,
            )
            if isinstance(offense, dict):
                team_id = _text(offense.get("id"), limit=100)
    team_identity = teams_by_id.get(team_id or "")
    if team_id is not None and team_identity is None:
        raise StateValidationError(
            "response_identity_mismatch", "play team is not a game participant"
        )
    scoring = _boolean(raw.get("scoringPlay"), "scoring play")
    play_type = raw.get("type")
    type_data = _object(play_type, "play type") if play_type is not None else {}
    event_kind: PlayEventKind
    substitution: BaseballSubstitution | None = None
    clock_data = raw.get("clock")
    clock = (
        _text(_object(clock_data, "play clock").get("displayValue"), limit=100)
        if clock_data is not None
        else None
    )
    context: BaseballPlayContext | FootballPlayContext
    if sport == "baseball":
        event_kind = _baseball_event_kind(raw, type_data, text)
        if event_kind == "substitution":
            substitution = BaseballSubstitution(
                substitution_type=(
                    _text(type_data.get("text") or type_data.get("type"), limit=100)
                    or "substitution"
                ),
                participants=_substitution_participants(raw),
            )
        half_text = (_text(period_data.get("type"), limit=20) or "").casefold()
        half: HalfInning = (
            "top"
            if half_text.startswith("top")
            else "bottom"
            if half_text.startswith("bot")
            else "unknown"
        )
        count = raw.get("resultCount") or raw.get("pitchCount") or {}
        count_data = _object(count, "baseball play count")
        context = BaseballPlayContext(
            half=half,
            balls=_optional_integer(count_data.get("balls"), "play balls", maximum=4),
            strikes=_optional_integer(count_data.get("strikes"), "play strikes", maximum=3),
            outs_before=outs_before,
            outs_after=outs_after,
            at_bat_id=_text(raw.get("atBatId"), limit=100),
        )
        period_label = _text(period_data.get("displayValue"), limit=100)
    else:
        event_kind = "football_play"
        end = raw.get("end")
        start = raw.get("start")
        position = end if isinstance(end, dict) else start if isinstance(start, dict) else {}
        position_data = _object(position, "football play position")
        raw_down = position_data.get("down")
        context = FootballPlayContext(
            down=(
                None
                if raw_down in {None, "", 0, "0", -1, "-1"}
                else _optional_integer(raw_down, "play down", minimum=1, maximum=4)
            ),
            distance=_optional_integer(position_data.get("distance"), "play distance", maximum=100),
            field_position=_text(
                position_data.get("possessionText") or position_data.get("downDistanceText"),
                limit=100,
            ),
            yards=_optional_integer(
                raw.get("statYardage"), "play yardage", minimum=-200, maximum=200
            ),
            turnover=_boolean(raw.get("isTurnover"), "play turnover"),
            penalty=_boolean(raw.get("isPenalty"), "play penalty"),
        )
        period_label = f"Quarter {period}" if period is not None else None
    return GamePlay(
        play_id=play_id,
        sequence=sequence,
        provider_sequence=_text(raw.get("sequenceNumber"), limit=100),
        period=period,
        period_label=period_label,
        clock=clock,
        play_type=_text(type_data.get("text") or type_data.get("abbreviation"), limit=100),
        event_kind=event_kind,
        substitution=substitution,
        text=text,
        team=team_identity[0] if team_identity else None,
        team_side=team_identity[1] if team_identity else None,
        scoring_play=scoring if scoring is not None else False,
        away_score=_optional_integer(raw.get("awayScore"), "play away score"),
        home_score=_optional_integer(raw.get("homeScore"), "play home score"),
        wallclock=_optional_timestamp(raw.get("wallclock"), "play wallclock"),
        context=context,
    )


def _play_feed_observation_id(feed: _PlayFeed) -> str:
    snapshot = feed.model_dump(mode="json")
    for field in ("retrieved_at", "observation_id", "cache_hit", "cache_age_ms"):
        snapshot.pop(field, None)
    serialized = json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode()
    return f"{feed.source}:{hashlib.sha256(serialized).hexdigest()}"


def _finalize_play_feed(feed: _PlayFeed) -> _PlayFeed:
    return feed.model_copy(update={"observation_id": _play_feed_observation_id(feed)})


def _espn_play_feed(
    root: Any, reference: _GameRefPayload, retrieved_at: datetime, source_url: str
) -> _PlayFeed:
    payload = _object(root, "summary response")
    state = _espn_detail(payload, reference, retrieved_at, source_url)
    teams_by_id = _espn_play_team_index(payload, state)
    raw_plays: list[dict[str, Any]] = []
    sport: Literal["baseball", "football"]
    granularity: Literal["pitch_or_action", "play", "at_bat"]
    if reference.league == "mlb":
        sport = "baseball"
        granularity = "pitch_or_action"
        raw_plays = _objects(payload.get("plays", []), "baseball plays")
    else:
        sport = "football"
        granularity = "play"
        drives = payload.get("drives")
        if drives is not None:
            drive_data = _object(drives, "football drives")
            for drive in _objects(drive_data.get("previous", []), "previous drives"):
                raw_plays.extend(_objects(drive.get("plays", []), "drive plays"))
            current = drive_data.get("current")
            if current is not None:
                raw_plays.extend(
                    _objects(_object(current, "current drive").get("plays", []), "current plays")
                )
    if len(raw_plays) > MAX_PLAYS:
        raise StateValidationError("response_too_large", "play feed exceeds 1,000 plays")
    plays: list[GamePlay] = []
    seen: set[str] = set()
    baseball_outs: dict[tuple[int | None, HalfInning], int] = {}
    warnings = list(state.warnings)
    discarded = 0
    for raw in raw_plays:
        raw_type = raw.get("type")
        if (
            sport == "baseball"
            and raw.get("text") is None
            and isinstance(raw_type, dict)
            and raw_type.get("type") == "end-batterpitcher"
        ):
            continue
        try:
            outs_before = outs_after = None
            if sport == "baseball":
                period_value = raw.get("period")
                period_data = _object(period_value or {}, "play period")
                inning = _optional_integer(period_data.get("number"), "play period", maximum=30)
                half_text = (_text(period_data.get("type"), limit=20) or "").casefold()
                half: HalfInning = (
                    "top"
                    if half_text.startswith("top")
                    else "bottom"
                    if half_text.startswith("bot")
                    else "unknown"
                )
                key = (inning, half)
                provider_outs = _optional_integer(raw.get("outs"), "play outs", maximum=3)
                prior_outs = baseball_outs.get(key)
                if provider_outs is not None:
                    outs_before = prior_outs if prior_outs is not None else 0
                    outs_after = provider_outs
                    baseball_outs[key] = provider_outs
                elif prior_outs is not None:
                    outs_before = outs_after = prior_outs
            play = _espn_play(
                raw,
                sequence=len(plays) + 1,
                sport=sport,
                teams_by_id=teams_by_id,
                outs_before=outs_before,
                outs_after=outs_after,
            )
            if play.event_kind == "substitution" and isinstance(play.context, BaseballPlayContext):
                stable_outs = play.context.outs_after
                if stable_outs is not None:
                    play = play.model_copy(
                        update={
                            "context": play.context.model_copy(update={"outs_before": stable_outs})
                        }
                    )
            if play.play_id in seen:
                raise StateValidationError("malformed_response", "duplicate play ID")
            seen.add(play.play_id)
            plays.append(play)
        except StateValidationError:
            discarded += 1
    if discarded:
        warnings.append(
            StateWarning(
                code="discarded_provider_plays",
                message=f"Skipped {discarded} malformed or conflicting provider play(s).",
            )
        )
    feed = _PlayFeed(
        league=state.league,
        sport=sport,
        granularity=granularity,
        game_ref=state.game_ref,
        provider_game_id=state.provider_game_id,
        source=state.source,
        source_url=state.source_url,
        scheduled_start=state.scheduled_start,
        timezone=state.timezone,
        local_date=state.local_date,
        lifecycle=state.lifecycle,
        period=state.period,
        period_label=state.period_label,
        away_team=BoxScoreTeam(name=state.away_team, score=state.away_score),
        home_team=BoxScoreTeam(name=state.home_team, score=state.home_score),
        retrieved_at=state.retrieved_at,
        observation_id="pending",
        is_partial=state.lifecycle != "final" or discarded > 0 or not plays,
        warnings=warnings[:20],
        plays=plays,
    )
    return _finalize_play_feed(feed)


def _play_projection_data(feed: _PlayFeed) -> dict[str, Any]:
    fields = set(_BoxScoreBase.model_fields) - {"usage_note"}
    return feed.model_dump(include=fields)


def select_play_window(
    feed: _PlayFeed,
    *,
    limit: int,
    before_play_id: str | None,
    after_play_id: str | None,
    play_filter: Literal["all", "scoring"],
    period: int | None,
    team: Literal["away", "home"] | None,
) -> PlayByPlay:
    if before_play_id is not None and after_play_id is not None:
        raise SportsStateError(
            "conflicting_play_anchors",
            "before_play_id and after_play_id are mutually exclusive",
            fields={"before_play_id": before_play_id, "after_play_id": after_play_id},
        )
    anchor = before_play_id or after_play_id
    positions = {play.play_id: index for index, play in enumerate(feed.plays)}
    if anchor is not None and anchor not in positions:
        raise SportsStateError(
            "play_not_found",
            "the anchor play_id is not present in this exact game observation",
            fields={"play_id": anchor},
        )
    candidates = [
        (index, play)
        for index, play in enumerate(feed.plays)
        if (play_filter == "all" or play.scoring_play)
        and (period is None or play.period == period)
        and (team is None or play.team_side == team)
    ]
    if before_play_id is not None:
        mode: Literal["latest", "before", "after"] = "before"
        eligible = [item for item in candidates if item[0] < positions[before_play_id]]
        selected = eligible[-limit:]
    elif after_play_id is not None:
        mode = "after"
        eligible = [item for item in candidates if item[0] > positions[after_play_id]]
        selected = eligible[:limit]
    else:
        mode = "latest"
        selected = candidates[-limit:]
    selected_plays = [play for _, play in selected]
    if selected:
        first_position = selected[0][0]
        last_position = selected[-1][0]
        has_earlier = any(index < first_position for index, _ in candidates)
        has_later = any(index > last_position for index, _ in candidates)
    elif before_play_id is not None:
        has_earlier = False
        has_later = bool(candidates)
    elif after_play_id is not None:
        has_earlier = bool(candidates)
        has_later = False
    else:
        has_earlier = has_later = False
    resume_after = (
        before_play_id
        if before_play_id is not None
        else selected_plays[-1].play_id
        if selected_plays
        else after_play_id
    )
    return PlayByPlay(
        **_play_projection_data(feed),
        sport=feed.sport,
        granularity=feed.granularity,
        anchor_mode=mode,
        anchor_play_id=anchor,
        requested_limit=limit,
        play_filter=play_filter,
        period_filter=period,
        team_filter=team,
        total_plays=len(feed.plays),
        matching_plays=len(candidates),
        plays=selected_plays,
        first_play_id=selected_plays[0].play_id if selected_plays else None,
        last_play_id=selected_plays[-1].play_id if selected_plays else None,
        has_earlier=has_earlier,
        has_later=has_later,
        next_before_play_id=(selected_plays[0].play_id if selected_plays and has_earlier else None),
        resume_after_play_id=resume_after,
    )


def _stat_map(group: dict[str, Any], label: str) -> dict[str, Any]:
    stats = _objects(group.get("stats"), f"{label}.stats")
    values: dict[str, Any] = {}
    for stat in stats:
        name = _text(stat.get("name"), limit=100)
        if name:
            values[name] = stat.get("displayValue", stat.get("value"))
    return values


def _espn_team_totals(
    root: dict[str, Any], team_ids: dict[str, str], scores: dict[str, int | None]
) -> dict[str, TeamTotals]:
    totals = {name: TeamTotals(runs=scores.get(name)) for name in team_ids.values()}
    boxscore = root.get("boxscore")
    if boxscore is None:
        return totals
    boxscore_data = _object(boxscore, "summary.boxscore")
    team_rows = _objects(boxscore_data.get("teams"), "summary.boxscore.teams")
    for row in team_rows:
        provider_team = _object(row.get("team"), "boxscore team")
        provider_id = _text(provider_team.get("id"), limit=100)
        canonical = team_ids.get(provider_id or "")
        if canonical is None:
            raise StateValidationError(
                "response_identity_mismatch", "box-score team is not a game participant"
            )
        groups = _objects(row.get("statistics"), "boxscore team statistics")
        by_name = {
            name: group
            for group in groups
            if (name := _text(group.get("name"), limit=100)) is not None
        }
        batting = _stat_map(by_name["batting"], "team batting") if "batting" in by_name else {}
        fielding = _stat_map(by_name["fielding"], "team fielding") if "fielding" in by_name else {}
        totals[canonical] = TeamTotals(
            runs=scores.get(canonical),
            hits=_optional_integer(batting.get("hits"), "team hits"),
            errors=_optional_integer(fielding.get("errors"), "team errors"),
            # ESPN's runnersLeftOnBase is a batter-event aggregate, not team LOB.
            left_on_base=None,
        )
    return totals


def _espn_stat_values(group: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    keys = group.get("keys")
    stats = entry.get("stats")
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        raise StateValidationError("malformed_response", "player statistic keys are invalid")
    if not isinstance(stats, list) or len(stats) != len(keys):
        raise StateValidationError("malformed_response", "player statistic values are invalid")
    return dict(zip(keys, stats, strict=True))


def _espn_player_identity(entry: dict[str, Any]) -> tuple[str, str]:
    athlete = _object(entry.get("athlete"), "boxscore athlete")
    player_id = _text(athlete.get("id"), limit=100)
    name = _text(athlete.get("displayName") or athlete.get("fullName"), limit=200)
    if player_id is None or name is None:
        raise StateValidationError("malformed_response", "boxscore athlete identity is missing")
    return player_id, name


def _espn_positions(entry: dict[str, Any]) -> list[str]:
    position = entry.get("position")
    if not isinstance(position, dict):
        return []
    abbreviation = _text(position.get("abbreviation"), limit=20)
    return [abbreviation] if abbreviation else []


def _innings_to_outs(value: Any, label: str) -> tuple[int | None, str | None]:
    display = _text(value, limit=20)
    if display is None:
        return None, None
    match = re.fullmatch(r"(\d+)\.([0-2])", display)
    if match is None:
        raise StateValidationError("impossible_state", f"{label} is not baseball innings notation")
    return int(match.group(1)) * 3 + int(match.group(2)), display


def _pitch_count(value: Any) -> tuple[int | None, int | None]:
    display = _text(value, limit=30)
    if display is None:
        return None, None
    match = re.fullmatch(r"(\d+)-(\d+)", display)
    if match is None:
        raise StateValidationError("malformed_response", "pitch/strike count is invalid")
    pitches, strikes = int(match.group(1)), int(match.group(2))
    if strikes > pitches:
        raise StateValidationError("impossible_state", "strikes exceed pitches")
    return pitches, strikes


def _espn_players(
    root: dict[str, Any], team_ids: dict[str, str]
) -> tuple[dict[str, list[BatterLine]], dict[str, list[PitcherLine]]]:
    batting: dict[str, list[BatterLine]] = {name: [] for name in team_ids.values()}
    pitching: dict[str, list[PitcherLine]] = {name: [] for name in team_ids.values()}
    boxscore = root.get("boxscore")
    if boxscore is None:
        return batting, pitching
    player_teams = _objects(
        _object(boxscore, "summary.boxscore").get("players"), "summary.boxscore.players"
    )
    for team_row in player_teams:
        provider_team = _object(team_row.get("team"), "player boxscore team")
        provider_id = _text(provider_team.get("id"), limit=100)
        canonical = team_ids.get(provider_id or "")
        if canonical is None:
            raise StateValidationError(
                "response_identity_mismatch", "player box-score team is not a participant"
            )
        groups = _objects(team_row.get("statistics"), "player statistic groups")
        for group in groups:
            group_type = _text(group.get("type"), limit=50)
            if group_type not in {"batting", "pitching"}:
                continue
            athletes = _objects(group.get("athletes"), f"{group_type} athletes")
            if len(athletes) > 30:
                raise StateValidationError(
                    "response_too_large", f"{group_type} box score exceeds 30 players"
                )
            for order, entry in enumerate(athletes, start=1):
                player_id, name = _espn_player_identity(entry)
                stats = _espn_stat_values(group, entry)
                starter = entry.get("starter") if isinstance(entry.get("starter"), bool) else None
                if group_type == "batting":
                    raw_slot = entry.get("batOrder")
                    lineup_slot = (
                        _optional_integer(raw_slot, "lineup slot", minimum=1, maximum=99)
                        if raw_slot not in (None, "", 0, "0")
                        else None
                    )
                    batting[canonical].append(
                        BatterLine(
                            player_id=player_id,
                            name=name,
                            lineup_slot=lineup_slot,
                            positions=_espn_positions(entry),
                            starter=starter,
                            at_bats=_optional_integer(stats.get("atBats"), "batter at-bats"),
                            runs=_optional_integer(stats.get("runs"), "batter runs"),
                            hits=_optional_integer(stats.get("hits"), "batter hits"),
                            doubles=_optional_integer(stats.get("doubles"), "batter doubles"),
                            triples=_optional_integer(stats.get("triples"), "batter triples"),
                            home_runs=_optional_integer(stats.get("homeRuns"), "batter home runs"),
                            rbi=_optional_integer(stats.get("RBIs"), "batter RBI"),
                            walks=_optional_integer(stats.get("walks"), "batter walks"),
                            strikeouts=_optional_integer(
                                stats.get("strikeouts"), "batter strikeouts"
                            ),
                            stolen_bases=_optional_integer(
                                stats.get("stolenBases"), "batter stolen bases"
                            ),
                        )
                    )
                else:
                    outs, innings_display = _innings_to_outs(
                        stats.get("fullInnings.partInnings"), "pitcher innings"
                    )
                    pitches, strikes = _pitch_count(stats.get("pitches-strikes"))
                    separate_pitches = _optional_integer(stats.get("pitches"), "pitcher pitches")
                    if pitches is None:
                        pitches = separate_pitches
                    elif separate_pitches is not None and separate_pitches != pitches:
                        raise StateValidationError(
                            "impossible_state", "pitcher pitch-count fields conflict"
                        )
                    pitching[canonical].append(
                        PitcherLine(
                            player_id=player_id,
                            name=name,
                            starter=starter,
                            appearance_order=order,
                            outs_recorded=outs,
                            innings_pitched_display=innings_display,
                            hits=_optional_integer(stats.get("hits"), "pitcher hits"),
                            runs=_optional_integer(stats.get("runs"), "pitcher runs"),
                            earned_runs=_optional_integer(
                                stats.get("earnedRuns"), "pitcher earned runs"
                            ),
                            walks=_optional_integer(stats.get("walks"), "pitcher walks"),
                            strikeouts=_optional_integer(
                                stats.get("strikeouts"), "pitcher strikeouts"
                            ),
                            home_runs=_optional_integer(stats.get("homeRuns"), "pitcher home runs"),
                            pitches=pitches,
                            strikes=strikes,
                        )
                    )
    return batting, pitching


def _collection_completeness(
    values: Sequence[BaseModel], required_fields: tuple[str, ...], *, available: bool
) -> CompletenessStatus:
    if not available or not values:
        return "unavailable"
    return (
        "complete"
        if all(getattr(value, field) is not None for value in values for field in required_fields)
        else "partial"
    )


def _missing_collection_fields(values: Sequence[BaseModel], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if any(getattr(value, field) is None for value in values)]


def _inning_participation(
    *,
    runs: int | None,
    role: Literal["away", "home"],
    inning: int,
    inning_count: int,
    lifecycle: Lifecycle,
    current_period: int | None,
    current_half: str,
    away_score: int | None,
    home_score: int | None,
) -> InningParticipation:
    if runs is not None:
        return "played"
    if (
        role == "home"
        and lifecycle == "live"
        and current_period == inning
        and current_half.startswith("top")
    ):
        return "not_reached"
    if (
        role == "home"
        and lifecycle == "final"
        and inning == inning_count
        and away_score is not None
        and home_score is not None
        and home_score > away_score
    ):
        return "not_played"
    return "unknown"


def _complete_box_sections(
    completeness: BaseballBoxScoreCompleteness | FootballBoxScoreCompleteness,
) -> bool:
    sections = (
        ("line_score", "team_totals", "batting", "pitching")
        if isinstance(completeness, BaseballBoxScoreCompleteness)
        else ("line_score", "team_stats", "player_stats")
    )
    return all(getattr(completeness, section) == "complete" for section in sections)


def _espn_box_score(
    root: Any, reference: _GameRefPayload, retrieved_at: datetime, source_url: str
) -> BoxScore:
    payload = _object(root, "summary response")
    state = _espn_detail(payload, reference, retrieved_at, source_url)
    if reference.league != "mlb":
        raise AssertionError("baseball box-score parser requires MLB")
    header = _object(payload.get("header"), "summary.header")
    competition = _objects(header.get("competitions"), "summary.header.competitions")[0]
    competitors = _objects(competition.get("competitors"), "competition.competitors")
    team_ids: dict[str, str] = {}
    by_role: dict[str, dict[str, Any]] = {}
    for competitor in competitors:
        role = competitor.get("homeAway")
        if role not in {"home", "away"} or role in by_role:
            raise StateValidationError(
                "malformed_response", "box-score home/away roles are invalid"
            )
        provider_team = _object(competitor.get("team"), "box-score competitor team")
        provider_id = _text(provider_team.get("id"), limit=100)
        if provider_id is None:
            raise StateValidationError("malformed_response", "box-score team ID is missing")
        canonical = state.home_team if role == "home" else state.away_team
        team_ids[provider_id] = canonical
        by_role[role] = competitor

    available = state.lifecycle not in NOT_STARTED_LIFECYCLES
    raw_lines: dict[str, list[dict[str, Any]]] = {}
    for role in ("away", "home"):
        value = by_role[role].get("linescores", []) if available else []
        raw_lines[role] = _objects(value, f"{role} inning lines")
        if len(raw_lines[role]) > 30:
            raise StateValidationError("response_too_large", "line score exceeds 30 innings")
    inning_count = max(len(raw_lines["away"]), len(raw_lines["home"]), state.period or 0)
    status = _object(competition.get("status"), "competition.status")
    prefix = (_text(status.get("periodPrefix"), limit=20) or "").lower()
    innings: list[InningLine] = []
    for number in range(1, inning_count + 1):
        values: dict[str, int | None] = {}
        for role in ("away", "home"):
            row = raw_lines[role][number - 1] if number <= len(raw_lines[role]) else {}
            values[role] = _optional_integer(
                row.get("displayValue"), f"{role} inning {number} runs"
            )
        if state.lifecycle == "live" and state.period == number and prefix == "top":
            values["home"] = None
        innings.append(
            InningLine(
                inning=number,
                away_runs=values["away"],
                home_runs=values["home"],
                away_participation=_inning_participation(
                    runs=values["away"],
                    role="away",
                    inning=number,
                    inning_count=inning_count,
                    lifecycle=state.lifecycle,
                    current_period=state.period,
                    current_half=prefix,
                    away_score=state.away_score,
                    home_score=state.home_score,
                ),
                home_participation=_inning_participation(
                    runs=values["home"],
                    role="home",
                    inning=number,
                    inning_count=inning_count,
                    lifecycle=state.lifecycle,
                    current_period=state.period,
                    current_half=prefix,
                    away_score=state.away_score,
                    home_score=state.home_score,
                ),
            )
        )

    scores = {state.away_team: state.away_score, state.home_team: state.home_score}
    totals = (
        _espn_team_totals(payload, team_ids, scores)
        if available
        else {name: TeamTotals() for name in team_ids.values()}
    )
    batting, pitching = (
        _espn_players(payload, team_ids)
        if available
        else (
            {name: [] for name in team_ids.values()},
            {name: [] for name in team_ids.values()},
        )
    )
    line_status: CompletenessStatus = (
        "unavailable"
        if not available or not innings
        else "complete"
        if all(
            inning.away_participation == "played"
            and inning.home_participation in {"played", "not_played", "not_reached"}
            for inning in innings
        )
        else "partial"
    )
    total_values = [totals[state.away_team], totals[state.home_team]]
    total_status: CompletenessStatus = (
        "unavailable"
        if not available
        else "complete"
        if all(
            getattr(total, field) is not None
            for total in total_values
            for field in ("runs", "hits", "errors")
        )
        else "partial"
    )
    batter_lines = [*batting[state.away_team], *batting[state.home_team]]
    pitcher_lines = [*pitching[state.away_team], *pitching[state.home_team]]
    batting_status = _collection_completeness(
        batter_lines,
        (
            "at_bats",
            "runs",
            "hits",
            "home_runs",
            "rbi",
            "walks",
            "strikeouts",
        ),
        available=available,
    )
    pitching_status = _collection_completeness(
        pitcher_lines,
        (
            "outs_recorded",
            "innings_pitched_display",
            "hits",
            "runs",
            "earned_runs",
            "walks",
            "strikeouts",
        ),
        available=available,
    )
    batting_required = _missing_collection_fields(
        batter_lines,
        ("at_bats", "runs", "hits", "home_runs", "rbi", "walks", "strikeouts"),
    )
    batting_optional = _missing_collection_fields(
        batter_lines, ("lineup_slot", "starter", "doubles", "triples", "stolen_bases")
    )
    pitching_required = _missing_collection_fields(
        pitcher_lines,
        (
            "outs_recorded",
            "innings_pitched_display",
            "hits",
            "runs",
            "earned_runs",
            "walks",
            "strikeouts",
        ),
    )
    pitching_optional = _missing_collection_fields(
        pitcher_lines, ("starter", "home_runs", "pitches", "strikes")
    )
    total_required = [
        field
        for field in ("runs", "hits", "errors")
        if any(getattr(total, field) is None for total in total_values)
    ]
    total_optional = (
        ["left_on_base"] if any(total.left_on_base is None for total in total_values) else []
    )
    line_required = [
        f"{role}_runs_inning_{inning.inning}"
        for inning in innings
        for role in ("away", "home")
        if getattr(inning, f"{role}_participation") == "unknown"
    ]
    completeness = BaseballBoxScoreCompleteness(
        line_score=line_status,
        team_totals=total_status,
        batting=batting_status,
        pitching=pitching_status,
        missing_required_fields={
            key: value
            for key, value in {
                "line_score": line_required,
                "team_totals": total_required,
                "batting": batting_required,
                "pitching": pitching_required,
            }.items()
            if value
        },
        missing_optional_fields={
            key: value
            for key, value in {
                "team_totals": total_optional,
                "batting": batting_optional,
                "pitching": pitching_optional,
            }.items()
            if value
        },
    )
    warnings = list(state.warnings)
    for section, fields in completeness.missing_required_fields.items():
        warnings.append(
            StateWarning(
                code="partial_box_score_section",
                message=(
                    f"ESPN omitted required {section} fields: {', '.join(fields)}. "
                    "Missing fields are omitted and season statistics were not substituted."
                ),
                section=section,
                fields=fields,
            )
        )
    for section, fields in completeness.missing_optional_fields.items():
        warnings.append(
            StateWarning(
                code="optional_box_score_fields_omitted",
                message=f"ESPN omitted optional {section} fields: {', '.join(fields)}.",
                section=section,
                fields=fields,
            )
        )
    box_score = BoxScore(
        league="mlb",
        sport="baseball",
        game_ref=state.game_ref,
        provider_game_id=state.provider_game_id,
        source=state.source,
        source_url=state.source_url,
        scheduled_start=state.scheduled_start,
        timezone=state.timezone,
        local_date=state.local_date,
        lifecycle=state.lifecycle,
        period=state.period,
        period_label=state.period_label,
        away_team=BoxScoreTeam(name=state.away_team, score=state.away_score),
        home_team=BoxScoreTeam(name=state.home_team, score=state.home_score),
        line_score=BaseballLineScore(
            innings=innings,
            away_totals=totals[state.away_team],
            home_totals=totals[state.home_team],
        ),
        batting=TeamBatting(away=batting[state.away_team], home=batting[state.home_team]),
        pitching=TeamPitching(away=pitching[state.away_team], home=pitching[state.home_team]),
        retrieved_at=state.retrieved_at,
        observation_id="pending",
        is_partial=state.lifecycle != "final" or not _complete_box_sections(completeness),
        completeness=completeness,
        warnings=warnings[:20],
    )
    return _finalize_box_score(box_score)


def _football_statistic(name: Any, label: Any, value: Any) -> FootballStatistic | None:
    safe_name = _text(name, limit=100)
    safe_label = _text(label, limit=100) or safe_name
    safe_value = _text(value, limit=100)
    if safe_name is None or safe_label is None or safe_value is None:
        return None
    return FootballStatistic(name=safe_name, label=safe_label, value=safe_value)


def _football_team_stats(
    root: dict[str, Any], team_ids: dict[str, str]
) -> dict[str, list[FootballStatistic]]:
    result: dict[str, list[FootballStatistic]] = {name: [] for name in team_ids.values()}
    boxscore = root.get("boxscore")
    if boxscore is None:
        return result
    rows = _objects(_object(boxscore, "summary.boxscore").get("teams"), "boxscore teams")
    for row in rows:
        team = _object(row.get("team"), "boxscore team")
        provider_id = _text(team.get("id"), limit=100)
        canonical = team_ids.get(provider_id or "")
        if canonical is None:
            raise StateValidationError(
                "response_identity_mismatch", "football box-score team is not a participant"
            )
        statistics = _objects(row.get("statistics"), "football team statistics")
        if len(statistics) > 100:
            raise StateValidationError(
                "response_too_large", "football team box score exceeds 100 statistics"
            )
        for statistic in statistics:
            parsed = _football_statistic(
                statistic.get("name"),
                statistic.get("label")
                or statistic.get("shortDisplayName")
                or statistic.get("displayName"),
                statistic.get("displayValue"),
            )
            if parsed is not None:
                result[canonical].append(parsed)
    return result


def _football_player_stats(
    root: dict[str, Any], team_ids: dict[str, str]
) -> dict[str, list[FootballPlayerGroup]]:
    result: dict[str, list[FootballPlayerGroup]] = {name: [] for name in team_ids.values()}
    boxscore = root.get("boxscore")
    if boxscore is None:
        return result
    team_rows = _objects(
        _object(boxscore, "summary.boxscore").get("players"), "football player teams"
    )
    for team_row in team_rows:
        team = _object(team_row.get("team"), "football player team")
        provider_id = _text(team.get("id"), limit=100)
        canonical = team_ids.get(provider_id or "")
        if canonical is None:
            raise StateValidationError(
                "response_identity_mismatch", "football player team is not a participant"
            )
        groups = _objects(team_row.get("statistics"), "football player groups")
        if len(groups) > 20:
            raise StateValidationError(
                "response_too_large", "football box score exceeds 20 player categories"
            )
        for group in groups:
            category = _text(group.get("name"), limit=100)
            if category is None:
                raise StateValidationError(
                    "malformed_response", "football player category is missing"
                )
            keys = group.get("keys")
            labels = group.get("labels")
            athletes = _objects(group.get("athletes"), f"football {category} athletes")
            if (
                not isinstance(keys, list)
                or not all(isinstance(key, str) for key in keys)
                or not isinstance(labels, list)
                or not all(isinstance(label, str) for label in labels)
                or len(keys) != len(labels)
            ):
                raise StateValidationError(
                    "malformed_response", "football player statistic metadata is invalid"
                )
            if len(athletes) > 100:
                raise StateValidationError(
                    "response_too_large", f"football {category} exceeds 100 players"
                )
            players: list[FootballPlayerLine] = []
            for entry in athletes:
                player_id, player_name = _espn_player_identity(entry)
                values = entry.get("stats")
                if not isinstance(values, list) or len(values) != len(keys):
                    raise StateValidationError(
                        "malformed_response", "football player statistic values are invalid"
                    )
                statistics = [
                    parsed
                    for key, label, value in zip(keys, labels, values, strict=True)
                    if (parsed := _football_statistic(key, label, value)) is not None
                ]
                players.append(
                    FootballPlayerLine(player_id=player_id, name=player_name, statistics=statistics)
                )
            if players:
                result[canonical].append(FootballPlayerGroup(category=category, players=players))
    return result


def _espn_football_box_score(
    root: Any, reference: _GameRefPayload, retrieved_at: datetime, source_url: str
) -> BoxScore:
    payload = _object(root, "summary response")
    state = _espn_detail(payload, reference, retrieved_at, source_url)
    if reference.league == "mlb":
        raise AssertionError("football box-score parser requires a football league")
    header = _object(payload.get("header"), "summary.header")
    competition = _objects(header.get("competitions"), "summary.header.competitions")[0]
    competitors = _objects(competition.get("competitors"), "competition.competitors")
    team_ids: dict[str, str] = {}
    by_role: dict[str, dict[str, Any]] = {}
    for competitor in competitors:
        role = competitor.get("homeAway")
        if role not in {"home", "away"} or role in by_role:
            raise StateValidationError(
                "malformed_response", "box-score home/away roles are invalid"
            )
        team = _object(competitor.get("team"), "box-score competitor team")
        provider_id = _text(team.get("id"), limit=100)
        if provider_id is None:
            raise StateValidationError("malformed_response", "box-score team ID is missing")
        canonical = state.home_team if role == "home" else state.away_team
        team_ids[provider_id] = canonical
        by_role[role] = competitor

    available = state.lifecycle not in NOT_STARTED_LIFECYCLES
    raw_lines: dict[str, list[dict[str, Any]]] = {}
    for role in ("away", "home"):
        raw_lines[role] = _objects(
            by_role[role].get("linescores", []) if available else [],
            f"{role} football period lines",
        )
        if len(raw_lines[role]) > 20:
            raise StateValidationError(
                "response_too_large", "football line score exceeds 20 periods"
            )
    period_count = max(len(raw_lines["away"]), len(raw_lines["home"]), state.period or 0)
    if state.lifecycle not in {"final", "cancelled", "postponed"} and state.period is not None:
        period_count = min(period_count, state.period)
    periods: list[FootballPeriodLine] = []
    for number in range(1, period_count + 1):
        values: dict[str, int | None] = {}
        for role in ("away", "home"):
            row = raw_lines[role][number - 1] if number <= len(raw_lines[role]) else {}
            values[role] = _optional_integer(
                row.get("displayValue"), f"{role} period {number} points"
            )
        periods.append(
            FootballPeriodLine(
                period=number, away_points=values["away"], home_points=values["home"]
            )
        )

    team_stats = (
        _football_team_stats(payload, team_ids)
        if available
        else {name: [] for name in team_ids.values()}
    )
    player_stats = (
        _football_player_stats(payload, team_ids)
        if available
        else {name: [] for name in team_ids.values()}
    )
    line_status: CompletenessStatus = (
        "unavailable"
        if not available or not periods
        else "complete"
        if all(
            period.away_points is not None and period.home_points is not None for period in periods
        )
        else "partial"
    )
    team_status: CompletenessStatus = (
        "complete"
        if available and team_stats[state.away_team] and team_stats[state.home_team]
        else "unavailable"
        if not available
        else "partial"
    )
    player_status: CompletenessStatus = (
        "complete"
        if available and player_stats[state.away_team] and player_stats[state.home_team]
        else "unavailable"
        if not available
        else "partial"
    )
    completeness = FootballBoxScoreCompleteness(
        line_score=line_status, team_stats=team_status, player_stats=player_status
    )
    warnings = list(state.warnings)
    for field, status_value in completeness.model_dump().items():
        if status_value == "partial":
            warnings.append(
                StateWarning(
                    code=f"partial_{field}",
                    message=f"ESPN did not report every requested {field.replace('_', ' ')} value.",
                )
            )
    box_score = BoxScore(
        sport="football",
        league=reference.league,
        game_ref=state.game_ref,
        provider_game_id=state.provider_game_id,
        source=state.source,
        source_url=state.source_url,
        scheduled_start=state.scheduled_start,
        timezone=state.timezone,
        local_date=state.local_date,
        lifecycle=state.lifecycle,
        period=state.period,
        period_label=state.period_label,
        away_team=BoxScoreTeam(name=state.away_team, score=state.away_score),
        home_team=BoxScoreTeam(name=state.home_team, score=state.home_score),
        line_score=FootballLineScore(periods=periods),
        team_stats=TeamFootballStatistics(
            away=team_stats[state.away_team], home=team_stats[state.home_team]
        ),
        player_stats=TeamFootballPlayers(
            away=player_stats[state.away_team], home=player_stats[state.home_team]
        ),
        retrieved_at=state.retrieved_at,
        observation_id="pending",
        is_partial=state.lifecycle != "final" or not _complete_box_sections(completeness),
        completeness=completeness,
        warnings=warnings[:20],
    )
    return _finalize_box_score(box_score)


def _mlb_team(value: Any) -> tuple[Team, str]:
    data = _object(value, "MLB team")
    name = _text(data.get("name"), limit=200)
    if name is None:
        raise StateValidationError("malformed_response", "MLB team name is missing")
    found = participant(name, "mlb")
    if found is None:
        raise StateValidationError("unknown_provider_team", "MLB team is not in reviewed catalog")
    return found, name


def _mlb_lifecycle(status: dict[str, Any]) -> Lifecycle:
    text = " ".join(
        filter(
            None,
            [
                _text(status.get("abstractGameState"), limit=100),
                _text(status.get("detailedState"), limit=100),
                _text(status.get("codedGameState"), limit=20),
            ],
        )
    ).lower()
    if "postpon" in text:
        return "postponed"
    if "cancel" in text:
        return "cancelled"
    if "suspend" in text:
        return "suspended"
    if "delay" in text:
        return "delayed"
    if "final" in text or "completed" in text:
        return "final"
    if "live" in text or "in progress" in text:
        return "live"
    if "preview" in text or "scheduled" in text or "pre-game" in text:
        return "scheduled"
    return "unknown"


def _mlb_schedule_candidates(root: Any, reference: _GameRefPayload) -> list[dict[str, Any]]:
    payload = _object(root, "MLB schedule response")
    dates = _objects(payload.get("dates"), "MLB schedule dates")
    games: list[dict[str, Any]] = []
    for day in dates:
        games.extend(_objects(day.get("games"), "MLB schedule games"))
    if len(games) > MAX_SCOREBOARD_EVENTS:
        raise StateValidationError("response_too_large", "MLB schedule exceeds 200 games")
    matched: list[dict[str, Any]] = []
    for game in games:
        try:
            teams_data = _object(game.get("teams"), "MLB schedule teams")
            home, _ = _mlb_team(_object(teams_data.get("home"), "home entry").get("team"))
            away, _ = _mlb_team(_object(teams_data.get("away"), "away entry").get("team"))
            if home.name == reference.home_team and away.name == reference.away_team:
                matched.append(game)
        except StateValidationError:
            continue
    if len(matched) <= 1:
        return matched
    close = [
        game
        for game in matched
        if abs(
            (
                _timestamp(game.get("gameDate"), "MLB gameDate") - reference.scheduled_start
            ).total_seconds()
        )
        <= 30 * 60
    ]
    return close if len(close) == 1 else []


def _mlb_detail(
    root: Any, reference: _GameRefPayload, retrieved_at: datetime, source_url: str
) -> GameState:
    payload = _object(root, "MLB live-feed response")
    game_data = _object(payload.get("gameData"), "MLB gameData")
    live_data = _object(payload.get("liveData"), "MLB liveData")
    datetime_data = _object(game_data.get("datetime"), "MLB datetime")
    scheduled = _timestamp(datetime_data.get("dateTime"), "MLB dateTime")
    teams_data = _object(game_data.get("teams"), "MLB teams")
    home, raw_home = _mlb_team(teams_data.get("home"))
    away, raw_away = _mlb_team(teams_data.get("away"))
    status = _object(game_data.get("status"), "MLB status")
    linescore = _object(live_data.get("linescore"), "MLB linescore")
    runs = _object(linescore.get("teams"), "MLB linescore teams")
    home_runs = _object(runs.get("home"), "MLB home linescore").get("runs")
    away_runs = _object(runs.get("away"), "MLB away linescore").get("runs")
    inning = _optional_integer(
        linescore.get("currentInning"), "MLB inning", maximum=30, zero_is_null=True
    )
    half_text = (_text(linescore.get("inningHalf"), limit=20) or "").lower()
    half: HalfInning = (
        "top"
        if half_text.startswith("top")
        else "bottom"
        if half_text.startswith("bot")
        else "unknown"
    )
    offense = _object(linescore.get("offense", {}), "MLB offense")
    defense = _object(linescore.get("defense", {}), "MLB defense")
    zone = ZoneInfo(reference.timezone)
    local = scheduled.astimezone(zone)
    game_pk = (
        game_data.get("game", {}).get("pk") if isinstance(game_data.get("game"), dict) else None
    )
    if game_pk is None:
        raise StateValidationError("malformed_response", "MLB gamePk is missing")
    provider_id = str(_integer(game_pk, "MLB gamePk", minimum=1))
    plays = _object(live_data.get("plays", {}), "MLB plays")
    current_play = plays.get("currentPlay")
    last_play = None
    if isinstance(current_play, dict) and isinstance(current_play.get("result"), dict):
        last_play = _text(current_play["result"].get("description"))
    lifecycle = _mlb_lifecycle(status)
    if lifecycle in NOT_STARTED_LIFECYCLES:
        home_runs = away_runs = inning = last_play = None
        half = "unknown"
    summary = GameSummary(
        league="mlb",
        game_ref=encode_game_ref(reference),
        source="mlb_statsapi",
        provider_game_id=provider_id,
        home_team=home.name,
        away_team=away.name,
        raw_home_team=raw_home,
        raw_away_team=raw_away,
        scheduled_start=scheduled,
        timezone=reference.timezone,
        local_date=local.date(),
        scheduled_start_local=local,
        home_score=None if home_runs is None else _integer(home_runs, "MLB home runs"),
        away_score=None if away_runs is None else _integer(away_runs, "MLB away runs"),
        lifecycle=lifecycle,
        period=inning,
        period_label=(
            None
            if lifecycle in NOT_STARTED_LIFECYCLES
            else _text(linescore.get("currentInningOrdinal"), limit=100)
        ),
        clock=None,
        last_play=last_play,
        retrieved_at=retrieved_at,
        observation_id=_observation_id("mlb_statsapi"),
        source_url=source_url,
        warnings=[
            StateWarning(
                code="mlb_fallback_used",
                message=(
                    "ESPN detail was unavailable; exact MLB StatsAPI identity matching was used."
                ),
            )
        ],
    )
    # The fallback uses a different provider ID, so validate all shared identity fields directly.
    if (
        summary.home_team != reference.home_team
        or summary.away_team != reference.away_team
        or summary.local_date
        != reference.scheduled_start.astimezone(ZoneInfo(reference.timezone)).date()
        or abs((summary.scheduled_start - reference.scheduled_start).total_seconds()) > 30 * 60
    ):
        raise IdentityMismatchError(
            "response_identity_mismatch", "MLB fallback detail conflicts with game_ref"
        )
    balls = _optional_integer(linescore.get("balls"), "MLB balls", maximum=4)
    strikes = _optional_integer(linescore.get("strikes"), "MLB strikes", maximum=3)
    outs = _optional_integer(linescore.get("outs"), "MLB outs", maximum=3)
    active = (
        lifecycle == "live"
        and inning is not None
        and half != "unknown"
        and (balls is None or balls <= 3)
        and (strikes is None or strikes <= 2)
        and (outs is None or outs <= 2)
    )
    phase: BaseballPhase = (
        "not_started"
        if lifecycle in NOT_STARTED_LIFECYCLES
        else "complete"
        if lifecycle == "final"
        else "active"
        if active
        else "transition"
        if lifecycle in {"live", "delayed", "suspended"}
        else "unavailable"
    )
    situation = (
        BaseballSituation(phase="not_started")
        if lifecycle in NOT_STARTED_LIFECYCLES
        else BaseballSituation(
            phase=phase,
            inning=inning,
            half=half,
            balls=balls,
            strikes=strikes,
            outs=outs,
            on_first="first" in offense,
            on_second="second" in offense,
            on_third="third" in offense,
            batter=_athlete_name(offense.get("batter")),
            pitcher=_athlete_name(defense.get("pitcher")),
        )
    )
    return GameState(**summary.model_dump(), situation=situation)


def _mlb_player(value: Any, label: str) -> tuple[str, str, dict[str, Any]]:
    player = _object(value, label)
    person = _object(player.get("person"), f"{label}.person")
    player_id = person.get("id")
    name = _text(person.get("fullName"), limit=200)
    if player_id is None or name is None:
        raise StateValidationError("malformed_response", f"{label} identity is missing")
    return str(_integer(player_id, f"{label} ID", minimum=1)), name, player


def _mlb_game_stat(value: Any, label: str) -> int | None:
    return _optional_integer(value, label)


def _mlb_batters(team_box: dict[str, Any]) -> list[BatterLine]:
    players = _object(team_box.get("players"), "MLB team players")
    batter_ids = team_box.get("batters")
    if not isinstance(batter_ids, list):
        raise StateValidationError("malformed_response", "MLB batter list is invalid")
    if len(batter_ids) > 30:
        raise StateValidationError("response_too_large", "MLB box score exceeds 30 batters")
    result: list[BatterLine] = []
    for raw_id in batter_ids:
        player_id = str(_integer(raw_id, "MLB batter ID", minimum=1))
        value = players.get(f"ID{player_id}")
        stable_id, name, player = _mlb_player(value, "MLB batter")
        stats = _object(player.get("stats"), "MLB batter stats")
        batting = _object(stats.get("batting", {}), "MLB game batting stats")
        if not batting:
            continue
        raw_order = _text(player.get("battingOrder"), limit=10)
        lineup_slot = None
        starter = None
        if raw_order is not None:
            numeric_order = _integer(raw_order, "MLB batting order", minimum=100, maximum=999)
            lineup_slot = numeric_order // 100
            starter = numeric_order % 100 == 0
        positions: list[str] = []
        all_positions = player.get("allPositions")
        if isinstance(all_positions, list):
            for position in all_positions[:10]:
                if isinstance(position, dict):
                    abbreviation = _text(position.get("abbreviation"), limit=20)
                    if abbreviation and abbreviation not in positions:
                        positions.append(abbreviation)
        result.append(
            BatterLine(
                player_id=stable_id,
                name=name,
                lineup_slot=lineup_slot,
                positions=positions,
                starter=starter,
                at_bats=_mlb_game_stat(batting.get("atBats"), "MLB batter at-bats"),
                runs=_mlb_game_stat(batting.get("runs"), "MLB batter runs"),
                hits=_mlb_game_stat(batting.get("hits"), "MLB batter hits"),
                doubles=_mlb_game_stat(batting.get("doubles"), "MLB batter doubles"),
                triples=_mlb_game_stat(batting.get("triples"), "MLB batter triples"),
                home_runs=_mlb_game_stat(batting.get("homeRuns"), "MLB batter home runs"),
                rbi=_mlb_game_stat(batting.get("rbi"), "MLB batter RBI"),
                walks=_mlb_game_stat(batting.get("baseOnBalls"), "MLB batter walks"),
                strikeouts=_mlb_game_stat(batting.get("strikeOuts"), "MLB batter strikeouts"),
                stolen_bases=_mlb_game_stat(batting.get("stolenBases"), "MLB batter stolen bases"),
            )
        )
    return result


def _mlb_pitchers(team_box: dict[str, Any]) -> list[PitcherLine]:
    players = _object(team_box.get("players"), "MLB team players")
    pitcher_ids = team_box.get("pitchers")
    if not isinstance(pitcher_ids, list):
        raise StateValidationError("malformed_response", "MLB pitcher list is invalid")
    if len(pitcher_ids) > 30:
        raise StateValidationError("response_too_large", "MLB box score exceeds 30 pitchers")
    result: list[PitcherLine] = []
    for order, raw_id in enumerate(pitcher_ids, start=1):
        player_id = str(_integer(raw_id, "MLB pitcher ID", minimum=1))
        stable_id, name, player = _mlb_player(players.get(f"ID{player_id}"), "MLB pitcher")
        stats = _object(player.get("stats"), "MLB pitcher stats")
        pitching = _object(stats.get("pitching", {}), "MLB game pitching stats")
        if not pitching:
            continue
        outs_from_display, display = _innings_to_outs(
            pitching.get("inningsPitched"), "MLB pitcher innings"
        )
        outs = _mlb_game_stat(pitching.get("outs"), "MLB pitcher outs")
        if outs is not None and outs_from_display is not None and outs != outs_from_display:
            raise StateValidationError(
                "impossible_state", "MLB pitcher outs conflict with innings pitched"
            )
        pitches = _mlb_game_stat(
            pitching.get("numberOfPitches", pitching.get("pitchesThrown")),
            "MLB pitcher pitches",
        )
        strikes = _mlb_game_stat(pitching.get("strikes"), "MLB pitcher strikes")
        if pitches is not None and strikes is not None and strikes > pitches:
            raise StateValidationError("impossible_state", "MLB pitcher strikes exceed pitches")
        games_started = _mlb_game_stat(pitching.get("gamesStarted"), "MLB pitcher games started")
        result.append(
            PitcherLine(
                player_id=stable_id,
                name=name,
                starter=games_started is not None and games_started > 0,
                appearance_order=order,
                outs_recorded=outs if outs is not None else outs_from_display,
                innings_pitched_display=display,
                hits=_mlb_game_stat(pitching.get("hits"), "MLB pitcher hits"),
                runs=_mlb_game_stat(pitching.get("runs"), "MLB pitcher runs"),
                earned_runs=_mlb_game_stat(pitching.get("earnedRuns"), "MLB pitcher earned runs"),
                walks=_mlb_game_stat(pitching.get("baseOnBalls"), "MLB pitcher walks"),
                strikeouts=_mlb_game_stat(pitching.get("strikeOuts"), "MLB pitcher strikeouts"),
                home_runs=_mlb_game_stat(pitching.get("homeRuns"), "MLB pitcher home runs"),
                pitches=pitches,
                strikes=strikes,
            )
        )
    return result


def _mlb_box_score(
    root: Any, reference: _GameRefPayload, retrieved_at: datetime, source_url: str
) -> BoxScore:
    payload = _object(root, "MLB live-feed response")
    state = _mlb_detail(payload, reference, retrieved_at, source_url)
    live_data = _object(payload.get("liveData"), "MLB liveData")
    linescore = _object(live_data.get("linescore"), "MLB linescore")
    innings_data = _objects(linescore.get("innings", []), "MLB innings")
    if len(innings_data) > 30:
        raise StateValidationError("response_too_large", "MLB line score exceeds 30 innings")
    innings: list[InningLine] = []
    inning_count = len(innings_data)
    current_half = (_text(linescore.get("inningHalf"), limit=20) or "").lower()
    for entry in innings_data:
        number = _integer(entry.get("num"), "MLB inning number", minimum=1, maximum=30)
        away = _object(entry.get("away", {}), "MLB away inning")
        home = _object(entry.get("home", {}), "MLB home inning")
        away_runs = _optional_integer(away.get("runs"), "MLB away inning runs")
        home_runs = _optional_integer(home.get("runs"), "MLB home inning runs")
        innings.append(
            InningLine(
                inning=number,
                away_runs=away_runs,
                home_runs=home_runs,
                away_participation=_inning_participation(
                    runs=away_runs,
                    role="away",
                    inning=number,
                    inning_count=inning_count,
                    lifecycle=state.lifecycle,
                    current_period=state.period,
                    current_half=current_half,
                    away_score=state.away_score,
                    home_score=state.home_score,
                ),
                home_participation=_inning_participation(
                    runs=home_runs,
                    role="home",
                    inning=number,
                    inning_count=inning_count,
                    lifecycle=state.lifecycle,
                    current_period=state.period,
                    current_half=current_half,
                    away_score=state.away_score,
                    home_score=state.home_score,
                ),
            )
        )
    if [inning.inning for inning in innings] != sorted({inning.inning for inning in innings}):
        raise StateValidationError("impossible_state", "MLB innings are duplicated or unordered")

    totals_data = _object(linescore.get("teams"), "MLB linescore teams")

    def totals(role: str) -> TeamTotals:
        value = _object(totals_data.get(role, {}), f"MLB {role} totals")
        return TeamTotals(
            runs=_optional_integer(value.get("runs"), f"MLB {role} runs"),
            hits=_optional_integer(value.get("hits"), f"MLB {role} hits"),
            errors=_optional_integer(value.get("errors"), f"MLB {role} errors"),
            left_on_base=_optional_integer(value.get("leftOnBase"), f"MLB {role} left on base"),
        )

    boxscore = _object(live_data.get("boxscore"), "MLB boxscore")
    team_boxes = _object(boxscore.get("teams"), "MLB boxscore teams")
    away_box = _object(team_boxes.get("away"), "MLB away boxscore")
    home_box = _object(team_boxes.get("home"), "MLB home boxscore")
    away_batting, home_batting = _mlb_batters(away_box), _mlb_batters(home_box)
    away_pitching, home_pitching = _mlb_pitchers(away_box), _mlb_pitchers(home_box)
    available = state.lifecycle not in NOT_STARTED_LIFECYCLES
    line_status: CompletenessStatus = (
        "unavailable"
        if not available or not innings
        else "complete"
        if all(
            inning.away_participation == "played"
            and inning.home_participation in {"played", "not_played", "not_reached"}
            for inning in innings
        )
        else "partial"
    )
    team_totals = [totals("away"), totals("home")]
    total_status: CompletenessStatus = (
        "unavailable"
        if not available
        else "complete"
        if all(
            getattr(team_total, field) is not None
            for team_total in team_totals
            for field in ("runs", "hits", "errors")
        )
        else "partial"
    )
    batter_lines = [*away_batting, *home_batting]
    pitcher_lines = [*away_pitching, *home_pitching]
    batting_status = _collection_completeness(
        batter_lines,
        (
            "at_bats",
            "runs",
            "hits",
            "home_runs",
            "rbi",
            "walks",
            "strikeouts",
        ),
        available=available,
    )
    pitching_status = _collection_completeness(
        pitcher_lines,
        (
            "outs_recorded",
            "innings_pitched_display",
            "hits",
            "runs",
            "earned_runs",
            "walks",
            "strikeouts",
        ),
        available=available,
    )
    batting_required = _missing_collection_fields(
        batter_lines,
        ("at_bats", "runs", "hits", "home_runs", "rbi", "walks", "strikeouts"),
    )
    batting_optional = _missing_collection_fields(
        batter_lines, ("lineup_slot", "starter", "doubles", "triples", "stolen_bases")
    )
    pitching_required = _missing_collection_fields(
        pitcher_lines,
        (
            "outs_recorded",
            "innings_pitched_display",
            "hits",
            "runs",
            "earned_runs",
            "walks",
            "strikeouts",
        ),
    )
    pitching_optional = _missing_collection_fields(
        pitcher_lines, ("starter", "home_runs", "pitches", "strikes")
    )
    total_required = [
        field
        for field in ("runs", "hits", "errors")
        if any(getattr(team_total, field) is None for team_total in team_totals)
    ]
    total_optional = (
        ["left_on_base"]
        if any(team_total.left_on_base is None for team_total in team_totals)
        else []
    )
    line_required = [
        f"{role}_runs_inning_{inning.inning}"
        for inning in innings
        for role in ("away", "home")
        if getattr(inning, f"{role}_participation") == "unknown"
    ]
    completeness = BaseballBoxScoreCompleteness(
        line_score=line_status,
        team_totals=total_status,
        batting=batting_status,
        pitching=pitching_status,
        missing_required_fields={
            key: value
            for key, value in {
                "line_score": line_required,
                "team_totals": total_required,
                "batting": batting_required,
                "pitching": pitching_required,
            }.items()
            if value
        },
        missing_optional_fields={
            key: value
            for key, value in {
                "team_totals": total_optional,
                "batting": batting_optional,
                "pitching": pitching_optional,
            }.items()
            if value
        },
    )
    warnings = list(state.warnings)
    for section, fields in completeness.missing_required_fields.items():
        warnings.append(
            StateWarning(
                code="partial_box_score_section",
                message=f"MLB StatsAPI omitted required {section} fields: {', '.join(fields)}.",
                section=section,
                fields=fields,
            )
        )
    for section, fields in completeness.missing_optional_fields.items():
        warnings.append(
            StateWarning(
                code="optional_box_score_fields_omitted",
                message=f"MLB StatsAPI omitted optional {section} fields: {', '.join(fields)}.",
                section=section,
                fields=fields,
            )
        )
    box_score_result = BoxScore(
        league="mlb",
        sport="baseball",
        game_ref=state.game_ref,
        provider_game_id=state.provider_game_id,
        source=state.source,
        source_url=state.source_url,
        scheduled_start=state.scheduled_start,
        timezone=state.timezone,
        local_date=state.local_date,
        lifecycle=state.lifecycle,
        period=state.period,
        period_label=state.period_label,
        away_team=BoxScoreTeam(name=state.away_team, score=state.away_score),
        home_team=BoxScoreTeam(name=state.home_team, score=state.home_score),
        line_score=BaseballLineScore(
            innings=innings, away_totals=team_totals[0], home_totals=team_totals[1]
        ),
        batting=TeamBatting(away=away_batting, home=home_batting),
        pitching=TeamPitching(away=away_pitching, home=home_pitching),
        retrieved_at=state.retrieved_at,
        observation_id="pending",
        is_partial=state.lifecycle != "final" or not _complete_box_sections(completeness),
        completeness=completeness,
        warnings=warnings[:20],
    )
    return _finalize_box_score(box_score_result)


def _mlb_play_feed(
    root: Any, reference: _GameRefPayload, retrieved_at: datetime, source_url: str
) -> _PlayFeed:
    payload = _object(root, "MLB live-feed response")
    state = _mlb_detail(payload, reference, retrieved_at, source_url)
    live_data = _object(payload.get("liveData"), "MLB liveData")
    plays_data = _object(live_data.get("plays", {}), "MLB plays")
    raw_plays = _objects(plays_data.get("allPlays", []), "MLB allPlays")
    if len(raw_plays) > MAX_PLAYS:
        raise StateValidationError("response_too_large", "MLB play feed exceeds 1,000 at-bats")
    plays: list[GamePlay] = []
    outs_by_half: dict[tuple[int | None, HalfInning], int] = {}
    warnings = list(state.warnings)
    discarded = 0
    for raw in raw_plays:
        try:
            about = _object(raw.get("about"), "MLB play about")
            result = _object(raw.get("result"), "MLB play result")
            at_bat_index = _integer(about.get("atBatIndex"), "MLB at-bat index", minimum=0)
            inning = _optional_integer(about.get("inning"), "MLB play inning", maximum=30)
            half_text = (_text(about.get("halfInning"), limit=20) or "").casefold()
            half: HalfInning = (
                "top"
                if half_text.startswith("top")
                else "bottom"
                if half_text.startswith("bot")
                else "unknown"
            )
            count = _object(raw.get("count", {}), "MLB play count")
            text = _text(result.get("description"))
            if text is None:
                raise StateValidationError("malformed_response", "MLB play text is missing")
            rbi = _optional_integer(result.get("rbi"), "MLB play RBI")
            scoring = _boolean(about.get("isScoringPlay"), "MLB scoring play")
            side: Literal["away", "home"] | None = (
                "away" if half == "top" else "home" if half == "bottom" else None
            )
            team = (
                state.away_team if side == "away" else state.home_team if side == "home" else None
            )
            outs_after = _optional_integer(count.get("outs"), "MLB play outs", maximum=3)
            half_key = (inning, half)
            outs_before = outs_by_half.get(half_key, 0) if outs_after is not None else None
            if outs_after is not None:
                outs_by_half[half_key] = outs_after
            plays.append(
                GamePlay(
                    play_id=f"{state.provider_game_id}:{at_bat_index}",
                    sequence=len(plays) + 1,
                    provider_sequence=str(at_bat_index),
                    period=inning,
                    period_label=(
                        f"{half.title()} {inning}"
                        if inning is not None and half != "unknown"
                        else None
                    ),
                    play_type=_text(result.get("event") or result.get("eventType"), limit=100),
                    event_kind="plate_appearance",
                    text=text,
                    team=team,
                    team_side=side,
                    scoring_play=scoring if scoring is not None else bool(rbi),
                    away_score=_optional_integer(result.get("awayScore"), "MLB away score"),
                    home_score=_optional_integer(result.get("homeScore"), "MLB home score"),
                    wallclock=_optional_timestamp(about.get("startTime"), "MLB play start time"),
                    context=BaseballPlayContext(
                        half=half,
                        balls=_optional_integer(count.get("balls"), "MLB play balls", maximum=4),
                        strikes=_optional_integer(
                            count.get("strikes"), "MLB play strikes", maximum=3
                        ),
                        outs_before=outs_before,
                        outs_after=outs_after,
                        at_bat_id=str(at_bat_index),
                    ),
                )
            )
        except StateValidationError:
            discarded += 1
    if discarded:
        warnings.append(
            StateWarning(
                code="discarded_provider_plays",
                message=f"Skipped {discarded} malformed MLB at-bat record(s).",
            )
        )
    feed = _PlayFeed(
        league="mlb",
        sport="baseball",
        granularity="at_bat",
        game_ref=state.game_ref,
        provider_game_id=state.provider_game_id,
        source=state.source,
        source_url=state.source_url,
        scheduled_start=state.scheduled_start,
        timezone=state.timezone,
        local_date=state.local_date,
        lifecycle=state.lifecycle,
        period=state.period,
        period_label=state.period_label,
        away_team=BoxScoreTeam(name=state.away_team, score=state.away_score),
        home_team=BoxScoreTeam(name=state.home_team, score=state.home_score),
        retrieved_at=state.retrieved_at,
        observation_id="pending",
        is_partial=state.lifecycle != "final" or discarded > 0 or not plays,
        warnings=warnings[:20],
        plays=plays,
    )
    return _finalize_play_feed(feed)


class SportsStateClient:
    """Two fixed-provider, bounded HTTP paths with no caller-controlled URLs."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10,
        max_attempts: int = 2,
        retry_backoff_seconds: float = 0.1,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        http_client: httpx.AsyncClient | None = None,
        now: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0 or not 1 <= max_attempts <= 2 or max_response_bytes < 1:
            raise ValueError("invalid sports-state client bounds")
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.max_response_bytes = max_response_bytes
        self._owns_client = http_client is None
        # Intentionally no custom User-Agent: feasibility showed browser-shaped identities fail.
        self._http = http_client or httpx.AsyncClient(timeout=self.timeout)
        self._now = now or (lambda: datetime.now(UTC))
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._box_score_cache: OrderedDict[str, _BoxScoreCacheEntry] = OrderedDict()
        self._play_feed_cache: OrderedDict[str, _PlayFeedCacheEntry] = OrderedDict()
        self._discoveries: OrderedDict[str, GameSummary] = OrderedDict()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _request_json(
        self, url: str, *, params: Mapping[str, str | int] | None, operation: str, league: League
    ) -> tuple[Any, httpx.Headers]:
        for attempt in range(1, self.max_attempts + 1):
            started = time.perf_counter()
            try:
                response = await self._http.get(url, params=params, timeout=self.timeout)
            except httpx.TransportError as error:
                log_event(
                    logger,
                    "sports_state_http",
                    operation=operation,
                    league=league,
                    status=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    error_type=type(error).__name__,
                )
                if attempt < self.max_attempts:
                    await asyncio.sleep(self.retry_backoff_seconds * attempt)
                    continue
                raise ProviderUnavailableError(
                    "provider_unavailable", "sports data provider is unreachable or timed out"
                ) from error
            log_event(
                logger,
                "sports_state_http",
                operation=operation,
                league=league,
                status=response.status_code,
                latency_ms=round((time.perf_counter() - started) * 1000),
                error_type=None,
            )
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt < self.max_attempts:
                await asyncio.sleep(self.retry_backoff_seconds * attempt)
                continue
            if not response.is_success:
                raise ProviderUnavailableError(
                    "provider_unavailable",
                    "sports data provider returned an unavailable response",
                    fields={"status": response.status_code},
                )
            if len(response.content) > self.max_response_bytes:
                raise StateValidationError(
                    "response_too_large", "sports provider response exceeded the 5 MiB limit"
                )
            try:
                return response.json(), response.headers
            except ValueError as error:
                raise StateValidationError(
                    "malformed_response", "sports provider returned invalid JSON"
                ) from error
        raise AssertionError("request loop exhausted")

    def _provider_dates(self, local_day: date, zone: ZoneInfo) -> list[date]:
        start = datetime.combine(local_day, datetime_time.min, zone).astimezone(UTC)
        end = datetime.combine(local_day + timedelta(days=1), datetime_time.min, zone).astimezone(
            UTC
        )
        values = [local_day, start.date(), (end - timedelta(microseconds=1)).date()]
        return list(dict.fromkeys(values))[:3]

    async def find_games(
        self,
        query: str,
        *,
        league: League,
        timezone: str,
        local_date: date | None = None,
        limit: int = 5,
        compact: bool = False,
    ) -> FindGamesResult:
        try:
            zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise SportsStateError(
                "invalid_timezone",
                "timezone must be a valid IANA timezone",
                fields={"timezone": timezone},
            ) from error
        if type(limit) is not int or not 1 <= limit <= 10:
            raise SportsStateError(
                "invalid_limit",
                "limit must be an integer from 1 through 10",
                fields={"limit": limit},
            )
        normalized_query = " ".join(re.findall(r"[a-z0-9]+", query.casefold()))
        schedule_query = normalized_query in {
            "all",
            "all games",
            "games",
            "schedule",
            "today games",
            "todays games",
        }
        if type(compact) is not bool:
            raise SportsStateError(
                "invalid_compact", "compact must be a boolean", fields={"compact": "invalid"}
            )
        sports_query = None if schedule_query else resolve_query(query, league)
        selected_day = local_date or self._now().astimezone(zone).date()
        if sports_query is not None and sports_query.clarification:
            example = sports_query.choices[0] if sports_query.choices else None
            retry = (
                f' Retry sports_state_find_games with query="{example}" and keep league, '
                "timezone, and local_date unchanged."
                if example
                else " Retry with one full team name or one exact matchup."
            )
            return FindGamesResult(
                query=query,
                league=league,
                timezone=zone.key,
                local_date=selected_day,
                discovery_mode="clarification",
                compact=compact,
                games=[],
                coverage=DiscoveryCoverage(
                    requested_local_date=selected_day,
                    derived_local_date=local_date is None,
                    scoreboard_requests=0,
                    provider_dates_requested=[],
                    events_scanned=0,
                    matching_games=0,
                    discarded_event_count=0,
                ),
                clarification=f"{sports_query.clarification}{retry}",
                choices=sports_query.choices,
                suggested_queries=sports_query.choices,
            )
        provider_dates = self._provider_dates(selected_day, zone)
        games: dict[str, GameSummary] = {}
        warnings: list[StateWarning] = []
        events_scanned = 0
        discarded = 0
        requests = 0
        for provider_day in provider_dates:
            sport, provider_league = ESPN_ROUTES[league]
            url = f"{ESPN_BASE}/{sport}/{provider_league}/scoreboard"
            root, _ = await self._request_json(
                url,
                params={"dates": provider_day.strftime("%Y%m%d"), "limit": 200},
                operation="find_games",
                league=league,
            )
            requests += 1
            payload = _object(root, "scoreboard response")
            events = _objects(payload.get("events"), "scoreboard events")
            if len(events) > MAX_SCOREBOARD_EVENTS:
                raise StateValidationError(
                    "response_too_large", "scoreboard returned more than 200 events"
                )
            events_scanned += len(events)
            retrieved_at = self._now()
            for event in events:
                try:
                    summary = _event_summary(event, league, zone, retrieved_at)
                except StateValidationError:
                    discarded += 1
                    if len(warnings) < 20:
                        warnings.append(
                            StateWarning(
                                code="discarded_provider_event",
                                message="One malformed provider event was skipped.",
                            )
                        )
                    continue
                if summary.local_date != selected_day:
                    continue
                if sports_query is not None and not all(
                    requested.name in {summary.home_team, summary.away_team}
                    for requested in sports_query.teams
                ):
                    continue
                games.setdefault(summary.provider_game_id, summary)
            if games and not schedule_query:
                break
        all_ranked = sorted(
            games.values(), key=lambda game: (game.scheduled_start, game.provider_game_id)
        )
        ranked = all_ranked[:limit]
        if len(all_ranked) > limit and len(warnings) < 20:
            warnings.append(
                StateWarning(
                    code="results_truncated",
                    message=f"Returned the first {limit} games from this bounded local-day slate.",
                )
            )
        for game in ranked:
            self._discoveries[game.game_ref] = game
            self._discoveries.move_to_end(game.game_ref)
        while len(self._discoveries) > MAX_CACHE_ENTRIES:
            self._discoveries.popitem(last=False)
        coverage = DiscoveryCoverage(
            requested_local_date=selected_day,
            derived_local_date=local_date is None,
            scoreboard_requests=requests,
            provider_dates_requested=provider_dates[:requests],
            events_scanned=events_scanned,
            matching_games=len(ranked),
            discarded_event_count=discarded,
            warnings=warnings,
            utc_boundary_check=requests > 1,
        )
        returned_games: list[GameSummary | CompactGameSummary] = (
            [
                CompactGameSummary(
                    **game.model_dump(
                        include={
                            "game_ref",
                            "home_team",
                            "away_team",
                            "scheduled_start",
                            "scheduled_start_local",
                            "lifecycle",
                        }
                    )
                )
                for game in ranked
            ]
            if compact
            else list(ranked)
        )
        return FindGamesResult(
            query=query,
            league=league,
            timezone=zone.key,
            local_date=selected_day,
            discovery_mode="schedule" if schedule_query else "team",
            compact=compact,
            games=returned_games,
            coverage=coverage,
        )

    def _cached(self, game_ref: str) -> GameState | None:
        entry = self._cache.get(game_ref)
        now = self._now()
        if entry is None:
            return None
        if now >= entry.expires_at:
            del self._cache[game_ref]
            return None
        self._cache.move_to_end(game_ref)
        age = max(0, int((now - entry.cached_at).total_seconds() * 1000))
        return entry.state.model_copy(update={"cache_hit": True, "cache_age_ms": age})

    def _cache_state(self, game_ref: str, state: GameState, headers: httpx.Headers) -> GameState:
        cap = (
            300
            if state.lifecycle in {"final", "postponed", "cancelled"}
            else 30
            if state.lifecycle in {"scheduled", "pregame"}
            else 5
        )
        max_age = cap
        match = re.search(r"(?:^|,)\s*max-age=(\d+)", headers.get("cache-control", ""), re.I)
        if match:
            max_age = min(cap, int(match.group(1)))
        now = self._now()
        self._cache[game_ref] = _CacheEntry(
            cached_at=now, expires_at=now + timedelta(seconds=max_age), state=state
        )
        self._cache.move_to_end(game_ref)
        while len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)
        return state

    def _cached_box_score(self, game_ref: str) -> BoxScore | None:
        entry = self._box_score_cache.get(game_ref)
        now = self._now()
        if entry is None:
            return None
        if now >= entry.expires_at:
            del self._box_score_cache[game_ref]
            return None
        self._box_score_cache.move_to_end(game_ref)
        age = max(0, int((now - entry.cached_at).total_seconds() * 1000))
        return entry.box_score.model_copy(update={"cache_hit": True, "cache_age_ms": age})

    def _cache_box_score(
        self, game_ref: str, box_score: BoxScore, headers: httpx.Headers
    ) -> BoxScore:
        cap = (
            300
            if box_score.lifecycle in {"final", "postponed", "cancelled"}
            else 30
            if box_score.lifecycle in {"scheduled", "pregame"}
            else 10
        )
        max_age = cap
        match = re.search(r"(?:^|,)\s*max-age=(\d+)", headers.get("cache-control", ""), re.I)
        if match:
            max_age = min(cap, int(match.group(1)))
        now = self._now()
        self._box_score_cache[game_ref] = _BoxScoreCacheEntry(
            cached_at=now,
            expires_at=now + timedelta(seconds=max_age),
            box_score=box_score,
        )
        self._box_score_cache.move_to_end(game_ref)
        while len(self._box_score_cache) > MAX_CACHE_ENTRIES:
            self._box_score_cache.popitem(last=False)
        return box_score

    def _cached_play_feed(self, game_ref: str) -> _PlayFeed | None:
        entry = self._play_feed_cache.get(game_ref)
        now = self._now()
        if entry is None:
            return None
        if now >= entry.expires_at:
            del self._play_feed_cache[game_ref]
            return None
        self._play_feed_cache.move_to_end(game_ref)
        age = max(0, int((now - entry.cached_at).total_seconds() * 1000))
        return entry.feed.model_copy(update={"cache_hit": True, "cache_age_ms": age})

    def _cache_play_feed(self, game_ref: str, feed: _PlayFeed, headers: httpx.Headers) -> _PlayFeed:
        cap = (
            300
            if feed.lifecycle in {"final", "postponed", "cancelled"}
            else 30
            if feed.lifecycle in {"scheduled", "pregame"}
            else 5
        )
        max_age = cap
        match = re.search(r"(?:^|,)\s*max-age=(\d+)", headers.get("cache-control", ""), re.I)
        if match:
            max_age = min(cap, int(match.group(1)))
        now = self._now()
        self._play_feed_cache[game_ref] = _PlayFeedCacheEntry(
            cached_at=now,
            expires_at=now + timedelta(seconds=max_age),
            feed=feed,
        )
        self._play_feed_cache.move_to_end(game_ref)
        while len(self._play_feed_cache) > MAX_CACHE_ENTRIES:
            self._play_feed_cache.popitem(last=False)
        return feed

    async def get_game_state(self, game_ref: str) -> GameState:
        reference = decode_game_ref(game_ref)
        cached = self._cached(game_ref)
        if cached is not None:
            log_event(
                logger,
                "sports_state_cache",
                operation="get_game_state",
                league=reference.league,
                cache_status="hit",
            )
            return cached
        sport, provider_league = ESPN_ROUTES[reference.league]
        url = f"{ESPN_BASE}/{sport}/{provider_league}/summary"
        try:
            root, headers = await self._request_json(
                url,
                params={"event": reference.event_id},
                operation="get_game_state",
                league=reference.league,
            )
            state = _espn_detail(
                root,
                reference,
                self._now(),
                _source_url(reference.league, "summary", reference.event_id),
            )
            return self._cache_state(game_ref, state, headers)
        except IdentityMismatchError:
            raise
        except (ProviderUnavailableError, StateValidationError) as primary_error:
            if reference.league != "mlb":
                raise primary_error
            return await self._mlb_fallback(game_ref, reference, primary_error)

    async def get_box_score(self, game_ref: str) -> BoxScore:
        reference = decode_game_ref(game_ref)
        cached = self._cached_box_score(game_ref)
        if cached is not None:
            log_event(
                logger,
                "sports_state_cache",
                operation="get_box_score",
                league=reference.league,
                cache_status="hit",
            )
            return cached
        sport, provider_league = ESPN_ROUTES[reference.league]
        url = f"{ESPN_BASE}/{sport}/{provider_league}/summary"
        try:
            root, headers = await self._request_json(
                url,
                params={"event": reference.event_id},
                operation="get_box_score",
                league=reference.league,
            )
            source_url = _source_url(reference.league, "summary", reference.event_id)
            box_score: BoxScore = (
                _espn_box_score(root, reference, self._now(), source_url)
                if reference.league == "mlb"
                else _espn_football_box_score(root, reference, self._now(), source_url)
            )
            return self._cache_box_score(game_ref, box_score, headers)
        except IdentityMismatchError:
            raise
        except (ProviderUnavailableError, StateValidationError) as primary_error:
            if reference.league != "mlb":
                raise primary_error
            return await self._mlb_box_score_fallback(game_ref, reference, primary_error)

    async def list_players(self, game_ref: str) -> PlayerDirectory:
        """List compact player selectors from the exact game's normalized box score."""

        return list_box_score_players(await self.get_box_score(game_ref))

    async def get_player_stats(self, game_ref: str, player_id: str) -> PlayerStats:
        """Return one player's game-only lines without exposing unrelated players."""

        if (
            not isinstance(player_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}", player_id) is None
        ):
            raise SportsStateError(
                "invalid_player_id",
                "player_id must be copied unchanged from sports_state_list_players",
                fields={"player_id": "invalid"},
            )
        return project_player_stats(await self.get_box_score(game_ref), player_id)

    async def get_play_by_play(
        self,
        game_ref: str,
        *,
        limit: int = 20,
        before_play_id: str | None = None,
        after_play_id: str | None = None,
        play_filter: Literal["all", "scoring"] = "all",
        period: int | None = None,
        team: Literal["away", "home"] | None = None,
    ) -> PlayByPlay:
        """Return a bounded play window with stable before/after anchors."""

        if type(limit) is not int or not 1 <= limit <= 50:
            raise SportsStateError(
                "invalid_play_limit",
                "limit must be an integer from 1 through 50",
                fields={"limit": limit},
            )
        if before_play_id is not None and after_play_id is not None:
            raise SportsStateError(
                "conflicting_play_anchors",
                "before_play_id and after_play_id are mutually exclusive",
                fields={"before_play_id": before_play_id, "after_play_id": after_play_id},
            )
        for field, value in (
            ("before_play_id", before_play_id),
            ("after_play_id", after_play_id),
        ):
            if (
                value is not None
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}", value) is None
            ):
                raise SportsStateError(
                    "invalid_play_id",
                    f"{field} must be copied unchanged from a play-by-play result",
                    fields={field: "invalid"},
                )
        if play_filter not in {"all", "scoring"}:
            raise SportsStateError(
                "invalid_play_filter",
                "play_filter must be all or scoring",
                fields={"play_filter": str(play_filter)},
            )
        if period is not None and (type(period) is not int or not 1 <= period <= 30):
            raise SportsStateError(
                "invalid_period",
                "period must be an integer from 1 through 30",
                fields={"period": period},
            )
        if team not in {None, "away", "home"}:
            raise SportsStateError(
                "invalid_team_filter",
                "team must be away or home",
                fields={"team": str(team)},
            )
        reference = decode_game_ref(game_ref)
        feed = self._cached_play_feed(game_ref)
        if feed is None:
            sport, provider_league = ESPN_ROUTES[reference.league]
            url = f"{ESPN_BASE}/{sport}/{provider_league}/summary"
            try:
                root, headers = await self._request_json(
                    url,
                    params={"event": reference.event_id},
                    operation="get_play_by_play",
                    league=reference.league,
                )
                feed = _espn_play_feed(
                    root,
                    reference,
                    self._now(),
                    _source_url(reference.league, "summary", reference.event_id),
                )
                feed = self._cache_play_feed(game_ref, feed, headers)
            except IdentityMismatchError:
                raise
            except (ProviderUnavailableError, StateValidationError) as primary_error:
                if reference.league != "mlb":
                    raise primary_error
                feed = await self._mlb_play_feed_fallback(game_ref, reference, primary_error)
        return select_play_window(
            feed,
            limit=limit,
            before_play_id=before_play_id,
            after_play_id=after_play_id,
            play_filter=play_filter,
            period=period,
            team=team,
        )

    async def _mlb_play_feed_fallback(
        self,
        game_ref: str,
        reference: _GameRefPayload,
        primary_error: SportsStateError,
    ) -> _PlayFeed:
        local_day = reference.scheduled_start.astimezone(ZoneInfo(reference.timezone)).date()
        schedule_url = f"{MLB_STATS_BASE}/v1/schedule"
        try:
            root, _ = await self._request_json(
                schedule_url,
                params={"sportId": 1, "date": local_day.isoformat(), "hydrate": "team"},
                operation="mlb_play_by_play_fallback_schedule",
                league="mlb",
            )
            candidates = _mlb_schedule_candidates(root, reference)
            if len(candidates) != 1:
                raise SportsStateError(
                    "mlb_fallback_ambiguous",
                    "MLB fallback could not uniquely match both teams and start time",
                    fields={"candidate_count": len(candidates)},
                )
            game_pk = _integer(candidates[0].get("gamePk"), "MLB gamePk", minimum=1)
            feed_url = f"{MLB_STATS_BASE}/v1.1/game/{game_pk}/feed/live"
            raw_feed, headers = await self._request_json(
                feed_url,
                params=None,
                operation="mlb_play_by_play_fallback_detail",
                league="mlb",
            )
            feed = _mlb_play_feed(raw_feed, reference, self._now(), feed_url)
            return self._cache_play_feed(game_ref, feed, headers)
        except SportsStateError as fallback_error:
            raise SportsStateError(
                "play_by_play_unavailable",
                "ESPN play-by-play failed and the MLB fallback could not verify an exact game",
                fields={
                    "primary_error": primary_error.code,
                    "fallback_error": fallback_error.code,
                },
            ) from fallback_error

    async def _mlb_box_score_fallback(
        self,
        game_ref: str,
        reference: _GameRefPayload,
        primary_error: SportsStateError,
    ) -> BoxScore:
        local_day = reference.scheduled_start.astimezone(ZoneInfo(reference.timezone)).date()
        schedule_url = f"{MLB_STATS_BASE}/v1/schedule"
        try:
            root, _ = await self._request_json(
                schedule_url,
                params={
                    "sportId": 1,
                    "date": local_day.isoformat(),
                    "hydrate": "team",
                },
                operation="mlb_box_score_fallback_schedule",
                league="mlb",
            )
            candidates = _mlb_schedule_candidates(root, reference)
            if len(candidates) != 1:
                raise SportsStateError(
                    "mlb_fallback_ambiguous",
                    "MLB fallback could not uniquely match both teams and start time",
                    fields={"candidate_count": len(candidates)},
                )
            game_pk = _integer(candidates[0].get("gamePk"), "MLB gamePk", minimum=1)
            feed_url = f"{MLB_STATS_BASE}/v1.1/game/{game_pk}/feed/live"
            feed, headers = await self._request_json(
                feed_url,
                params=None,
                operation="mlb_box_score_fallback_detail",
                league="mlb",
            )
            box_score = _mlb_box_score(feed, reference, self._now(), feed_url)
            return self._cache_box_score(game_ref, box_score, headers)
        except SportsStateError as fallback_error:
            raise SportsStateError(
                "box_score_unavailable",
                "ESPN box score failed and the MLB fallback could not verify an exact game",
                fields={
                    "primary_error": primary_error.code,
                    "fallback_error": fallback_error.code,
                },
            ) from fallback_error

    async def _mlb_fallback(
        self,
        game_ref: str,
        reference: _GameRefPayload,
        primary_error: SportsStateError,
    ) -> GameState:
        local_day = reference.scheduled_start.astimezone(ZoneInfo(reference.timezone)).date()
        schedule_url = f"{MLB_STATS_BASE}/v1/schedule"
        try:
            root, _ = await self._request_json(
                schedule_url,
                params={
                    "sportId": 1,
                    "date": local_day.isoformat(),
                    "hydrate": "team",
                },
                operation="mlb_fallback_schedule",
                league="mlb",
            )
            candidates = _mlb_schedule_candidates(root, reference)
            if len(candidates) != 1:
                raise SportsStateError(
                    "mlb_fallback_ambiguous",
                    "MLB fallback could not uniquely match both teams and start time",
                    fields={"candidate_count": len(candidates)},
                )
            game_pk = _integer(candidates[0].get("gamePk"), "MLB gamePk", minimum=1)
            feed_url = f"{MLB_STATS_BASE}/v1.1/game/{game_pk}/feed/live"
            feed, headers = await self._request_json(
                feed_url,
                params=None,
                operation="mlb_fallback_detail",
                league="mlb",
            )
            state = _mlb_detail(feed, reference, self._now(), feed_url)
            discovery = self._discoveries.get(game_ref)
            if discovery is not None:
                primary = GameState(**discovery.model_dump(), situation=BaseballSituation())
                conflict = observations_conflict(primary, state)
                if conflict is not None:
                    primary = primary.model_copy(update={"warnings": [*primary.warnings, conflict]})
                    return self._cache_state(game_ref, primary, headers)
            return self._cache_state(game_ref, state, headers)
        except SportsStateError as fallback_error:
            raise SportsStateError(
                "game_state_unavailable",
                "ESPN detail failed and the MLB fallback could not verify an exact game",
                fields={
                    "primary_error": primary_error.code,
                    "fallback_error": fallback_error.code,
                },
            ) from fallback_error


def observations_conflict(primary: GameState, fallback: GameState) -> StateWarning | None:
    """Describe a disagreement without averaging or selecting the favorable state."""

    comparable = (
        primary.home_team == fallback.home_team
        and primary.away_team == fallback.away_team
        and primary.local_date == fallback.local_date
    )
    if not comparable:
        raise IdentityMismatchError(
            "response_identity_mismatch", "provider observations describe different games"
        )
    values = (
        primary.home_score,
        primary.away_score,
        primary.lifecycle,
        primary.period,
    )
    other = (
        fallback.home_score,
        fallback.away_score,
        fallback.lifecycle,
        fallback.period,
    )
    if values == other:
        return None
    return StateWarning(
        code="provider_state_conflict",
        message="ESPN and MLB StatsAPI report conflicting current state; ESPN remains primary.",
    )
