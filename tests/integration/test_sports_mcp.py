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
from market_agent.mcp.kalshi import create_server as kalshi_server
from market_agent.mcp.polymarket import create_server as poly_server
from market_agent.mcp.tavily import create_server as tavily_server
from market_agent.providers import KalshiClient, PolymarketClient
from market_agent.providers.research import TavilyResearchClient

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


@asynccontextmanager
async def tavily_connection(mode="success"):
    calls = []

    def handler(request):
        calls.append(request)
        if mode == "error":
            return httpx.Response(503, text="sensitive research diagnostic")
        return httpx.Response(
            200,
            json={
                "query": "generated query",
                "request_id": "request-1",
                "results": [
                    {
                        "title": "Yankees vs Padres injuries — September 20, 2026",
                        "url": "https://sports.example/yankees-padres",
                        "content": (
                            "New York Yankees and San Diego Padres play on September 20, 2026."
                        ),
                        "score": 0.9,
                        "published_date": "Sun, 20 Sep 2026 16:00:00 GMT",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        server = tavily_server(TavilyResearchClient(http_client=http))
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
        assert result.structuredContent["result_kind"] == "sports_games"
        assert result.structuredContent["contracts_location"] == "games[].contracts"
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
        assert market["quote_freshness"] == "timestamp_unavailable"
        assert market["quote_is_stale"] is False
        assert market["quote_stale_reason"] is None
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
@pytest.mark.parametrize("mode", ["timeout", "429", "503", "malformed"])
async def test_sports_errors_are_controlled(provider, mode):
    async with connection(provider, mode) as (session, _):
        result = await session.call_tool(provider + "_search_markets", {"query": "Yankees"})
        assert result.isError
        assert "sensitive diagnostic" not in str(result)
        assert (await session.list_tools()).tools


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


async def test_agent_blocks_tavily_before_typed_game_detail_without_upstream_call():
    @asynccontextmanager
    async def connect():
        async with tavily_connection() as (session, calls):
            connect.calls = calls
            yield await load_mcp_tools(session)

    connect.calls = []
    model = ScriptedModel(
        replies=[
            tool_call(
                "tavily_search_game_evidence",
                {
                    "league": "mlb",
                    "team_a": "New York Yankees",
                    "team_b": "San Diego Padres",
                    "game_date": "2026-09-20",
                    "scheduled_start": "2026-09-20T00:10:00Z",
                    "focus": "injuries",
                },
            ),
            AIMessage("Research was correctly blocked until the game is verified."),
        ]
    )

    turn = await ChatAgent(model, connect).chat_detailed("Research this game", "blocked-research")

    assert connect.calls == []
    assert [item.model_dump() for item in turn.activity] == [
        {
            "tool": "tavily_search_game_evidence",
            "server": "tavily",
            "status": "skipped",
            "arguments": {
                "league": "mlb",
                "team_a": "New York Yankees",
                "team_b": "San Diego Padres",
                "game_date": "2026-09-20",
                "scheduled_start": "2026-09-20T00:10:00Z",
                "focus": "injuries",
            },
            "summary": "Research identity did not match a typed detail observation.",
            "duration_ms": turn.activity[0].duration_ms,
        }
    ]
