"""Asynchronous client and response parser for public Polymarket Gamma data."""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from pydantic import HttpUrl, ValidationError

from market_agent.domain import CanonicalMarket, MarketStatus, Platform
from market_agent.providers.base import AsyncMarketClient
from market_agent.providers.exceptions import MarketMissingDataError, MarketValidationError


class PolymarketClient(AsyncMarketClient):
    """Read-only Polymarket Gamma client."""

    provider = "polymarket"

    def __init__(self, *, max_search_pages: int = 3, **kwargs: Any) -> None:
        if type(max_search_pages) is not int or not 1 <= max_search_pages <= 10:
            raise ValueError("max_search_pages must be between 1 and 10")
        self.max_search_pages = max_search_pages
        super().__init__(base_url="https://gamma-api.polymarket.com", **kwargs)

    async def search_markets(
        self,
        query: str,
        *,
        status: MarketStatus | None = MarketStatus.OPEN,
        limit: int = 10,
    ) -> tuple[CanonicalMarket, ...]:
        query = query.strip()
        if not query:
            raise ValueError("query cannot be empty")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        results: list[CanonicalMarket] = []
        seen: set[str] = set()
        query_tokens = set(re.findall(r"\w+", query.casefold()))
        for page in range(1, self.max_search_pages + 1):
            payload = await self._request_json(
                "/public-search",
                params={
                    "q": query,
                    "page": page,
                    "limit_per_type": max(5, min(limit, 20)),
                    "events_status": "active"
                    if status in {MarketStatus.OPEN, MarketStatus.PAUSED}
                    else "all",
                    "search_tags": "false",
                    "search_profiles": "false",
                    # Optimized responses omit Gamma IDs and order-acceptance flags.
                    "optimized": "false",
                },
            )
            root = _as_dict(payload, self.provider, "search response")
            events = root.get("events")
            if events is None:
                events = []  # Gamma documents nullable/omitted event results.
            if not isinstance(events, list):
                raise MarketValidationError(self.provider, "search response events must be a list")
            pagination = root.get("pagination", {})
            pagination = _as_dict(pagination, self.provider, "search pagination")
            has_more = pagination.get("hasMore", False)
            if not isinstance(has_more, bool):
                raise MarketValidationError(self.provider, "pagination hasMore must be boolean")
            retrieved_at = datetime.now(UTC)
            candidates: list[tuple[int, dict[str, Any], str | None]] = []
            new_ids = 0
            for event_value in events:
                event = _as_dict(event_value, self.provider, "search event")
                event_id = _optional_str(event.get("id"))
                markets = event.get("markets")
                if markets is None:
                    markets = []
                if not isinstance(markets, list):
                    raise MarketValidationError(
                        self.provider, "search event markets must be a list"
                    )
                for market_value in markets:
                    market = _as_dict(market_value, self.provider, "search market")
                    market_id = _required_str(market, "id")
                    if market_id in seen:
                        continue
                    seen.add(market_id)
                    new_ids += 1
                    if status is not None and _status(market) != status:
                        continue
                    title = _required_str(market, "question")
                    tokens = set(re.findall(r"\w+", title.casefold()))
                    score = len(query_tokens & tokens)
                    candidates.append((score, market, event_id))
            # Preserve provider order for ties, but don't let unrelated sibling contracts
            # consume the limit before a candidate whose question matches the query.
            candidates.sort(key=lambda item: item[0], reverse=True)
            for _, market, event_id in candidates:
                parsed = _parse_market(market, event_id=event_id, retrieved_at=retrieved_at)
                if status is None or parsed.status == status:
                    results.append(parsed)
                if len(results) == limit:
                    return tuple(results)
            if not has_more or not events or not new_ids:
                break
        return tuple(results)

    async def get_market(self, market_id: str) -> CanonicalMarket:
        market_id = market_id.strip()
        if not market_id:
            raise ValueError("market_id cannot be empty")
        payload = await self._request_json(f"/markets/{quote(market_id, safe='')}")
        market = _as_dict(payload, self.provider, "market response")
        if _required_str(market, "id") != market_id:
            raise MarketValidationError(self.provider, "market identifier does not match request")
        parsed = _parse_market(market, event_id=_event_id(market), retrieved_at=datetime.now(UTC))
        from market_agent.providers.sports_search import objects, poly_event

        events = objects(
            market["events"] if market.get("events") is not None else [], "polymarket", "events"
        )
        if len(events) == 1:
            sports = poly_event(events[0], market)
            if sports:
                parsed.provider_data["sports"] = sports.model_dump(mode="json")
            parsed.provider_data["event_slug"] = events[0].get("slug")
        return parsed


def _parse_market(
    market: dict[str, Any],
    *,
    event_id: str | None,
    retrieved_at: datetime,
) -> CanonicalMarket:
    market_id = _required_str(market, "id")
    title = _required_str(market, "question")
    outcomes = _json_array(market.get("outcomes"), "outcomes")
    if len(outcomes) < 2 or not all(isinstance(item, str) and item.strip() for item in outcomes):
        raise MarketValidationError("polymarket", "outcomes must contain at least two labels")
    outcomes = [item.strip() for item in outcomes]
    if len({item.casefold() for item in outcomes}) != len(outcomes):
        raise MarketValidationError("polymarket", "outcome labels must be unique")
    prices = _json_array(market.get("outcomePrices"), "outcomePrices", allow_missing=True)
    if prices and len(prices) != len(outcomes):
        raise MarketValidationError("polymarket", "outcomes and outcomePrices lengths differ")
    for value in prices:
        price = _optional_decimal(value, "outcomePrices")
        if price is not None and not Decimal("0") <= price <= Decimal("1"):
            raise MarketValidationError("polymarket", "outcomePrices must be between 0 and 1")

    yes_index = next(
        (index for index, label in enumerate(outcomes) if str(label).casefold() == "yes"), None
    )
    no_index = next(
        (index for index, label in enumerate(outcomes) if str(label).casefold() == "no"), None
    )
    yes_price = _array_decimal(prices, yes_index, "outcomePrices")
    no_price = _array_decimal(prices, no_index, "outcomePrices")
    # Gamma's bestBid/bestAsk do not carry an outcome label. Only map them to
    # YES for the standard Yes/No ordering; do not mislabel team or reversed outcomes.
    standard_binary = [label.casefold() for label in outcomes] == ["yes", "no"]
    slug = _optional_str(market.get("slug"))
    source_url = f"https://gamma-api.polymarket.com/markets/{quote(market_id, safe='')}"
    resolution_source = _optional_str(market.get("resolutionSource"))

    try:
        return CanonicalMarket(
            platform=Platform.POLYMARKET,
            market_id=market_id,
            event_id=event_id,
            title=title,
            description=_optional_str(market.get("description")),
            outcomes=tuple(str(item) for item in outcomes),
            yes_price=yes_price,
            no_price=no_price,
            yes_bid=_optional_decimal(market.get("bestBid"), "bestBid")
            if standard_binary
            else None,
            yes_ask=_optional_decimal(market.get("bestAsk"), "bestAsk")
            if standard_binary
            else None,
            open_time=_optional_datetime(market.get("startDate"), "startDate"),
            close_time=_optional_datetime(market.get("endDate"), "endDate"),
            resolution_deadline=None,
            resolution_source=resolution_source,
            rules=_optional_str(market.get("description")),
            status=_status(market),
            liquidity=_optional_decimal(market.get("liquidity"), "liquidity"),
            volume=_optional_decimal(market.get("volume"), "volume"),
            source_url=HttpUrl(source_url),
            retrieved_at=retrieved_at,
            provider_data={
                "raw_title": title,
                "outcome_quotes": [
                    {
                        "label": label,
                        "side": label.casefold() if standard_binary else None,
                        "price": str(_array_decimal(prices, i, "outcomePrices"))
                        if _array_decimal(prices, i, "outcomePrices") is not None
                        else None,
                        "price_kind": "provider_snapshot",
                        "bid": None,
                        "ask": None,
                    }
                    for i, label in enumerate(outcomes)
                ],
                "provider_updated_at": market.get("updatedAt"),
                "provider_last_trade_price": market.get("lastTradePrice"),
                "provider_last_trade_outcome": "Yes" if standard_binary else None,
                "price_observed_at": None,
                "last_trade_at": None,
                "slug": slug,
                "active": market.get("active"),
                "closed": market.get("closed"),
                "archived": market.get("archived"),
                "accepting_orders": market.get("acceptingOrders"),
                "last_trade_price": market.get("lastTradePrice"),
                "uma_resolution_status": market.get("umaResolutionStatus"),
            },
        )
    except ValidationError as error:
        raise MarketValidationError("polymarket", f"invalid market {market_id}: {error}") from error


def _status(market: dict[str, Any]) -> MarketStatus:
    if market.get("archived") is True:
        return MarketStatus.ARCHIVED
    if market.get("closed") is True:
        if (_optional_str(market.get("umaResolutionStatus")) or "").casefold() == "resolved":
            return MarketStatus.RESOLVED
        return MarketStatus.CLOSED
    if market.get("active") is True and market.get("acceptingOrders") is True:
        return MarketStatus.OPEN
    if market.get("active") is True:
        return MarketStatus.PAUSED
    if market.get("active") is False:
        return MarketStatus.UNOPENED
    return MarketStatus.UNKNOWN


def _event_id(market: dict[str, Any]) -> str | None:
    events = market.get("events")
    if not isinstance(events, list) or not events:
        return None
    event = events[0]
    return _optional_str(event.get("id")) if isinstance(event, dict) else None


def _as_dict(value: Any, provider: str, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MarketValidationError(provider, f"{label} must be an object")
    return value


def _required_str(data: dict[str, Any], key: str) -> str:
    value = _optional_str(data.get(key))
    if value is None:
        raise MarketMissingDataError("polymarket", f"required field {key} is missing")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _json_array(value: Any, name: str, *, allow_missing: bool = False) -> list[Any]:
    if value is None and allow_missing:
        return []
    if not isinstance(value, str):
        raise MarketValidationError("polymarket", f"{name} must be a JSON-encoded array")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise MarketValidationError("polymarket", f"{name} is not valid JSON") from error
    if not isinstance(parsed, list):
        raise MarketValidationError("polymarket", f"{name} must decode to an array")
    return parsed


def _array_decimal(values: list[Any], index: int | None, name: str) -> Decimal | None:
    if index is None or not values:
        return None
    return _optional_decimal(values[index], name)


def _optional_decimal(value: Any, name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise MarketValidationError("polymarket", f"{name} must be numeric")
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite():
            raise MarketValidationError("polymarket", f"{name} must be finite")
        return parsed
    except InvalidOperation as error:
        raise MarketValidationError("polymarket", f"{name} must be numeric") from error


def _optional_datetime(value: Any, name: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MarketValidationError("polymarket", f"{name} must be an ISO timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MarketValidationError("polymarket", f"{name} must be an ISO timestamp") from error
