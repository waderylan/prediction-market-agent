"""Sports identity, contract scope, coverage, and quote regressions without live games."""

import asyncio
import copy
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from market_agent.domain import MarketStatus
from market_agent.mcp.common import group_games, project
from market_agent.providers import KalshiClient, MarketValidationError, PolymarketClient
from market_agent.providers.sports import date_bounds, participant, resolve_query, teams
from market_agent.providers.sports_search import (
    KALSHI_SERIES,
    _polymarket_search_terms,
    kalshi_event,
    poly_event,
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


@pytest.mark.parametrize(
    "query",
    [
        "Giants",
        "MLB New York",
        "MLB Chicago",
        "NFL Los Angeles",
        "NCAA football Miami",
        "NCAA football OSU",
        "USC",
        "NCAA football USC",
        "Yankees vs Mystery",
        "Yankees tomorrow",
        "Yankees spread",
        "NCAA football FCS Michigan",
        "MLB Yankees NFL",
    ],
)
def test_ambiguity_unknown_opponent_and_unsupported_scope_require_clarification(query):
    assert resolve_query(query).clarification


def test_team_catalog_and_same_city_identity():
    assert len([t for t in teams() if t.league == "mlb"]) == 30
    assert len([t for t in teams() if t.league == "nfl"]) == 32
    assert participant("New York Y", "mlb").name == "New York Yankees"
    assert participant("New York M", "mlb").name == "New York Mets"
    assert participant("New York", "mlb") is None
    assert participant("Michigan", "ncaa_football").name == "Michigan Wolverines"
    assert participant("Michigan State", "ncaa_football").name == "Michigan State Spartans"
    assert participant("padreship", "mlb") is None
    assert resolve_query("Bitcoin").league is None


@pytest.mark.parametrize(
    "query,expected_query,expected_tag",
    [
        ("Marlins vs Padres", "Miami Marlins vs San Diego Padres", "mlb"),
        ("Bengals vs Texans", "Bengals vs Texans", "nfl"),
        (
            "NCAA football Coastal Carolina vs Delaware",
            "Coastal Carolina vs Delaware",
            "cfb",
        ),
        ("NCAA football Michigan", "Michigan", "cfb"),
    ],
)
def test_polymarket_search_terms_preserve_the_full_requested_matchup(
    query, expected_query, expected_tag
):
    assert _polymarket_search_terms(resolve_query(query)) == (expected_query, expected_tag)


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


async def test_polymarket_matchup_query_and_local_date_find_a_prior_game_on_page_two():
    earlier = poly_fixture("1016819", "4610001", ("Miami Marlins", "San Diego Padres"))
    target = poly_fixture("1022709", "4610002", ("Miami Marlins", "San Diego Padres"))
    target["startTime"] = "2026-09-20T20:10:00Z"
    target["markets"][0]["gameStartTime"] = "2026-09-20T20:10:00Z"
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["q"] == "Miami Marlins vs San Diego Padres"
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "events": [earlier] if page == 1 else [target],
                "pagination": {"hasMore": page == 1, "totalResults": 6},
            },
        )

    start, end, zone = date_bounds(local_date=date(2026, 9, 20), timezone="America/Los_Angeles")
    async with http_client(handler, "polymarket") as http:
        markets, coverage = await search_polymarket(
            PolymarketClient(http_client=http),
            resolve_query("Miami Marlins vs San Diego Padres"),
            status=None,
            limit=10,
            date_range=(start, end),
            timezone=zone.key,
        )

    assert len(calls) == 2
    assert [market.event_id for market in markets] == ["1022709"]
    assert markets[0].provider_data["sports"]["local_date"] == "2026-09-20"
    assert coverage.events_scanned == 2 and not coverage.truncated


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


async def test_ncaa_scope_shares_page_budget():
    calls = []

    def handler(request):
        calls.append(request)
        if "/series/" in request.url.path:
            ticker = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(
                200, json={"series": {"ticker": ticker, "title": KALSHI_SERIES[ticker][1]}}
            )
        return httpx.Response(200, json={"events": [], "cursor": str(len(calls))})

    async with http_client(handler, "kalshi") as http:
        _, coverage = await search_kalshi(
            KalshiClient(http_client=http),
            resolve_query("NCAA football Montana"),
            status=None,
            limit=10,
            series_ticker=None,
            event_date=None,
        )
    event_calls = [r for r in calls if r.url.path.endswith("/events")]
    assert len(calls) == 5 and len(event_calls) == 3
    assert {r.url.params["series_ticker"] for r in event_calls} == {"KXNCAAFGAME", "KXNCAAFCSGAME"}
    assert coverage.stop_reason == "page_budget" and coverage.truncated


async def test_resolved_sibling_does_not_filter_event_to_settled():
    event, milestone = kalshi_fixture()
    event["markets"][0]["status"] = "finalized"

    def handler(request):
        if "/series/" in request.url.path:
            return httpx.Response(
                200,
                json={"series": {"ticker": "KXMLBGAME", "title": KALSHI_SERIES["KXMLBGAME"][1]}},
            )
        assert "status" not in request.url.params
        return httpx.Response(
            200, json={"events": [event], "milestones": [milestone], "cursor": ""}
        )

    async with http_client(handler, "kalshi") as http:
        markets, _ = await search_kalshi(
            KalshiClient(http_client=http),
            resolve_query("Yankees"),
            status=MarketStatus.RESOLVED,
            limit=10,
            series_ticker=None,
            event_date=None,
        )
    assert len(markets) == 1 and markets[0].status == MarketStatus.RESOLVED


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


def test_unknown_time_is_not_derived_from_trading_close():
    event, _ = kalshi_fixture()
    assert kalshi_event(event, []).scheduled_start is None
    event = poly_fixture()
    del event["startTime"]
    del event["markets"][0]["gameStartTime"]
    assert poly_event(event, event["markets"][0]).scheduled_start is None


@pytest.mark.parametrize("change", ["naive", "conflict", "duplicate", "malformed"])
def test_poly_sports_metadata_errors(change):
    event = poly_fixture()
    market = event["markets"][0]
    if change == "naive":
        market["gameStartTime"] = "2026-09-20T00:10:00"
    elif change == "conflict":
        event["startTime"] = "2026-09-21T00:10:00Z"
    elif change == "duplicate":
        market["outcomes"] = '["Yankees","New York Yankees"]'
    else:
        market["outcomes"] = "{}"
    with pytest.raises(MarketValidationError):
        poly_event(event, market)


@pytest.mark.parametrize(
    "payload",
    [
        {"events": {}},
        {"events": False},
        {"events": [None]},
        {"events": [], "pagination": {"hasMore": "yes"}},
        {"events": [], "pagination": {"hasMore": True, "totalResults": -1}},
    ],
)
async def test_malformed_search_is_an_error(payload):
    async with http_client(lambda r: httpx.Response(200, json=payload), "polymarket") as http:
        with pytest.raises(MarketValidationError):
            await search_polymarket(
                PolymarketClient(http_client=http),
                resolve_query("Yankees"),
                status=MarketStatus.OPEN,
                limit=5,
                event_date=None,
            )


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
async def test_cancellation_propagates(provider):
    def handler(request):
        raise asyncio.CancelledError

    async with http_client(handler, provider) as http:
        cls = KalshiClient if provider == "kalshi" else PolymarketClient
        fn = search_kalshi if provider == "kalshi" else search_polymarket
        with pytest.raises(asyncio.CancelledError):
            await fn(
                cls(http_client=http),
                resolve_query("Yankees"),
                status=MarketStatus.OPEN,
                limit=5,
                event_date=None,
                **({"series_ticker": None} if provider == "kalshi" else {}),
            )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
async def test_kalshi_nonfinite_price_typed_error(value):
    event, _ = kalshi_fixture()
    market = copy.deepcopy(event["markets"][0])
    market["last_price_dollars"] = value
    # Generic ID avoids optional sports metadata retrieval.
    market.update(ticker="TEST", event_ticker="TEST-EVENT")
    async with http_client(
        lambda r: httpx.Response(200, json={"market": market}), "kalshi"
    ) as http:
        with pytest.raises(MarketValidationError):
            await KalshiClient(http_client=http).get_market("TEST")


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


async def test_kalshi_series_metadata_change_fails_before_catalog():
    async with http_client(
        lambda r: httpx.Response(
            200, json={"series": {"ticker": "KXMLBGAME", "title": "First inning winner"}}
        ),
        "kalshi",
    ) as http:
        with pytest.raises(MarketValidationError, match="series metadata"):
            await search_kalshi(
                KalshiClient(http_client=http),
                resolve_query("Yankees"),
                status=MarketStatus.OPEN,
                limit=5,
                series_ticker=None,
                event_date=None,
            )


async def test_sports_precision_filter_cannot_switch_contract_type():
    calls = []
    async with http_client(lambda r: calls.append(r), "kalshi") as http:
        with pytest.raises(ValueError, match="conflicts"):
            await search_kalshi(
                KalshiClient(http_client=http),
                resolve_query("Yankees"),
                status=MarketStatus.OPEN,
                limit=5,
                series_ticker="KXMLBSPREAD",
                event_date=None,
            )
    assert not calls


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


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
def test_event_title_conflicting_opponent_is_rejected(provider):
    if provider == "kalshi":
        event, milestone = kalshi_fixture()
        event["title"] = "New York Y vs New York M"
        with pytest.raises(MarketValidationError, match="participants conflict"):
            kalshi_event(event, [milestone])
    else:
        event = poly_fixture()
        event["title"] = "Yankees vs Mets"
        with pytest.raises(MarketValidationError, match="participants conflict"):
            poly_event(event, event["markets"][0])


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
def test_malformed_nested_metadata_is_typed(provider):
    if provider == "kalshi":
        event, milestone = kalshi_fixture()
        milestone["related_event_tickers"] = None
        with pytest.raises(MarketValidationError):
            kalshi_event(event, [milestone])
    else:
        event = poly_fixture()
        event["tags"] = [{"slug": []}]
        with pytest.raises(MarketValidationError):
            poly_event(event, event["markets"][0])


def test_local_calendar_date_uses_requested_timezone():
    start, end, zone = date_bounds(local_date=date(2026, 9, 19), timezone="America/Los_Angeles")
    assert zone.key == "America/Los_Angeles"
    assert start == datetime(2026, 9, 19, 7, tzinfo=UTC)
    assert end == datetime(2026, 9, 20, 7, tzinfo=UTC)
    with pytest.raises(ValueError, match="IANA"):
        date_bounds(local_date=date(2026, 9, 19), timezone="Pacific/Nowhere")
    with pytest.raises(ValueError, match="date range"):
        date_bounds(local_date=date(2026, 9, 19), date_from=date(2026, 9, 18))


async def test_limit_counts_games_and_groups_both_kalshi_contracts():
    first, first_milestone = kalshi_fixture()
    second, second_milestone = kalshi_fixture("KXMLBGAME-OPAQUE-2")
    second_milestone["start_date"] = "2026-09-21T00:10:00Z"

    def handler(request):
        if "/series/" in request.url.path:
            return httpx.Response(
                200,
                json={"series": {"ticker": "KXMLBGAME", "title": KALSHI_SERIES["KXMLBGAME"][1]}},
            )
        return httpx.Response(
            200,
            json={
                "events": [second, first],
                "milestones": [first_milestone, second_milestone],
                "cursor": "",
            },
        )

    async with http_client(handler, "kalshi") as http:
        markets, coverage = await search_kalshi(
            KalshiClient(http_client=http),
            resolve_query("Yankees vs Padres"),
            status=MarketStatus.OPEN,
            limit=1,
            series_ticker=None,
            event_date=None,
        )
    assert len(markets) == 2
    assert {market.event_id for market in markets} == {first["event_ticker"]}
    assert coverage.candidate_event_count == 2 and coverage.truncated
    assert coverage.selection_required
    assert len(coverage.matching_events) == 2
    assert all(choice.provider == "kalshi" for choice in coverage.matching_events)
    assert all(
        choice.event_id and len(choice.participants) == 2 for choice in coverage.matching_events
    )
    assert all(
        choice.local_date and choice.scheduled_start_local for choice in coverage.matching_events
    )
    assert all("UTC" in choice.label for choice in coverage.matching_events)
    games = group_games(markets, timezone="America/Los_Angeles")
    assert len(games) == 1 and len(games[0].contracts) == 2
    assert games[0].local_date == "2026-09-19"
    assert "PDT" in games[0].label


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


async def test_dirty_kalshi_market_is_discarded_without_losing_game():
    event, milestone = kalshi_fixture()
    dirty = copy.deepcopy(event["markets"][0])
    dirty["ticker"] = "KXMLBGAME-OPAQUE-1-DIRTY"
    dirty["last_price_dollars"] = "not-a-price"
    event["markets"].insert(0, dirty)

    def handler(request):
        if "/series/" in request.url.path:
            return httpx.Response(
                200,
                json={"series": {"ticker": "KXMLBGAME", "title": KALSHI_SERIES["KXMLBGAME"][1]}},
            )
        return httpx.Response(
            200,
            json={"events": [event], "milestones": [milestone], "cursor": ""},
        )

    async with http_client(handler, "kalshi") as http:
        markets, coverage = await search_kalshi(
            KalshiClient(http_client=http),
            resolve_query("Yankees"),
            status=MarketStatus.OPEN,
            limit=1,
            series_ticker=None,
        )
    assert len(markets) == 2
    assert coverage.discarded_record_count == 1
    assert coverage.warnings[0].record_id == dirty["ticker"]


async def test_continuation_cursor_can_be_passed_back_to_kalshi_search():
    event, milestone = kalshi_fixture()
    event_cursors = []

    def handler(request):
        if "/series/" in request.url.path:
            return httpx.Response(
                200,
                json={"series": {"ticker": "KXMLBGAME", "title": KALSHI_SERIES["KXMLBGAME"][1]}},
            )
        event_cursors.append(request.url.params.get("cursor"))
        return httpx.Response(
            200,
            json={"events": [event], "milestones": [milestone], "cursor": "next-page"},
        )

    async with http_client(handler, "kalshi") as http:
        client = KalshiClient(http_client=http, max_search_pages=1)
        _, first = await search_kalshi(
            client,
            resolve_query("Yankees"),
            status=MarketStatus.OPEN,
            limit=1,
            series_ticker=None,
            event_date=None,
        )
        assert first.next_cursor
        await search_kalshi(
            client,
            resolve_query("Yankees"),
            status=MarketStatus.OPEN,
            limit=1,
            series_ticker=None,
            event_date=None,
            continuation=first.next_cursor,
        )
    assert event_cursors == [None, "next-page"]


def test_settlement_and_quote_freshness_are_explicit():
    from market_agent.providers.kalshi import _parse_market

    event, _ = kalshi_fixture()
    market = event["markets"][0]
    market.update(
        status="finalized",
        result="yes",
        settlement_value_dollars="1.0000",
        settlement_ts="2026-09-20T05:00:00Z",
        updated_time="2026-09-20T05:00:00Z",
        series_ticker="KXMLBGAME",
    )
    canonical = _parse_market(
        market,
        retrieved_at=datetime(2026, 9, 21, 6, tzinfo=UTC),
    ).model_copy(
        update={
            "provider_data": {
                **_parse_market(
                    market, retrieved_at=datetime(2026, 9, 21, 6, tzinfo=UTC)
                ).provider_data,
                "sports": kalshi_event(event, []).model_dump(mode="json"),
            }
        }
    )
    summary = project(canonical, detail=True)
    assert summary.settlement_value == 1
    assert summary.winning_outcome == "New York Y"
    assert summary.resolved_at == datetime(2026, 9, 20, 5, tzinfo=UTC)
    assert summary.quote_as_of is None
    assert summary.quote_is_stale
    assert "authoritative timestamp" in summary.quote_stale_reason


def test_open_quote_flags_old_provider_metadata():
    from market_agent.providers.kalshi import _parse_market

    event, _ = kalshi_fixture()
    market = event["markets"][0]
    market["updated_time"] = "2026-09-01T00:00:00Z"
    summary = project(_parse_market(market, retrieved_at=datetime(2026, 9, 19, tzinfo=UTC)))
    assert summary.quote_as_of is None
    assert summary.quote_is_stale
    assert "authoritative timestamp" in summary.quote_stale_reason


def test_invalid_expected_resolution_is_omitted_with_warning():
    from market_agent.providers.kalshi import _parse_market

    event, milestone = kalshi_fixture()
    event["markets"][0]["expected_expiration_time"] = "2026-09-19T23:00:00Z"
    canonical = _parse_market(event["markets"][0], retrieved_at=datetime(2026, 9, 19, tzinfo=UTC))
    canonical = canonical.model_copy(
        update={
            "provider_data": {
                **canonical.provider_data,
                "sports": kalshi_event(event, [milestone]).model_dump(mode="json"),
            }
        }
    )
    summary = project(canonical)
    assert summary.expected_resolution_time is None
    assert "preceded" in summary.timing_warning


def test_game_lifecycle_moves_from_pregame_to_awaiting_resolution():
    from market_agent.providers.kalshi import _parse_market

    event, milestone = kalshi_fixture()
    sports = kalshi_event(event, [milestone])
    market = event["markets"][0]
    before = _parse_market(market, retrieved_at=datetime(2026, 9, 19, 23, tzinfo=UTC))
    after = _parse_market(market, retrieved_at=datetime(2026, 9, 20, 7, tzinfo=UTC))
    before = before.model_copy(
        update={"provider_data": {**before.provider_data, "sports": sports.model_dump(mode="json")}}
    )
    after = after.model_copy(
        update={"provider_data": {**after.provider_data, "sports": sports.model_dump(mode="json")}}
    )
    assert group_games([before], timezone="UTC")[0].live_status == "pregame"
    assert group_games([after], timezone="UTC")[0].live_status == "awaiting_resolution"


async def test_polymarket_continuation_resumes_the_reported_page():
    requested_pages = []

    def handler(request):
        requested_pages.append(int(request.url.params["page"]))
        page = requested_pages[-1]
        return httpx.Response(
            200,
            json={
                "events": [poly_fixture(str(page), str(200 + page))],
                "pagination": {"hasMore": True, "totalResults": 20},
            },
        )

    async with http_client(handler, "polymarket") as http:
        client = PolymarketClient(http_client=http, max_search_pages=1)
        _, first = await search_polymarket(
            client,
            resolve_query("Yankees"),
            status=MarketStatus.OPEN,
            limit=1,
            event_date=None,
        )
        assert first.next_cursor
        await search_polymarket(
            client,
            resolve_query("Yankees"),
            status=MarketStatus.OPEN,
            limit=1,
            event_date=None,
            continuation=first.next_cursor,
        )
    assert requested_pages == [1, 2]


async def test_next_and_most_recent_game_selection():
    now = datetime.now(UTC).replace(microsecond=0)
    past, past_milestone = kalshi_fixture("KXMLBGAME-PAST")
    future, future_milestone = kalshi_fixture("KXMLBGAME-FUTURE")
    past_milestone["start_date"] = (now - timedelta(days=1)).isoformat()
    future_milestone["start_date"] = (now + timedelta(days=1)).isoformat()

    def handler(request):
        if "/series/" in request.url.path:
            return httpx.Response(
                200,
                json={"series": {"ticker": "KXMLBGAME", "title": KALSHI_SERIES["KXMLBGAME"][1]}},
            )
        return httpx.Response(
            200,
            json={
                "events": [future, past],
                "milestones": [future_milestone, past_milestone],
                "cursor": "",
            },
        )

    async with http_client(handler, "kalshi") as http:
        client = KalshiClient(http_client=http)
        upcoming, _ = await search_kalshi(
            client,
            resolve_query("Yankees"),
            status=MarketStatus.OPEN,
            limit=5,
            series_ticker=None,
            event_date=None,
            next_game_only=True,
        )
        recent, _ = await search_kalshi(
            client,
            resolve_query("Yankees"),
            status=MarketStatus.OPEN,
            limit=5,
            series_ticker=None,
            event_date=None,
            most_recent_game_only=True,
        )
    assert {market.event_id for market in upcoming} == {future["event_ticker"]}
    assert {market.event_id for market in recent} == {past["event_ticker"]}


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


def test_polymarket_resolution_normalizes_short_utc_offset():
    from market_agent.providers.polymarket import _parse_market

    event = poly_fixture()
    market = event["markets"][0]
    market.update(
        active=False,
        closed=True,
        umaResolutionStatus="resolved",
        closedTime="2025-09-21 09:02:18+00",
        outcomePrices='["1","0"]',
    )

    summary = project(_parse_market(market, event_id=event["id"], retrieved_at=datetime.now(UTC)))

    assert summary.resolved_at == datetime(2025, 9, 21, 9, 2, 18, tzinfo=UTC)
