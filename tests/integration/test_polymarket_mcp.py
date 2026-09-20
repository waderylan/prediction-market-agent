import json
from datetime import timedelta

import httpx
import pytest
from jsonschema import validate
from mcp.shared.memory import create_connected_server_and_client_session

from market_agent.mcp.polymarket import create_server
from market_agent.providers import PolymarketClient

pytestmark = pytest.mark.integration


@pytest.fixture
def protocol(load_fixture):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def connect(mode="success"):
        calls = []

        def handler(request):
            calls.append(request)
            if mode == "timeout":
                raise httpx.ReadTimeout("private upstream detail", request=request)
            if mode in ("404", "429", "503"):
                return httpx.Response(int(mode), text="private upstream detail")
            if mode == "json":
                return httpx.Response(200, text="not json")
            if mode == "missing":
                return httpx.Response(200, json={})
            name = "search_success" if request.url.path == "/public-search" else "market_success"
            if mode == "empty":
                name = "search_empty"
            if mode == "malformed":
                name = "market_malformed"
            payload = load_fixture("polymarket", name)
            if mode == "long":
                payload["description"] = "r" * 20000
            if mode == "oversized":
                payload["question"] = "t" * 501
            if mode == "nan":
                payload["bestBid"] = "NaN"
            if mode == "unresolved":
                payload.update(closed=True, outcomePrices='["0", "0"]')
            if mode == "teams":
                payload["outcomes"] = '["Padres", "Marlins"]'
            if mode == "many":
                original = payload["events"][0]["markets"][0]
                payload["events"][0]["markets"] = [
                    {**original, "id": str(561229 + i)} for i in range(30)
                ]
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(
            base_url="https://gamma-api.polymarket.com", transport=httpx.MockTransport(handler)
        ) as http:
            server = create_server(PolymarketClient(http_client=http, max_retries=0))
            async with create_connected_server_and_client_session(
                server, read_timeout_seconds=timedelta(seconds=5)
            ) as session:
                yield session, calls

    return connect


async def test_discovery_and_calls(protocol):
    async with protocol() as (session, calls):
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert set(tools) == {"polymarket_search_markets", "polymarket_get_market"}
        search = tools["polymarket_search_markets"]
        assert search.inputSchema["properties"]["limit"]["maximum"] == 10
        assert search.inputSchema["properties"]["query"]["maxLength"] == 200
        assert "ctx" not in search.inputSchema["properties"]
        for name, args in [
            ("polymarket_search_markets", {"query": "Vance", "limit": 2}),
            ("polymarket_get_market", {"market_id": "561229"}),
        ]:
            result = await session.call_tool(name, args)
            assert not result.isError
            validate(result.structuredContent, tools[name].outputSchema)
            assert "provider_data" not in json.dumps(result.structuredContent)
        assert len(calls) == 2


@pytest.mark.parametrize(
    "args",
    [
        {"query": " "},
        {"query": "x" * 201},
        {"query": 42},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 11},
        {"query": "x", "limit": True},
        {"query": "x", "status": "invalid"},
        {},
    ],
)
async def test_invalid_search_before_upstream(protocol, args):
    async with protocol() as (session, calls):
        assert (await session.call_tool("polymarket_search_markets", args)).isError
        assert not calls


@pytest.mark.parametrize("identifier", ["", "../1", "slug", "1" * 21, 123])
async def test_invalid_identifier(protocol, identifier):
    async with protocol() as (session, calls):
        assert (await session.call_tool("polymarket_get_market", {"market_id": identifier})).isError
        assert not calls


@pytest.mark.parametrize(
    "mode", ["timeout", "404", "429", "503", "json", "missing", "malformed", "oversized", "nan"]
)
async def test_controlled_errors_and_session_survives(protocol, mode):
    async with protocol(mode) as (session, _):
        result = await session.call_tool("polymarket_get_market", {"market_id": "561229"})
        assert result.isError
        assert "private upstream detail" not in str(result)
        assert len((await session.list_tools()).tools) == 2


@pytest.mark.parametrize("mode", ["teams", "unresolved"])
async def test_quote_and_settlement_semantics(protocol, mode):
    async with protocol(mode) as (session, _):
        result = await session.call_tool("polymarket_get_market", {"market_id": "561229"})
        assert not result.isError
        if mode == "teams":
            assert result.structuredContent["yes_bid"] is None
            assert result.structuredContent["yes_ask"] is None
        else:
            assert result.structuredContent["status"] == "closed"


async def test_empty_bounded_and_truncated(protocol):
    async with protocol("empty") as (session, _):
        result = await session.call_tool("polymarket_search_markets", {"query": "unlikely"})
        assert result.structuredContent["markets"] == []
    async with protocol("many") as (session, _):
        result = await session.call_tool(
            "polymarket_search_markets", {"query": "Vance", "limit": 3}
        )
        assert len(result.structuredContent["markets"]) == 3
        assert len(result.model_dump_json()) < 30000
    async with protocol("long") as (session, _):
        result = await session.call_tool("polymarket_get_market", {"market_id": "561229"})
        assert result.structuredContent["rules_truncated"]
        assert len(result.structuredContent["rules"]) == 12000
        assert len(result.model_dump_json()) < 40000
