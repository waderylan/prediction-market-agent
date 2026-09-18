from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from market_agent.domain import MarketStatus, Platform
from market_agent.providers import (
    KalshiClient,
    MarketHTTPError,
    MarketMissingDataError,
    MarketTransportError,
    MarketValidationError,
)

FixtureLoader = Callable[[str, str], Any]


def _http_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://external-api.kalshi.com/trade-api/v2",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.unit
async def test_get_market_normalizes_and_enriches_success(
    load_fixture: FixtureLoader,
) -> None:
    market_payload = load_fixture("kalshi", "market_success")
    series_payload = load_fixture("kalshi", "series_success")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = series_payload if "/series/" in request.url.path else market_payload
        return httpx.Response(200, json=payload, request=request)

    http = _http_client(handler)
    client = KalshiClient(http_client=http)

    market = await client.get_market("KXPRESPERSON-28-JVAN")
    await http.aclose()

    assert market.platform is Platform.KALSHI
    assert market.market_id == "KXPRESPERSON-28-JVAN"
    assert market.event_id == "KXPRESPERSON-28"
    assert market.status is MarketStatus.OPEN
    assert market.yes_price == Decimal("0.2200")
    assert market.no_price == Decimal("0.7800")
    assert market.yes_bid == Decimal("0.2200")
    assert market.resolution_source is not None
    assert "Office of the Presidency" in market.resolution_source


@pytest.mark.unit
async def test_search_uses_bounded_local_ranking(load_fixture: FixtureLoader) -> None:
    payload = load_fixture("kalshi", "search_success")
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.url.params["with_nested_markets"] == "false"
        return httpx.Response(200, json=payload, request=request)

    http = _http_client(handler)
    client = KalshiClient(http_client=http, max_search_pages=3)

    results = await client.search_markets("JD Vance 2028 presidential election", limit=2)
    await http.aclose()

    assert requests == 1
    assert [market.market_id for market in results] == ["KXPRESPERSON-28-JVAN"]


@pytest.mark.unit
async def test_search_empty_results(load_fixture: FixtureLoader) -> None:
    payload = load_fixture("kalshi", "search_empty")
    http = _http_client(lambda request: httpx.Response(200, json=payload, request=request))
    client = KalshiClient(http_client=http)

    results = await client.search_markets("no such market")
    await http.aclose()

    assert results == ()


@pytest.mark.unit
async def test_incomplete_market_preserves_missing_values(load_fixture: FixtureLoader) -> None:
    payload = load_fixture("kalshi", "market_incomplete")
    http = _http_client(lambda request: httpx.Response(200, json=payload, request=request))
    client = KalshiClient(http_client=http)

    market = await client.get_market("KXINCOMPLETE-1")
    await http.aclose()

    assert market.yes_price is None
    assert market.no_price is None
    assert market.yes_bid is None
    assert market.close_time is None


@pytest.mark.unit
async def test_malformed_market_raises_validation_error(load_fixture: FixtureLoader) -> None:
    payload = load_fixture("kalshi", "market_malformed")
    http = _http_client(lambda request: httpx.Response(200, json=payload, request=request))
    client = KalshiClient(http_client=http)

    with pytest.raises(MarketValidationError, match="yes_bid_dollars must be numeric"):
        await client.get_market("KXMALFORMED-1")
    await http.aclose()


@pytest.mark.unit
async def test_missing_identity_raises_missing_data_error() -> None:
    payload = {"market": {"ticker": "KXMISSING-1"}}
    http = _http_client(lambda request: httpx.Response(200, json=payload, request=request))
    client = KalshiClient(http_client=http)

    with pytest.raises(MarketMissingDataError, match="required field event_ticker"):
        await client.get_market("KXMISSING-1")
    await http.aclose()


@pytest.mark.unit
async def test_server_error_retries_then_raises_typed_http_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, text="temporarily unavailable", request=request)

    http = _http_client(handler)
    client = KalshiClient(http_client=http, max_retries=1, retry_backoff_seconds=0)

    with pytest.raises(MarketHTTPError) as caught:
        await client.get_market("KXTEST-1")
    await http.aclose()

    assert attempts == 2
    assert caught.value.status_code == 503
    assert caught.value.retryable is True


@pytest.mark.unit
async def test_timeout_failure_is_typed() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    http = _http_client(timeout)
    client = KalshiClient(http_client=http, max_retries=0)

    with pytest.raises(MarketTransportError) as caught:
        await client.get_market("KXTEST-1")
    await http.aclose()

    assert caught.value.attempts == 1
