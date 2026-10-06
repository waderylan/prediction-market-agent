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
from market_agent.mcp.kalshi import create_server
from market_agent.providers import KalshiClient

pytestmark = pytest.mark.integration


@pytest.fixture
def kalshi_protocol(load_fixture):
    @asynccontextmanager
    async def connect(mode="success"):
        calls = []

        def handle(request):
            calls.append(request)
            if mode == "timeout":
                raise httpx.ConnectTimeout("private diagnostic")
            if mode in ("404", "503"):
                return httpx.Response(int(mode), text="private diagnostic")
            if mode == "missing":
                return httpx.Response(200, json={"market": {}})
            name = "market_success"
            if request.url.path.endswith("/series"):
                return httpx.Response(
                    200,
                    json={"series": [{"ticker": "KXPRESPERSON", "title": "Presidential election"}]},
                )
            if "/series/" in request.url.path:
                name = "series_success"
            elif request.url.path.endswith("/events"):
                name = "search_empty" if mode == "empty" else "search_success"
            elif mode == "malformed":
                name = "market_malformed"
            payload = load_fixture("kalshi", name)
            if mode == "long" and name == "market_success":
                payload["market"]["rules_primary"] = "r" * 20000
            if mode == "ambiguous" and name == "market_success":
                payload["market"]["rules_primary"] = (
                    "The winner is determined under official rules."
                )
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(
            base_url="https://external-api.kalshi.com/trade-api/v2",
            transport=httpx.MockTransport(handle),
        ) as http:
            server = create_server(KalshiClient(http_client=http, max_retries=0))
            async with create_connected_server_and_client_session(server) as session:
                yield session, calls

    return connect


async def test_kalshi_discovery_and_tools(kalshi_protocol):
    async with kalshi_protocol() as (session, calls):
        tools = {t.name: t for t in (await session.list_tools()).tools}
        assert set(tools) == {"kalshi_search_series", "kalshi_search_markets", "kalshi_get_market"}
        for name, args in [
            ("kalshi_search_series", {"query": "presidential election"}),
            ("kalshi_search_markets", {"query": "Vance presidential election", "limit": 2}),
            ("kalshi_get_market", {"market_id": "KXPRESPERSON-28-JVAN"}),
        ]:
            result = await session.call_tool(name, args)
            assert not result.isError
            validate(result.structuredContent, tools[name].outputSchema)
            assert "provider_data" not in result.model_dump_json()
        assert len(calls) == 4  # series catalog, events, market, series detail


@pytest.mark.parametrize("mode", ["timeout", "404", "503", "missing", "malformed"])
async def test_kalshi_errors(kalshi_protocol, mode):
    async with kalshi_protocol(mode) as (session, _):
        result = await session.call_tool("kalshi_get_market", {"market_id": "KXTEST-1"})
        assert result.isError
        assert "private diagnostic" not in str(result)
        assert len((await session.list_tools()).tools) == 3


async def test_agent_series_to_markets_to_details(kalshi_protocol):
    @asynccontextmanager
    async def connect():
        async with kalshi_protocol() as (session, _):
            yield await load_mcp_tools(session)

    model = ScriptedModel(
        replies=[
            tool_call("kalshi_search_series", {"query": "presidential election"}, "1"),
            tool_call(
                "kalshi_search_markets",
                {"query": "Vance presidential election", "series_ticker": "KXPRESPERSON"},
                "2",
            ),
            tool_call("kalshi_get_market", {"market_id": "KXPRESPERSON-28-JVAN"}, "3"),
            AIMessage("Done"),
        ]
    )
    assert await ChatAgent(model, connect).chat("Find the Kalshi contract", "series") == "Done"
    messages = [m for m in model.observed[-1] if isinstance(m, ToolMessage)]
    assert len(messages) == 3
    assert all(m.status == "success" for m in messages)
    assert json.loads(messages[0].content)["series"][0]["ticker"] == "KXPRESPERSON"
    assert json.loads(messages[1].content)["series_ticker"] == "KXPRESPERSON"
