from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from market_agent.domain import MarketStatus, Platform
from market_agent.providers import (
    MarketHTTPError,
    MarketMissingDataError,
    MarketTransportError,
    MarketValidationError,
    PolymarketClient,
)

FixtureLoader = Callable[[str, str], Any]


def _http_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.unit
async def test_get_market_normalizes_success(load_fixture: FixtureLoader) -> None:
    payload = load_fixture("polymarket", "market_success")
    http = _http_client(lambda request: httpx.Response(200, json=payload, request=request))
    client = PolymarketClient(http_client=http)

    market = await client.get_market("561229")
    await http.aclose()

    assert market.platform is Platform.POLYMARKET
    assert market.market_id == "561229"
    assert market.event_id == "31552"
    assert market.status is MarketStatus.OPEN
    assert market.yes_price == Decimal("0.2105")
    assert market.no_price == Decimal("0.7895")
    assert market.yes_bid == Decimal("0.21")
    assert market.resolution_source is None
    assert market.close_time is not None and market.close_time.utcoffset().total_seconds() == 0


@pytest.mark.unit
async def test_search_markets_and_empty_results(load_fixture: FixtureLoader) -> None:
    payloads = [
        load_fixture("polymarket", "search_success"),
        load_fixture("polymarket", "search_empty"),
    ]
    http = _http_client(lambda request: httpx.Response(200, json=payloads.pop(0), request=request))
    client = PolymarketClient(http_client=http)

    results = await client.search_markets("JD Vance", limit=2)
    empty = await client.search_markets("no such market", limit=2)
    await http.aclose()

    assert [market.market_id for market in results] == ["561229"]
    assert empty == ()


@pytest.mark.unit
async def test_incomplete_market_preserves_missing_values(load_fixture: FixtureLoader) -> None:
    payload = load_fixture("polymarket", "market_incomplete")
    http = _http_client(lambda request: httpx.Response(200, json=payload, request=request))
    client = PolymarketClient(http_client=http)

    market = await client.get_market("incomplete-1")
    await http.aclose()

    assert market.yes_price is None
    assert market.no_price is None
    assert market.yes_bid is None
    assert market.liquidity is None


@pytest.mark.unit
async def test_malformed_market_raises_validation_error(load_fixture: FixtureLoader) -> None:
    payload = load_fixture("polymarket", "market_malformed")
    http = _http_client(lambda request: httpx.Response(200, json=payload, request=request))
    client = PolymarketClient(http_client=http)

    with pytest.raises(MarketValidationError, match="outcomes is not valid JSON"):
        await client.get_market("malformed-1")
    await http.aclose()


@pytest.mark.unit
async def test_missing_identity_raises_missing_data_error() -> None:
    http = _http_client(lambda request: httpx.Response(200, json={}, request=request))
    client = PolymarketClient(http_client=http)

    with pytest.raises(MarketMissingDataError, match="required field id"):
        await client.get_market("missing")
    await http.aclose()


@pytest.mark.unit
async def test_http_failure_is_typed() -> None:
    http = _http_client(
        lambda request: httpx.Response(404, json={"error": "not found"}, request=request)
    )
    client = PolymarketClient(http_client=http)

    with pytest.raises(MarketHTTPError) as caught:
        await client.get_market("missing")
    await http.aclose()

    assert caught.value.status_code == 404
    assert caught.value.retryable is False


@pytest.mark.unit
async def test_timeout_retries_are_bounded() -> None:
    attempts = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timed out", request=request)

    http = _http_client(timeout)
    client = PolymarketClient(http_client=http, max_retries=1, retry_backoff_seconds=0)

    with pytest.raises(MarketTransportError) as caught:
        await client.get_market("561229")
    await http.aclose()

    assert attempts == 2
    assert caught.value.attempts == 2
