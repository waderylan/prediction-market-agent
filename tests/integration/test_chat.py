import json
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import Field

from market_agent.agent import ChatAgent
from market_agent.app import create_app
from market_agent.mcp.polymarket import create_server
from market_agent.providers import PolymarketClient

pytestmark = pytest.mark.integration


class ScriptedModel(BaseChatModel):
    """Test-only model script; proves orchestration, not semantic model quality."""

    replies: list = Field(default_factory=list)
    observed: list = Field(default_factory=list)
    observed_options: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "scripted-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.observed.append(messages)
        self.observed_options.append(kwargs)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        reply = item(messages) if callable(item) else item
        return ChatResult(generations=[ChatGeneration(message=reply)])


def tool_call(name="polymarket_get_market", args=None, call_id="call-1"):
    return AIMessage(
        "",
        tool_calls=[
            {
                "name": name,
                "args": args or {"market_id": "561229"},
                "id": call_id,
            }
        ],
    )


@pytest.fixture
def connections(load_fixture):
    def make(mode="success"):
        @asynccontextmanager
        async def connect():
            if mode == "unavailable":
                raise ConnectionError("sensitive diagnostic")
            if mode == "invalid_mcp":
                server = FastMCP("invalid-response", log_level="CRITICAL")

                @server.tool()
                async def polymarket_get_market(market_id: str) -> dict:
                    return {"unexpected": "sensitive diagnostic"}
            else:

                def handler(request):
                    if mode == "error":
                        return httpx.Response(503, text="sensitive diagnostic")
                    name = (
                        "search_success"
                        if request.url.path == "/public-search"
                        else "market_success"
                    )
                    return httpx.Response(200, json=load_fixture("polymarket", name))

                http = httpx.AsyncClient(
                    base_url="https://gamma-api.polymarket.com",
                    transport=httpx.MockTransport(handler),
                )
                server = create_server(PolymarketClient(http_client=http, max_retries=0))
            try:
                async with create_connected_server_and_client_session(server) as session:
                    yield await load_mcp_tools(session)
            finally:
                if mode != "invalid_mcp":
                    await http.aclose()

        return connect

    return make


def test_http_search_detail_and_observability(connections, caplog):
    caplog.set_level("INFO", logger="market_agent.agent")
    model = ScriptedModel(
        replies=[
            tool_call("polymarket_search_markets", {"query": "Vance", "limit": 2}),
            tool_call(call_id="call-2"),
            AIMessage("Contract readout"),
        ]
    )
    with TestClient(create_app(ChatAgent(model, connections()))) as client:
        response = client.post("/chat", json={"query": "Explain the contract", "session_id": "a"})
    assert response.status_code == 200
    assert response.json() == {"response": "Contract readout"}
    messages = model.observed[-1]
    tools = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tools) == 2 and all(m.status == "success" for m in tools)
    assert json.loads(tools[-1].content)["market_id"] == "561229"
    events = [r.safe_fields for r in caplog.records if r.message == "mcp_tool_finished"]
    assert [e["tool"] for e in events] == ["polymarket_search_markets", "polymarket_get_market"]
    assert "Explain the contract" not in caplog.text


def test_no_tool_memory_and_isolation(connections, caplog):
    def recall(messages):
        humans = [m.content for m in messages if isinstance(m, HumanMessage)]
        return AIMessage(humans[0] if len(humans) > 1 else "No prior context")

    model = ScriptedModel(replies=[AIMessage("Understood"), recall, recall])
    with TestClient(create_app(ChatAgent(model, connections()))) as client:
        assert (
            client.post(
                "/chat", json={"query": "Remember the election", "session_id": "a"}
            ).status_code
            == 200
        )
        same = client.post("/chat", json={"query": "Which topic?", "session_id": "a"})
        other = client.post("/chat", json={"query": "Which topic?", "session_id": "b"})
    assert same.json()["response"] == "Remember the election"
    assert other.json()["response"] == "No prior context"
    assert not any(isinstance(m, ToolMessage) for turn in model.observed for m in turn)


@pytest.mark.parametrize("mode", ["error", "invalid_mcp"])
def test_tool_failure_is_controlled(connections, mode):
    def explain(messages):
        assert messages[-1].status == "error"
        assert "sensitive diagnostic" not in messages[-1].content
        return AIMessage("Could not verify market data")

    model = ScriptedModel(replies=[tool_call(), explain])
    with TestClient(create_app(ChatAgent(model, connections(mode)))) as client:
        result = client.post("/chat", json={"query": "Read this market", "session_id": "a"})
    assert result.status_code == 200
    assert result.json()["response"] == "Could not verify market data"


async def test_one_mcp_failure_preserves_other_provider_result(load_fixture):
    def polymarket_handler(request):
        return httpx.Response(200, json=load_fixture("polymarket", "market_success"))

    http = httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com",
        transport=httpx.MockTransport(polymarket_handler),
    )
    polymarket = create_server(PolymarketClient(http_client=http, max_retries=0))
    kalshi = FastMCP("failed-kalshi", log_level="CRITICAL")

    @kalshi.tool()
    async def kalshi_get_market(market_id: str) -> dict:
        raise ToolError("sensitive provider diagnostic")

    @asynccontextmanager
    async def connect():
        try:
            async with (
                create_connected_server_and_client_session(polymarket) as polymarket_session,
                create_connected_server_and_client_session(kalshi) as kalshi_session,
            ):
                yield [
                    *await load_mcp_tools(polymarket_session),
                    *await load_mcp_tools(kalshi_session),
                ]
        finally:
            await http.aclose()

    def answer(messages):
        results = [message for message in messages if isinstance(message, ToolMessage)]
        assert {message.tool_call_id for message in results} == {"good", "failed"}
        good = next(message for message in results if message.tool_call_id == "good")
        failed = next(message for message in results if message.tool_call_id == "failed")
        assert good.status == "success"
        assert json.loads(good.content)["market_id"] == "561229"
        assert failed.status == "error"
        assert "sensitive provider diagnostic" not in failed.content
        return AIMessage("Polymarket is verified; Kalshi is unavailable.")

    model = ScriptedModel(
        replies=[
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "polymarket_get_market",
                        "args": {"market_id": "561229"},
                        "id": "good",
                    },
                    {"name": "kalshi_get_market", "args": {"market_id": "bad"}, "id": "failed"},
                ],
            ),
            answer,
        ]
    )
    turn = await ChatAgent(model, connect).chat_detailed("Compare sources", "partial-failure")
    assert turn.response == "Polymarket is verified; Kalshi is unavailable."
    assert [(item.server, item.status) for item in turn.activity] == [
        ("polymarket", "success"),
        ("kalshi", "error"),
    ]


def test_connection_failure(connections):
    model = ScriptedModel()
    with TestClient(create_app(ChatAgent(model, connections("unavailable")))) as client:
        result = client.post("/chat", json={"query": "Read this market", "session_id": "a"})
    assert result.status_code == 200
    assert "could not be connected" in result.json()["response"]
    assert not model.observed


async def test_parallel_tool_results_keep_each_call_id(connections):
    def answer(messages):
        results = [message for message in messages if isinstance(message, ToolMessage)]
        assert {message.tool_call_id for message in results} == {"first", "second"}
        assert all(message.status == "success" for message in results)
        assert all(json.loads(message.content)["market_id"] == "561229" for message in results)
        return AIMessage("Both tool results received")

    model = ScriptedModel(
        replies=[
            AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "polymarket_get_market",
                        "args": {"market_id": "561229"},
                        "id": "first",
                    },
                    {
                        "name": "polymarket_get_market",
                        "args": {"market_id": "561229"},
                        "id": "second",
                    },
                ],
            ),
            answer,
        ]
    )
    turn = await ChatAgent(model, connections()).chat_detailed("Read both", "parallel-results")
    assert turn.response == "Both tool results received"
    assert [item.status for item in turn.activity] == ["success", "success"]
