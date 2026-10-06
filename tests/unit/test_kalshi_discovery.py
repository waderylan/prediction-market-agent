"""Discovery regressions use synthetic quotes and preserve real opaque timed IDs."""

import httpx
import pytest

from market_agent.providers import KalshiClient

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
