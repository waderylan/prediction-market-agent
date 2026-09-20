"""Server-side sports identity and bounded evidence; no cross-provider equivalence claims."""

import json
import re
from datetime import UTC, date, datetime, time
from functools import lru_cache
from importlib.resources import files
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from market_agent.providers.exceptions import MarketRequestError

League = Literal["mlb", "nfl", "ncaa_football"]
Division = Literal["FBS", "FCS"]
LiveStatus = Literal["pregame", "live", "awaiting_resolution", "settled"]


def words(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


class Team(BaseModel):
    league: League
    name: str
    kalshi_name: str
    kalshi_id: str
    aliases: list[str]
    division: Division | None


@lru_cache(maxsize=1)
def teams() -> tuple[Team, ...]:
    data = json.loads(files("market_agent.providers").joinpath("sports_teams.json").read_text())
    return tuple(Team.model_validate(item) for item in data["teams"])


@lru_cache(maxsize=1)
def team_index() -> tuple[dict[str, Team], dict[tuple[str, str], list[Team]]]:
    by_id = {t.kalshi_id: t for t in teams()}
    aliases: dict[tuple[str, str], list[Team]] = {}
    for team in teams():
        for alias in {words(a) for a in [team.name, *team.aliases]}:
            aliases.setdefault((team.league, alias), []).append(team)
    return by_id, aliases


@lru_cache(maxsize=1)
def alias_patterns() -> tuple[tuple[Team, str, re.Pattern[str]], ...]:
    return tuple(
        (team, alias, re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)"))
        for team in teams()
        for alias in sorted({words(a) for a in team.aliases})
    )


class SportsQuery(BaseModel):
    league: League | None = None
    teams: list[Team] = Field(default_factory=list)
    clarification: str | None = None
    choices: list[str] = Field(default_factory=list)


def resolve_query(query: str, league: League | None = None) -> SportsQuery:
    """Longest exact alias wins; shared aliases never silently choose a team."""
    text = words(query)
    hints: dict[str, League] = {
        "mlb": "mlb",
        "nfl": "nfl",
        "ncaa football": "ncaa_football",
        "college football": "ncaa_football",
        "ncaaf": "ncaa_football",
        "cfb": "ncaa_football",
    }
    hinted = {v for k, v in hints.items() if f" {k} " in f" {text} "}
    if len(hinted) > 1 or (league and hinted and league not in hinted):
        return SportsQuery(clarification="Choose one league: MLB, NFL, or NCAA football.")
    if hinted and not league:
        league = next(iter(hinted))
    # Popular college abbreviations have multiple real-world interpretations even
    # when one provider assigns the abbreviation to only one structured target.
    ambiguous = {
        "osu": ["Ohio State Buckeyes", "Oregon State Beavers", "Oklahoma State Cowboys"],
        "usc": ["USC Trojans", "South Carolina Gamecocks"],
        "miami": ["Miami (FL) Hurricanes", "Miami (OH) RedHawks"],
    }
    if league == "ncaa_football" or text in {"osu", "usc"}:
        for alias, choices in ambiguous.items():
            if (
                re.search(rf"\b{alias}\b", text)
                and not any(words(name) in text for name in choices)
                and not (alias == "miami" and re.search(r"miami (fl|oh)", text))
            ):
                return SportsQuery(
                    league=league, clarification=f"Clarify '{alias}'.", choices=choices
                )
    matches: list[tuple[int, int, Team]] = []
    for team, normalized, pattern in alias_patterns():
        if league and team.league != league:
            continue
        # Short codes need explicit context to avoid incidental words in generic queries.
        if len(normalized) <= 3 and not league and text != normalized:
            continue
        for match in pattern.finditer(text):
            matches.append((match.start(), match.end(), team))
    matches = [
        m
        for m in matches
        if not any(a <= m[0] and b >= m[1] and (a < m[0] or b > m[1]) for a, b, _ in matches)
    ]
    if matches and league is None:
        spans = {(a, b) for a, b, _ in matches}
        possible = set.intersection(
            *[{t.league for a, b, t in matches if (a, b) == span} for span in spans]
        )
        if len(possible) == 1:
            return resolve_query(query, next(iter(possible)))
    inferred = {t.league for _, _, t in matches}
    if not league and len(inferred) == 1:
        league = next(iter(inferred))
    unique = {t.kalshi_id: t for _, _, t in sorted(matches, key=lambda match: match[0])}
    overlaps = any(
        a < d and c < b and t.kalshi_id != u.kalshi_id for a, b, t in matches for c, d, u in matches
    )
    if overlaps or len(inferred) > 1:
        return SportsQuery(
            league=league,
            clarification="Specify the league and full team name.",
            choices=sorted({f"{t.name} ({t.league})" for t in unique.values()})[:20],
        )
    selected = list(unique.values())
    if len(selected) > 2:
        return SportsQuery(league=league, clarification="Request one team or one matchup.")
    if league and not selected:
        return SportsQuery(league=league, clarification="Specify a full team name or matchup.")
    if selected:
        # Do not discard unknown opponent/date/type tokens and return the known team's games.
        remaining = list(text)
        for a, b, _ in matches:
            remaining[a:b] = " " * (b - a)
        residue = words("".join(remaining))
        for phrase in sorted(hints, key=len, reverse=True):
            residue = re.sub(rf"\b{phrase}\b", " ", residue)
        allowed = {
            "vs",
            "v",
            "versus",
            "at",
            "and",
            "game",
            "games",
            "winner",
            "moneyline",
            "find",
            "show",
            "me",
            "markets",
            "market",
            "for",
            "the",
            "win",
            "will",
            "who",
            "wins",
            "mlb",
            "nfl",
        }
        unknown = set(residue.split()) - allowed
        if unknown:
            return SportsQuery(
                league=league,
                clarification=(
                    "Use team names only; set event_date (UTC) for dates. "
                    "Only full-game winner contracts are supported. Unrecognized terms: "
                    + ", ".join(sorted(unknown))
                ),
            )
    return SportsQuery(league=league, teams=selected)


def participant(label: str, league: League, *, kalshi_id: str | None = None) -> Team | None:
    by_id, aliases = team_index()
    if kalshi_id:
        team = by_id.get(kalshi_id)
        return team if team and team.league == league else None
    matches = aliases.get((league, words(label)), [])
    return matches[0] if len(matches) == 1 else None


class SportsEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    league: League
    provider_event_id: str
    raw_title: str
    participants: list[str] = Field(min_length=2, max_length=2)
    raw_participants: list[str] = Field(min_length=2, max_length=2)
    divisions: list[Division] = Field(default_factory=list)
    market_type: Literal["game_winner"] = "game_winner"
    line: None = None
    scheduled_start: datetime | None = None
    schedule_source: str | None = None
    timezone: str | None = None
    local_date: str | None = None
    scheduled_start_local: datetime | None = None
    comparison_eligibility: Literal["insufficient_evidence"] = "insufficient_evidence"
    comparison_eligibility_reason: str = (
        "Discovery verifies event identity and contract type, but not equivalent settlement rules."
    )

    @field_validator("scheduled_start")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("scheduled_start must have a timezone")
        return value.astimezone(UTC) if value else None

    @field_validator("scheduled_start_local")
    @classmethod
    def aware_local(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("scheduled_start_local must have a timezone")
        return value

    def localized(self, timezone: str) -> "SportsEvent":
        """Attach the caller's display context without changing provider event identity."""
        zone = ZoneInfo(timezone)
        local = self.scheduled_start.astimezone(zone) if self.scheduled_start else None
        return self.model_copy(
            update={
                "timezone": zone.key,
                "local_date": local.date().isoformat() if local else None,
                "scheduled_start_local": local,
            }
        )


class DiscoveryWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal["discarded_provider_record"] = "discarded_provider_record"
    record_type: Literal["event", "market"]
    record_id: str | None = None
    message: str


class DiscoveryGameChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["kalshi", "polymarket"]
    event_id: str
    participants: list[str] = Field(min_length=2, max_length=2)
    scheduled_start: datetime | None
    timezone: str
    local_date: str | None
    scheduled_start_local: datetime | None
    label: str


class DiscoveryCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pages_scanned: int = Field(default=0, ge=0)
    events_scanned: int = Field(default=0, ge=0)
    markets_scanned: int = Field(default=0, ge=0)
    candidates_matched: int = Field(default=0, ge=0)
    candidate_event_count: int = Field(default=0, ge=0)
    matching_events: list[DiscoveryGameChoice] = Field(default_factory=list, max_length=10)
    selection_required: bool = False
    truncated: bool = False
    has_more: bool | None = None
    continuation: list[dict[str, str | int]] = Field(default_factory=list)
    next_cursor: str | None = None
    provider_total: int | None = Field(default=None, ge=0)
    total_meaning: str | None = None
    stop_reason: str = "not_started"
    scope: str = "bounded discovery; not proof of market absence"
    discarded_record_count: int = Field(default=0, ge=0)
    warnings: list[DiscoveryWarning] = Field(default_factory=list, max_length=20)


def date_bounds(
    *,
    event_date: date | None = None,
    local_date: date | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone: str = "UTC",
) -> tuple[datetime | None, datetime | None, ZoneInfo]:
    """Build an inclusive local-calendar filter as a half-open UTC interval."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise MarketRequestError(
            "invalid_timezone",
            "timezone must be a valid IANA timezone",
            fields={"timezone": timezone},
        ) from error
    exact = local_date or event_date
    if event_date and local_date and event_date != local_date:
        raise MarketRequestError(
            "conflicting_date_filters",
            "event_date and local_date cannot specify different dates",
            fields={"event_date": event_date.isoformat(), "local_date": local_date.isoformat()},
        )
    if exact and (date_from or date_to):
        supplied = {
            key: value.isoformat()
            for key, value in {
                "event_date": event_date,
                "local_date": local_date,
                "date_from": date_from,
                "date_to": date_to,
            }.items()
            if value is not None
        }
        raise MarketRequestError(
            "conflicting_date_filters",
            "exact date filters and date range filters are mutually exclusive",
            fields=supplied,
        )
    start_date = exact or date_from
    end_date = exact or date_to
    if start_date and end_date and start_date > end_date:
        raise MarketRequestError(
            "reversed_date_range",
            "date_from must be on or before date_to",
            fields={"date_from": start_date.isoformat(), "date_to": end_date.isoformat()},
        )
    start = datetime.combine(start_date, time.min, zone).astimezone(UTC) if start_date else None
    end = (
        datetime.combine(date.fromordinal(end_date.toordinal() + 1), time.min, zone).astimezone(UTC)
        if end_date
        else None
    )
    return start, end, zone


def event_matches(
    event: SportsEvent,
    query: SportsQuery,
    date_range: tuple[datetime | None, datetime | None],
) -> bool:
    if not all(t.name in event.participants for t in query.teams):
        return False
    start, end = date_range
    if start is None and end is None:
        return True
    if event.scheduled_start is None:
        return False
    return (start is None or event.scheduled_start >= start) and (
        end is None or event.scheduled_start < end
    )


def sports_payload(event: SportsEvent, raw_market: dict[str, Any]) -> dict[str, Any]:
    return {
        "sports": event.model_dump(mode="json"),
        "raw_title": raw_market.get("title") or raw_market.get("question"),
    }
