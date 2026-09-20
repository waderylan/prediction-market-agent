import json

import httpx
import pytest

from market_agent.domain import MarketStatus
from market_agent.providers import MarketValidationError, PolymarketClient

pytestmark = pytest.mark.unit


def client_for(handler, **kwargs):
    http = httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com", transport=httpx.MockTransport(handler)
    )
    return http, PolymarketClient(http_client=http, **kwargs)


async def test_pages_past_filtered_contracts_and_prioritizes_matching_sibling(load_fixture):
    market = load_fixture("polymarket", "market_success")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["events_status"] == "active"
        assert request.url.params["search_profiles"] == "false"
        assert request.url.params["optimized"] == "false"
        if request.url.params["page"] == "1":
            return httpx.Response(
                200,
                json={
                    "events": [{"markets": [{**market, "id": "1", "closed": True}]}],
                    "pagination": {"hasMore": True},
                },
            )
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "markets": [
                            {**market, "id": "2", "question": "Will someone else win?"},
                            market,
                            market,
                        ]
                    }
                ],
                "pagination": {"hasMore": True},
            },
        )

    http, client = client_for(handler)
    async with http:
        results = await client.search_markets("JD Vance", limit=1)
    assert [m.market_id for m in results] == ["561229"]
    assert len(calls) == 2  # Stop as soon as the unique result limit is satisfied.


@pytest.mark.parametrize("repeat,expected_calls", [(True, 2), (False, 3)])
async def test_duplicate_pages_and_page_budget(load_fixture, repeat, expected_calls):
    market = load_fixture("polymarket", "market_success")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "events": [{"markets": [{**market, "id": "1" if repeat else str(len(calls))}]}],
                "pagination": {"hasMore": True},
            },
        )

    http, client = client_for(handler)
    async with http:
        results = await client.search_markets("Vance", limit=10)
    assert len(calls) == expected_calls
    assert len(results) == (1 if repeat else 3)


@pytest.mark.parametrize(
    "payload",
    [
        {"events": None},
        {"events": []},
        {"pagination": {"hasMore": False}},
        {"events": [{"markets": None}]},
    ],
)
async def test_nullable_empty_search(payload):
    http, client = client_for(lambda r: httpx.Response(200, json=payload))
    async with http:
        assert await client.search_markets("missing") == ()


@pytest.mark.parametrize(
    "payload",
    [{"events": {}}, {"events": [None]}, {"pagination": {"hasMore": "false"}}, {"pagination": []}],
)
async def test_malformed_search(payload):
    http, client = client_for(lambda r: httpx.Response(200, json=payload))
    async with http:
        with pytest.raises(MarketValidationError):
            await client.search_markets("missing")


@pytest.mark.parametrize(
    "prices,resolution,expected",
    [
        (["0", "0"], None, MarketStatus.CLOSED),
        (["1", "0"], "proposed", MarketStatus.CLOSED),
        (["0.5", "0.5"], "resolved", MarketStatus.RESOLVED),
        (["0", "1"], "resolved", MarketStatus.RESOLVED),
    ],
)
async def test_resolution_requires_metadata(load_fixture, prices, resolution, expected):
    market = load_fixture("polymarket", "market_success")
    market.update(closed=True, outcomePrices=json.dumps(prices), umaResolutionStatus=resolution)
    http, client = client_for(lambda r: httpx.Response(200, json=market))
    async with http:
        assert (await client.get_market("561229")).status == expected


@pytest.mark.parametrize("outcomes", [["Padres", "Marlins"], ["No", "Yes"]])
async def test_no_mislabeled_yes_quotes(load_fixture, outcomes):
    market = load_fixture("polymarket", "market_success")
    market["outcomes"] = json.dumps(outcomes)
    http, client = client_for(lambda r: httpx.Response(200, json=market))
    async with http:
        result = await client.get_market("561229")
    assert result.yes_bid is None and result.yes_ask is None
    if outcomes == ["No", "Yes"]:
        assert str(result.yes_price) == "0.7895"
    else:
        assert result.yes_price is None and result.no_price is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("bestBid", "NaN"),
        ("bestAsk", "Infinity"),
        ("outcomePrices", '["NaN", "0"]'),
        ("outcomes", '["Yes", "yes"]'),
    ],
)
async def test_invalid_values_are_typed_errors(load_fixture, field, value):
    market = load_fixture("polymarket", "market_success")
    market[field] = value
    http, client = client_for(lambda r: httpx.Response(200, json=market))
    async with http:
        with pytest.raises(MarketValidationError):
            await client.get_market("561229")


async def test_detail_identity_mismatch(load_fixture):
    http, client = client_for(
        lambda r: httpx.Response(200, json=load_fixture("polymarket", "market_success"))
    )
    async with http:
        with pytest.raises(MarketValidationError, match="identifier"):
            await client.get_market("123")


async def test_historical_filter(load_fixture):
    def handler(request):
        assert request.url.params["events_status"] == "all"
        return httpx.Response(200, json=load_fixture("polymarket", "search_empty"))

    http, client = client_for(handler)
    async with http:
        await client.search_markets("election", status=MarketStatus.RESOLVED)
