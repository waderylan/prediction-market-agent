"""Asynchronous client and response parser for public Kalshi market data."""

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from pydantic import HttpUrl, ValidationError

from market_agent.domain import CanonicalMarket, MarketStatus, Platform
from market_agent.providers.base import AsyncMarketClient
from market_agent.providers.exceptions import MarketMissingDataError, MarketValidationError

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {"a", "an", "and", "in", "is", "of", "on", "the", "to", "will"}
_STOP_WORDS |= {"vs", "versus"}
# Deliberately limited to unambiguous team nicknames; never infer ticker components.
_TEAM_ALIASES = {"padres": "san diego", "marlins": "miami"}


def normalize_query(query: str) -> str:
    normalized = query.casefold()
    for alias, city in _TEAM_ALIASES.items():
        # Also collapse full names ("San Diego Padres") into the canonical city.
        normalized = re.sub(rf"\b(?:{city}\s+)?{alias}\b", city, normalized)
    return " ".join(_TOKEN_PATTERN.findall(normalized))


class KalshiClient(AsyncMarketClient):
    """Read-only Kalshi client with bounded local catalog ranking."""

    provider = "kalshi"

    def __init__(self, *, max_search_pages: int = 3, **kwargs: Any) -> None:
        if type(max_search_pages) is not int or not 1 <= max_search_pages <= 10:
            raise ValueError("max_search_pages must be between 1 and 10")
        self.max_search_pages = max_search_pages
        super().__init__(base_url="https://external-api.kalshi.com/trade-api/v2", **kwargs)

    async def search_series(
        self,
        query: str,
        *,
        category: str | None = None,
        tags: str | None = None,
        limit: int = 10,
    ) -> tuple[dict[str, Any], ...]:
        """Rank the series catalog, with provider-side category/tag filtering."""
        if not query.strip() or not 1 <= limit <= 50:
            raise ValueError("query must be nonempty and limit between 1 and 50")
        params = {}
        for key, value in (("category", category), ("tags", tags)):
            if value is not None:
                if not value.strip():
                    raise ValueError(f"{key} cannot be empty")
                params[key] = value.strip()
        root = _as_dict(await self._request_json("/series", params=params), "series response")
        values = root.get("series")
        if not isinstance(values, list):
            raise MarketValidationError(self.provider, "series response series must be a list")
        ranked = []
        seen = set()
        for value in values:
            series = _as_dict(value, "series")
            ticker = _required_str(series, "ticker")
            title = _required_str(series, "title")
            score = _relevance(
                query,
                " ".join(
                    str(series.get(key, "")) for key in ("ticker", "title", "category", "tags")
                ),
            )
            if score and ticker not in seen:
                seen.add(ticker)
                ranked.append(
                    (
                        score + _relevance(query, title),
                        {
                            "ticker": ticker,
                            "title": title,
                            "category": _optional_str(series.get("category")),
                        },
                    )
                )
        ranked.sort(key=lambda item: (-item[0], item[1]["ticker"]))
        return tuple(item for _, item in ranked[:limit])

    async def search_markets(
        self,
        query: str,
        *,
        status: MarketStatus | None = MarketStatus.OPEN,
        limit: int = 10,
        series_ticker: str | None = None,
    ) -> tuple[CanonicalMarket, ...]:
        query = query.strip()
        if not query:
            raise ValueError("query cannot be empty")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        if series_ticker is not None and not re.fullmatch(
            r"[A-Z0-9][A-Z0-9._-]{0,99}", series_ticker
        ):
            raise ValueError("invalid series_ticker")
        query = normalize_query(query)

        cursor: str | None = None
        seen_cursors: set[str] = set()
        seen_events: set[str] = set()
        ranked_events: list[tuple[int, dict[str, Any]]] = []
        for _ in range(self.max_search_pages):
            params: dict[str, str | int] = {
                "limit": 200,
                "with_nested_markets": "true" if series_ticker else "false",
            }
            if series_ticker:
                params["series_ticker"] = series_ticker
            provider_filter = _status_filter(status)
            if provider_filter is not None:
                params["status"] = provider_filter
            if cursor:
                params["cursor"] = cursor
            payload = await self._request_json("/events", params=params)
            root = _as_dict(payload, "events response")
            events = root.get("events")
            if not isinstance(events, list):
                raise MarketValidationError(self.provider, "events response events must be a list")
            for event_value in events:
                event = _as_dict(event_value, "event")
                event_id = _required_str(event, "event_ticker")
                if event_id in seen_events:
                    continue
                seen_events.add(event_id)
                text = _event_text(event)
                nested = event.get("markets")
                if isinstance(nested, list):
                    text += " " + " ".join(
                        _market_text(_as_dict(m, "event market")) for m in nested
                    )
                score = _relevance(query, text)
                if score > 0:
                    ranked_events.append(
                        (score + _relevance(query, str(event.get("title", ""))), event)
                    )
            if root.get("cursor") is not None and not isinstance(root["cursor"], str):
                raise MarketValidationError(self.provider, "cursor must be a string")
            cursor = _optional_str(root.get("cursor"))
            if cursor is None or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)

        ranked_events.sort(key=lambda item: item[0], reverse=True)
        retrieved_at = datetime.now(UTC)
        ranked_markets: list[tuple[int, CanonicalMarket]] = []
        seen_markets: set[str] = set()
        for event_score, event in ranked_events[: min(limit * 2, 10)]:
            markets = event.get("markets")
            if not isinstance(markets, list):
                event_ticker = _required_str(event, "event_ticker")
                event = await self._get_event(event_ticker)
                markets = event.get("markets", [])
            if not isinstance(markets, list):
                raise MarketValidationError(self.provider, "event markets must be a list")
            for market_value in markets:
                market = _as_dict(market_value, "event market")
                parsed = _parse_market(market, retrieved_at=retrieved_at)
                if (status is not None and parsed.status != status) or (
                    parsed.market_id in seen_markets
                ):
                    continue
                seen_markets.add(parsed.market_id)
                market_score = event_score + _relevance(query, _market_text(market))
                ranked_markets.append((market_score, self.record_observation(parsed)))

        ranked_markets.sort(key=lambda item: item[0], reverse=True)
        return tuple(market for _, market in ranked_markets[:limit])

    async def get_market(self, market_id: str) -> CanonicalMarket:
        market_id = market_id.strip()
        if not market_id:
            raise ValueError("market_id cannot be empty")
        payload = await self._request_json(f"/markets/{quote(market_id, safe='')}")
        root = _as_dict(payload, "market response")
        market = _as_dict(root.get("market"), "market response market")
        if _required_str(market, "ticker") != market_id:
            raise MarketValidationError(self.provider, "market identifier does not match request")
        retrieved_at = datetime.now(UTC)
        from market_agent.providers.sports_search import (
            KALSHI_SERIES,
            kalshi_event,
            objects,
            winner_market,
        )

        event_id = _required_str(market, "event_ticker")
        if event_id.split("-", 1)[0] in KALSHI_SERIES:
            event_root = _as_dict(
                await self._request_json(
                    "/events",
                    params={
                        "tickers": event_id,
                        "with_nested_markets": "true",
                        "with_milestones": "true",
                    },
                ),
                "event response",
            )
            event_values = objects(event_root.get("events"), "kalshi", "events")
            if len(event_values) != 1:
                raise MarketValidationError(self.provider, "expected one event")
            event = event_values[0]
            if event.get("event_ticker") != event_id:
                raise MarketValidationError(self.provider, "event identifier mismatch")
            sports = kalshi_event(
                event, objects(event_root.get("milestones", []), "kalshi", "milestones")
            )
            parsed = _parse_market(
                market, retrieved_at=retrieved_at, resolution_source=_resolution_source(event)
            )
            if sports and winner_market(market, sports.raw_title):
                parsed.provider_data["sports"] = sports.model_dump(mode="json")
                parsed.provider_data["series_ticker"] = event.get("series_ticker")
            return self.reuse_observation(parsed)
        resolution_source: str | None = None
        series_ticker = _optional_str(market.get("series_ticker"))
        if series_ticker is not None:
            series_payload = await self._request_json(f"/series/{quote(series_ticker, safe='')}")
            series_root = _as_dict(series_payload, "series response")
            series = _as_dict(series_root.get("series"), "series response series")
            resolution_source = _resolution_source(series)
        return self.reuse_observation(
            _parse_market(
                market,
                retrieved_at=retrieved_at,
                resolution_source=resolution_source,
            )
        )

    async def _get_event(self, event_ticker: str) -> dict[str, Any]:
        payload = await self._request_json(
            f"/events/{quote(event_ticker, safe='')}",
            params={"with_nested_markets": "true"},
        )
        root = _as_dict(payload, "event response")
        return _as_dict(root.get("event"), "event response event")


def _parse_market(
    market: dict[str, Any],
    *,
    retrieved_at: datetime,
    resolution_source: str | None = None,
) -> CanonicalMarket:
    market_id = _required_str(market, "ticker")
    event_id = _required_str(market, "event_ticker")
    base_title = _required_str(market, "title")
    outcome_title = _optional_str(market.get("yes_sub_title"))
    title = f"{base_title} — {outcome_title}" if outcome_title else base_title
    yes_price = _optional_decimal(market.get("last_price_dollars"), "last_price_dollars")
    no_price = Decimal("1") - yes_price if yes_price is not None else None
    rules_parts = [
        value
        for value in (
            _optional_str(market.get("rules_primary")),
            _optional_str(market.get("rules_secondary")),
        )
        if value is not None
    ]
    rules = "\n\n".join(rules_parts) or None
    provider_status = _optional_str(market.get("status"))
    result = _optional_str(market.get("result"))
    settlement_value = _optional_decimal(
        market.get("settlement_value_dollars"), "settlement_value_dollars"
    )
    winning_outcome = None
    if result and result.casefold() == "yes":
        winning_outcome = outcome_title or "Yes"
    elif result and result.casefold() == "no":
        winning_outcome = f"Not {outcome_title}" if outcome_title else "No"
    source_url = f"https://external-api.kalshi.com/trade-api/v2/markets/{quote(market_id, safe='')}"
    try:
        return CanonicalMarket(
            platform=Platform.KALSHI,
            market_id=market_id,
            event_id=event_id,
            title=title,
            description=_optional_str(market.get("subtitle")),
            outcomes=("Yes", "No"),
            yes_price=yes_price,
            no_price=no_price,
            yes_bid=_optional_decimal(market.get("yes_bid_dollars"), "yes_bid_dollars"),
            yes_ask=_optional_decimal(market.get("yes_ask_dollars"), "yes_ask_dollars"),
            open_time=_optional_datetime(market.get("open_time"), "open_time"),
            close_time=_optional_datetime(market.get("close_time"), "close_time"),
            resolution_deadline=_optional_datetime(
                market.get("latest_expiration_time") or market.get("expiration_time"),
                "latest_expiration_time",
            ),
            resolution_source=resolution_source,
            rules=rules,
            status=_status(provider_status),
            liquidity=_optional_decimal(market.get("liquidity_dollars"), "liquidity_dollars"),
            volume=_optional_decimal(market.get("volume_fp"), "volume_fp"),
            source_url=HttpUrl(source_url),
            retrieved_at=retrieved_at,
            provider_data={
                "raw_title": base_title,
                "outcome_quotes": [
                    {
                        "label": outcome_title or "Yes",
                        "side": "yes",
                        "price": str(yes_price) if yes_price is not None else None,
                        "price_kind": "last_trade",
                        "bid": market.get("yes_bid_dollars"),
                        "ask": market.get("yes_ask_dollars"),
                    },
                    {
                        "label": f"Not {outcome_title}" if outcome_title else "No",
                        "side": "no",
                        "price": str(no_price) if no_price is not None else None,
                        "price_kind": "derived_complement",
                        "bid": market.get("no_bid_dollars"),
                        "ask": market.get("no_ask_dollars"),
                    },
                ],
                "provider_updated_at": market.get("updated_time"),
                "provider_last_trade_price": str(yes_price) if yes_price is not None else None,
                "provider_last_trade_outcome": outcome_title or "Yes",
                "price_observed_at": None,
                "last_trade_at": None,
                "expected_resolution_time": market.get("expected_expiration_time"),
                "latest_expiration_time": market.get("latest_expiration_time"),
                "series_ticker": _optional_str(market.get("series_ticker")),
                "provider_status": provider_status,
                "result": result,
                "settlement_value": str(settlement_value) if settlement_value is not None else None,
                "winning_outcome": winning_outcome,
                "resolved_at": market.get("settlement_ts"),
                "last_price": market.get("last_price_dollars"),
            },
        )
    except ValidationError as error:
        raise MarketValidationError("kalshi", f"invalid market {market_id}: {error}") from error


def _status(value: str | None) -> MarketStatus:
    normalized = value.casefold() if value else ""
    if normalized in {"active", "open"}:
        return MarketStatus.OPEN
    if normalized in {"initialized", "unopened"}:
        return MarketStatus.UNOPENED
    if normalized == "paused":
        return MarketStatus.PAUSED
    if normalized in {"closed", "inactive"}:
        return MarketStatus.CLOSED
    if normalized in {"determined", "finalized", "settled"}:
        return MarketStatus.RESOLVED
    return MarketStatus.UNKNOWN


def _status_filter(status: MarketStatus | None) -> str | None:
    if status is None or status in {
        MarketStatus.UNKNOWN,
        MarketStatus.ARCHIVED,
        MarketStatus.PAUSED,
    }:
        return None
    if status is MarketStatus.RESOLVED:
        return "settled"
    return status.value


def _resolution_source(series: dict[str, Any]) -> str | None:
    values = series.get("settlement_sources")
    if not isinstance(values, list):
        return None
    sources: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        name = _optional_str(item.get("name"))
        url = _optional_str(item.get("url"))
        if name and url:
            sources.append(f"{name} ({url})")
        elif name or url:
            sources.append(name or url or "")
    return "; ".join(sources) or None


def _event_text(event: dict[str, Any]) -> str:
    fields = ("event_ticker", "series_ticker", "title", "sub_title", "category")
    return " ".join(str(event.get(field, "")) for field in fields)


def _market_text(market: dict[str, Any]) -> str:
    fields = ("ticker", "title", "subtitle", "yes_sub_title", "no_sub_title")
    return " ".join(str(market.get(field, "")) for field in fields)


def _relevance(query: str, candidate: str) -> int:
    query_tokens = set(_TOKEN_PATTERN.findall(query.casefold())) - _STOP_WORDS
    candidate_tokens = set(_TOKEN_PATTERN.findall(candidate.casefold()))
    overlap = len(query_tokens & candidate_tokens)
    # Require at least 60% coverage and two terms for multi-token queries.
    if not query_tokens or overlap < min(2, len(query_tokens)):
        return 0
    if overlap / len(query_tokens) < 0.6:
        return 0
    phrase = " ".join(_TOKEN_PATTERN.findall(query.casefold()))
    normalized_candidate = " ".join(_TOKEN_PATTERN.findall(candidate.casefold()))
    # A multi-word city must not outweigh the other requested participant.
    for city in _TEAM_ALIASES.values():
        if f" {city} " in f" {phrase} " and f" {city} " not in f" {normalized_candidate} ":
            return 0
    return overlap * 10 + (20 if f" {phrase} " in f" {normalized_candidate} " else 0)


def _as_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MarketValidationError("kalshi", f"{label} must be an object")
    return value


def _required_str(data: dict[str, Any], key: str) -> str:
    value = _optional_str(data.get(key))
    if value is None:
        raise MarketMissingDataError("kalshi", f"required field {key} is missing")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None or not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _optional_decimal(value: Any, name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise MarketValidationError("kalshi", f"{name} must be numeric")
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise MarketValidationError("kalshi", f"{name} must be finite")
        return parsed
    except InvalidOperation as error:
        raise MarketValidationError("kalshi", f"{name} must be numeric") from error


def _optional_datetime(value: Any, name: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MarketValidationError("kalshi", f"{name} must be an ISO timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MarketValidationError("kalshi", f"{name} must be an ISO timestamp") from error
