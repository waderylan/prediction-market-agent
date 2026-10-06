"""Sports identity, contract scope, coverage, and quote regressions without live games."""

import json
from datetime import UTC, date, datetime

import httpx
import pytest

from market_agent.domain import MarketStatus
from market_agent.mcp.common import project
from market_agent.providers import KalshiClient, MarketValidationError, PolymarketClient
from market_agent.providers.sports import participant, resolve_query
from market_agent.providers.sports_search import (
    KALSHI_SERIES,
    kalshi_event,
    search_kalshi,
    search_polymarket,
)

pytestmark = pytest.mark.unit


def kalshi_fixture(event_id="KXMLBGAME-OPAQUE-1", labels=("New York Y", "San Diego")):
    event = {
        "event_ticker": event_id,
        "series_ticker": "KXMLBGAME",
        "title": " vs ".join(labels),
        "settlement_sources": [{"name": "League", "url": "https://www.mlb.com/"}],
        "markets": [
            {
                "ticker": f"{event_id}-{i}",
                "event_ticker": event_id,
                "title": f"{label} wins",
                "yes_sub_title": label,
                "status": "active",
                "market_type": "binary",
                "last_price_dollars": "0.40",
                "yes_bid_dollars": "0.39",
                "yes_ask_dollars": "0.41",
                "close_time": "2026-09-23T00:00:00Z",
                "latest_expiration_time": "2026-09-24T00:00:00Z",
                "expected_expiration_time": "2026-09-20T03:00:00Z",
                "rules_primary": "Full game winner. Overtime included.",
                "rules_secondary": "Postponements within two days count.",
            }
            for i, label in enumerate(labels)
        ],
    }
    milestone = {"related_event_tickers": [event_id], "start_date": "2026-09-20T00:10:00Z"}
    return event, milestone


def poly_fixture(event_id="101", market_id="201", labels=("New York Yankees", "San Diego Padres")):
    market = {
        "id": market_id,
        "question": " vs ".join(labels),
        "slug": "provider-slug",
        "outcomes": json.dumps(labels),
        "outcomePrices": '["0.42","0.58"]',
        "sportsMarketType": "moneyline",
        "active": True,
        "acceptingOrders": True,
        "closed": False,
        "bestBid": 0.41,
        "bestAsk": 0.43,
        "lastTradePrice": 0.44,
        "gameStartTime": "2026-09-20 00:10:00+00",
        "endDate": "2026-09-27T00:10:00Z",
        "description": "Full game winner. Cancellations resolve 50-50.",
    }
    return {
        "id": event_id,
        "title": market["question"],
        "tags": [{"slug": "mlb"}],
        "markets": [market],
        "startTime": "2026-09-20T00:10:00Z",
    }


def http_client(handler, provider):
    return httpx.AsyncClient(
        base_url=(
            "https://external-api.kalshi.com/trade-api/v2"
            if provider == "kalshi"
            else "https://gamma-api.polymarket.com"
        ),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    "query,league,names",
    [
        ("Yankees", "mlb", {"New York Yankees"}),
        ("Padres", "mlb", {"San Diego Padres"}),
        ("Miami Padres", "mlb", {"Miami Marlins", "San Diego Padres"}),
        ("New York Y", "mlb", {"New York Yankees"}),
        ("MLB New York M", "mlb", {"New York Mets"}),
        ("Chiefs vs Bills", "nfl", {"Kansas City Chiefs", "Buffalo Bills"}),
        ("NFL Giants", "nfl", {"New York Giants"}),
        (
            "NCAA football Ohio State vs Michigan",
            "ncaa_football",
            {"Ohio State Buckeyes", "Michigan Wolverines"},
        ),
        ("NCAA football PSU", "ncaa_football", {"Penn State Nittany Lions"}),
        ("USC Trojans", "ncaa_football", {"USC Trojans"}),
        ("NCAA football Miami (OH)", "ncaa_football", {"Miami (OH) RedHawks"}),
    ],
)
def test_exact_alias_resolution(query, league, names):
    result = resolve_query(query)
    assert result.clarification is None
    assert result.league == league
    assert {t.name for t in result.teams} == names


async def test_kalshi_automatic_series_wrong_opponents_siblings_and_doubleheaders():
    event, milestone = kalshi_fixture()
    second, second_milestone = kalshi_fixture("KXMLBGAME-OPAQUE-2")
    second_milestone["start_date"] = "2026-09-20T20:10:00Z"
    wrong, wrong_milestone = kalshi_fixture("KXMLBGAME-WRONG", ("New York M", "San Diego"))
    event["markets"].insert(
        0,
        {
            **event["markets"][0],
            "ticker": "PROP",
            "title": "New York Y first inning runs",
            "floor_strike": 1,
        },
    )
    calls = []

    def handler(request):
        calls.append(request)
        if "/series/" in request.url.path:
            return httpx.Response(
                200,
                json={"series": {"ticker": "KXMLBGAME", "title": KALSHI_SERIES["KXMLBGAME"][1]}},
            )
        assert request.url.params["with_nested_markets"] == "true"
        assert request.url.params["with_milestones"] == "true"
        return httpx.Response(
            200,
            json={
                "events": [wrong, second, event, event],
                "milestones": [milestone, second_milestone, wrong_milestone],
                "cursor": "",
            },
        )

    async with http_client(handler, "kalshi") as http:
        markets, coverage = await search_kalshi(
            KalshiClient(http_client=http),
            resolve_query("Yankees vs Padres"),
            status=MarketStatus.OPEN,
            limit=10,
            series_ticker=None,
            event_date=date(2026, 9, 20),
        )
    assert len(calls) == 2
    assert len(markets) == 4
    assert [m.event_id for m in markets] == [event["event_ticker"]] * 2 + [
        second["event_ticker"]
    ] * 2
    assert coverage.candidates_matched == 4
    assert not coverage.truncated
    summary = project(markets[0], detail=True)
    assert summary.sports.scheduled_start < summary.close_time
    assert summary.resolution_deadline > summary.expected_resolution_time
    assert summary.outcome_quotes[0].price_kind == "last_trade"
    assert summary.outcome_quotes[1].price_kind == "derived_complement"
    assert summary.outcome_quotes[1].label == "Not New York Y"
    assert summary.last_trade_at is None and summary.price_observed_at is None
    assert summary.market_url == ("https://kalshi.com/markets/kxmlbgame/x/kxmlbgame-opaque-1")
    assert summary.rules.endswith("Postponements within two days count.")


async def test_poly_siblings_wrong_opponents_pagination_named_prices():
    right = poly_fixture()
    wrong = poly_fixture("102", "202", ("New York Mets", "San Diego Padres"))
    right["markets"].insert(
        0,
        {
            **right["markets"][0],
            "id": "203",
            "sportsMarketType": "totals",
            "outcomes": '["Over","Under"]',
        },
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["q"] == "New York Yankees vs San Diego Padres"
        assert request.url.params["events_tag"] == "mlb"
        assert request.url.params["optimized"] == "false"
        return httpx.Response(
            200,
            json={
                "events": [wrong] if len(calls) == 1 else [right, right],
                "pagination": {"hasMore": len(calls) == 1, "totalResults": 2},
            },
        )

    async with http_client(handler, "polymarket") as http:
        markets, coverage = await search_polymarket(
            PolymarketClient(http_client=http),
            resolve_query("Yankees Padres"),
            status=MarketStatus.OPEN,
            limit=1,
            event_date=None,
        )
    assert len(calls) == 2
    assert [m.market_id for m in markets] == ["201"]
    assert coverage.pages_scanned == 2 and coverage.provider_total == 2
    assert "before local" in coverage.total_meaning
    summary = project(markets[0])
    assert summary.yes_price is None and summary.yes_bid is None
    assert summary.outcome_quotes[0].label == "New York Yankees"
    assert str(summary.outcome_quotes[0].price) == "0.42"
    assert summary.outcome_quotes[0].price_kind == "provider_snapshot"
    assert str(summary.provider_last_trade_price) == "0.44"
    assert summary.provider_last_trade_outcome is None
    assert summary.market_url == "https://polymarket.com/market/provider-slug"
    assert summary.api_url.startswith("https://gamma-api.polymarket.com/")


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
async def test_repeated_pages_stop_with_honest_coverage(provider):
    event, milestone = kalshi_fixture()
    calls = []

    def handler(request):
        calls.append(request)
        if "/series/" in request.url.path:
            return httpx.Response(
                200,
                json={"series": {"ticker": "KXMLBGAME", "title": KALSHI_SERIES["KXMLBGAME"][1]}},
            )
        return httpx.Response(
            200,
            json=(
                {"events": [event], "milestones": [milestone], "cursor": "same"}
                if provider == "kalshi"
                else {
                    "events": [poly_fixture()],
                    "pagination": {"hasMore": True, "totalResults": 90},
                }
            ),
        )

    async with http_client(handler, provider) as http:
        cls = KalshiClient if provider == "kalshi" else PolymarketClient
        fn = search_kalshi if provider == "kalshi" else search_polymarket
        markets, coverage = await fn(
            cls(http_client=http),
            resolve_query("Yankees"),
            status=MarketStatus.OPEN,
            limit=10,
            event_date=None,
            **({"series_ticker": None} if provider == "kalshi" else {}),
        )
    assert coverage.pages_scanned == 2
    assert coverage.has_more and coverage.truncated
    assert coverage.continuation
    assert "repeated" in coverage.stop_reason
    assert len(markets) == (2 if provider == "kalshi" else 1)


@pytest.mark.parametrize("change", ["schedule", "identity", "participant"])
def test_kalshi_conflicts_are_not_silently_normalized(change):
    event, milestone = kalshi_fixture()
    milestones = [milestone]
    if change == "schedule":
        milestones.append({**milestone, "start_date": "2026-09-21T00:00:00Z"})
    elif change == "identity":
        event["markets"][0]["event_ticker"] = "OTHER"
    else:
        event["markets"][0]["custom_strike"] = {
            "baseball_team": participant("New York M", "mlb").kalshi_id
        }
    with pytest.raises(MarketValidationError):
        kalshi_event(event, milestones)


async def test_college_catalog_fallback_repairs_empty_search_with_shared_budget():
    event = poly_fixture(labels=("Ohio State", "Michigan"))
    event["tags"] = [{"slug": "cfb"}]
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/public-search":
            assert request.url.params["q"] == "Michigan"
            assert request.url.params["events_tag"] == "cfb"
            return httpx.Response(
                200, json={"events": [], "pagination": {"hasMore": False, "totalResults": 0}}
            )
        if request.url.path == "/sports":
            return httpx.Response(200, json=[{"sport": "cfb", "series": "987"}])
        assert request.url.params["series_id"] == "987"
        assert request.url.params["limit"] == "10"
        assert request.url.params["offset"] == "0"
        return httpx.Response(200, json=[event])

    async with http_client(handler, "polymarket") as http:
        markets, coverage = await search_polymarket(
            PolymarketClient(http_client=http),
            resolve_query("NCAA football Michigan"),
            status=MarketStatus.OPEN,
            limit=5,
            event_date=None,
        )
    assert len(calls) == 3 and coverage.pages_scanned == 2
    assert len(markets) == 1
    assert coverage.provider_total == 0  # Search total is NOT the catalog or contract count.
    assert "fallback" in coverage.scope
    assert project(markets[0]).outcome_quotes[1].canonical_participant == "Michigan Wolverines"


async def test_search_page_budget_leaves_continuation():
    calls = []

    def handler(request):
        calls.append(request)
        page = len(calls)
        event = poly_fixture(str(page), str(page + 100))
        return httpx.Response(
            200, json={"events": [event], "pagination": {"hasMore": True, "totalResults": 100}}
        )

    async with http_client(handler, "polymarket") as http:
        markets, coverage = await search_polymarket(
            PolymarketClient(http_client=http),
            resolve_query("Yankees"),
            status=MarketStatus.OPEN,
            limit=10,
            event_date=None,
        )
    assert len(calls) == 3 and len(markets) == 3
    assert coverage.truncated and coverage.stop_reason == "page_budget"
    assert coverage.continuation[0]["page"] == 4
    assert coverage.selection_required and coverage.candidate_event_count == 3


def test_market_id_collision_cannot_change_event():
    from datetime import UTC, datetime

    from market_agent.providers.polymarket import _parse_market
    from market_agent.providers.sports_search import retain

    event = poly_fixture()
    market = _parse_market(event["markets"][0], event_id="1", retrieved_at=datetime.now(UTC))
    results = {}
    retain(results, market)
    with pytest.raises(MarketValidationError, match="conflicting events"):
        retain(results, market.model_copy(update={"event_id": "2"}))


async def test_dirty_polymarket_record_is_discarded_without_losing_valid_candidate():
    valid = poly_fixture()
    dirty = poly_fixture("dirty-event", "dirty-market")
    dirty["markets"][0]["outcomes"] = "not-json"

    def handler(request):
        return httpx.Response(
            200,
            json={
                "events": [dirty, valid],
                "pagination": {"hasMore": False, "totalResults": 2},
            },
        )

    async with http_client(handler, "polymarket") as http:
        markets, coverage = await search_polymarket(
            PolymarketClient(http_client=http),
            resolve_query("Yankees"),
            status=MarketStatus.OPEN,
            limit=2,
        )
    assert [market.market_id for market in markets] == ["201"]
    assert coverage.discarded_record_count == 1
    assert coverage.warnings[0].record_id == "dirty-market"
    assert "outcomes" in coverage.warnings[0].message


def test_polymarket_resolution_uses_explicit_metadata_not_trade_price():
    from market_agent.providers.polymarket import _parse_market

    event = poly_fixture()
    market = event["markets"][0]
    market.update(
        active=False,
        closed=True,
        umaResolutionStatus="resolved",
        closedTime="2026-09-20T05:00:00Z",
        outcomePrices='["1","0"]',
    )
    summary = project(_parse_market(market, event_id=event["id"], retrieved_at=datetime.now(UTC)))
    assert summary.status == MarketStatus.RESOLVED
    assert summary.winning_outcome == "New York Yankees"
    assert summary.settlement_value == 1
    assert summary.resolved_at == datetime(2026, 9, 20, 5, tzinfo=UTC)
