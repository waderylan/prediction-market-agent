"""Real MCP discovery/calls and existing agent consumption of additive sports schemas."""

import json
from contextlib import asynccontextmanager

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

    def handler(request):
        calls.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("sensitive diagnostic")
        if mode == "503":
            return httpx.Response(503, text="sensitive diagnostic")
        if mode == "malformed":
            return httpx.Response(200, json={"events": False})
        if "/series/" in request.url.path:
            return httpx.Response(
                200, json={"series": {"ticker": "KXMLBGAME", "title": "Professional Baseball Game"}}
            )
        if "/markets/" in request.url.path:
            market = payload["events"][0]["markets"][0].copy()
            if provider == "kalshi":
                return httpx.Response(200, json={"market": market})
            market["events"] = [{k: v for k, v in payload["events"][0].items() if k != "markets"}]
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
        assert "league" in properties and "event_date" in properties
        bad = await session.call_tool(name, {"query": "Yankees", "limit": 20})
        assert bad.isError and not calls
        unclear = await session.call_tool(name, {"query": "Giants"})
        assert unclear.structuredContent["clarification"]
        assert not calls
        result = await session.call_tool(name, {"query": "Yankees vs Padres", "limit": 2})
        assert not result.isError
        validate(result.structuredContent, tools[name].outputSchema)
        market = result.structuredContent["markets"][0]
        assert market["sports"]["league"] == "mlb"
        assert market["outcome_quotes"][0]["price"] == "0.42"
        detail_name = provider + "_get_market"
        detail = await session.call_tool(detail_name, {"market_id": market["market_id"]})
        assert not detail.isError
        validate(detail.structuredContent, tools[detail_name].outputSchema)
        assert detail.structuredContent["sports"]["scheduled_start"] == "2026-09-20T00:10:00Z"


@pytest.mark.parametrize("provider", ["kalshi", "polymarket"])
@pytest.mark.parametrize("mode", ["timeout", "503", "malformed"])
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
    assert json.loads(messages[0].content)["markets"][0]["sports"]["league"] == "mlb"
    assert json.loads(messages[1].content)["outcome_quotes"][0]["price"] == "0.42"
