import json
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import Field

from market_agent.agent import MAX_DATA_TOOL_CALLS, MAX_TOOL_CALLS, ChatAgent
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


def test_system_prompt_declares_partial_source_availability(connections):
    def answer(messages):
        prompt = next(message.content for message in messages if isinstance(message, SystemMessage))
        assert "available=polymarket" in prompt
        assert "unavailable=kalshi, sports_state, tavily" in prompt
        return AIMessage("Polymarket is available; the other requested sources are unavailable.")

    model = ScriptedModel(replies=[answer])
    with TestClient(create_app(ChatAgent(model, connections()))) as client:
        response = client.post(
            "/chat",
            json={"query": "Which sources can answer this?", "session_id": "sources"},
        )

    assert response.status_code == 200
    assert "Polymarket is available" in response.json()["response"]


def test_http_selects_allowlisted_model_and_effort(connections):
    model = ScriptedModel(replies=[AIMessage("Selected")])
    body = {
        "query": "Explain this",
        "session_id": "model-choice",
        "model": "terra",
        "reasoning_effort": "high",
    }
    with TestClient(create_app(ChatAgent(model, connections()))) as client:
        response = client.post("/chat", json=body)
    assert response.status_code == 200
    assert model.observed_options == [{"model": "gpt-5.6-terra", "reasoning_effort": "high"}]


def test_inspection_endpoint_returns_bounded_tool_activity(connections):
    model = ScriptedModel(replies=[tool_call(), AIMessage("Contract readout")])
    with TestClient(create_app(ChatAgent(model, connections()))) as client:
        response = client.post(
            "/chat/inspect",
            json={"query": "Read market 561229", "session_id": "trace"},
        )
    assert response.status_code == 200
    assert response.json()["response"] == "Contract readout"
    assert response.json()["activity"] == [
        {
            "tool": "polymarket_get_market",
            "server": "polymarket",
            "status": "success",
            "arguments": {"market_id": "561229"},
            "summary": "Retrieved polymarket 561229; YES 0.2105; rules included.",
            "duration_ms": response.json()["activity"][0]["duration_ms"],
        }
    ]
    assert response.json()["activity"][0]["duration_ms"] >= 0


def test_inspection_endpoint_distinguishes_no_tool_and_failure(connections):
    no_tool = ScriptedModel(replies=[AIMessage("General answer")])
    with TestClient(create_app(ChatAgent(no_tool, connections()))) as client:
        response = client.post(
            "/chat/inspect",
            json={"query": "Explain probability", "session_id": "no-tool"},
        )
    assert response.json() == {"response": "General answer", "activity": []}

    failed = ScriptedModel(replies=[tool_call(), AIMessage("Could not verify")])
    with TestClient(create_app(ChatAgent(failed, connections("error")))) as client:
        response = client.post(
            "/chat/inspect",
            json={"query": "Read market", "session_id": "failed-tool"},
        )
    activity = response.json()["activity"]
    assert activity[0]["status"] == "error"
    assert activity[0]["summary"] == "The polymarket tool failed or returned invalid data."
    assert "sensitive diagnostic" not in response.text


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


def test_connection_failure(connections):
    model = ScriptedModel()
    with TestClient(create_app(ChatAgent(model, connections("unavailable")))) as client:
        result = client.post("/chat", json={"query": "Read this market", "session_id": "a"})
    assert result.status_code == 200
    assert "could not be connected" in result.json()["response"]
    assert not model.observed


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": "x"},
        {"query": " ", "session_id": "a"},
        {"query": "x", "session_id": ""},
        {"query": 1, "session_id": "a"},
        {"query": "x", "session_id": "a", "extra": True},
        {"query": "x" * 4001, "session_id": "a"},
        {"query": "x", "session_id": "a/b"},
        {"query": "x", "session_id": "a", "model": "astra"},
        {"query": "x", "session_id": "a", "reasoning_effort": "ultra"},
    ],
)
def test_invalid_http(connections, body):
    model = ScriptedModel()
    with TestClient(create_app(ChatAgent(model, connections()))) as client:
        assert client.post("/chat", json=body).status_code == 422
    assert not model.observed


async def test_tool_budget_and_reset(connections):
    model = ScriptedModel(
        replies=[
            *[tool_call(call_id=str(i)) for i in range(MAX_DATA_TOOL_CALLS)],
            AIMessage("Limit reached"),
            tool_call(),
            AIMessage("Next turn"),
        ]
    )
    agent = ChatAgent(model, connections())
    assert await agent.chat("Research", "a") == "Limit reached"
    assert await agent.chat("Refresh", "a") == "Next turn"
    assert (
        len([m for m in model.observed[MAX_DATA_TOOL_CALLS] if isinstance(m, ToolMessage)])
        == MAX_DATA_TOOL_CALLS
    )


async def test_parallel_tool_requests_cannot_overflow_activity_contract(connections):
    calls = [
        {
            "name": "polymarket_get_market",
            "args": {"market_id": "561229"},
            "id": f"parallel-{index}",
        }
        for index in range(MAX_TOOL_CALLS + 2)
    ]
    model = ScriptedModel(replies=[AIMessage("", tool_calls=calls)])

    turn = await ChatAgent(model, connections()).chat_detailed("Read every copy", "parallel")

    assert len(turn.activity) == MAX_TOOL_CALLS
    assert [item.status for item in turn.activity[:MAX_DATA_TOOL_CALLS]] == [
        "success"
    ] * MAX_DATA_TOOL_CALLS
    assert [item.status for item in turn.activity[MAX_DATA_TOOL_CALLS:]] == ["skipped"] * (
        MAX_TOOL_CALLS - MAX_DATA_TOOL_CALLS
    )
    assert "tool-call limit was reached" in turn.response.lower()


async def test_model_failure_and_session_recovery(connections):
    model = ScriptedModel(replies=[RuntimeError("sensitive diagnostic"), AIMessage("Recovered")])
    agent = ChatAgent(model, connections())
    assert "could not finish" in await agent.chat("Hello", "a")
    assert await agent.chat("Retry", "a") == "Recovered"
