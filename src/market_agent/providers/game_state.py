"""Bounded, read-only current game state from ESPN with an MLB-only fallback."""

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
from collections.abc import Mapping
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
NOT_STARTED_LIFECYCLES = {"scheduled", "pregame"}

DISCOVERY_USAGE = (
    "Discovery is a lightweight scoreboard snapshot for choosing a game. Copy game_ref unchanged "
    "into sports_state_get_game_state for authoritative normalized state fields. Scheduled and "
    "pregame state placeholders are returned as null. game_ref is scoped to the requested timezone."
)
DETAIL_USAGE = (
    "Detail is the authoritative normalized sporting-state snapshot for this game reference. "
    "Null fields are unavailable or not meaningful for the lifecycle; sporting state does not "
    "establish prediction-market settlement."
)

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_SCOREBOARD_EVENTS = 200
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
    scope: str = "Requested local day only; bounded UTC-boundary checks are not a season scan."


class FindGamesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    league: League
    timezone: str
    local_date: date
    discovery_mode: Literal["team", "schedule"] = "team"
    games: list[GameSummary] = Field(max_length=10)
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


def _athlete_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    athlete = value.get("athlete")
    if isinstance(athlete, dict):
        return _text(athlete.get("displayName") or athlete.get("fullName"), limit=200)
    return _text(value.get("fullName") or value.get("displayName"), limit=200)


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
            on_first=_boolean(state.get("onFirst"), "first-base occupancy"),
            on_second=_boolean(state.get("onSecond"), "second-base occupancy"),
            on_third=_boolean(state.get("onThird"), "third-base occupancy"),
            batter=_athlete_name(state.get("batter")),
            pitcher=_athlete_name(state.get("pitcher")),
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
                discovery_mode="team",
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
            if games:
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
        )
        return FindGamesResult(
            query=query,
            league=league,
            timezone=zone.key,
            local_date=selected_day,
            discovery_mode="schedule" if schedule_query else "team",
            games=ranked,
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
