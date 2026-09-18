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
        assert set(tools) == {"kalshi_search_markets", "kalshi_get_market"}
        for name, args in [
            ("kalshi_search_markets", {"query": "Vance presidential election", "limit": 2}),
            ("kalshi_get_market", {"market_id": "KXPRESPERSON-28-JVAN"}),
        ]:
            result = await session.call_tool(name, args)
            assert not result.isError
            validate(result.structuredContent, tools[name].outputSchema)
            assert "provider_data" not in result.model_dump_json()
        assert len(calls) == 3  # events, market, series


@pytest.mark.parametrize(
    "name,args",
    [
        ("kalshi_search_markets", {"query": " "}),
        ("kalshi_search_markets", {"query": "x", "limit": 11}),
        ("kalshi_search_markets", {"query": "x", "status": "paused"}),
        ("kalshi_search_markets", {"query": "x", "limit": True}),
        ("kalshi_get_market", {"market_id": "../x"}),
        ("kalshi_get_market", {"market_id": "lower-case"}),
        ("kalshi_get_market", {"market_id": "X" * 101}),
        ("kalshi_get_market", {"market_id": 12}),
    ],
)
async def test_kalshi_invalid_before_upstream(kalshi_protocol, name, args):
    async with kalshi_protocol() as (session, calls):
        assert (await session.call_tool(name, args)).isError
        assert not calls


@pytest.mark.parametrize("mode", ["timeout", "404", "503", "missing", "malformed"])
async def test_kalshi_errors(kalshi_protocol, mode):
    async with kalshi_protocol(mode) as (session, _):
        result = await session.call_tool("kalshi_get_market", {"market_id": "KXTEST-1"})
        assert result.isError
        assert "private diagnostic" not in str(result)
        assert len((await session.list_tools()).tools) == 2


async def test_kalshi_empty_and_bound(kalshi_protocol):
    async with kalshi_protocol("empty") as (session, _):
        result = await session.call_tool("kalshi_search_markets", {"query": "unknown"})
        assert result.structuredContent["markets"] == []
        assert "three-page" in result.structuredContent["coverage"]
    async with kalshi_protocol("long") as (session, _):
        result = await session.call_tool("kalshi_get_market", {"market_id": "KXPRESPERSON-28-JVAN"})
        assert result.structuredContent["rules_truncated"]
        assert len(result.structuredContent["rules"]) == 12000


@pytest.mark.parametrize("platforms", [[], ["polymarket"], ["kalshi"], ["polymarket", "kalshi"]])
@pytest.mark.parametrize("mode", ["success", "ambiguous"])
async def test_platform_tool_paths(kalshi_protocol, load_fixture, platforms, mode):
    from market_agent.mcp.polymarket import create_server as poly_server
    from market_agent.providers import PolymarketClient

    @asynccontextmanager
    async def connect():
        async with (
            httpx.AsyncClient(
                base_url="https://gamma-api.polymarket.com",
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(200, json=load_fixture("polymarket", "market_success"))
                ),
            ) as http,
            create_connected_server_and_client_session(
                poly_server(PolymarketClient(http_client=http))
            ) as poly,
            kalshi_protocol(mode) as (kalshi, _),
        ):
            yield [*await load_mcp_tools(poly), *await load_mcp_tools(kalshi)]

    replies = [
        tool_call(
            f"{p}_get_market",
            {"market_id": "561229" if p == "polymarket" else "KXPRESPERSON-28-JVAN"},
            str(i),
        )
        for i, p in enumerate(platforms)
    ]
    model = ScriptedModel(replies=[*replies, AIMessage("Done"), AIMessage("Earlier quote")])
    agent = ChatAgent(model, connect)
    answer = await agent.chat("Inspect selected contracts", "a")
    assert answer.endswith("Done")
    if len(platforms) == 2:
        assert (
            "Not equivalent: settlement trigger" if mode == "success" else "Equivalence unverified"
        ) in answer
        # Deterministic evidence reaches the model BEFORE its final synthesis.
        audit = json.loads(model.observed[-1][-1].content)["matching_report"]
        assert audit["pairs"][0]["verdict"] == ("different" if mode == "success" else "ambiguous")
        assert audit["pairs"][0]["review_required"] == (mode == "ambiguous")
        assert not audit["pairs"][0]["comparison_allowed"]
    returned = [m for m in model.observed[-1] if isinstance(m, ToolMessage)]
    assert len(returned) == len(platforms)
    assert all(m.status == "success" for m in returned)
    assert await agent.chat("What was that quote?", "a") == "Earlier quote"
