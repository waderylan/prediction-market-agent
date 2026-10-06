from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from market_agent.domain import MarketStatus, Platform
from market_agent.providers import (
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
async def test_malformed_market_raises_validation_error(load_fixture: FixtureLoader) -> None:
    payload = load_fixture("polymarket", "market_malformed")
    http = _http_client(lambda request: httpx.Response(200, json=payload, request=request))
    client = PolymarketClient(http_client=http)

    with pytest.raises(MarketValidationError, match="outcomes is not valid JSON"):
        await client.get_market("malformed-1")
    await http.aclose()


@pytest.mark.unit
async def test_response_byte_limit_is_enforced_before_json_parsing() -> None:
    http = _http_client(
        lambda request: httpx.Response(200, json={"padding": "x" * 100}, request=request)
    )
    client = PolymarketClient(http_client=http, max_response_bytes=32)

    with pytest.raises(MarketValidationError, match="32 byte safety limit"):
        await client.get_market("1")
    await http.aclose()
