import json

import httpx
import pytest

from market_agent.providers import PolymarketClient

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
