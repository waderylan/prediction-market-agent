"""Real MCP discovery/calls and existing agent consumption of additive sports schemas."""

import json
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy

import httpx
import pytest
from jsonschema import validate
from langchain_core.messages import AIMessage, ToolMessage
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.shared.memory import create_connected_server_and_client_session
from test_chat import ScriptedModel, tool_call

from market_agent.agent import ChatAgent
from market_agent.jev import JevReviewer
from market_agent.mcp.kalshi import create_server as kalshi_server
from market_agent.mcp.polymarket import create_server as poly_server
from market_agent.providers import KalshiClient, PolymarketClient

pytestmark = pytest.mark.integration


def fixture(provider):
    if provider == "kalshi":
        event = {
            "event_ticker": "KXMLBGAME-OPAQUE",
            "series_ticker": "KXMLBGAME",
            "title": "New York Y vs San Diego",
            "markets": [
                {
                    "ticker": f"KXMLBGAME-OPAQUE-{i}",
                    "event_ticker": "KXMLBGAME-OPAQUE",
                    "title": label + " wins",
                    "yes_sub_title": label,
                    "status": "active",
                    "last_price_dollars": "0.42",
                    "rules_primary": "Full game winner.",
                }
                for i, label in enumerate(["New York Y", "San Diego"])
            ],
        }
        return {
            "events": [event],
            "cursor": "",
            "milestones": [
                {
                    "related_event_tickers": ["KXMLBGAME-OPAQUE"],
                    "start_date": "2026-09-20T00:10:00Z",
                }
            ],
        }
    return {
        "events": [
            {
                "id": "101",
                "title": "Yankees vs Padres",
                "tags": [{"slug": "mlb"}],
                "markets": [
                    {
                        "id": "201",
                        "question": "Yankees vs Padres",
                        "sportsMarketType": "moneyline",
                        "outcomes": '["Yankees","Padres"]',
                        "outcomePrices": '["0.42","0.58"]',
                        "active": True,
                        "acceptingOrders": True,
                        "gameStartTime": "2026-09-20T00:10:00Z",
                        "description": "Full game winner.",
                        "slug": "provider-supplied-slug",
                    }
                ],
            }
        ],
        "pagination": {"hasMore": False, "totalResults": 1},
    }


@asynccontextmanager
async def connection(provider, mode="success"):
    calls = []
    payload = fixture(provider)
    if mode == "dirty":
        if provider == "kalshi":
            dirty = deepcopy(payload["events"][0]["markets"][0])
            dirty["ticker"] = "KXMLBGAME-OPAQUE-DIRTY"
            dirty["last_price_dollars"] = "not-a-price"
            payload["events"][0]["markets"].insert(0, dirty)
        else:
            dirty_event = deepcopy(payload["events"][0])
            dirty_event["id"] = "dirty-event"
            dirty_event["markets"][0]["id"] = "dirty-market"
            dirty_event["markets"][0]["outcomes"] = "not-json"
            payload["events"].insert(0, dirty_event)
    if mode == "short_resolved_offset" and provider == "polymarket":
        market = payload["events"][0]["markets"][0]
        market.update(
            active=False,
            acceptingOrders=False,
            closed=True,
            umaResolutionStatus="resolved",
            closedTime="2025-09-21 09:02:18+00",
            outcomePrices='["1","0"]',
        )

    def handler(request):
        calls.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("sensitive diagnostic")
        if mode == "503":
            return httpx.Response(503, text="sensitive diagnostic")
        if mode == "429":
            return httpx.Response(429, text="sensitive diagnostic")
        if mode == "malformed":
            return httpx.Response(200, json={"events": False})
        if mode == "pagination" and provider == "polymarket":
            payload["pagination"]["hasMore"] = True
        if "/series/" in request.url.path:
            return httpx.Response(
                200, json={"series": {"ticker": "KXMLBGAME", "title": "Professional Baseball Game"}}
            )
        if "/markets/" in request.url.path:
            market = payload["events"][0]["markets"][0].copy()
            if provider == "kalshi":
                return httpx.Response(200, json={"market": market})
            # Gamma currently omits event metadata from some market-detail responses.
            return httpx.Response(200, json=market)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        base_url="https://test", transport=httpx.MockTransport(handler)
    ) as http:
        client = (KalshiClient if provider == "kalshi" else PolymarketClient)(
            http_client=http, max_retries=0
        )
        server = (kalshi_server if provider == "kalshi" else poly_server)(client)
        async with create_connected_server_and_client_session(server) as session:
            yield session, calls


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
async def test_sports_schema_and_call(provider):
    async with connection(provider) as (session, calls):
        tools = {t.name: t for t in (await session.list_tools()).tools}
        name = provider + "_search_markets"
        properties = tools[name].inputSchema["properties"]
        assert properties["limit"]["minimum"] == 1
        assert properties["limit"]["maximum"] == 10
        assert properties["limit"]["type"] == "integer"
        assert {
            "league",
            "event_date",
            "local_date",
            "date_from",
            "date_to",
            "timezone",
            "next_game_only",
            "most_recent_game_only",
            "continuation",
        } <= properties.keys()
        bad = await session.call_tool(name, {"query": "Yankees", "limit": 20})
        assert bad.isError and not calls
        unclear = await session.call_tool(name, {"query": "Giants"})
        assert unclear.structuredContent["clarification"]
        assert not calls
        result = await session.call_tool(
            name,
            {
                "query": "Yankees vs Padres",
                "limit": 2,
                "timezone": "America/Los_Angeles",
            },
        )
        assert not result.isError
        validate(result.structuredContent, tools[name].outputSchema)
        assert result.structuredContent["markets"] == []
        game = result.structuredContent["games"][0]
        market = game["contracts"][0]
        assert game["local_date"] == "2026-09-19"
        assert game["timezone"] == "America/Los_Angeles"
        assert game["label"].endswith("PDT")
        assert market["sports"]["league"] == "mlb"
        assert market["sports"]["local_date"] == "2026-09-19"
        assert market["sports"]["scheduled_start_local"].endswith("-07:00")
        assert market["outcome_quotes"][0]["price"] == "0.42"
        assert market["quote_as_of"] is None
        assert market["quote_is_stale"] is True
        assert market["observation_id"].startswith(provider + ":")
        assert market["cache_hit"] is False
        assert market["market_url"].startswith(f"https://{provider}.com/")
        detail_name = provider + "_get_market"
        detail = await session.call_tool(detail_name, {"market_id": market["market_id"]})
        assert not detail.isError
        validate(detail.structuredContent, tools[detail_name].outputSchema)
        assert detail.structuredContent["sports"]["scheduled_start"] == "2026-09-20T00:10:00Z"
        assert detail.structuredContent["sports"] == market["sports"]
        assert detail.structuredContent["observation_id"] == market["observation_id"]
        assert detail.structuredContent["retrieved_at"] == market["retrieved_at"]
        assert detail.structuredContent["cache_hit"] is True
        assert detail.structuredContent["cache_age_ms"] >= 0


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
@pytest.mark.parametrize(
    "arguments,code,field,value",
    [
        (
            {"query": "Padres", "timezone": "Pacific/Nowhere"},
            "invalid_timezone",
            "timezone",
            "Pacific/Nowhere",
        ),
        (
            {"query": "Padres", "local_date": "2026-09-19", "date_from": "2026-09-18"},
            "conflicting_date_filters",
            "local_date",
            "2026-09-19",
        ),
        (
            {"query": "Padres", "date_from": "2026-09-21", "date_to": "2026-09-19"},
            "reversed_date_range",
            "date_from",
            "2026-09-21",
        ),
        (
            {"query": "Padres", "next_game_only": True, "most_recent_game_only": True},
            "conflicting_selectors",
            "next_game_only",
            "true",
        ),
    ],
)
async def test_semantic_validation_is_specific_and_makes_no_provider_call(
    provider, arguments, code, field, value
):
    async with connection(provider) as (session, calls):
        result = await session.call_tool(provider + "_search_markets", arguments)
        text = str(result)
        assert result.isError
        assert code in text and field in text and value.lower() in text.lower()
        assert calls == []


async def test_cursor_mismatches_are_specific_and_local():
    async with connection("polymarket", "pagination") as (session, calls):
        first = await session.call_tool(
            "polymarket_search_markets", {"query": "Yankees", "limit": 1}
        )
        cursor = first.structuredContent["discovery"]["next_cursor"]
        call_count = len(calls)
        mismatch = await session.call_tool(
            "polymarket_search_markets",
            {"query": "Padres", "limit": 1, "continuation": cursor},
        )
        assert mismatch.isError and "cursor_query_mismatch" in str(mismatch)
        assert len(calls) == call_count

    async with connection("kalshi") as (session, calls):
        mismatch = await session.call_tool(
            "kalshi_search_markets", {"query": "Yankees", "continuation": cursor}
        )
        assert mismatch.isError and "cursor_provider_mismatch" in str(mismatch)
        assert calls == []


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
async def test_dirty_record_returns_partial_mcp_result_with_warning(provider):
    async with connection(provider, "dirty") as (session, _):
        result = await session.call_tool(provider + "_search_markets", {"query": "Yankees"})
        assert not result.isError
        assert result.structuredContent["games"]
        discovery = result.structuredContent["discovery"]
        assert discovery["discarded_record_count"] == 1
        assert discovery["warnings"][0]["code"] == "discarded_provider_record"


async def test_resolved_polymarket_short_offset_survives_full_mcp_projection():
    async with connection("polymarket", "short_resolved_offset") as (session, _):
        result = await session.call_tool(
            "polymarket_search_markets",
            {"query": "Yankees", "status": "resolved", "limit": 1},
        )

        assert not result.isError
        contract = result.structuredContent["games"][0]["contracts"][0]
        assert contract["resolved_at"] == "2025-09-21T09:02:18Z"


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
@pytest.mark.parametrize("mode", ["timeout", "429", "503", "malformed"])
async def test_sports_errors_are_controlled(provider, mode):
    async with connection(provider, mode) as (session, _):
        result = await session.call_tool(provider + "_search_markets", {"query": "Yankees"})
        assert result.isError
        assert "sensitive diagnostic" not in str(result)
        assert (await session.list_tools()).tools


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
async def test_existing_agent_consumes_sports_search_and_detail(provider):
    @asynccontextmanager
    async def connect():
        async with connection(provider) as (session, _):
            yield await load_mcp_tools(session)

    market_id = "KXMLBGAME-OPAQUE-0" if provider == "kalshi" else "201"
    model = ScriptedModel(
        replies=[
            tool_call(provider + "_search_markets", {"query": "Yankees vs Padres"}, "1"),
            tool_call(provider + "_get_market", {"market_id": market_id}, "2"),
            AIMessage("Verified labeled sports prices"),
        ]
    )
    response = await ChatAgent(model, connect).chat("Find the game", "sports")
    assert response == "Verified labeled sports prices"
    messages = [m for m in model.observed[-1] if isinstance(m, ToolMessage)]
    assert len(messages) == 2 and all(m.status == "success" for m in messages)
    assert json.loads(messages[0].content)["games"][0]["league"] == "mlb"
    assert json.loads(messages[1].content)["outcome_quotes"][0]["price"] == "0.42"


async def test_agent_preserves_sports_detail_evidence_in_typed_cross_market_report():
    @asynccontextmanager
    async def connect():
        async with AsyncExitStack() as stack:
            poly, _ = await stack.enter_async_context(connection("polymarket"))
            kalshi, _ = await stack.enter_async_context(connection("kalshi"))
            yield [*await load_mcp_tools(poly), *await load_mcp_tools(kalshi)]

    model = ScriptedModel(
        replies=[
            tool_call("polymarket_search_markets", {"query": "Yankees vs Padres"}, "1"),
            tool_call("polymarket_get_market", {"market_id": "201"}, "2"),
            tool_call("kalshi_search_markets", {"query": "Yankees vs Padres"}, "3"),
            tool_call("kalshi_get_market", {"market_id": "KXMLBGAME-OPAQUE-0"}, "4"),
            AIMessage("The supplied sports terms still require settlement review."),
        ]
    )

    answer = await ChatAgent(model, connect).chat("Compare Yankees vs Padres", "sports-pair")
    assert "Equivalence unverified" in answer
    messages = [message for message in model.observed[-1] if isinstance(message, ToolMessage)]
    report = json.loads(messages[-1].content)["matching_report"]
    pair = report["pairs"][0]
    assert pair["event_identity"]["verdict"] == "match"
    assert pair["contract_equivalence"]["verdict"] == "ambiguous"
    assert (
        next(
            check
            for check in pair["contract_equivalence"]["checks"]
            if check["dimension"] == "named_outcome_mapping"
        )["state"]
        == "match"
    )
    assert pair["review_required"] is True
    assert pair["comparison_allowed"] is False
    assert report["market_to_game"] == []


async def test_agent_runs_jev_after_real_mcp_details_without_overwriting_deterministic_audit():
    @asynccontextmanager
    async def connect():
        async with AsyncExitStack() as stack:
            poly, _ = await stack.enter_async_context(connection("polymarket"))
            kalshi, _ = await stack.enter_async_context(connection("kalshi"))
            yield [*await load_mcp_tools(poly), *await load_mcp_tools(kalshi)]

    def jev_response(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answers": {
                    "equivalence": {
                        "type": "choice",
                        "choice": "equivalent",
                        "probabilities": {
                            "equivalent": 0.94,
                            "different": 0.01,
                            "ambiguous": 0.05,
                        },
                    }
                },
                "usage": {"inputTokens": 400},
                "providerMetadata": {"typesafe": {"confidence": {"equivalence": 0.8}}},
            },
        )

    model = ScriptedModel(
        replies=[
            tool_call("polymarket_search_markets", {"query": "Yankees vs Padres"}, "1"),
            tool_call("polymarket_get_market", {"market_id": "201"}, "2"),
            tool_call("kalshi_search_markets", {"query": "Yankees vs Padres"}, "3"),
            tool_call("kalshi_get_market", {"market_id": "KXMLBGAME-OPAQUE-0"}, "4"),
            AIMessage("The bounded semantic review passed."),
        ]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(jev_response)) as http:
        reviewer = JevReviewer("test-key", http_client=http)
        answer = await ChatAgent(model, connect, semantic_reviewer=reviewer).chat(
            "Compare Yankees vs Padres", "sports-jev"
        )

    report = json.loads(model.observed[-1][-1].content)["matching_report"]
    pair = report["pairs"][0]
    assert "Equivalent after Jev semantic review" in answer, pair
    assert pair["verdict"] == "equivalent"
    assert pair["comparison_allowed"] is True
    assert pair["semantic_review"]["provider"] == "jev"
    assert pair["contract_equivalence"]["verdict"] == "ambiguous"
