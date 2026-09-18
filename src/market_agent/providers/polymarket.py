"""Asynchronous client and response parser for public Polymarket Gamma data."""

import json
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

    def __init__(self, **kwargs: Any) -> None:
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
        payload = await self._request_json("/public-search", params={"q": query, "page": 1})
        root = _as_dict(payload, self.provider, "search response")
        events = root.get("events", [])
        if not isinstance(events, list):
            raise MarketValidationError(self.provider, "search response events must be a list")

        retrieved_at = datetime.now(UTC)
        results: list[CanonicalMarket] = []
        for event_value in events:
            event = _as_dict(event_value, self.provider, "search event")
            event_id = _optional_str(event.get("id"))
            markets = event.get("markets", [])
            if not isinstance(markets, list):
                raise MarketValidationError(self.provider, "search event markets must be a list")
            for market_value in markets:
                market = _as_dict(market_value, self.provider, "search market")
                parsed = _parse_market(market, event_id=event_id, retrieved_at=retrieved_at)
                if status is None or parsed.status == status:
                    results.append(parsed)
                if len(results) == limit:
                    return tuple(results)
        return tuple(results)

    async def get_market(self, market_id: str) -> CanonicalMarket:
        market_id = market_id.strip()
        if not market_id:
            raise ValueError("market_id cannot be empty")
        payload = await self._request_json(f"/markets/{quote(market_id, safe='')}")
        market = _as_dict(payload, self.provider, "market response")
        return _parse_market(market, event_id=_event_id(market), retrieved_at=datetime.now(UTC))


def _parse_market(
    market: dict[str, Any],
    *,
    event_id: str | None,
    retrieved_at: datetime,
) -> CanonicalMarket:
    market_id = _required_str(market, "id")
    title = _required_str(market, "question")
    outcomes = _json_array(market.get("outcomes"), "outcomes")
    if len(outcomes) < 2 or not all(isinstance(item, str) and item for item in outcomes):
        raise MarketValidationError("polymarket", "outcomes must contain at least two labels")
    prices = _json_array(market.get("outcomePrices"), "outcomePrices", allow_missing=True)
    if prices and len(prices) != len(outcomes):
        raise MarketValidationError("polymarket", "outcomes and outcomePrices lengths differ")

    yes_index = next(
        (index for index, label in enumerate(outcomes) if str(label).casefold() == "yes"), None
    )
    no_index = next(
        (index for index, label in enumerate(outcomes) if str(label).casefold() == "no"), None
    )
    yes_price = _array_decimal(prices, yes_index, "outcomePrices")
    no_price = _array_decimal(prices, no_index, "outcomePrices")
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
            yes_bid=_optional_decimal(market.get("bestBid"), "bestBid"),
            yes_ask=_optional_decimal(market.get("bestAsk"), "bestAsk"),
            open_time=_optional_datetime(market.get("startDate"), "startDate"),
            close_time=_optional_datetime(market.get("endDate"), "endDate"),
            resolution_deadline=None,
            resolution_source=resolution_source,
            rules=_optional_str(market.get("description")),
            status=_status(market, prices),
            liquidity=_optional_decimal(market.get("liquidity"), "liquidity"),
            volume=_optional_decimal(market.get("volume"), "volume"),
            source_url=HttpUrl(source_url),
            retrieved_at=retrieved_at,
            provider_data={
                "slug": slug,
                "active": market.get("active"),
                "closed": market.get("closed"),
                "archived": market.get("archived"),
                "accepting_orders": market.get("acceptingOrders"),
                "last_trade_price": market.get("lastTradePrice"),
            },
        )
    except ValidationError as error:
        raise MarketValidationError("polymarket", f"invalid market {market_id}: {error}") from error


def _status(market: dict[str, Any], prices: list[Any]) -> MarketStatus:
    if market.get("archived") is True:
        return MarketStatus.ARCHIVED
    if market.get("closed") is True:
        parsed = [_optional_decimal(value, "outcomePrices") for value in prices]
        if any(value in (Decimal("0"), Decimal("1")) for value in parsed if value is not None):
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
        return Decimal(str(value))
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
