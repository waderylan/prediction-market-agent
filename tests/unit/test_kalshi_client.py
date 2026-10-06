from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from market_agent.domain import MarketStatus, Platform
from market_agent.providers import (
    KalshiClient,
    MarketHTTPError,
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
async def test_malformed_market_raises_validation_error(load_fixture: FixtureLoader) -> None:
    payload = load_fixture("kalshi", "market_malformed")
    http = _http_client(lambda request: httpx.Response(200, json=payload, request=request))
    client = KalshiClient(http_client=http)

    with pytest.raises(MarketValidationError, match="yes_bid_dollars must be numeric"):
        await client.get_market("KXMALFORMED-1")
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
