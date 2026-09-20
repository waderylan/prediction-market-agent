"""Discovery regressions use synthetic quotes and preserve real opaque timed IDs."""

import httpx
import pytest

from market_agent.domain import MarketStatus
from market_agent.providers import KalshiClient, MarketValidationError
from market_agent.providers.kalshi import _relevance, normalize_query

pytestmark = pytest.mark.unit
EVENT = "KXMLBGAME-26SEP192040MIASD"


def matchup():
    return {
        "event_ticker": EVENT,
        "series_ticker": "KXMLBGAME",
        "title": "Miami vs. San Diego",
        "sub_title": "September 19",
        "markets": [
            {
                "ticker": f"{EVENT}-{team}",
                "event_ticker": EVENT,
                "title": "Miami vs. San Diego",
                "yes_sub_title": city,
                "status": "active",
            }
            for team, city in [("MIA", "Miami"), ("SD", "San Diego")]
        ],
    }


@pytest.mark.parametrize(
    "query", ["Miami San Diego", "Marlins Padres", "Miami vs Padres", "Miami vs San Diego Padres"]
)
async def test_filtered_timed_matchup(query):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["series_ticker"] == "KXMLBGAME"
        assert request.url.params["with_nested_markets"] == "true"
        assert request.url.params["status"] == "open"
        return httpx.Response(200, json={"events": [matchup()], "cursor": ""})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = KalshiClient(http_client=http)
        # Use a base URL for the injected HTTP client.
        http.base_url = client.base_url
        markets = await client.search_markets(query, series_ticker="KXMLBGAME")
    assert {m.market_id for m in markets} == {f"{EVENT}-MIA", f"{EVENT}-SD"}
    assert len(calls) == 1  # No N+1 event detail calls.


def test_ranking_and_alias_boundaries():
    assert _relevance("Miami San Diego", "Who stars in Miami Vice?") == 0
    assert _relevance("Miami San Diego", "San Diego vs Los Angeles") == 0
    assert _relevance("Miami San Diego", "Miami vs. San Diego") > 0
    assert _relevance("baseball game", "baseball game") > _relevance(
        "baseball game", "game of baseball"
    )
    assert normalize_query("San Diego Padres vs Miami Marlins") == "san diego vs miami"
    assert normalize_query("padreship") == "padreship"
    assert _relevance("the and", "the and") == 0


async def test_series_filters_and_ranking():
    def handler(request):
        assert request.url.path.endswith("/series")
        assert dict(request.url.params) == {"category": "Sports", "tags": "Baseball"}
        return httpx.Response(
            200,
            json={
                "series": [
                    {"ticker": "ACTOR", "title": "Miami Vice actor"},
                    {
                        "ticker": "KXMLBGAME",
                        "title": "Professional Baseball Game",
                        "category": "Sports",
                    },
                    {"ticker": "KXMLBGAME", "title": "Professional Baseball Game"},
                ]
            },
        )

    async with httpx.AsyncClient(
        base_url="https://test", transport=httpx.MockTransport(handler)
    ) as h:
        results = await KalshiClient(http_client=h).search_series(
            "professional baseball game", category="Sports", tags="Baseball", limit=1
        )
    assert len(results) == 1
    assert results[0]["ticker"] == "KXMLBGAME"


async def test_cursor_cycle_and_duplicate_events_are_bounded():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"events": [matchup(), matchup()], "cursor": "same"})

    async with httpx.AsyncClient(
        base_url="https://test", transport=httpx.MockTransport(handler)
    ) as h:
        markets = await KalshiClient(http_client=h).search_markets("Miami San Diego")
    assert len(calls) == 2
    assert len(markets) == 2


async def test_second_page_retains_series_filter():
    def handler(request):
        assert request.url.params["series_ticker"] == "KXMLBGAME"
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={"events": [], "cursor": "next"})
        assert request.url.params["cursor"] == "next"
        return httpx.Response(200, json={"events": [matchup()], "cursor": ""})

    async with httpx.AsyncClient(
        base_url="https://test", transport=httpx.MockTransport(handler)
    ) as h:
        assert (
            len(
                await KalshiClient(http_client=h).search_markets(
                    "Miami San Diego", series_ticker="KXMLBGAME"
                )
            )
            == 2
        )


@pytest.mark.parametrize(
    "path,payload",
    [
        ("series", {}),
        ("series", {"series": {}}),
        ("series", {"series": [None]}),
        ("events", {}),
        ("events", {"events": {}}),
        ("events", {"events": [], "cursor": 123}),
    ],
)
async def test_malformed_discovery_is_not_reported_as_empty(path, payload):
    async with httpx.AsyncClient(
        base_url="https://test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
    ) as h:
        client = KalshiClient(http_client=h)
        with pytest.raises(MarketValidationError):
            if path == "series":
                await client.search_series("baseball")
            else:
                await client.search_markets("baseball")


@pytest.mark.parametrize(
    "status,expected",
    [(MarketStatus.RESOLVED, "settled"), (MarketStatus.PAUSED, None), (None, None)],
)
async def test_provider_status_filters(status, expected):
    def handler(request):
        assert request.url.params.get("status") == expected
        return httpx.Response(200, json={"events": [matchup()], "cursor": ""})

    async with httpx.AsyncClient(
        base_url="https://test", transport=httpx.MockTransport(handler)
    ) as h:
        markets = await KalshiClient(http_client=h).search_markets("Miami San Diego", status=status)
    assert len(markets) == (2 if status is None else 0)
