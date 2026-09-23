"""Bounded sports discovery inside the existing read-only provider servers."""

import json
from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from urllib.parse import quote

from market_agent.domain import CanonicalMarket, MarketStatus
from market_agent.providers.exceptions import (
    MarketDataError,
    MarketRequestError,
    MarketValidationError,
)
from market_agent.providers.kalshi import KalshiClient, _resolution_source, _status_filter
from market_agent.providers.kalshi import _parse_market as parse_kalshi
from market_agent.providers.polymarket import PolymarketClient
from market_agent.providers.polymarket import _parse_market as parse_poly
from market_agent.providers.polymarket import _status as poly_status
from market_agent.providers.sports import (
    DiscoveryCoverage,
    DiscoveryGameChoice,
    DiscoveryWarning,
    League,
    SportsEvent,
    SportsQuery,
    Team,
    event_matches,
    participant,
    resolve_query,
    sports_payload,
    words,
)

MAX_EVENT_RECORD_BYTES = 5_000_000
MAX_MARKET_RECORD_BYTES = 250_000

# Observed provider series, not identifier templates. Revalidate titles before use.
KALSHI_SERIES: dict[str, tuple[League, str]] = {
    "KXMLBGAME": ("mlb", "Professional Baseball Game"),
    "KXNFLGAME": ("nfl", "Professional Football Game"),
    "KXNCAAFGAME": ("ncaa_football", "College Football Game"),
    "KXNCAAFCSGAME": ("ncaa_football", "College Football FCS Game"),
}
POLY_TAGS: dict[str, League] = {"mlb": "mlb", "nfl": "nfl", "cfb": "ncaa_football"}
POLY_CATALOG_PAGE_SIZE = 10


def _validate_game_selectors(next_game_only: bool, most_recent_game_only: bool) -> None:
    if next_game_only and most_recent_game_only:
        raise MarketRequestError(
            "conflicting_selectors",
            "next_game_only and most_recent_game_only are mutually exclusive",
            fields={"next_game_only": True, "most_recent_game_only": True},
        )


def _resolve_date_range(
    date_range: tuple[datetime | None, datetime | None] | None,
    event_date: date | None,
) -> tuple[datetime | None, datetime | None]:
    if date_range is not None:
        return date_range
    if event_date is None:
        return None, None
    start = datetime.combine(event_date, time.min, UTC)
    return start, start + timedelta(days=1)


def _kalshi_series_scope(query: SportsQuery, series_ticker: str | None) -> list[str]:
    scope = [ticker for ticker, (league, _) in KALSHI_SERIES.items() if league == query.league]
    if series_ticker:
        if series_ticker not in scope:
            raise MarketRequestError(
                "conflicting_series_filter",
                "series_ticker conflicts with the requested league and game-winner scope",
                fields={"series_ticker": series_ticker, "league": query.league},
            )
        return [series_ticker]
    if query.league == "ncaa_football" and all(team.division == "FBS" for team in query.teams):
        return ["KXNCAAFGAME"]
    return scope


def _validate_kalshi_cursor(
    state: dict[str, Any],
    *,
    normalized_query: str,
    expected_scope: dict[str, Any],
    series_scope: list[str],
) -> dict[str, str]:
    if state and state.get("normalized_query") != normalized_query:
        raise MarketRequestError(
            "cursor_query_mismatch",
            "continuation does not match the requested sports query",
            fields={"query": normalized_query, "cursor_query": state.get("normalized_query")},
        )
    if state and any(state.get(key) != value for key, value in expected_scope.items()):
        raise MarketRequestError(
            "cursor_filter_mismatch",
            "continuation does not match the requested league, status, date, or selectors",
            fields={"query": normalized_query, "league": expected_scope["league"]},
        )
    saved_cursors = state.get("cursors", {})
    if not isinstance(saved_cursors, dict) or any(
        key not in series_scope or not isinstance(value, str)
        for key, value in saved_cursors.items()
    ):
        raise MarketRequestError(
            "cursor_filter_mismatch",
            "continuation does not match the requested sports series scope",
            fields={"series_scope": series_scope},
        )
    return saved_cursors


def _professional_nickname(team: Team) -> str:
    """Return a reviewed unique nickname, never a guessed abbreviation."""
    nicknames = [
        alias
        for alias in team.aliases
        if len(words(alias)) > 3
        and alias not in {team.name, team.kalshi_name}
        and words(team.name).endswith(" " + words(alias))
        and participant(alias, team.league) == team
    ]
    return min(nicknames, key=len) if nicknames else team.name


def _polymarket_search_terms(query: SportsQuery) -> tuple[str, str]:
    if query.league == "ncaa_football":
        search_terms = [team.kalshi_name.replace(" St.", " State") for team in query.teams]
    elif query.league == "mlb" and len(query.teams) == 2:
        # Gamma MLB event titles use full club names. Both participants sharply narrow
        # historical searches that otherwise bury a game behind hundreds of team results.
        search_terms = [team.name for team in query.teams]
    else:
        # Gamma NFL event titles use nicknames. A one-team search keeps the prior
        # behavior, while a matchup search includes both resolved participants.
        search_terms = [_professional_nickname(team) for team in query.teams]
    search_query = " vs ".join(search_terms)
    tag = next(tag for tag, league in POLY_TAGS.items() if league == query.league)
    return search_query, tag


def _validate_polymarket_cursor(
    state: dict[str, Any],
    *,
    search_query: str,
    expected_filters: dict[str, Any],
) -> tuple[int, str | None, int]:
    if state and state.get("query") != search_query:
        raise MarketRequestError(
            "cursor_query_mismatch",
            "continuation does not match the requested sports query",
            fields={"query": search_query, "cursor_query": state.get("query")},
        )
    if state and any(state.get(key) != value for key, value in expected_filters.items()):
        raise MarketRequestError(
            "cursor_filter_mismatch",
            "continuation does not match the requested league, status, date, or selectors",
            fields={"query": search_query, "league": expected_filters["league"]},
        )

    mode = state.get("mode", "search")
    if mode not in {"search", "catalog"}:
        raise MarketRequestError("invalid_cursor", "continuation contains an invalid mode")
    page = state.get("page", 1)
    catalog_series = state.get("series") if mode == "catalog" else None
    catalog_offset = state.get("offset", 0)
    if (
        type(page) is not int
        or page < 1
        or (
            catalog_series is not None
            and (not isinstance(catalog_series, str) or not catalog_series.isdigit())
        )
        or type(catalog_offset) is not int
        or catalog_offset < 0
    ):
        raise MarketRequestError("invalid_cursor", "continuation contains invalid page state")
    return page, catalog_series, catalog_offset


def continuation_token(provider: str, state: dict[str, Any]) -> str:
    payload = json.dumps({"provider": provider, **state}, separators=(",", ":"), sort_keys=True)
    return urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def continuation_state(token: str | None, provider: str) -> dict[str, Any]:
    if token is None:
        return {}
    try:
        padding = "=" * (-len(token) % 4)
        decoded = b64decode(token + padding, altchars=b"-_", validate=True)
        value = json.loads(decoded.decode("utf-8"))
    except (Base64Error, UnicodeDecodeError, ValueError, TypeError) as error:
        raise MarketRequestError(
            "invalid_cursor", "continuation is not a valid server-issued cursor"
        ) from error
    if not isinstance(value, dict):
        raise MarketRequestError(
            "invalid_cursor", "continuation is not a valid server-issued cursor"
        )
    cursor_provider = value.pop("provider", None)
    if cursor_provider != provider:
        raise MarketRequestError(
            "cursor_provider_mismatch",
            "continuation belongs to a different provider",
            fields={"expected_provider": provider, "cursor_provider": cursor_provider},
        )
    return value


def winner_market(market: dict[str, Any], event_title: str) -> bool:
    """Reject a sibling with different contract semantics even inside a winner series."""
    label = market.get("yes_sub_title")
    return (
        isinstance(label, str)
        and market.get("market_type", "binary") == "binary"
        and market.get("floor_strike") is None
        and market.get("cap_strike") is None
        and words(str(market.get("title", ""))) in {words(label + " wins"), words(event_title)}
    )


def objects(value: Any, provider: str, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise MarketValidationError(provider, f"{field} must be an array of objects")
    return value


def root_object(value: Any, provider: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MarketValidationError(provider, "response must be an object")
    return value


def record_size(
    value: dict[str, Any], provider: str, record_type: Literal["event", "market"]
) -> None:
    """Reject one pathological record without rejecting an otherwise usable page."""
    try:
        size = len(json.dumps(value, separators=(",", ":")).encode())
    except (TypeError, ValueError) as error:
        raise MarketValidationError(provider, f"{record_type} is not JSON serializable") from error
    limit = MAX_EVENT_RECORD_BYTES if record_type == "event" else MAX_MARKET_RECORD_BYTES
    if size > limit:
        raise MarketValidationError(provider, f"{record_type} exceeds {limit} byte safety limit")


def discard_record(
    coverage: DiscoveryCoverage,
    record_type: Literal["event", "market"],
    record: dict[str, Any],
    error: MarketDataError,
) -> None:
    coverage.discarded_record_count += 1
    if len(coverage.warnings) >= 20:
        return
    identifier = (
        record.get("ticker") or record.get("id")
        if record_type == "market"
        else record.get("event_ticker") or record.get("id")
    )
    coverage.warnings.append(
        DiscoveryWarning(
            record_type=record_type,
            record_id=str(identifier)[:100] if identifier is not None else None,
            message=error.message[:300],
        )
    )


def retain(results: dict[str, CanonicalMarket], market: CanonicalMarket) -> None:
    existing = results.get(market.market_id)
    if existing and existing.event_id != market.event_id:
        raise MarketValidationError(market.platform.value, "market belongs to conflicting events")
    results.setdefault(market.market_id, market)


def timestamp(value: Any, provider: str) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone missing")
        return result.astimezone(UTC)
    except (ValueError, TypeError, AttributeError) as error:
        raise MarketValidationError(provider, "invalid scheduled timestamp") from error


def check_event_title(title: Any, league: League, names: set[str], provider: str) -> None:
    if not isinstance(title, str):
        raise MarketValidationError(provider, "event title must be a string")
    parsed = resolve_query(title, league)
    if (
        not parsed.clarification
        and len(parsed.teams) == 2
        and {team.name for team in parsed.teams} != names
    ):
        raise MarketValidationError(provider, "event title and participants conflict")


def kalshi_event(event: dict[str, Any], milestones: list[dict[str, Any]]) -> SportsEvent | None:
    series = event.get("series_ticker")
    if not isinstance(series, str):
        raise MarketValidationError("kalshi", "event series must be a string")
    if series not in KALSHI_SERIES:
        return None
    league = KALSHI_SERIES[series][0]
    event_id = event.get("event_ticker")
    if not isinstance(event_id, str) or not event_id:
        raise MarketValidationError("kalshi", "event identity missing")
    participants: dict[str, str] = {}
    divisions = set()
    for market in objects(event.get("markets", []), "kalshi", "markets"):
        if market.get("event_ticker") != event_id:
            raise MarketValidationError("kalshi", "nested market event identity mismatch")
        if not winner_market(market, str(event.get("title", ""))):
            continue
        label = market.get("yes_sub_title")
        if not isinstance(label, str):
            continue
        strike = market.get("custom_strike") or {}
        if not isinstance(strike, dict):
            raise MarketValidationError("kalshi", "custom_strike must be an object")
        team_id = strike.get("baseball_team") or strike.get("football_team")
        team = participant(label, league, kalshi_id=team_id)
        if team:
            # An ID must not quietly override a contradictory provider label.
            label_team = participant(label, league)
            if label_team and label_team.kalshi_id != team.kalshi_id:
                raise MarketValidationError("kalshi", "participant ID and label conflict")
            participants[team.name] = label
            if team.division:
                divisions.add(team.division)
    if len(participants) != 2:
        return None
    check_event_title(event.get("title"), league, set(participants), "kalshi")
    starts = set()
    for milestone in milestones:
        related = []
        for field in ("related_event_tickers", "primary_event_tickers"):
            values = milestone.get(field, [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise MarketValidationError("kalshi", "invalid milestone event references")
            related.extend(values)
        if event_id in related:
            start = timestamp(milestone.get("start_date"), "kalshi")
            if start is not None:
                starts.add(start)
    if len(starts) > 1:
        raise MarketValidationError("kalshi", "conflicting milestone start times")
    return SportsEvent(
        league=league,
        provider_event_id=event_id,
        raw_title=event.get("title", ""),
        participants=list(participants),
        raw_participants=list(participants.values()),
        divisions=sorted(divisions),
        scheduled_start=next(iter(starts), None),
        schedule_source="milestone.start_date" if starts else None,
    )


async def search_kalshi(
    client: KalshiClient,
    query: SportsQuery,
    *,
    status: MarketStatus | None,
    limit: int,
    series_ticker: str | None,
    date_range: tuple[datetime | None, datetime | None] | None = None,
    event_date: date | None = None,
    continuation: str | None = None,
    next_game_only: bool = False,
    most_recent_game_only: bool = False,
    timezone: str = "UTC",
) -> tuple[list[CanonicalMarket], DiscoveryCoverage]:
    _validate_game_selectors(next_game_only, most_recent_game_only)
    date_range = _resolve_date_range(date_range, event_date)
    scope = _kalshi_series_scope(query, series_ticker)
    state = continuation_state(continuation, "kalshi")
    normalized_query = " vs ".join(team.name for team in query.teams)
    expected_scope = {
        "version": 2,
        "normalized_query": normalized_query,
        "league": query.league,
        "teams": sorted(team.kalshi_id for team in query.teams),
        "status": status.value if status else None,
        "date_range": [value.isoformat() if value else None for value in date_range],
        "next_game_only": next_game_only,
        "most_recent_game_only": most_recent_game_only,
    }
    saved_cursors = _validate_kalshi_cursor(
        state,
        normalized_query=normalized_query,
        expected_scope=expected_scope,
        series_scope=scope,
    )
    # All request-owned cursor state is validated before contacting Kalshi.
    for ticker in scope:
        payload = root_object(
            await client._request_json(f"/series/{quote(ticker, safe='')}"), "kalshi"
        )
        series = root_object(payload.get("series"), "kalshi")
        if series.get("ticker") != ticker or series.get("title") != KALSHI_SERIES[ticker][1]:
            raise MarketValidationError("kalshi", "sports series metadata changed")
    coverage = DiscoveryCoverage(
        scope="Full-game winners; historical nested markets may be omitted."
    )
    cursors: dict[str, str | None] = {key: saved_cursors.get(key) for key in scope}
    exhausted: set[str] = set()
    seen_cursors: set[tuple[str, str]] = set()
    seen_events: set[str] = set()
    results: dict[str, CanonicalMarket] = {}
    retrieved_at = datetime.now(UTC)
    # One shared page budget across NCAA scopes; round-robin avoids starving FCS.
    for page in range(client.max_search_pages):
        available = [s for s in scope if s not in exhausted]
        if not available:
            break
        ticker = available[page % len(available)]
        params: dict[str, str | int] = {
            "series_ticker": ticker,
            "limit": 200,
            "with_nested_markets": "true",
            "with_milestones": "true",
        }
        # Closed/settled event status can exclude a settled sibling of a mixed event.
        # Only the open scope is safe to narrow here; filter each contract below.
        if status == MarketStatus.OPEN:
            params["status"] = _status_filter(status) or "open"
        if cursors[ticker]:
            params["cursor"] = cursors[ticker] or ""
        root = root_object(await client._request_json("/events", params=params), "kalshi")
        events = objects(root.get("events"), "kalshi", "events")
        milestones = objects(root.get("milestones", []), "kalshi", "milestones")
        coverage.pages_scanned += 1
        coverage.events_scanned += len(events)
        for event in events:
            try:
                record_size(event, "kalshi", "event")
                event_id = event.get("event_ticker")
                if not isinstance(event_id, str) or event.get("series_ticker") != ticker:
                    raise MarketValidationError("kalshi", "event scope mismatch")
                raw_markets = objects(event.get("markets"), "kalshi", "markets")
                sports = kalshi_event(event, milestones)
            except MarketDataError as error:
                discard_record(coverage, "event", event, error)
                continue
            if event_id in seen_events:
                continue
            seen_events.add(event_id)
            coverage.markets_scanned += len(raw_markets)
            if sports is None or not event_matches(sports, query, date_range):
                continue
            sports = sports.localized(timezone)
            for market in raw_markets:
                try:
                    record_size(market, "kalshi", "market")
                    if (
                        not winner_market(market, sports.raw_title)
                        or market.get("yes_sub_title") not in sports.raw_participants
                    ):
                        continue
                    parsed = parse_kalshi(
                        market,
                        retrieved_at=retrieved_at,
                        resolution_source=_resolution_source(event),
                    )
                except MarketDataError as error:
                    discard_record(coverage, "market", market, error)
                    continue
                if status is not None and parsed.status != status:
                    continue
                parsed = parsed.model_copy(
                    update={
                        "provider_data": {
                            **parsed.provider_data,
                            **sports_payload(sports, market),
                            "series_ticker": ticker,
                            "requested_outcome": any(
                                t.kalshi_name == market.get("yes_sub_title") for t in query.teams
                            ),
                        }
                    }
                )
                parsed = client.record_observation(parsed)
                retain(results, parsed)
        cursor = root.get("cursor")
        if cursor is not None and not isinstance(cursor, str):
            raise MarketValidationError("kalshi", "cursor must be a string")
        cursors[ticker] = cursor or None
        if not cursor:
            exhausted.add(ticker)
        elif (ticker, cursor) in seen_cursors:
            coverage.stop_reason = "repeated_cursor"
            break
        else:
            seen_cursors.add((ticker, cursor))
    coverage.has_more = len(exhausted) < len(scope)
    coverage.continuation = [
        {"series_ticker": s, **({"cursor": c} if c else {})}
        for s, c in cursors.items()
        if s not in exhausted
    ]
    if coverage.continuation:
        coverage.next_cursor = continuation_token(
            "kalshi",
            {
                "cursors": {key: value for key, value in cursors.items() if value},
                **expected_scope,
            },
        )
    if coverage.stop_reason == "not_started":
        coverage.stop_reason = "page_budget" if coverage.has_more else "provider_exhausted"
    return finalize_discovery_results(
        results,
        coverage,
        limit,
        next_game_only=next_game_only,
        most_recent_game_only=most_recent_game_only,
    )


def poly_event(
    event: dict[str, Any], market: dict[str, Any], league: League | None = None
) -> SportsEvent | None:
    if market.get("sportsMarketType") != "moneyline":
        return None
    tag_values = objects(event.get("tags", []), "polymarket", "tags")
    if any(not isinstance(tag.get("slug"), str) for tag in tag_values):
        raise MarketValidationError("polymarket", "tag slug must be a string")
    tags = {tag["slug"] for tag in tag_values}
    leagues = {v for k, v in POLY_TAGS.items() if k in tags}
    if len(leagues) != 1 or (league and league not in leagues):
        return None
    resolved_league = next(iter(leagues))
    try:
        labels = json.loads(market.get("outcomes", "null"))
    except (ValueError, TypeError) as error:
        raise MarketValidationError("polymarket", "invalid outcomes") from error
    if (
        not isinstance(labels, list)
        or len(labels) != 2
        or not all(isinstance(label, str) for label in labels)
    ):
        raise MarketValidationError("polymarket", "moneyline requires two labeled outcomes")
    found = [participant(label, resolved_league) for label in labels]
    if any(t is None for t in found):
        return None
    resolved = [t for t in found if t is not None]
    if resolved[0].kalshi_id == resolved[1].kalshi_id:
        raise MarketValidationError("polymarket", "duplicate participant")
    check_event_title(event.get("title"), resolved_league, {t.name for t in resolved}, "polymarket")
    start = timestamp(market.get("gameStartTime"), "polymarket")
    event_start = timestamp(event.get("startTime"), "polymarket")
    if start and event_start and start != event_start:
        raise MarketValidationError("polymarket", "game and event start times conflict")
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id:
        raise MarketValidationError("polymarket", "event identity missing")
    return SportsEvent(
        league=resolved_league,
        provider_event_id=event_id,
        raw_title=event.get("title", ""),
        participants=[t.name for t in resolved],
        raw_participants=labels,
        divisions=sorted({t.division for t in resolved if t.division}),
        scheduled_start=start or event_start,
        schedule_source="gameStartTime" if start else "event.startTime" if event_start else None,
    )


async def search_polymarket(
    client: PolymarketClient,
    query: SportsQuery,
    *,
    status: MarketStatus | None,
    limit: int,
    date_range: tuple[datetime | None, datetime | None] | None = None,
    event_date: date | None = None,
    continuation: str | None = None,
    next_game_only: bool = False,
    most_recent_game_only: bool = False,
    timezone: str = "UTC",
) -> tuple[list[CanonicalMarket], DiscoveryCoverage]:
    _validate_game_selectors(next_game_only, most_recent_game_only)
    date_range = _resolve_date_range(date_range, event_date)
    coverage = DiscoveryCoverage(
        scope="Full-game moneyline; exact participants within provider-ranked event search.",
        total_meaning="Provider public-search totalResults before local contract/team filters.",
    )
    results: dict[str, CanonicalMarket] = {}
    seen: set[str] = set()
    retrieved_at = datetime.now(UTC)
    # Scope by the verified league tag before requiring every participant locally.
    search_query, tag = _polymarket_search_terms(query)
    state = continuation_state(continuation, "polymarket")
    expected_filters = {
        "version": 3,
        "league": query.league,
        "tag": tag,
        "status": status.value if status else None,
        "date_range": [value.isoformat() if value else None for value in date_range],
        "next_game_only": next_game_only,
        "most_recent_game_only": most_recent_game_only,
    }
    page_start, catalog_series, catalog_offset = _validate_polymarket_cursor(
        state,
        search_query=search_query,
        expected_filters=expected_filters,
    )
    for request_index in range(client.max_search_pages):
        page = page_start + request_index
        if catalog_series is not None:
            params: dict[str, str | int] = {
                "series_id": catalog_series,
                "limit": POLY_CATALOG_PAGE_SIZE,
                "offset": catalog_offset,
                "order": "startTime",
                "ascending": "true",
            }
            if status == MarketStatus.OPEN:
                params["closed"] = "false"
            events = objects(
                await client._request_json("/events", params=params), "polymarket", "events"
            )
            catalog_offset += len(events)
            more = len(events) == POLY_CATALOG_PAGE_SIZE
            # Offset feeds supply no hasMore flag or total; fullness is only a hint.
            coverage.has_more = None if more else False
        else:
            root = root_object(
                await client._request_json(
                    "/public-search",
                    params={
                        "q": search_query,
                        "events_tag": tag,
                        "page": page,
                        "limit_per_type": 20,
                        "events_status": "active" if status == MarketStatus.OPEN else "all",
                        "search_tags": "false",
                        "search_profiles": "false",
                        "optimized": "false",
                    },
                ),
                "polymarket",
            )
            events = objects(
                root["events"] if root.get("events") is not None else [], "polymarket", "events"
            )
            pagination = root_object(root.get("pagination", {}), "polymarket")
            more = pagination.get("hasMore", False)
            total = pagination.get("totalResults")
            if not isinstance(more, bool) or (
                total is not None and (type(total) is not int or total < 0)
            ):
                raise MarketValidationError("polymarket", "invalid pagination")
            coverage.provider_total = total
            coverage.has_more = more
        coverage.pages_scanned += 1
        coverage.events_scanned += len(events)
        new_events = 0
        for event in events:
            try:
                record_size(event, "polymarket", "event")
                event_id = event.get("id")
                if not isinstance(event_id, str):
                    raise MarketValidationError("polymarket", "event identity missing")
                markets = objects(
                    event["markets"] if event.get("markets") is not None else [],
                    "polymarket",
                    "markets",
                )
            except MarketDataError as error:
                discard_record(coverage, "event", event, error)
                continue
            if event_id in seen:
                continue
            seen.add(event_id)
            new_events += 1
            coverage.markets_scanned += len(markets)
            for market in markets:
                try:
                    record_size(market, "polymarket", "market")
                    if status is not None and poly_status(market) != status:
                        continue
                    sports = poly_event(event, market, query.league)
                    if sports is None or not event_matches(sports, query, date_range):
                        continue
                    sports = sports.localized(timezone)
                    parsed = parse_poly(market, event_id=event_id, retrieved_at=retrieved_at)
                except MarketDataError as error:
                    discard_record(coverage, "market", market, error)
                    continue
                parsed = parsed.model_copy(
                    update={
                        "provider_data": {
                            **parsed.provider_data,
                            **sports_payload(sports, market),
                            "event_slug": event.get("slug"),
                        }
                    }
                )
                client.remember_sports_context(
                    parsed.market_id,
                    event_id=event_id,
                    sports=sports.model_dump(mode="json"),
                    event_slug=event.get("slug") if isinstance(event.get("slug"), str) else None,
                )
                parsed = client.record_observation(parsed)
                retain(results, parsed)
        coverage.continuation = (
            [{"series_id": catalog_series, "offset": catalog_offset}]
            if more and catalog_series
            else [{"query": search_query, "events_tag": tag, "page": page + 1}]
            if more
            else []
        )
        coverage.next_cursor = (
            continuation_token(
                "polymarket",
                {
                    "mode": "catalog",
                    "series": catalog_series,
                    "offset": catalog_offset,
                    "query": search_query,
                    **expected_filters,
                },
            )
            if more and catalog_series
            else continuation_token(
                "polymarket",
                {
                    "mode": "search",
                    "page": page + 1,
                    "query": search_query,
                    **expected_filters,
                },
            )
            if more
            else None
        )
        if not more:
            if (
                not results
                and catalog_series is None
                and request_index + 1 < client.max_search_pages
            ):
                metadata = objects(
                    await client._request_json("/sports"), "polymarket", "sports metadata"
                )
                matches = [s for s in metadata if s.get("sport") == tag]
                if len(matches) != 1 or not isinstance(matches[0].get("series"), str):
                    raise MarketValidationError("polymarket", "league series metadata missing")
                catalog_series = matches[0]["series"]
                if not catalog_series.isdigit():
                    raise MarketValidationError("polymarket", "invalid league series identity")
                coverage.scope += " League-series fallback shares the same page budget."
                continue
            coverage.stop_reason = "provider_exhausted"
            break
        if not new_events:
            coverage.stop_reason = "repeated_or_empty_page"
            break
        # Rank all qualifying contracts on this page before early completion.
        if len({market.event_id for market in results.values()}) >= limit:
            coverage.stop_reason = "result_limit"
            break
    if coverage.stop_reason == "not_started":
        coverage.stop_reason = "page_budget"
    return finalize_discovery_results(
        results,
        coverage,
        limit,
        next_game_only=next_game_only,
        most_recent_game_only=most_recent_game_only,
    )


def finalize_discovery_results(
    results: dict[str, CanonicalMarket],
    coverage: DiscoveryCoverage,
    limit: int,
    *,
    next_game_only: bool = False,
    most_recent_game_only: bool = False,
) -> tuple[list[CanonicalMarket], DiscoveryCoverage]:
    _validate_game_selectors(next_game_only, most_recent_game_only)
    coverage.candidates_matched = len(results)
    events = {m.event_id: m.provider_data["sports"] for m in results.values()}
    providers = {m.event_id: m.platform.value for m in results.values()}
    coverage.candidate_event_count = len(events)
    coverage.selection_required = len(events) > 1
    coverage.matching_events = []
    for key, value in sorted(
        events.items(), key=lambda item: (item[1]["scheduled_start"] or "9999", item[0] or "")
    )[:10]:
        local = value.get("scheduled_start_local")
        label = value["raw_title"]
        if local:
            local_at = datetime.fromisoformat(local.replace("Z", "+00:00"))
            label = f"{label} — {local_at.strftime('%Y-%m-%d %I:%M %p %Z')}"
        coverage.matching_events.append(
            DiscoveryGameChoice(
                provider="kalshi" if providers[key] == "kalshi" else "polymarket",
                event_id=key or "",
                participants=value["participants"],
                scheduled_start=value["scheduled_start"],
                timezone=value.get("timezone") or "UTC",
                local_date=value.get("local_date"),
                scheduled_start_local=value.get("scheduled_start_local"),
                label=label,
            )
        )
    coverage.truncated = (
        bool(coverage.has_more) or bool(coverage.continuation) or len(events) > limit
    )
    # All candidates already satisfy exact identity/type. Stable chronological
    # order retains doubleheaders as separate event IDs, never merges them.
    event_order = sorted(
        events,
        key=lambda event_id: (events[event_id]["scheduled_start"] or "9999", event_id or ""),
    )
    now = datetime.now(UTC)
    if next_game_only:
        event_order = [
            event_id
            for event_id in event_order
            if events[event_id]["scheduled_start"]
            and datetime.fromisoformat(events[event_id]["scheduled_start"].replace("Z", "+00:00"))
            >= now
        ][:1]
    elif most_recent_game_only:
        event_order = [
            event_id
            for event_id in reversed(event_order)
            if events[event_id]["scheduled_start"]
            and datetime.fromisoformat(events[event_id]["scheduled_start"].replace("Z", "+00:00"))
            <= now
        ][:1]
    else:
        event_order = event_order[:limit]
    selected = set(event_order)
    ranked = sorted(
        (market for market in results.values() if market.event_id in selected),
        key=lambda market: (
            event_order.index(market.event_id),
            not market.provider_data.get("requested_outcome", False),
            market.market_id,
        ),
    )
    return ranked, coverage
